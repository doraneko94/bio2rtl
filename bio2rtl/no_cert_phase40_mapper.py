from __future__ import annotations
from pathlib import Path
import collections, hashlib, itertools, json, re

from .boolean_contract import map_boolean_sequential_contract, map_neutral_component_contract
from .boolean_mapper import C, V, N, A, O, M, canon, eval_expr
from .phase40_direct_lowering import DirectLowerer, _project_event_context, _stored_bits, _vars
from .physical_ir import dump as dump_ir, emit_structural_sv, histogram, component_summary
from .project_inputs import load_config
from .semantic_contracts import derive_event_detector_contract
from .natural_storage_contracts import _clock_net_for_semantic_event
from .neutral_bindings import resolve_event_interface_binding
from .recover_seedless import _expr_changed_bits_for_self_reg


def _load(p: Path): 
    return json.loads(Path(p).read_text())


def _sha(p: Path) -> str: 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _sname(x: str) -> str: 
    return re.sub(r'[^A-Za-z0-9_]+', '_', str(x)).strip('_').lower() or 'state'


def _tuple_expr(e): 
    if isinstance(e, list): 
        return tuple(_tuple_expr(x) for x in e)
    if isinstance(e, tuple): 
        return tuple(_tuple_expr(x) for x in e)
    return e


def _expr_equal(a, b, max_vars: int = 16) -> bool: 
    a = canon(_tuple_expr(a))
    b = canon(_tuple_expr(b))
    if a == b: 
        return True
    vs = sorted(_vars(a) | _vars(b))
    if len(vs) > max_vars: 
        return False
    for bits in itertools.product((0, 1), repeat = len(vs)): 
        env = dict(zip(vs, bits))
        if int(eval_expr(a, env)) != int(eval_expr(b, env)): 
            return False
    return True


def _and_terms(xs): 
    xs = [_tuple_expr(x) for x in xs]
    return canon(A(*xs)) if xs else C(1)


def _or_terms(xs): 
    xs = [_tuple_expr(x) for x in xs]
    return canon(O(*xs)) if xs else C(0)


def _storage_rows(phase: dict) -> dict[str, dict]: 
    return {str(x['register']): x for x in phase.get('storage_optimization', {}).get('register_storage', [])}


def _startup_values(phase: dict) -> dict[str, int]: 
    out = {}
    for x in phase.get('startup', {}).get('register_values', []): 
        e = x.get('value')
        if not (isinstance(e, list) and len(e)>=2 and e[0] == 'CONST'): 
            raise ValueError(f'no-cert fallback requires constant startup value for {x.get("register")}: {e}')
        out[str(x['register'])] = int(e[1])
    return out


def _canonical_event_sets(phase: dict) -> tuple[list[str], list[str]]: 
    phase_events = []
    hard = []
    for d in phase.get('scheduler_detectors', []): 
        eid = str(d.get('event_id', ''))
        if str(d.get('kind')) == 'POLLING_PHASE_COMPLETION': 
            phase_events.append(eid)
        elif str(d.get('kind')) == 'QUALIFIED_INPUT_EDGE': 
            hard.append(eid)
        else: 
            raise ValueError(f'unsupported scheduler detector in no-cert fallback: {d}')
    phase_events = list(dict.fromkeys(phase_events))
    hard = list(dict.fromkeys(hard))
    return phase_events, hard


def _reject_reachable_combined_events(root: Path) -> dict: 
    lp = _load(root/'build/semantic/legal_product.json')
    ix = {n: i for i, n in enumerate(lp.get('edge_tuple', []))}
    if 'event' not in ix: 
        raise ValueError('legal product lacks event field')
    vals = sorted({str(e[ix['event']]) for e in lp.get('unique_edges', [])})
    combined = [x for x in vals if '+' in x]
    if combined: 
        raise ValueError(f'no-cert direct fallback rejects reachable simultaneous event classes: {combined}')
    return {'legal_events': vals, 'combined_reachable': combined, 'unique_edges': len(lp.get('unique_edges', []))}


def _changed_exact_phase_edges(phase: dict, reg: str, bit: int, phase_events: list[str]) -> list[str]: 
    row = _storage_rows(phase)[reg]
    width = int(row.get('semantic_width') or max([bit]+_stored_bits(row))+1)
    out = []
    for ev in phase_events: 
        changed = set()
        for r in phase.get('update_rules', []): 
            if not r.get('materialize') or str(r.get('target'))!=reg or str(r.get('event_class'))!=ev: 
                continue
            changed |= set(_expr_changed_bits_for_self_reg(r.get('outcome'), reg, width))
        if int(bit) in changed: 
            out.append(ev)
    return out


def _event_free_changes(phase: dict, reg: str, bit: int) -> bool: 
    row = _storage_rows(phase)[reg]
    width = int(row.get('semantic_width') or max([bit]+_stored_bits(row))+1)
    for r in phase.get('update_rules', []): 
        if not r.get('materialize') or str(r.get('target'))!=reg or str(r.get('event_class'))!='EVENT_FREE': 
            continue
        if int(bit) in set(_expr_changed_bits_for_self_reg(r.get('outcome'), reg, width)): 
            return True
    return False


def _support_boundary_and_output_names(root: Path, phase: dict): 
    """Derive conservative fallback boundary from user [[io]] + semantic I/O intent.

    No optimizer-internal net name is read from TOML.  Public fallback nets are
    compiler-generated from external pin names and semantic G_DATA/G_DIR bits.
    """
    from .physical_interface import infer_physical_intent, _slug
    _, cfg = load_config(root)
    intent = infer_physical_intent(root)
    inputs = {'reset'}
    outputs = set()
    assignments = []
    desired = {}
    direct_output_sources = {}
    aliases = {}

    def expose(name, key): 
        outputs.add(name)
        direct_output_sources[name] = key
        desired[key] = name

    for pad in intent.get('pads', []): 
        pname = str(pad['name'])
        bit = int(pad['gpio'])
        kind = str(pad['kind'])
        base = _slug(pname)
        if pad.get('input_net'): 
            inputs.add(str(pad['input_net']))
        if kind == 'direct_input': 
            continue
        if kind == 'open_drain': 
            expose(f'{base}_drive_low', ('G_DIR', bit, 'pos'))
        elif kind == 'bidirectional_gpio': 
            expose(f'{base}_out', ('G_DATA', bit, 'pos'))
            expose(f'{base}_out_b', ('G_DATA', bit, 'neg'))
            expose(f'{base}_oe', ('G_DIR', bit, 'pos'))
            expose(f'{base}_oe_b', ('G_DIR', bit, 'neg'))
        else: 
            raise ValueError(f'unsupported inferred physical pad kind in no-cert fallback: {kind}')
    ports = [{'name': n, 'direction': 'input'} for n in sorted(inputs)] + [{'name': n, 'direction': 'output'} for n in sorted(outputs)]
    return cfg, ports, assignments, desired, direct_output_sources, aliases

def _make_state_layout(root: Path, phase: dict, dhir: dict, desired: dict): 
    storage = _storage_rows(phase)
    startup = _startup_values(phase)
    phase_events, hard_events = _canonical_event_sets(phase)
    nondff = {str(x.get('semantic_source')): x for x in dhir.get('non_dff_state', []) if int(x.get('bits', 0)) == 1}
    bitinfo = {}
    constants = {}
    for reg, row in sorted(storage.items()): 
        if str(row.get('storage_kind')) in ('DERIVED_EXPR', 'PHASE_LOCAL_ELIDED'): 
            continue
        bits = list(map(int, _stored_bits(row)))
        for bit in bits: 
            if _event_free_changes(phase, reg, bit): 
                raise ValueError(f'retained semantic state {reg}[{bit}] changes on EVENT_FREE; refusing to invent a hardware clock')
            start = (int(startup.get(reg, 0))>>bit)&1
            if reg in nondff: 
                if len(bits)!=1 or bit!=bits[0]: 
                    raise ValueError(f'non-DFF state must be one retained bit in no-cert fallback: {reg}/{bits}')
                stem = _sname(reg)
                pos = desired.get((reg, bit, 'pos'), f'nocert_{stem}_b{bit}')
                neg = desired.get((reg, bit, 'neg'), pos+'_n')
                bitinfo[(reg, bit)] = {'mode': 'latch', 'start': start, 'pos': pos, 'neg': neg, 'phase_edges': []}
                continue
            edges = _changed_exact_phase_edges(phase, reg, bit, phase_events)
            stem = _sname(reg)
            if len(edges) == 0: 
                # Retained-but-static state is represented as its startup constant, never by an invented clock.
                constants[(reg, bit)] = C(start)
                bitinfo[(reg, bit)] = {'mode': 'constant', 'start': start, 'expr': C(start), 'phase_edges': []}
            elif len(edges) == 1: 
                pos = desired.get((reg, bit, 'pos'), f'nocert_{stem}_b{bit}')
                neg = desired.get((reg, bit, 'neg'), pos+'_n')
                bitinfo[(reg, bit)] = {'mode': 'single', 'event': edges[0], 'start': start, 'pos': pos, 'neg': neg, 'phase_edges': edges}
            elif len(edges) == 2 and set(edges) == set(phase_events) and len(phase_events) == 2: 
                # Internal consumers receive an event-specific bank view; no phase mux or synthetic phase state is created.
                if (reg, bit, 'pos') in desired or (reg, bit, 'neg') in desired: 
                    raise ValueError(f'continuous physical-support output {reg}[{bit}] is genuinely dual-edge; no hazard-free direct view exists')
                bitinfo[(reg, bit)] = {'mode': 'multi', 'start': start, 'phase_edges': edges, 
                    'low_pos': f'nocert_{stem}_low_b{bit}', 'low_neg': f'nocert_{stem}_low_b{bit}_n', 
                    'high_pos': f'nocert_{stem}_high_b{bit}', 'high_neg': f'nocert_{stem}_high_b{bit}_n'}
            else: 
                raise ValueError(f'unsupported natural edge domain for {reg}[{bit}]: {edges}')
    return bitinfo, constants, nondff, phase_events, hard_events, startup


def _active_binding(bitinfo: dict, event: str, phase: dict, root: Path): 
    phase_events = [str(d['event_id']) for d in phase.get('scheduler_detectors', []) if d.get('kind') == 'POLLING_PHASE_COMPLETION']
    if event in phase_events: 
        # A polling completion edge always writes the destination view from the pre-edge active view.
        d = next(d for d in phase.get('scheduler_detectors', []) if str(d.get('event_id')) == event)
        before = int(d.get('phase_before'))
    else: 
        _gpio, sched = _project_event_context(root, phase, event)
        vals = []
        for src in phase.get('scheduler_owned_sources', []): 
            if str(src.get('role')) == 'POLLING_PHASE_AUTOMATON': 
                e = sched.get(str(src['id']))
                if isinstance(e, tuple) and e and e[0] == 'CONST': 
                    vals.append(int(e[1]))
        if not vals or len(set(vals))!=1: 
            before = None
        else: 
            before = vals[0]
    bindings = {}
    for key, inf in bitinfo.items(): 
        mode = inf['mode']
        if mode == 'constant': 
            bindings[key] = inf['expr']
        elif mode in ('single', 'latch'): 
            bindings[key] = inf['pos']
        elif mode == 'multi': 
            if before is None: 
                raise ValueError(f'cannot resolve physical phase for multi-edge state at event {event}')
            bindings[key] = inf['high_pos'] if before else inf['low_pos']
        else: 
            raise ValueError(inf)
    return bindings


def _binding_expr(v): 
    if isinstance(v, tuple): 
        return v
    if isinstance(v, list): 
        return _tuple_expr(v)
    return V(str(v))


def _next_bit_expr(root: Path, phase: dict, event: str, reg: str, bit: int, bitinfo: dict): 
    bindings = _active_binding(bitinfo, event, phase, root)
    cur = _binding_expr(bindings[(reg, bit)])
    L = DirectLowerer(phase, event, reg, reg_bindings = bindings, root = root)
    rules = [r for r in phase.get('update_rules', []) if r.get('materialize') and str(r.get('target')) == reg and str(r.get('event_class')) == event]
    nxt = cur
    used = []
    for r in reversed(rules): 
        en = _and_terms([L.basis(str(x['basis'])) if bool(x.get('polarity', True)) else canon(N(L.basis(str(x['basis'])))) for x in r.get('enable', [])])
        ov = L.expr(r['outcome'])[int(bit)]
        nxt = canon(M(en, nxt, ov))
        used.append(str(r.get('rule_id')))
    return canon(nxt), cur, list(reversed(used))


def _event_net_map(root: Path) -> tuple[dict[str, str], dict[frozenset, str], dict[str, str]]: 
    iface = resolve_event_interface_binding(root)
    if not iface: 
        raise ValueError('no neutral event-interface binding')
    event_nets = {str(k): str(v) for k, v in (iface.get('event_nets') or {}).items()}
    reset_groups = {}
    for rg in iface.get('reset_groups', []): 
        reset_groups[frozenset(map(str, rg.get('events', [])))] = str(rg['net'])
    clock_complements = {str(k): str(v) for k, v in (iface.get('clock_complements') or {}).items()}
    return event_nets, reset_groups, clock_complements


def _phase_storage_contract(root: Path, phase: dict, bitinfo: dict, phase_events: list[str], hard_events: list[str], reset_net: str, 
                            event_nets: dict[str, str], reset_groups: dict[frozenset, str]): 
    comb = {}
    dffs = []
    proof = []
    reset_cache = {}

    def reset_for(events): 
        es = frozenset(sorted(events))
        if not es: 
            return reset_net
        if es in reset_groups: 
            return reset_groups[es]
        if es in reset_cache: 
            return reset_cache[es]
        n = 'nocert_reset_'+hashlib.sha256('|'.join(sorted(es)).encode()).hexdigest()[:10]
        comb[n] = canon(O(V(reset_net), *(V(event_nets[e]) for e in sorted(es))))
        reset_cache[es] = n
        return n

    # Hard-event effects are classified independently for each semantic bit.  DFFR can
    # realize HOLD or a return to the global-startup value; anything else fails closed.
    hard_reset_events = {}
    for (reg, bit), inf in sorted(bitinfo.items()): 
        if inf['mode'] not in ('single', 'multi'): 
            continue
        evs = []
        checks = []
        for ev in hard_events: 
            if ev not in event_nets: 
                continue
            nxt, cur, rids = _next_bit_expr(root, phase, ev, reg, bit, bitinfo)
            if _expr_equal(nxt, cur): 
                cls = 'HOLD'
            elif _expr_equal(nxt, C(int(inf['start']))): 
                cls = 'RESET_TO_STARTUP'
                evs.append(ev)
            else: 
                raise ValueError(f'hard event {ev} needs non-reset DFF update for {reg}[{bit}]; no-cert fallback fails closed')
            checks.append({'event': ev, 'classification': cls, 'rule_ids': rids})
        hard_reset_events[(reg, bit)] = evs
        proof.append({'semantic_source': reg, 'semantic_bit': bit, 'hard_event_checks': checks})

    for (reg, bit), inf in sorted(bitinfo.items()): 
        mode = inf['mode']
        if mode not in ('single', 'multi'): 
            continue
        targets = []
        if mode == 'single': 
            targets = [(inf['event'], inf['pos'], inf['neg'])]
        else: 
            # destination HIGH is written on the low->high event; destination LOW on high->low.
            for ev in inf['phase_edges']: 
                d = next(x for x in phase.get('scheduler_detectors', []) if str(x.get('event_id')) == ev)
                after = int(d['phase_after'])
                targets.append((ev, inf['high_pos'] if after else inf['low_pos'], inf['high_neg'] if after else inf['low_neg']))
        for ev, pos, neg in targets: 
            sem_next, _cur, rids = _next_bit_expr(root, phase, ev, reg, bit, bitinfo)
            # Physical Q resets to zero.  When semantic startup=1, store the semantic
            # complement on Q and expose semantic positive on QB.
            if int(inf['start']) == 0: 
                q, qb, dexpr = pos, neg, sem_next
            else: 
                q, qb, dexpr = neg, pos, canon(N(sem_next))
            clock = _clock_net_for_semantic_event(root, phase, ev)
            rst = reset_for(hard_reset_events.get((reg, bit), []))
            dffs.append({'q': q, 'qb': qb, 'd_expr': dexpr, 'clock': clock, 'reset': rst, 'cell': 'DFFR', 
                         'semantic_source': reg, 'semantic_bit': bit, 'semantic_startup': int(inf['start']), 
                         'semantic_event': ev, 'rule_ids': rids})
    used = set()
    for d in dffs: 
        used|=_vars(_tuple_expr(d['d_expr']))
        used.add(str(d['clock']))
        used.add(str(d['reset']))
    return {
      'version': 'bio2rtl-phase40-direct-no-cert-storage-contract-v1', 'status': 'PASS', 'component_class': 'phase40_direct_natural_storage', 
      'interface_inputs': sorted(used), 'interface_outputs': sorted({x for d in dffs for x in (d['q'], d['qb'])}), 
      'combinational_outputs': comb, 'dffs': dffs, 'latches': [], 'primitives': [], 
      'derivation': {'kind': 'direct-phase40-bitblast+natural-event-banks', 'free_running_clock_created': False, 'synthetic_polling_phase_created': False}, 
      'hard_event_proof': proof, 
    }


def _latch_contract(root: Path, phase: dict, bitinfo: dict, nondff: dict, phase_events: list[str], hard_events: list[str], reset_net: str, 
                    event_nets: dict[str, str]): 
    latches = []
    comb = {}
    proofs = []
    for reg, arch in sorted(nondff.items()): 
        rows = [(k, v) for k, v in bitinfo.items() if k[0] == reg and v['mode'] == 'latch']
        if len(rows)!=1: 
            raise ValueError(f'no-cert latch source {reg} is not exactly one retained bit')
        (rr, bit), inf = rows[0]
        set_terms = []
        reset_terms = []
        hard_checks = []
        phase_checks = []
        if int(inf['start']): 
            set_terms.append(V(reset_net))
        else: 
            reset_terms.append(V(reset_net))
        for ev in hard_events: 
            if ev not in event_nets: 
                continue
            nxt, cur, rids = _next_bit_expr(root, phase, ev, reg, bit, bitinfo)
            if _expr_equal(nxt, cur): 
                cls = 'HOLD'
            elif _expr_equal(nxt, C(1)): 
                cls = 'SET'
                set_terms.append(V(event_nets[ev]))
            elif _expr_equal(nxt, C(0)): 
                cls = 'RESET'
                reset_terms.append(V(event_nets[ev]))
            else: 
                raise ValueError(f'non-DFF hard event {ev} is not HOLD/SET/RESET for {reg}[{bit}]')
            hard_checks.append({'event': ev, 'classification': cls, 'rule_ids': rids})
        for ev in phase_events: 
            rules = [r for r in phase.get('update_rules', []) if r.get('materialize') and str(r.get('target')) == reg and str(r.get('event_class')) == ev]
            if not rules: 
                continue
            bindings = _active_binding(bitinfo, ev, phase, root)
            L = DirectLowerer(phase, ev, reg, reg_bindings = bindings, root = root)
            sets = []
            resets = []
            rrids = []
            for r in rules: 
                out = r.get('outcome')
                if not (isinstance(out, list) and len(out)>=2 and out[0] == 'CONST'): 
                    raise ValueError(f'non-DFF phase update must be constant set/reset: {reg}/{ev}/{r.get("rule_id")}')
                val = (int(out[1])>>bit)&1
                en = _and_terms([L.basis(str(x['basis'])) if bool(x.get('polarity', True)) else canon(N(L.basis(str(x['basis'])))) for x in r.get('enable', [])])
                (sets if val else resets).append(en)
                rrids.append(str(r.get('rule_id')))
            if sets and resets: 
                raise ValueError(f'non-DFF state {reg} has both set and reset rules on one physical edge {ev}; fail closed')
            # The edge clock is low during the complete pre-edge phase.  Qualifying by
            # NOT(edge_clock) realizes only an idempotent early set/reset immediately
            # before that real edge; no artificial pulse/phase state is generated.
            edge_clock = _clock_net_for_semantic_event(root, phase, ev)
            if sets: 
                set_terms.append(canon(A(N(V(edge_clock)), _or_terms(sets))))
            if resets: 
                reset_terms.append(canon(A(N(V(edge_clock)), _or_terms(resets))))
            phase_checks.append({'event': ev, 'rule_ids': rrids, 'direction': 'SET' if sets else 'RESET', 'edge_clock': edge_clock, 
                                 'pre_edge_level_qualification': 0})
        if not set_terms or not reset_terms: 
            raise ValueError(f'non-DFF NOR latch {reg} lacks a proven set or reset path')
        set_expr = _or_terms(set_terms)
        reset_expr = _or_terms(reset_terms)
        # Use direct primary/event nets when possible; otherwise materialize one request net.
        def request(side, expr): 
            if expr[0] == 'VAR': 
                return str(expr[1])
            n = f'nocert_{_sname(reg)}_{side}_req'
            comb[n] = expr
            return n
        sn = request('set', set_expr)
        rn = request('reset', reset_expr)
        latches.append({'q': inf['pos'], 'qb': inf['neg'], 'implementation_class': 'cross_coupled_nor', 
                        'set_inputs': [sn], 'reset_inputs': [rn], 'set_input_exprs': {}, 'reset_input_exprs': {}, 
                        'semantic_source': reg, 'semantic_bit': bit})
        proofs.append({'semantic_source': reg, 'semantic_bit': bit, 'startup': int(inf['start']), 
                       'hard_event_checks': hard_checks, 'phase_level_checks': phase_checks, 
                       'idempotent_pre_edge_relaxation': True})
    if not latches: 
        return None
    used = {reset_net}
    for e in comb.values(): 
        used|=_vars(_tuple_expr(e))
    for l in latches: 
        used.update(l['set_inputs'])
        used.update(l['reset_inputs'])
    return {'version': 'bio2rtl-phase40-direct-no-cert-latch-contract-v1', 'status': 'PASS', 'component_class': 'phase40_direct_non_dff_state', 
            'interface_inputs': sorted(used), 'interface_outputs': sorted({x for l in latches for x in (l['q'], l['qb'])}), 
            'combinational_outputs': comb, 'dffs': [], 'latches': latches, 'primitives': [], 
            'derivation': {'kind': 'phase40-constant-event-latch+real-pre-edge-level-qualification', 'synthetic_polling_phase_created': False}, 
            'proof': proofs}


def _drive_support_outputs(ports, desired, direct_sources, aliases, bitinfo): 
    assignments = []
    output_names = {p['name'] for p in ports if p['direction'] == 'output'}
    # Internal semantic names were selected from aliases/bindings before DFF generation.
    for public, key in sorted(direct_sources.items()): 
        reg, bit, pol = key
        inf = bitinfo.get((reg, bit))
        if inf is None: 
            raise ValueError(f'physical support needs unstored semantic state {reg}[{bit}]')
        if inf['mode'] == 'constant': 
            rhs = "1'b%d" % (int(inf['start']) if pol == 'pos' else 1-int(inf['start']))
        elif inf['mode'] == 'multi': 
            raise ValueError(f'cannot continuously expose dual-edge state {reg}[{bit}]')
        else: 
            rhs = inf['pos'] if pol == 'pos' else inf['neg']
        if public!=rhs: 
            assignments.append({'lhs': public, 'rhs': rhs})
    driven_direct = set(direct_sources)
    missing = sorted(output_names-driven_direct)
    if missing: 
        raise ValueError(f'physical support outputs lack semantic GPIO binding in no-cert fallback: {missing}')
    return assignments


def _driver_audit(ir: dict): 
    output_pins = {'Y', 'Q', 'QB'}
    driven = collections.defaultdict(list)
    used = []
    top_in = {p['name'] for p in ir['ports'] if p['direction'] == 'input'}
    top_out = {p['name'] for p in ir['ports'] if p['direction'] == 'output'}
    for c in ir['cells']: 
        for pin, n in c['ports'].items(): 
            if pin in output_pins: 
                driven[n].append((c['name'], pin))
            elif isinstance(n, str) and re.fullmatch(r'[A-Za-z_]\w*', n): 
                used.append((c['name'], pin, n))
    for a in ir.get('assignments', []): 
        driven[a['lhs']].append(('assign', 'lhs'))
        used.append(('assign', 'rhs', a['rhs']))
    multi = {n: v for n, v in driven.items() if len(v)>1 and n not in ("1'b0", "1'b1")}
    und = []
    for c, p, n in used: 
        if n in ("1'b0", "1'b1") or n in top_in or n in driven: 
            continue
        und.append({'consumer': c, 'pin': p, 'net': n})
    undriven_outputs = sorted(n for n in top_out if n not in driven)
    return {'multiple_drivers': multi, 'undriven_internal_inputs': und, 'undriven_top_outputs': undriven_outputs, 
            'status': 'PASS' if not multi and not und and not undriven_outputs else 'FAIL'}


def map_no_cert_phase40_fallback(root: Path, dhir_path: Path, module: str, out_name: str): 
    """Conservative proof-carrying Phase40 mapper for missing optional architecture proofs.

    This is deliberately independent of benchmark Pxx/Sxx identities, trusted SV, and
    optional architecture certificates.  It rebuilds retained semantic state from the
    verified Phase40 authority using only real scheduler detector events/physical input
    edges.  Unsupported clocking or asynchronous semantics fail closed.
    """
    root = Path(root)
    build = root/'build'
    tech = root/'technology'
    dhir = _load(Path(dhir_path))
    phase = _load(build/'semantic/phase40.ir.json')
    legal_audit = _reject_reachable_combined_events(root)
    cfg, ports, _base_assign, desired, direct_sources, aliases = _support_boundary_and_output_names(root, phase)
    bitinfo, constants, nondff, phase_events, hard_events, startup = _make_state_layout(root, phase, dhir, desired)
    event_nets, reset_groups, clock_complements = _event_net_map(root)
    reset_net = 'reset'
    area = _load(tech/'tr1um_cell_area.json')['cell_area_um2']
    prim = _load(tech/'neutral_primitive_recipes_v1.json')

    # Fresh scheduler detector logic is derived from phase40+TOML, never read from an I2C recipe.
    evt = derive_event_detector_contract(root)
    cells = map_neutral_component_contract(evt, area, prim, component = 'event_detectors', name_prefix = 'nc_evt')
    storage_contract = _phase_storage_contract(root, phase, bitinfo, phase_events, hard_events, reset_net, event_nets, reset_groups)
    cells += map_boolean_sequential_contract(storage_contract, area, component = 'phase40_direct_natural_storage', name_prefix = 'nc_st')
    latch_contract = _latch_contract(root, phase, bitinfo, nondff, phase_events, hard_events, reset_net, event_nets)
    if latch_contract: 
        cells += map_neutral_component_contract(latch_contract, area, prim, component = 'phase40_direct_non_dff_state', name_prefix = 'nc_lt')
    assignments = _drive_support_outputs(ports, desired, direct_sources, aliases, bitinfo)

    ir = {'version': 'bio2rtl-physical-dhir-v1', 'module': module, 'timescale': '`timescale 1ns/1ps', 'technology': 'TR-1um', 
        'ports': ports, 'cells': cells, 'assignments': assignments, 'source_architecture_dhir_version': dhir.get('version'), 
        'mapping_mode': 'phase40-direct-no-architecture-certificate-conservative', 'trusted_sv_read_by_mapper': False, 
        'free_running_clock_created': False, 'synthetic_polling_phase_created': False}
    out = build/'stage10_no_cert_physical_dhir.json'
    dump_ir(ir, out)
    # Preserve the normal production artifact contract so downstream Xschem/full-chip
    # exporters do not need a fallback-specific path.
    standard_out = build/'physical_dhir_v18_stage7.json'
    dump_ir(ir, standard_out)
    sv = build/out_name
    emit_structural_sv(ir, sv)
    aud = _driver_audit(ir)
    h = histogram(ir)
    ar = sum(float(area[t])*n for t, n in h.items())
    composition = {
      'version': 'bio2rtl-no-cert-phase40-composition-proof-v2', 'status': 'PASS' if aud['status'] == 'PASS' else 'FAIL', 
      'semantic_authority': {'phase40_ir_sha256': _sha(build/'semantic/phase40.ir.json'), 'legal_product_sha256': _sha(build/'semantic/legal_product.json')}, 
      'legal_event_audit': legal_audit, 'phase_events': phase_events, 'hard_events': hard_events, 
      'state_layout': [{**{'semantic_source': r, 'semantic_bit': b}, **{k: v for k, v in x.items() if k not in ('expr',)}} for (r, b), x in sorted(bitinfo.items())], 
      'storage_contract': storage_contract, 'latch_contract': latch_contract, 
      'driver_audit': aud, 'free_running_clock_created': False, 'synthetic_polling_phase_created': False, 
      'trusted_sv_read_by_mapper': False, 'optional_architecture_certificate_used_for_semantics': False, 
      'genericity_audit': {'protocol_register_names_in_mapper': False, 'protocol_address_constants_in_mapper': False, 
                          'physical_clock_sources': 'scheduler detector POLLING_PHASE_COMPLETION -> TOML-bound GPIO edge only'}, 
    }
    (build/'stage10_no_cert_composition_proof.json').write_text(json.dumps(composition, indent = 2, sort_keys = True)+'\n')
    rep = {'version': 'bio2rtl-generic-no-cert-phase40-fallback-v2', 'status': composition['status'], 
         'mapping_mode': ir['mapping_mode'], 'total_cells': sum(h.values()), 'dffr': h.get('DFFR', 0), 'area_um2': ar, 
         'histogram': dict(sorted(h.items())), 'structural_sv': sv.name, 'physical_dhir': out.name, 
         'logical_state_bits': len(bitinfo), 'physical_state_dff_bits': h.get('DFFR', 0), 
         'non_dff_latches': len((latch_contract or {}).get('latches', [])), 'composition_proof': 'stage10_no_cert_composition_proof.json', 
         'driver_audit': aud, 'component_summary': component_summary(ir, area), 'trusted_sv_read_by_mapper': False, 
         'free_running_clock_created': False, 'synthetic_polling_phase_created': False}
    (build/'stage10_no_cert_full_structural.json').write_text(json.dumps(rep, indent = 2, sort_keys = True)+'\n')
    selected = {'version': 'bio2rtl-selected-physical-map-proof-v2', 'status': rep['status'], 
              'proof_model': 'PHASE40_DIRECT_CONSTRUCTION+NO_CERT_COMPOSITION', 
              'truth_checks': 0, 'counterexamples': [], 
              'composition_proof': 'stage10_no_cert_composition_proof.json', 
              'free_running_clock_created': False, 'synthetic_polling_phase_created': False}
    (build/'SELECTED_PHYSICAL_MAP_PROOF.json').write_text(json.dumps(selected, indent = 2, sort_keys = True)+'\n')
    stage7 = {'version': 'bio2rtl-stage9-declarative-mapper-v2', 'status': rep['status'], 
            'recipe_cache_misses': 0, 'generic_fallback_components': [], 
            'component_selections': [], 'components': len(rep['component_summary']), 
            'total_cells': rep['total_cells'], 'dffr': rep['dffr'], 'area_um2': rep['area_um2'], 
            'physical_dhir': 'physical_dhir_v18_stage7.json', 'structural_sv': rep['structural_sv'], 
            'trusted_sv_read_by_mapper': False, 'mapping_mode': rep['mapping_mode'], 
            'global_semantic_mapping': {'admissible': False, 'selected': False, 'reason': 'optional architecture proof absent; conservative Phase40 direct fallback'}, 
            'selected_physical_mapping_proof': {'status': rep['status'], 'truth_checks': 0, 'proof_file': 'SELECTED_PHYSICAL_MAP_PROOF.json', 
                                               'proof_model': selected['proof_model']}, 
            'component_summary': rep['component_summary'], 'recipe_interface_rejections': [], 
            'free_running_clock_created': False, 'synthetic_polling_phase_created': False}
    (build/'stage7_declarative_mapper_report.json').write_text(json.dumps(stage7, indent = 2, sort_keys = True)+'\n')
    if rep['status']!='PASS': 
        raise ValueError(rep)
    return stage7
