from __future__ import annotations
from pathlib import Path
import json, re, hashlib, itertools
from collections import Counter, defaultdict


def _load(path: Path): 
    with path.open() as f: 
        return json.load(f)


def _sha256(path: Path) -> str: 
    h = hashlib.sha256()
    with path.open('rb') as f: 
        for chunk in iter(lambda: f.read(1<<20), b''): 
            h.update(chunk)
    return h.hexdigest()


def _derived_nonzero_relation(cert: dict): 
    pat = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*1\s+iff\s+([A-Za-z_][A-Za-z0-9_]*)\s*!=\s*0\s*$')
    rows = []
    for v in cert.values(): 
        if not isinstance(v, dict) or v.get('status')!='PASS' or v.get('counterexamples'): 
            continue
        m = pat.match(str(v.get('relation', '')))
        if m: 
            rows.append((m.group(1), m.group(2), v))
    if len(rows)>1: 
        raise ValueError(f'ambiguous derived NONZERO relations: {[(a,b) for a,b,_ in rows]}')
    return rows[0] if rows else None


def _discover_certificates(cert_dir: Path, require_complete: bool = True): 
    """Schema-driven discovery. File names are not selection criteria."""
    found = {}
    audit = []
    for p in sorted(cert_dir.glob('*.json')): 
        d = _load(p)
        kind = None
        if d.get('status') == 'PASS' and {'total_ff_bits', 'rise_clocked_bits', 'fall_clocked_bits', 'state_rows'} <= d.keys(): 
            kind = 'control_factorization'
        elif d.get('status') == 'PASS' and {'quotient_state_count', 'shift_transition', 'shift_quotient_well_defined'} <= d.keys(): 
            kind = 'shift_quotient'
        elif d.get('status') == 'PASS' and 'representation' in d and 'lifted_transition_checks' in d and ('readpoints_by_source' in d or {'p06_readpoints', 'p10_readpoints'} <= d.keys()): 
            kind = 'shared_counter'
        elif d.get('status') == 'PASS' and {'coverage_ok', 'claims', 'tx_read_states'} <= d.keys() and ('load_edges_by_selector' in d or 'load_edges_by_p12' in d): 
            kind = 'observation_snapshot'
        elif d.get('status') == 'PASS' and {'natural_recurrence', 'fall_source_points', 'checks'} <= d.keys(): 
            kind = 'oe_recurrence'
        elif d.get('status') == 'PASS' and 'gpio_mirror_fusion' in d and 'relation_sha256' in d and _derived_nonzero_relation(d): 
            kind = 'storage_relations'
        elif d.get('status') == 'PASS' and 'result' in d and isinstance(d['result'], dict) and d['result'].get('classification') == 'PASS_CANONICAL_ELIMINATION': 
            kind = 'canonical_elimination'
        elif 'results' in d and any(isinstance(x, dict) and x.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION' for x in d.get('results', [])): 
            kind = 'observation_local_bit_elimination'
        elif d.get('status') == 'PASS' and {'stale_pc_non7_counterexamples', 'tx_exit_low_class_phase'} <= d.keys(): 
            kind = 'shared_counter_sync_exit'
        elif d.get('status') == 'PASS' and {'mismatch_hold_failures', 'mismatch_lifted_cases', 'physical_consequence'} <= d.keys(): 
            kind = 'shared_counter_terminal_hold'
        if kind: 
            if kind in found: 
                raise ValueError(f'duplicate certificate schema {kind}: {found[kind][0]} and {p}')
            found[kind] = (p, d)
            audit.append({'kind': kind, 'path': p.name, 'sha256': _sha256(p), 'version': d.get('version')})
    required = {
        'control_factorization', 'shift_quotient', 'shared_counter', 'observation_snapshot', 
        'oe_recurrence', 'storage_relations', 'canonical_elimination', 
        'observation_local_bit_elimination', 'shared_counter_sync_exit', 'shared_counter_terminal_hold'
    }
    missing = sorted(required-set(found))
    if require_complete and missing: 
        raise ValueError(f'missing certificate schemas: {missing}')
    return found, audit




def _normalize_register_refs(value): 
    """Return semantic-register references from schema-flexible certificate fields.

    Certificates may encode named sources as a list/tuple of register names or as
    a role->register mapping (e.g. increment_source/decrement_source).  Recovery
    must consume the register values, never the mapping keys.
    """
    out = []
    def add(v): 
        if isinstance(v, str): 
            out.append(v)
        elif isinstance(v, dict): 
            for vv in v.values(): 
                add(vv)
        elif isinstance(v, (list, tuple, set)): 
            for vv in v: 
                add(vv)
    add(value)
    # Preserve first occurrence order while removing duplicates.
    return list(dict.fromkeys(out))

def _storage_map(phase): 
    return {x['register']: dict(x) for x in phase['storage_optimization']['register_storage']}


def _parse_relation_target_source(text: str): 
    # Generic simple relation adapter used only to identify proven eliminated storage endpoints.
    m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(~)?([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+):(\d+)\])?\s*$', text)
    if not m: 
        return None
    return {'target': m.group(1), 'invert': bool(m.group(2)), 'source': m.group(3), 
            'hi': int(m.group(4)) if m.group(4) else None, 'lo': int(m.group(5)) if m.group(5) else None}


def _event_latch_candidates(phase, storage): 
    """Find 1-bit semantic state naturally expressible as event set/reset latch."""
    rules_by = defaultdict(list)
    for r in phase['update_rules']: 
        rules_by[r['target']].append(r)
    edge_events = {x['event_id'] for x in phase['scheduler_detectors'] if x.get('kind') == 'QUALIFIED_INPUT_EDGE'}
    out = []
    for reg, desc in storage.items(): 
        if desc.get('storage_bits')!=1: 
            continue
        arch = next((x for x in phase['architectural_registers'] if x['id'] == reg), None)
        if not arch or arch.get('kind')!='SEMANTIC': 
            continue
        rs = rules_by.get(reg, [])
        vals = []
        edge_vals = []
        ok = True
        for r in rs: 
            o = r.get('outcome')
            if not (isinstance(o, list) and len(o) == 2 and o[0] == 'CONST' and o[1] in (0, 1)): 
                ok = False; break
            vals.append(o[1])
            if any(ev in r['event_class'].split('+') for ev in edge_events): 
                edge_vals.append(o[1])
        if ok and {0, 1} <= set(vals) and {0, 1} <= set(edge_vals): 
            out.append(reg)
    return out


def _counter_candidates(storage): 
    return sorted(r for r, d in storage.items() if d.get('storage_bits', 0)>0 and d.get('recurrence_classes', {}).get('COUNT', 0)>0)


def _shift_candidates(storage): 
    return sorted(r for r, d in storage.items() if d.get('storage_bits', 0)>0 and d.get('storage_kind') == 'DIRECT' and d.get('recurrence_classes', {}).get('SHIFT', 0)>0)


def _gpio_payload_and_oe(phase, storage): 
    gpio_regs = [x for x in phase['architectural_registers'] if x.get('kind') == 'GPIO']
    # Direction-like GPIO register is detected from startup nonzero bits plus update masks; here only one packed
    # GPIO register contains scheduler primary data input bits. This is structural, not protocol-name based.
    qualified_primary_bits = {x['input_bit'] for x in phase['scheduler_detectors'] if x.get('kind') == 'QUALIFIED_INPUT_EDGE'}
    packed = {r['id']: storage.get(r['id'], {}) for r in gpio_regs}
    oe = []; residual = {}
    for reg, desc in packed.items(): 
        bits = list(desc.get('stored_bits', []))
        oe_bits = sorted(set(bits)&qualified_primary_bits)
        if oe_bits: 
            oe.extend((reg, b) for b in oe_bits)
            bits = [b for b in bits if b not in oe_bits]
        residual[reg] = bits
    return oe, residual




def _expr_changed_bits_for_self_reg(expr, reg, width): 
    """Conservative bit-effect analysis for phase-IR self-update expressions.

    For bitwise expressions built from REG(self), CONST and AND/OR/XOR, compute the
    exact unary truth function of each bit. Unknown/cross-bit forms conservatively
    mark every bit as potentially changed.
    """
    width = int(width); allbits = set(range(width))
    def unary(e, bit): 
        if not isinstance(e, list) or not e: 
            return None
        if e == ['REG', reg]: 
            return (0, 1)
        if e[0] == 'CONST': 
            b = (int(e[1])>>bit)&1; return (b, b)
        if e[0] == 'OP' and len(e)>=3 and e[1] in ('AND', 'OR', 'XOR'): 
            vals = [unary(a, bit) for a in e[2]]
            if any(v is None for v in vals): 
                return None
            out = []
            for q in (0, 1): 
                xs = [v[q] for v in vals]
                if e[1] == 'AND': 
                    y = int(all(xs))
                elif e[1] == 'OR': 
                    y = int(any(xs))
                else: 
                    y = 0
                    for x in xs: 
                        y^=x
                out.append(y)
            return tuple(out)
        return None
    changed = set()
    for bit in range(width): 
        u = unary(expr, bit)
        if u is None: 
            return allbits
        if u!=(0, 1): 
            changed.add(bit)
    return changed

def _natural_realization_domain(phase, reg, semantic_bits = None): 
    """Infer physical event domains for the retained bits of a natural-storage entry.

    This is metadata, not an implementation claim.  A multi-edge result tells the
    physical backend that a single ordinary edge-triggered DFF bank is insufficient
    unless a later proof retimes/merges those updates.
    """
    storage = _storage_map(phase)[reg]
    width = int(storage.get('semantic_width') or storage.get('storage_bits') or 1)
    bits = set(int(x) for x in (semantic_bits if semantic_bits is not None else range(width)))
    active = []
    for r in phase.get('update_rules', []): 
        if r.get('target')!=reg: 
            continue
        changed = _expr_changed_bits_for_self_reg(r.get('outcome'), reg, width)
        if bits & changed: 
            active.append(r.get('event_class', ''))
    toks = set()
    for ev in active: 
        toks.update(x for x in str(ev).split('+') if x)
    phase_edges = [x for x in ('PHEVT_RISE', 'PHEVT_FALL') if x in toks]
    hard_events = sorted(x for x in toks if x.startswith('HEVT'))
    if not active: 
        return {'clock_domain': 'STATIC_HOLD', 'phase_edges': [], 'async_event_candidates': [], 'active_event_classes': []}
    if len(phase_edges) == 1: 
        cd = phase_edges[0]
    elif len(phase_edges)>1: 
        cd = 'MULTI_PHASE_EDGE'
    else: 
        cd = 'EVENT_ONLY'
    return {'clock_domain': cd, 'phase_edges': phase_edges, 'async_event_candidates': hard_events, 
            'active_event_classes': sorted(set(active))}


def _eval_phase_value(expr, regs, gpio_value): 
    if not isinstance(expr, list) or not expr: 
        raise ValueError(expr)
    tag = expr[0]
    if tag == 'CONST': 
        return int(expr[1])
    if tag == 'REG': 
        return int(regs[str(expr[1])])
    if tag == 'GPIO_INPUT': 
        return int(gpio_value)
    if tag == 'BIT_VALUE': 
        return (_eval_phase_value(expr[1], regs, gpio_value)>>int(expr[2]))&1
    if tag == 'OP': 
        op = expr[1]; vals = [_eval_phase_value(x, regs, gpio_value) for x in expr[2]]
        if op == 'AND': 
            z = 0xffffffff
            for v in vals: 
                z&=v
            return z
        if op == 'OR': 
            z = 0
            for v in vals: 
                z|=v
            return z
        if op == 'XOR': 
            z = 0
            for v in vals: 
                z^=v
            return z
        if op == 'ADD': 
            return sum(vals)&0xffffffff
        if op == 'SUB': 
            z = vals[0]
            for v in vals[1:]: 
                z-=v
            return z&0xffffffff
        raise ValueError(op)
    if tag in ('EQ', 'NE', 'ULT', 'UGE'): 
        a = _eval_phase_value(expr[1], regs, gpio_value); b = _eval_phase_value(expr[2], regs, gpio_value)
        return int({'EQ': a == b, 'NE': a!=b, 'ULT': a<b, 'UGE': a>=b}[tag])
    raise ValueError(tag)


def _phase_expr_refs(expr): 
    out = set()
    def walk(x): 
        if not isinstance(x, list) or not x: 
            return
        if x[0] == 'REG' and len(x)>1: 
            out.add(str(x[1])); return
        for y in x[1:]: 
            if isinstance(y, list) and y and isinstance(y[0], list): 
                for z in y: 
                    walk(z)
            else: 
                walk(y)
    walk(expr)
    return out


def _detector_free_hidden_state_quotient_candidate(phase, legal_product, reg, bit): 
    """Exact reachable quotient for a one-bit detector-free visible state.

    Some BIO programs keep input history in ordinary registers/stack slots rather
    than scheduler-owned edge history.  Such hidden state must not be converted to
    an invented hardware clock.  This routine enumerates the reachable phase-IR
    relation under the legal environmental input cube and eliminates non-GPIO
    storage only when the projected visible recurrence is deterministic and every
    hidden dependency is confined to the visible state or the hidden subsystem.
    """
    if legal_product.get('proof_model')!='DETECTOR_FREE_INPUT_REACTIVE': 
        return None
    input_bits = list(map(int, (legal_product.get('topology') or {}).get('input_bits', [])))
    if len(input_bits)>8: 
        return None
    storage = _storage_map(phase)
    arch = {str(x['id']): x for x in phase.get('architectural_registers', [])}
    live = [r for r, d in storage.items() if int(d.get('storage_bits', 0))>0]
    if reg not in live: 
        return None
    hidden = [r for r in live if r!=reg and arch.get(r, {}).get('kind')!='GPIO']
    if not hidden: 
        return None
    hidden_set = set(hidden)
    basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    rules = [r for r in phase.get('update_rules', []) if r.get('materialize')]

    # Hidden sources may control themselves and the candidate visible register only.
    # Any dependency into another retained state means a one-output quotient is not exact.
    for rr in rules: 
        tgt = str(rr.get('target'))
        if tgt in hidden_set or tgt == reg: 
            continue
        refs = set(_phase_expr_refs(rr.get('outcome')))
        for e in rr.get('enable', []): 
            refs |= set(_phase_expr_refs(basis.get(str(e.get('basis')), [])))
        if refs & hidden_set: 
            return None

    # Full semantic state is small by construction here; bound conservatively.
    if sum(int(storage[r].get('storage_bits', 0)) for r in live)>12: 
        return None
    reset = {}
    for r in live: 
        rex = arch.get(r, {}).get('reset', ['CONST', 0])
        try: 
            reset[r] = int(_eval_phase_value(rex, {r: 0}, 0))
        except Exception: 
            return None
    widths = {r: int(arch.get(r, {}).get('width') or storage[r].get('semantic_width') or 32) for r in live}
    bytarget = {r: [] for r in live}
    for rr in rules: 
        t = str(rr.get('target'))
        if t in bytarget: 
            bytarget[t].append(rr)

    def step(state, gpio): 
        bvals = {k: bool(_eval_phase_value(e, state, gpio)) for k, e in basis.items()}
        nxt = dict(state)
        for t in live: 
            app = [rr for rr in bytarget[t]
                 if str(rr.get('event_class')) == 'EVENT_FREE' and
                    all(bvals.get(str(x['basis'])) == bool(x.get('polarity', True)) for x in rr.get('enable', []))]
            if not app: 
                continue
            vals = []
            for rr in app: 
                try: 
                    vals.append(int(_eval_phase_value(rr['outcome'], state, gpio)))
                except Exception: 
                    return None
            if len(set(vals))!=1: 
                return None
            mask = (1<<widths[t])-1 if widths[t]<63 else (1<<63)-1
            nxt[t] = vals[0]&mask
        return nxt

    order = tuple(sorted(live))
    def key(st): 
        return tuple(int(st[r]) for r in order)
    seen = {key(reset): dict(reset)}; queue = [dict(reset)]; transitions = []
    inputs = list(itertools.product((0, 1), repeat = len(input_bits))) or [()]
    while queue: 
        st = queue.pop(0)
        for vals in inputs: 
            gpio = sum(int(v)<<b for b, v in zip(input_bits, vals))
            ns = step(st, gpio)
            if ns is None: 
                return None
            transitions.append((dict(st), tuple(vals), dict(ns)))
            k = key(ns)
            if k not in seen: 
                if len(seen)>=4096: 
                    return None
                seen[k] = dict(ns); queue.append(dict(ns))

    # Project away hidden state.  The visible bit must have one exact recurrence for
    # every input cube, independent of which reachable hidden representative is used.
    table = []
    for vals in inputs: 
        pair = []
        for q in (0, 1): 
            ys = set()
            for st, iv, ns in transitions: 
                if iv!=tuple(vals) or ((int(st[reg])>>int(bit))&1)!=q: 
                    continue
                ys.add((int(ns[reg])>>int(bit))&1)
            if not ys: 
                # No reachable representative for this visible value: use hold only
                # as a don't-care completion; it cannot affect reachable behavior.
                ys = {q}
            if len(ys)!=1: 
                return None
            pair.append(next(iter(ys)))
        pair = tuple(pair)
        action = {(0, 1): 'HOLD', (1, 1): 'SET', (0, 0): 'RESET'}.get(pair)
        if action is None: 
            return None
        table.append({'inputs': {str(b): int(v) for b, v in zip(input_bits, vals)}, 
                      'action': action, 'next_by_q': list(pair)})
    if not any(x['action'] in ('SET', 'RESET') for x in table): 
        return None
    reset_bit = (int(reset[reg])>>int(bit))&1
    return {'input_bits': input_bits, 'truth_table': table, 'reset_value': reset_bit, 
            'hidden_sources': sorted(hidden), 'reachable_full_states': len(seen), 
            'reachable_full_state_values': [dict(x) for x in sorted(seen.values(), key = key)], 
            'semantic_transition_checks': len(transitions), 
            'proof_model': 'EXACT_REACHABLE_EVENT_FREE_HIDDEN_STATE_QUOTIENT_SET_RESET_HOLD_NO_TOGGLE'}

def _detector_free_async_latch_candidate(phase, legal_product, reg, bit): 
    """Prove a one-bit EVENT_FREE recurrence is SET/RESET/HOLD only.

    The external GPIO values are enumerated from the generic legal-product input
    dimensions.  A TOGGLE row is rejected, so CPU polling is never reinterpreted
    as a fabricated DFF clock.
    """
    if legal_product.get('proof_model')!='DETECTOR_FREE_INPUT_REACTIVE': 
        return None
    input_bits = list(map(int, (legal_product.get('topology') or {}).get('input_bits', [])))
    if len(input_bits)>8: 
        return None
    basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    rules = [r for r in phase.get('update_rules', []) if r.get('materialize') and r.get('target') == reg and str(r.get('event_class')) == 'EVENT_FREE']
    if not rules: 
        return None
    reset_expr = next((x.get('reset') for x in phase.get('architectural_registers', []) if x.get('id') == reg), ['CONST', 0])
    try: 
        reset_full = _eval_phase_value(reset_expr, {reg: 0}, 0)
    except Exception: 
        return None
    reset_bit = (int(reset_full)>>int(bit))&1
    table = []
    for vals in itertools.product((0, 1), repeat = len(input_bits)): 
        gpio = sum(int(v)<<b for b, v in zip(input_bits, vals))
        nxt = []
        for q in (0, 1): 
            regs = {reg: int(q)<<int(bit)}
            try: 
                bvals = {k: bool(_eval_phase_value(e, regs, gpio)) for k, e in basis.items()}
            except (KeyError, ValueError): 
                # A predicate depends on another architectural state.  The direct
                # one-bit proof is inapplicable; the exact reachable hidden-state
                # quotient path below may still prove a safe asynchronous recurrence.
                return None
            app = [r for r in rules if all(bvals.get(str(x['basis'])) == bool(x.get('polarity', True)) for x in r.get('enable', []))]
            if not app: 
                ys = {q}
            else: 
                try: 
                    ys = {(_eval_phase_value(r['outcome'], regs, gpio)>>int(bit))&1 for r in app}
                except Exception: 
                    return None
            if len(ys)!=1: 
                return None
            nxt.append(next(iter(ys)))
        pair = tuple(nxt)
        action = {(0, 1): 'HOLD', (1, 1): 'SET', (0, 0): 'RESET'}.get(pair)
        if action is None: 
            return None
        table.append({'inputs': {str(b): int(v) for b, v in zip(input_bits, vals)}, 'action': action, 'next_by_q': list(pair)})
    if not any(x['action'] in ('SET', 'RESET') for x in table): 
        return None
    return {'input_bits': input_bits, 'truth_table': table, 'reset_value': reset_bit, 
            'proof_model': 'FULL_INPUT_CUBE_SET_RESET_HOLD_NO_TOGGLE'}

def _normalize_state_entry(entry): 
    role = entry['role']
    return (role, int(entry['bits']))


def _recover_benchmark_dhir(phase_path: Path, cert_dir: Path): 
    phase = _load(phase_path)
    certs, cert_audit = _discover_certificates(cert_dir)
    C = {k: v[1] for k, v in certs.items()}
    storage = _storage_map(phase)

    # 1) Proven derived/mirror eliminations.
    derived = []; eliminated = set()
    rel = C['storage_relations']
    dr = _derived_nonzero_relation(rel)
    if not dr: 
        raise ValueError('derived NONZERO relation certificate unavailable')
    target, source, _row = dr
    eliminated.add(target); derived.append({'target': target, 'formula': f'{source}!=0', 'proof': 'storage_relations'})
    gm = rel['gpio_mirror_fusion']
    if gm.get('status')!='PASS' or gm.get('counterexamples'): 
        raise ValueError('mirror relation certificate invalid')
    for text in gm.get('relations', []): 
        pr = _parse_relation_target_source(text)
        if not pr: 
            raise ValueError(f'unsupported mirror relation {text}')
        eliminated.add(pr['target']); derived.append({'target': pr['target'], 'formula': text.split('=', 1)[1].strip(), 'proof': 'storage_relations'})
    ce = C['canonical_elimination']['result']
    eliminated.add(ce['register']); derived.append({'target': ce['register'], 'formula': ce['formula_text'], 'proof': 'canonical_elimination'})

    # 2) Observation-local packed-bit elimination and snapshot specialization.
    ob = C['observation_local_bit_elimination']
    passbits = [x for x in ob['results'] if x.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION']
    if not passbits: 
        raise ValueError('no observation-local bit elimination')
    target_regs = {x['register'] for x in passbits}
    if len(target_regs)!=1: 
        raise ValueError('snapshot target ambiguity')
    obs_reg = next(iter(target_regs))
    obs_elim_bits = {int(x['semantic_bit']) for x in passbits}
    for x in passbits: 
        derived.append({'target': f"{x['register']}[{x['semantic_bit']}]", 'formula': x['formula_text'], 'proof': 'observation_local_bit_elimination'})
    obs_desc = storage[obs_reg]
    packed_bits = set(obs_desc.get('stored_bits', range(obs_desc.get('storage_bits', 0))))
    snapshot_bits = sorted(packed_bits-obs_elim_bits)
    snap = C['observation_snapshot']
    if not snap.get('coverage_ok') or snap.get('violations'): 
        raise ValueError('snapshot specialization certificate invalid')
    if not snapshot_bits: 
        raise ValueError('snapshot specialization produced zero bits')
    eliminated.add(obs_reg)
    selector_regs = {x.get('formula', {}).get('atom', {}).get('register') for x in passbits if x.get('formula', {}).get('kind') == 'ATOM'}-{None}

    # 3) Generic recurrence families: COUNT registers coalesce; SHIFT register becomes observation quotient.
    counters = _counter_candidates(storage)
    shifts = _shift_candidates(storage)
    if len(counters)<2: 
        raise ValueError(f'shared-counter proof but fewer than two COUNT candidates: {counters}')
    if len(shifts)!=1: 
        raise ValueError(f'expected unique SHIFT candidate for quotient: {shifts}')
    sc = C['shared_counter']
    if sc.get('counterexamples') or not sc.get('representation', {}).get('bits'): 
        raise ValueError('shared-counter certificate invalid')
    for r in counters: 
        eliminated.add(r)
    shift_reg = shifts[0]; eliminated.add(shift_reg)
    rx = C['shift_quotient']
    if not rx.get('shift_quotient_well_defined') or not rx.get('partition_counter_context_independent'): 
        raise ValueError('shift quotient certificate invalid')
    quotient_bits = (int(rx['quotient_state_count'])-1).bit_length()

    # 4) Event-set/reset semantic state -> non-DFF latch candidate.
    latch_regs = _event_latch_candidates(phase, storage)
    if len(latch_regs)!=1: 
        raise ValueError(f'expected one event latch candidate, found {latch_regs}')
    latch_reg = latch_regs[0]; eliminated.add(latch_reg)

    # 5) Split packed GPIO direction bit tied to qualified edge input into OE recurrence state.
    oe_bits, gpio_residual = _gpio_payload_and_oe(phase, storage)
    oe = C['oe_recurrence']
    if len(oe_bits)!=1 or oe.get('counterexamples') or int(oe.get('checks', 0))<=0: 
        raise ValueError(f'OE recurrence/split invalid: bits={oe_bits}')

    # 6) Control domain: factorized certificate replaces residual boolean joint-control substrate.
    ctrl = C['control_factorization']
    if not ctrl.get('collision_phase_ok') or not ctrl.get('fall_preserves_rise_code') or not ctrl.get('rise_preserves_fall_code'): 
        raise ValueError('control factorization certificate invalid')
    joint = set(phase['joint_control_domain']['core_registers'])
    control_support = []
    for r in sorted(joint): 
        d = storage.get(r)
        if not d: 
            continue
        if r in eliminated: 
            continue
        # Non-control multi-bit state stays explicit. Remaining 1-bit joint-control storage is absorbed.
        if int(d.get('storage_bits', 0)) == 1: 
            control_support.append(r)
            eliminated.add(r)

    states = []
    def add(role, bits, **meta): 
        states.append({'role': role, 'bits': int(bits), **meta})
    add('control_rise_bank', ctrl['rise_clocked_bits'], clock_domain = 'PHEVT_RISE', semantic_support = control_support, proof = 'control_factorization')
    add('control_fall_bank', ctrl['fall_clocked_bits'], clock_domain = 'PHEVT_FALL', semantic_support = control_support, proof = 'control_factorization')
    add('shared_counter', sc['representation']['bits'], clock_domain = 'PHEVT_RISE', semantic_sources = counters, representation = sc['representation'], proof = 'shared_counter')
    add('shift_observation_quotient', quotient_bits, clock_domain = 'GATED_PHEVT_RISE', semantic_source = shift_reg, quotient_states = rx['quotient_state_count'], proof = 'shift_quotient')

    # 7) Explicit residual non-GPIO storage from phase IR after transformations.
    residual_regs = []
    for reg, d in storage.items(): 
        bits = int(d.get('storage_bits', 0))
        if bits<=0 or reg in eliminated: 
            continue
        arch = next((x for x in phase['architectural_registers'] if x['id'] == reg), None)
        if arch and arch.get('kind') == 'GPIO': 
            continue
        if reg == obs_reg: 
            continue
        residual_regs.append(reg)
    for reg in sorted(residual_regs): 
        d = storage[reg]
        role = 'selector_context' if reg in selector_regs else 'direct_state'
        dom = _natural_realization_domain(phase, reg)
        add(role, d['storage_bits'], clock_domain = dom['clock_domain'], semantic_source = reg, proof = 'phase_ir_natural_storage', natural_update_domain = dom)

    add('observation_snapshot', len(snapshot_bits), clock_domain = 'PHEVT_RISE', semantic_source = obs_reg, semantic_bits = snapshot_bits, selector_context = sorted(selector_regs), proof = 'observation_snapshot')

    # 8) GPIO payload bits and OE state.
    # Stable GPIO payload/state bits remain storage; edge-primary direction bit is OE recurrence.
    for reg, bits in sorted(gpio_residual.items()): 
        if bits: 
            role = 'gpio_data_payload' if next(x for x in phase['architectural_registers'] if x['id'] == reg)['provenance'] == 'gpio_data' else 'gpio_direction_payload'
            add(role, len(bits), clock_domain = 'PHEVT_RISE', semantic_source = reg, semantic_bits = bits, proof = 'phase_ir_gpio_storage')
    add('open_drain_oe', 1, clock_domain = 'PHEVT_FALL/event-clear', semantic_source = {'register': oe_bits[0][0], 'bit': oe_bits[0][1]}, recurrence = oe['natural_recurrence'], proof = 'oe_recurrence')

    total = sum(x['bits'] for x in states)

    # Verify no storage-bearing semantic register was silently lost unless it was explicitly transformed.
    accounted = set(eliminated)
    accounted.update(x.get('semantic_source') for x in states if isinstance(x.get('semantic_source'), str))
    for x in states: 
        accounted.update(x.get('semantic_sources', []))
    for reg, bits in gpio_residual.items(): 
        if bits: 
            accounted.add(reg)
    accounted.add(oe_bits[0][0])
    unaccounted = []
    for reg, d in storage.items(): 
        if int(d.get('storage_bits', 0))>0 and reg not in accounted and reg!=obs_reg: 
            unaccounted.append(reg)
    if unaccounted: 
        raise AssertionError(f'unaccounted storage candidates: {unaccounted}')

    return {
        'version': 'bio2rtl-dhir-v16-stage3-seedless-v1', 
        'status': 'PASS', 
        'recovery_mode': 'seedless certificate composition from phase IR; no architecture inventory seed; no handwritten RTL/SCH/REV33 used', 
        'input_phase_ir_sha256': _sha256(phase_path), 
        'certificate_audit': cert_audit, 
        'physical_dff_bits': total, 
        'physical_state': states, 
        'non_dff_state': [{
            'role': 'event_set_reset_latch', 'bits': 1, 'semantic_source': latch_reg, 
            'implementation_class': 'cross_coupled_latch_candidate', 
            'proof': 'phase_ir_event_update_structure'
        }], 
        'derived_state': derived, 
        'discovery_audit': {
            'phase_ir_natural_storage_bits': phase['storage_optimization']['natural_storage_bits'], 
            'storage_candidates': {k: {'bits': v.get('storage_bits', 0), 'kind': v.get('storage_kind'), 'recurrence_classes': v.get('recurrence_classes', {})} for k, v in storage.items()}, 
            'count_recurrence_candidates': counters, 
            'shift_recurrence_candidates': shifts, 
            'event_latch_candidates': latch_regs, 
            'qualified_edge_gpio_oe_bits': [{'register': r, 'bit': b} for r, b in oe_bits], 
            'control_support_residual': control_support, 
            'observation_packed_register': obs_reg, 
            'observation_eliminated_bits': sorted(obs_elim_bits), 
            'observation_snapshot_bits': snapshot_bits, 
            'selector_context_registers': sorted(selector_regs), 
            'eliminated_or_transformed_registers': sorted(eliminated), 
            'v10_seed_read': False, 
        }, 
        'physical_realization_note': 'Stage3 discovers state inventory only. The v15 low-phase mismatch-clear timing repair is a backend physical-realization policy and is intentionally not used to select state components.'
    }



def recover_seedless_dhir(phase_path: Path, cert_dir: Path): 
    """Recover a physical-state DHIR using optional proof-backed transforms.

    Every optimization is opportunistic.  A missing/non-applicable transform leaves
    its natural phase-IR storage intact instead of making architecture recovery fail.
    This is the generic compiler path; benchmark-specific expectations belong only in
    post-generation verification.
    """
    phase = _load(phase_path)
    legal_path = Path(phase_path).parent/'legal_product.json'
    legal_product = _load(legal_path) if legal_path.exists() else {}
    certs, cert_audit = _discover_certificates(cert_dir, require_complete = False)
    C = {k: v[1] for k, v in certs.items()}
    storage = _storage_map(phase)
    arch = {x['id']: x for x in phase.get('architectural_registers', [])}
    eliminated = set(); derived = []; transforms = []
    states = []; non_dff = []
    selector_regs = set(); obs_reg = None; snapshot_bits = []; obs_elim_bits = set()

    def add(role, bits, **meta): 
        bits = int(bits)
        if bits>0: 
            states.append({'role': role, 'bits': bits, **meta})
    def applied(name, **meta): 
        transforms.append({'transform': name, 'status': 'APPLIED', **meta})
    def skipped(name, reason): 
        transforms.append({'transform': name, 'status': 'SKIPPED', 'reason': reason})

    # Proven derived/mirror eliminations are independent optional transforms.
    rel = C.get('storage_relations')
    if rel: 
        try: 
            dr = _derived_nonzero_relation(rel)
            if dr: 
                target, source, _row = dr
                if target in storage: 
                    eliminated.add(target); derived.append({'target': target, 'formula': f'{source}!=0', 'proof': 'storage_relations'})
            gm = rel.get('gpio_mirror_fusion', {})
            if gm.get('status') == 'PASS' and not gm.get('counterexamples'): 
                for text in gm.get('relations', []): 
                    pr = _parse_relation_target_source(text)
                    if pr and pr['target'] in storage: 
                        eliminated.add(pr['target']); derived.append({'target': pr['target'], 'formula': text.split('=', 1)[1].strip(), 'proof': 'storage_relations'})
            applied('storage_relations', eliminated = sorted(x for x in eliminated))
        except Exception as e: 
            skipped('storage_relations', f'invalid certificate: {e}')
    else: 
        skipped('storage_relations', 'certificate not present')

    ce_cert = C.get('canonical_elimination')
    if ce_cert: 
        ce = ce_cert.get('result', {})
        reg = ce.get('register')
        if ce.get('classification') == 'PASS_CANONICAL_ELIMINATION' and reg in storage: 
            eliminated.add(reg); derived.append({'target': reg, 'formula': ce.get('formula_text'), 'proof': 'canonical_elimination'})
            applied('canonical_elimination', target = reg)
        else: 
            skipped('canonical_elimination', 'certificate target not applicable')
    else: 
        skipped('canonical_elimination', 'certificate not present')

    # Observation-local elimination is applied only together with a complete snapshot
    # lifetime proof. Otherwise the original packed storage remains untouched.
    ob = C.get('observation_local_bit_elimination'); snap = C.get('observation_snapshot')
    if ob and snap and snap.get('coverage_ok') and not snap.get('violations'): 
        candidate = snap.get('observation_register')
        passbits = [x for x in ob.get('results', [])
                  if x.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION' and x.get('register') == candidate]
        if passbits and candidate in storage: 
            desc = storage[candidate]
            packed_bits = set(desc.get('stored_bits', range(int(desc.get('storage_bits', 0)))))
            eliminated_bits = {int(x['semantic_bit']) for x in passbits}
            remain = sorted(packed_bits-eliminated_bits)
            if remain: 
                obs_reg = candidate; obs_elim_bits = eliminated_bits; snapshot_bits = remain
                eliminated.add(obs_reg)
                for x in passbits: 
                    derived.append({'target': f"{x['register']}[{x['semantic_bit']}]", 'formula': x.get('formula_text'), 'proof': 'observation_local_bit_elimination'})
                selector = snap.get('selector_register')
                if selector: 
                    selector_regs.add(selector)
                add('observation_snapshot', len(snapshot_bits), clock_domain = 'PHEVT_RISE', semantic_source = obs_reg, 
                    semantic_bits = snapshot_bits, selector_context = sorted(selector_regs), proof = 'observation_snapshot')
                applied('observation_snapshot', source = obs_reg, bits = snapshot_bits)
            else: 
                skipped('observation_snapshot', 'no residual observable bits')
        else: 
            skipped('observation_snapshot', 'certificate-selected observation-local target not present')
    else: 
        skipped('observation_snapshot', 'complete observation/snapshot proof not present')

    # Shared-counter coalescing consumes only the semantic sources named by a generic
    # proof certificate; unrelated COUNT recurrence storage remains residual.
    sc = C.get('shared_counter')
    if sc and not sc.get('counterexamples') and sc.get('representation', {}).get('bits'): 
        sources = [r for r in _normalize_register_refs(sc.get('semantic_sources', [])) if r in storage]
        if len(sources)>=2 and all(storage[r].get('recurrence_classes', {}).get('COUNT', 0)>0 for r in sources): 
            for r in sources: 
                eliminated.add(r)
            add('shared_counter', sc['representation']['bits'], clock_domain = 'PHEVT_RISE', semantic_sources = sources, 
                representation = sc['representation'], proof = 'shared_counter')
            applied('shared_counter', sources = sources, bits = sc['representation']['bits'])
        else: 
            skipped('shared_counter', 'certificate sources not applicable')
    else: 
        skipped('shared_counter', 'proof not present/applicable')

    rx = C.get('shift_quotient')
    if rx and rx.get('shift_quotient_well_defined') and rx.get('partition_counter_context_independent'): 
        sreg = rx.get('shift_register')
        if sreg in storage and storage[sreg].get('recurrence_classes', {}).get('SHIFT', 0)>0: 
            qstates = int(rx['quotient_state_count']); qbits = max(1, (qstates-1).bit_length())
            eliminated.add(sreg)
            add('shift_observation_quotient', qbits, clock_domain = 'GATED_PHEVT_RISE', semantic_source = sreg, 
                quotient_states = qstates, proof = 'shift_quotient')
            applied('shift_quotient', source = sreg, bits = qbits, states = qstates)
        else: 
            skipped('shift_quotient', 'certificate source not applicable')
    else: 
        skipped('shift_quotient', 'proof not present/applicable')

    # Any independently discovered event set/reset semantic bit may use a latch. This is
    # structural and does not depend on there being exactly one such bit.
    latch_regs = [r for r in _event_latch_candidates(phase, storage) if r not in eliminated]
    for r in latch_regs: 
        eliminated.add(r)
        non_dff.append({'role': 'event_set_reset_latch', 'bits': 1, 'semantic_source': r, 
                        'implementation_class': 'cross_coupled_latch_candidate', 'proof': 'phase_ir_event_update_structure'})
    if latch_regs: 
        applied('event_latch', sources = latch_regs)
    else: 
        skipped('event_latch', 'no structural candidates')

    # OE split is optional and certificate-directed. Without the recurrence proof all
    # GPIO bits stay in ordinary storage.
    gpio_bit_exclude = defaultdict(set)
    oe = C.get('oe_recurrence')
    if oe and not oe.get('counterexamples') and int(oe.get('checks', 0))>0: 
        src = oe.get('oe_semantic_source', {})
        reg = src.get('register'); bit = src.get('bit')
        if reg in storage and isinstance(bit, int) and bit in set(storage[reg].get('stored_bits', [])): 
            gpio_bit_exclude[reg].add(bit)
            add('open_drain_oe', 1, clock_domain = 'PHEVT_FALL/event-clear', semantic_source = {'register': reg, 'bit': bit}, 
                recurrence = oe['natural_recurrence'], proof = 'oe_recurrence')
            applied('oe_recurrence', source = {'register': reg, 'bit': bit})
        else: 
            skipped('oe_recurrence', 'certificate source not applicable')
    else: 
        skipped('oe_recurrence', 'proof not present/applicable')

    # Factorized control only replaces the exact semantic support proven by the cert.
    ctrl = C.get('control_factorization')
    control_support = []
    if ctrl and ctrl.get('collision_phase_ok') and ctrl.get('fall_preserves_rise_code') and ctrl.get('rise_preserves_fall_code'): 
        control_support = [r for r in ctrl.get('control_registers', []) if r in storage and r not in eliminated]
        if control_support: 
            for r in control_support: 
                eliminated.add(r)
            add('control_rise_bank', ctrl['rise_clocked_bits'], clock_domain = 'PHEVT_RISE', semantic_support = control_support, proof = 'control_factorization')
            add('control_fall_bank', ctrl['fall_clocked_bits'], clock_domain = 'PHEVT_FALL', semantic_support = control_support, proof = 'control_factorization')
            applied('control_factorization', sources = control_support, bits = ctrl['total_ff_bits'])
        else: 
            skipped('control_factorization', 'proven support already transformed/not applicable')
    else: 
        skipped('control_factorization', 'proof not present/applicable')

    # Everything not transformed above remains natural storage. GPIO packed bits are
    # handled bitwise so an OE split removes only the proven bit.
    accounted = set(eliminated)
    for reg, d in sorted(storage.items()): 
        if reg in eliminated or int(d.get('storage_bits', 0))<=0: 
            continue
        a = arch.get(reg, {})
        if a.get('kind') == 'GPIO': 
            stored = list(d.get('stored_bits', []))
            keep = [b for b in stored if b not in gpio_bit_exclude.get(reg, set())]
            if not stored: 
                keep = list(range(int(d.get('storage_bits', 0))))
            if not keep: 
                accounted.add(reg)
                continue
            prov = a.get('provenance')
            role = 'gpio_data_payload' if prov == 'gpio_data' else ('gpio_direction_payload' if prov == 'gpio_direction' else 'gpio_storage')
            dom = _natural_realization_domain(phase, reg, keep)
            if len(keep) == 1 and dom.get('clock_domain') == 'EVENT_ONLY' and dom.get('active_event_classes') == ['EVENT_FREE']: 
                ac = _detector_free_async_latch_candidate(phase, legal_product, reg, int(keep[0]))
                proof = 'phase_ir_detector_free_async_recurrence'
                if ac is None: 
                    ac = _detector_free_hidden_state_quotient_candidate(phase, legal_product, reg, int(keep[0]))
                    proof = 'phase_ir_event_free_hidden_state_exact_quotient'
                if ac is not None: 
                    for h in ac.get('hidden_sources', []): 
                        eliminated.add(h); accounted.add(h)
                        derived.append({'target': h, 'formula': f'quotiented from reachable projection of {reg}[{int(keep[0])}]', 
                                        'proof': 'event_free_hidden_state_exact_quotient'})
                    non_dff.append({'role': role+'_async_latch', 'bits': 1, 'semantic_source': reg, 'semantic_bits': [int(keep[0])], 
                                    'implementation_class': 'cross_coupled_latch_candidate', 
                                    'proof': proof, 'async_recurrence': ac})
                    accounted.add(reg)
                    continue
            add(role, len(keep), clock_domain = dom['clock_domain'], semantic_source = reg, semantic_bits = keep, proof = 'phase_ir_natural_storage', natural_update_domain = dom)
            accounted.add(reg)
            continue
        role = 'selector_context' if reg in selector_regs else 'direct_state'
        dom = _natural_realization_domain(phase, reg)
        add(role, d['storage_bits'], clock_domain = dom['clock_domain'], semantic_source = reg, proof = 'phase_ir_natural_storage', natural_update_domain = dom)
        accounted.add(reg)

    # Mark sources represented by transformed state components.
    for x in states: 
        if isinstance(x.get('semantic_source'), str): 
            accounted.add(x['semantic_source'])
        for r in x.get('semantic_sources', []): 
            accounted.add(r)
    for r in latch_regs: 
        accounted.add(r)
    unaccounted = [r for r, d in storage.items() if int(d.get('storage_bits', 0))>0 and r not in accounted]
    if unaccounted: 
        raise AssertionError(f'unaccounted natural storage candidates: {unaccounted}')

    logical_total = sum(int(x['bits']) for x in states)
    # A semantic storage bank updated on both recovered phase edges cannot be mapped
    # to one ordinary single-edge DFF bank without an additional retiming proof.
    # Record the conservative phase-split realization cost separately.  Optimized
    # architectures (such as the shared counter) normally have no such entries.
    realized_total = 0
    realization_audit = []
    for x in states: 
        bits = int(x['bits']); dom = x.get('natural_update_domain', {})
        multi = (x.get('proof') == 'phase_ir_natural_storage' and dom.get('clock_domain') == 'MULTI_PHASE_EDGE')
        banks = len(dom.get('phase_edges', [])) if multi else 1
        if multi and banks<2: 
            banks = 2
        rb = bits*banks
        realized_total+=rb
        x['logical_state_bits'] = bits
        x['realization_dff_bits'] = rb
        x['requires_phase_split_realization'] = bool(multi)
        if multi: 
            x['realization_policy'] = 'separate single-edge banks; phase-specific consumer views; no synthetic dual-edge clock'
        realization_audit.append({'role': x['role'], 'semantic_source': x.get('semantic_source'), 
                                  'logical_bits': bits, 'realization_dff_bits': rb, 
                                  'requires_phase_split': bool(multi)})
    return {
      'version': 'bio2rtl-dhir-v18-generic-optional-passes-v2', 'status': 'PASS', 
      'recovery_mode': 'generic optional proof-backed transforms over natural phase-IR storage', 
      'input_phase_ir_sha256': _sha256(phase_path), 'certificate_audit': cert_audit, 
      'logical_state_bits': logical_total, 
      'physical_dff_bits': realized_total, 
      'semantic_storage_bits_legacy': logical_total, 
      'physical_state': states, 'non_dff_state': non_dff, 'derived_state': derived, 
      'physical_realization_audit': realization_audit, 
      'transform_audit': transforms, 
      'discovery_audit': {
        'phase_ir_natural_storage_bits': phase['storage_optimization'].get('natural_storage_bits'), 
        'count_recurrence_candidates': _counter_candidates(storage), 
        'shift_recurrence_candidates': _shift_candidates(storage), 
        'event_latch_candidates': _event_latch_candidates(phase, storage), 
        'v10_seed_read': False, 
      }, 
      'physical_realization_note': 'Only proof-backed transformations are applied; non-applicable structures remain natural storage.'
    }

def _trusted_role(name): 
    m = {
        'control_rise_bank': 'control_rise_bank', 
        'control_fall_bank': 'control_fall_bank', 
        'shared_protocol_PC': 'shared_counter', 
        'rx_quotient': 'shift_observation_quotient', 
        'P08': 'direct_state', 
        'P12': 'selector_context', 
        'gpio_input_snapshot': 'observation_snapshot', 
        'G_DATA_19_18': 'gpio_data_payload', 
        'G_DIR_19_18': 'gpio_direction_payload', 
        'SDA_OE': 'open_drain_oe', 
    }
    return m[name]


def compare_normalized_inventory(dhir, trusted): 
    got = Counter(_normalize_state_entry(x) for x in dhir['physical_state'])
    exp = Counter((_trusted_role(x['name']), int(x['bits'])) for x in trusted['physical_state'])
    got_non = sum(int(x['bits']) for x in dhir['non_dff_state'])
    exp_non = sum(int(x['bits']) for x in trusted['non_dff_state'])
    return {
        'status': 'PASS' if got == exp and dhir['physical_dff_bits'] == trusted['physical_dff_bits'] and got_non == exp_non else 'FAIL', 
        'dff_bits_match': dhir['physical_dff_bits'] == trusted['physical_dff_bits'], 
        'non_dff_bits_match': got_non == exp_non, 
        'normalized_role_inventory_match': got == exp, 
        'recovered_inventory': [{ 'role': k[0], 'bits': k[1], 'count': v} for k, v in sorted(got.items())], 
        'trusted_inventory': [{ 'role': k[0], 'bits': k[1], 'count': v} for k, v in sorted(exp.items())], 
    }
