from __future__ import annotations
from pathlib import Path
import hashlib, json, itertools
from .truth_minimize import minimize_truth_table, minimize_truth_table_candidates
from .recipe_keys import component_recipe_keys, resolve_cached_control_encoding_plan
from .boolean_mapper import V, N, A, O, X, XN, M


def _load(p): 
    return json.loads(Path(p).read_text())
def _sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def resolve_control_encoding_plan(root: Path, recipe_cache: dict, neutral_cache: dict, keys: dict)->dict: 
    """Resolve the physical encoding choice independently of semantic code labels.

    Preferred source is the selected physical control recipe, then a matching neutral
    control contract.  If neither exists, return the canonical semantic component-code
    assignment as a future generic-control synthesis plan.
    """
    root = Path(root); wanted = keys.get('phase_factorized_control')
    if wanted is None: 
        return {
          'version': 'bio2rtl-no-factorized-control-v1', 
          'rise_component_codes': [], 'fall_component_codes': [], 
          'bits': {'rise': 0, 'fall': 0}, 
          'source': 'no factorized control component is applicable', 
          'resolved_from': 'not_applicable', 
        }
    registered = resolve_cached_control_encoding_plan(root)
    if registered: 
        return {**registered, 'resolved_from': 'control_encoding_plan_cache'}
    rec = recipe_cache.get('recipes', {}).get('phase_factorized_control')
    if rec and rec.get('selector_sha256') == wanted and rec.get('control_encoding_plan'): 
        return {**rec['control_encoding_plan'], 'resolved_from': 'physical_control_recipe'}
    nc = neutral_cache.get('contracts', {}).get('phase_factorized_control')
    if nc and nc.get('selector_sha256') == wanted and nc.get('control_encoding_plan'): 
        return {**nc['control_encoding_plan'], 'resolved_from': 'neutral_control_contract'}
    ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    return {
      'version': 'bio2rtl-control-physical-encoding-plan-v1-canonical-fallback', 
      'rise_component_codes': [int(x) for x in ctrl['rise_component_codes']], 
      'fall_component_codes': [int(x) for x in ctrl['fall_component_codes']], 
      'bits': {'rise': int(ctrl['rise_clocked_bits']), 'fall': int(ctrl['fall_clocked_bits'])}, 
      'source': 'canonical semantic component-code assignment; used only when no mapped control plan exists', 
      'resolved_from': 'canonical_semantic_fallback', 
    }



def _control_state_physical_codes(ctrl: dict, plan: dict): 
    rise_index = {int(st): i for i, grp in enumerate(ctrl['rise_components']) for st in grp}
    fall_index = {int(st): i for i, grp in enumerate(ctrl['fall_components']) for st in grp}
    rc = [int(x) for x in plan['rise_component_codes']]
    fc = [int(x) for x in plan['fall_component_codes']]
    return {int(r['state']): (rc[rise_index[int(r['state'])]], fc[fall_index[int(r['state'])]]) for r in ctrl['state_rows']}


def _code_bits(code: int, width: int)->tuple[int, ...]: 
    return tuple((int(code)>>i)&1 for i in range(int(width)))


def _tuple_expr(x): 
    if isinstance(x, tuple): 
        return x
    if not isinstance(x, list): 
        return x
    if not x: 
        return tuple()
    if x[0] in ('VAR', 'CONST'): 
        return tuple(x)
    if x[0] == 'NOT': 
        return ('NOT', _tuple_expr(x[1]))
    if x[0] in ('AND', 'OR'): 
        raw = x[1] if len(x) == 2 and isinstance(x[1], list) else x[1:]
        return (x[0], tuple(_tuple_expr(z) for z in raw))
    if x[0] in ('XOR', 'XNOR', 'MUX'): 
        return tuple([x[0]]+[_tuple_expr(z) for z in x[1:]])
    return tuple([x[0]]+[_tuple_expr(z) if isinstance(z, list) else z for z in x[1:]])

def _role_expr_map(plan: dict)->dict[str, tuple]: 
    out = {}
    for bank in ('rise', 'fall'): 
        for r in plan.get('branch_roles', {}).get(bank, []): 
            rid = str(r['id'])
            if 'expression' in r: 
                out[rid] = _tuple_expr(r['expression'])
            elif 'net' in r: 
                out[rid] = V(str(r['net']))
            else: 
                raise ValueError(f'branch role {rid} has neither expression nor net')
    return out

def _role_combinations(role_specs: list[dict], target_code: int|None, ambiguous: bool, source_state: int|None = None)->list[dict[str, int]]: 
    """Enumerate abstract role-bit values for one control family.

    A discovered role can be scoped to one or more ambiguous semantic source states.
    Outside its family it is a don't-care unless explicitly qualified-low.  Historical
    active_when/target-code bindings remain supported for migration.
    """
    if not role_specs: 
        return [{}]
    ids = [str(r['id']) for r in role_specs]; out = []
    for vals in itertools.product((0, 1), repeat = len(ids)): 
        row = dict(zip(ids, vals)); ok = True
        for r in role_specs: 
            rid = str(r['id']); scoped = r.get('source_states'); in_scope = True
            if scoped is not None and source_state is not None: 
                in_scope = int(source_state) in set(map(int, scoped))
            if not ambiguous or not in_scope: 
                if bool(r.get('qualified_outside_ambiguous_family')) and row[rid]!=0: 
                    ok = False
                    break
                continue
            active = True; aw = r.get('active_when')
            if aw: 
                active = row[str(aw['role'])] == int(aw['value'])
            if active and 'target_code_bit' in r: 
                want = (int(target_code)>>int(r['target_code_bit']))&1
                if row[rid]!=want: 
                    ok = False
                    break
        if ok: 
            out.append(row)
    return out

def _subst_role_vars(expr, role_exprs: dict[str, tuple]): 
    if not isinstance(expr, tuple): 
        return expr
    if expr[0] == 'VAR' and str(expr[1]) in role_exprs: 
        return role_exprs[str(expr[1])]
    if expr[0] in ('CONST', 'VAR'): 
        return expr
    if expr[0] == 'NOT': 
        return ('NOT', _subst_role_vars(expr[1], role_exprs))
    if expr[0] in ('AND', 'OR'): 
        return (expr[0], tuple(_subst_role_vars(z, role_exprs) for z in expr[1]))
    return tuple([expr[0]]+[_subst_role_vars(z, role_exprs) if isinstance(z, tuple) else z for z in expr[1:]])

def _helper_expr(helper: dict, role_exprs: dict[str, tuple]): 
    xs = [role_exprs[str(x)] for x in helper.get('roles', [])]
    op = str(helper.get('op', 'AND')).upper()
    if op == 'AND': 
        return A(*xs)
    if op == 'OR': 
        return O(*xs)
    raise ValueError(f'unsupported neutral helper op {op}')


def _project_gpio_core_inputs(root: Path)->dict[int, str]: 
    """Compiler-owned GPIO input-net map derived only from user [[io]]."""
    from .project_inputs import load_config
    from .physical_interface import gpio_input_map_from_config
    _, project = load_config(root)
    return gpio_input_map_from_config(project)


def derive_shift_quotient_contract(root: Path)->dict: 
    """Lower a proof-backed finite shift quotient through a content-addressed encoding plan."""
    from .neutral_bindings import resolve_shift_interface_binding
    root = Path(root); g = root/'build/generated_certificates'
    cert = _load(g/'shift_quotient.json')
    if cert.get('status')!='PASS': 
        raise ValueError('shift quotient certificate not PASS')
    plan = resolve_shift_interface_binding(root)
    if not plan: 
        raise ValueError('no neutral shift-interface/encoding binding for current quotient shape')
    n = int(cert['quotient_state_count']); enc = [int(x) for x in plan['encoding']]
    if len(enc)!=n or sorted(enc)!=list(range(n)): 
        raise ValueError('shift encoding is not a permutation of quotient codes')
    width = (n-1).bit_length()
    if n!=(1<<width): 
        raise ValueError('current exact shift lowering requires power-of-two quotient state count')
    bits = list(plan['state_bits'])
    if len(bits)!=width: 
        raise ValueError('shift state-bit binding width mismatch')
    q = [str(x['net']) for x in bits]; qb = [str(x['complement']) for x in bits]
    serial = str(plan['serial_input']['net']); serial_n = str(plan['serial_input'].get('complement', ''))
    vars = q+[serial]
    code_to_state = {code: i for i, code in enumerate(enc)}
    trans = {(int(k.split(',')[0]), int(k.split(',')[1])): int(v) for k, v in cert['shift_transition'].items()}
    spec = {}
    for code in range(n): 
        st = code_to_state[code]
        for b in (0, 1): 
            ns = trans[(st, b)]; ncode = enc[ns]
            row = tuple((code>>i)&1 for i in range(width))+(b,)
            spec[row] = tuple((ncode>>i)&1 for i in range(width))
    allrows = list(itertools.product((0, 1), repeat = len(vars)))
    if len(spec)!=len(allrows): 
        raise ValueError('shift encoded truth table not total')
    logical_next = []
    for bi in range(width): 
        ones = [r for r, v in spec.items() if v[bi]]
        logical_next.append(minimize_truth_table(vars, ones, []))
    reset_state = int(plan['reset_quotient_state']); reset_code = enc[reset_state]
    dffs = []
    for bi, (qn, qbn, nexpr) in enumerate(zip(q, qb, logical_next)): 
        reset_level = (reset_code>>bi)&1
        if reset_level: 
            # DFFR physical Q resets to 0, so expose logical-one-at-reset through QB.
            dffs.append({'q': qbn, 'qb': qn, 'd_expr': N(nexpr), 'clock': str(plan['clock_gate']['clock_net']), 'reset': str(plan['reset']), 'cell': 'DFFR', 'logical_state_net': qn, 'reset_logical_level': 1})
        else: 
            dffs.append({'q': qn, 'qb': qbn, 'd_expr': nexpr, 'clock': str(plan['clock_gate']['clock_net']), 'reset': str(plan['reset']), 'cell': 'DFFR', 'logical_state_net': qn, 'reset_logical_level': 0})
    # Observation outputs are functions of quotient state and therefore exact on all codes.
    classes = {int(x['state']): x for x in cert.get('quotient_classes', [])}
    pred_order = [str(x) for x in cert.get('observation_predicates', [])]
    comb = {}
    for ob in plan.get('observation_outputs', []): 
        pid = str(ob['predicate_id'])
        if pid not in pred_order: 
            raise ValueError(f'shift observation predicate {pid} absent from proof')
        oi = pred_order.index(pid); ones = []
        for code in range(n): 
            st = code_to_state[code]
            val = int(classes[st]['observation_vector'][oi])
            if bool(ob.get('invert')): 
                val = 1-val
            if val: 
                ones.append(tuple((code>>i)&1 for i in range(width)))
        comb[str(ob['net'])] = minimize_truth_table(q, ones, [])
    cg = plan['clock_gate']; raw = str(cg['raw_net'])
    enable_expr = _tupleize_local(cg['enable_expression']) if 'enable_expression' in cg else V(str(cg['enable']))
    clock_input_expr = A(V(str(cg['clock_source'])), enable_expr)
    primitives = [{'kind': str(cg['primitive_kind']), 'implementation_class': str(cg['implementation_class']), 
      'input': raw, 'output': str(cg['clock_net']), 'input_expr': clock_input_expr, 
      'semantic': 'clock_path_buffer_preserved'}]
    ins = sorted(_neutral_expr_vars(enable_expr)|{str(cg['clock_source']), serial})
    if serial_n and serial_n not in ins: 
        ins.append(serial_n)
    if str(plan['reset']) not in ins: 
        ins.append(str(plan['reset']))
    outs = []
    for n0 in q+[str(x['net']) for x in plan.get('observation_outputs', [])]: 
        if n0 not in outs: 
            outs.append(n0)
    complements = {}
    if serial_n: 
        complements[serial] = serial_n
        complements[serial_n] = serial
    return {'version': 'bio2rtl-neutral-shift-quotient-contract-v1', 'component_class': 'shift_observation_quotient', 
      'selector_sha256': component_recipe_keys(root)['shift_observation_quotient'], 'neutral_interface_binding': plan, 
      'interface_inputs': ins, 'interface_outputs': outs, 'combinational_outputs': comb, 'dffs': dffs, 'latches': [], 
      'primitives': primitives, 'complements': complements, 
      'derivation': {'kind': 'fresh-shift-quotient-proof+content-addressed-encoding-plan', 'shift_certificate_sha256': _sha(g/'shift_quotient.json'), 
        'encoded_truth_rows': len(spec), 'reset_quotient_state': reset_state, 'reset_code': reset_code, 'protocol_signal_names_embedded_in_lowering_code': False}}



def derive_shared_counter_latch_contract(root: Path, control_encoding_plan: dict|None = None)->dict: 
    """Lower the proof-backed shared-counter architecture without a physical recipe.

    Counter operation families come from the fresh legal-product proof. Concrete net
    spellings and the deliberately retimed latch-clear interface are content-addressed
    binding policy. The event-latch set/stop events are re-discovered from phase40
    update semantics; no protocol event/signal name is embedded here.
    """
    from .neutral_bindings import (resolve_shared_counter_interface_binding, 
                                   resolve_control_interface_binding, 
                                   resolve_event_interface_binding)
    from .project_inputs import load_config
    root = Path(root); g = root/'build/generated_certificates'
    sc = _load(g/'shared_counter.json'); ctrl = _load(g/'control_factorization.json')
    if sc.get('status')!='PASS' or sc.get('counterexamples'): 
        raise ValueError('shared-counter certificate not PASS')
    if not control_encoding_plan: 
        raise ValueError('no physical control encoding plan supplied by mapper')
    sif = resolve_shared_counter_interface_binding(root); cif = resolve_control_interface_binding(root); eif = resolve_event_interface_binding(root)
    if not sif or not cif or not eif: 
        raise ValueError('missing neutral shared-counter/control/event binding')
    width = int(sc['counter_width']); bits = list(sif['counter_bits'])
    if len(bits)!=width: 
        raise ValueError('shared-counter state binding width mismatch')
    q = [str(x['net']) for x in bits]; qb = [str(x['complement']) for x in bits]
    banks = cif['banks']; rq = [str(x) for x in banks['rise']['q']]; fq = [str(x) for x in banks['fall']['q']]
    rqb = [str(x) for x in banks['rise']['qb']]; fqb = [str(x) for x in banks['fall']['qb']]
    if len(rq)!=int(control_encoding_plan['bits']['rise']) or len(fq)!=int(control_encoding_plan['bits']['fall']): 
        raise ValueError('shared-counter control bank width mismatch')
    state_codes = _control_state_physical_codes(ctrl, control_encoding_plan)
    fam = sc.get('operation_families', {})
    required = ['TX_EARLY_SEED_MAX', 'TX_EARLY_DEC', 'TX_EXIT_SYNC_CLEAR', 'RX_INC_MOD', 'RX_TERMINAL_HOLD_MAX', 'FRAME_RESET']
    missing = [x for x in required if x not in fam]
    if missing: 
        raise ValueError(f'shared-counter certificate missing operation families {missing}')

    # Only LOW-phase source states are sampled by the rising-domain counter DFFs.
    opcodes = {}
    for op in ('TX_EARLY_SEED_MAX', 'TX_EARLY_DEC', 'TX_EXIT_SYNC_CLEAR'): 
        states = [int(x) for x in fam[op]['source_control_states']]
        codes = {tuple(state_codes[s]) for s in states}
        if not codes: 
            raise ValueError(f'empty counter operation code family {op}')
        opcodes[op] = codes
    # A physical control code may collapse phase-disjoint semantic states, but two
    # distinct rising-domain arithmetic operations may not collide on that code.
    rev = {}
    for op, codes in opcodes.items(): 
        for c in codes: 
            if c in rev and rev[c]!=op: 
                raise ValueError(f'counter operation encoding collision {c}: {rev[c]} vs {op}')
            rev[c] = op

    inc_expr = _tupleize_local(sif['increment_enable_expression']) if 'increment_enable_expression' in sif else V(str(sif['increment_enable']))
    inc_inputs = sorted(_neutral_expr_vars(inc_expr))
    rb = len(rq); fb = len(fq); maxv = int(sc['max_value'])
    if maxv != (1<<width)-1: 
        raise ValueError('shared-counter seed lowering currently requires all-ones seed value')
    cvars = rq+fq
    def code_pred(codes): 
        ones = [_code_bits(rc, rb)+_code_bits(fc, fb) for rc, fc in sorted(codes)]
        return minimize_truth_table(cvars, ones, [])
    seed_expr = code_pred(opcodes['TX_EARLY_SEED_MAX'])
    dec_expr = code_pred(opcodes['TX_EARLY_DEC'])
    sync_expr = code_pred(opcodes['TX_EXIT_SYNC_CLEAR'])
    seed_net = 'shared_counter_seed_enable'; dec_net = 'shared_counter_dec_enable'; syncn_net = 'shared_counter_sync_clear_n'
    # Generic modulo up/down counter recurrence.  The toggle chain is an exact ripple
    # formulation that remains compact under technology mapping: bit1 is a direction
    # mux on q0; higher carries/borrows extend while adjacent lower bits agree.
    toggles = []
    if width: 
        toggles.append(O(inc_expr, V(dec_net)))
    if width>=2: 
        toggles.append(M(V(q[0]), V(dec_net), inc_expr))
    for i in range(2, width): 
        toggles.append(A(toggles[i-1], XN(V(q[i-1]), V(q[i-2]))))
    next_expr = []
    for i in range(width): 
        base = X(V(q[i]), toggles[i])
        seeded = O(base, V(seed_net))
        next_expr.append(A(seeded, V(syncn_net)))

    # Discover event-latch source and event semantics from architecture + phase40.
    arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json'); phase = _load(root/'build/semantic/phase40.ir.json')
    latch_entries = [x for x in arch.get('non_dff_state', []) if x.get('role') == 'event_set_reset_latch']
    if len(latch_entries)!=1: 
        raise ValueError(f'expected one proof-backed event latch, got {len(latch_entries)}')
    lsrc = str(latch_entries[0]['semantic_source'])
    lrules = [r for r in phase.get('update_rules', []) if str(r.get('target')) == lsrc and r.get('materialize')]
    def const_out(r): 
        o = r.get('outcome'); return int(o[1]) if isinstance(o, list) and len(o)>=2 and o[0] == 'CONST' else None
    set_events = set(); clear_events = set()
    for r in lrules: 
        val = const_out(r)
        if val not in (0, 1): 
            continue
        for ev in str(r.get('event_class', '')).split('+'): 
            if ev.startswith('PHEVT_'): 
                continue
            (set_events if val else clear_events).add(ev)
    if len(set_events)!=1 or len(clear_events)!=1: 
        raise ValueError(f'event latch set/clear event ambiguity set={sorted(set_events)} clear={sorted(clear_events)}')
    set_event = next(iter(set_events)); clear_event = next(iter(clear_events))
    event_nets = {str(k): str(v) for k, v in eif.get('event_nets', {}).items()}
    if set_event not in event_nets or clear_event not in event_nets: 
        raise ValueError('event latch scheduler event has no neutral detector output binding')
    set_net = event_nets[set_event]; clear_net = event_nets[clear_event]
    # Counter async reset is the reset group tied to the frame-open event family.
    frame_events = set(map(str, fam['FRAME_RESET']['events']))
    reset_groups = [x for x in eif.get('reset_groups', []) if frame_events and frame_events.issubset(set(map(str, x.get('events', []))))]
    if not reset_groups: 
        raise ValueError(f'no event reset group covers frame-reset events {sorted(frame_events)}')
    reset_group = min(reset_groups, key = lambda x: len(x.get('events', [])))
    counter_reset = str(reset_group['net'])

    # Build the low-phase sampled clear term from the semantic sync-clear operation
    # family.  Physical retiming policy binds only the sampled data/complement net.
    scl = str(sif['clock']); sdn = str(sif['sampled_data_complement'])
    sampled_clear = N(O(V(syncn_net), V(scl), V(sdn)))
    if 'residual_latch_clear_expression' in sif: 
        aux_expr = _tupleize_local(sif['residual_latch_clear_expression']); aux_inputs = sorted(_neutral_expr_vars(aux_expr))
    else: 
        aux = str(sif['residual_latch_clear']); aux_expr = V(aux); aux_inputs = [aux]
    global_reset = str(sif['global_reset'])
    base_clear = O(V(global_reset), V(clear_net), aux_expr)
    comb = {seed_net: seed_expr, dec_net: dec_expr, syncn_net: N(sync_expr), 
          'shared_sampled_latch_clear': sampled_clear, 'shared_latch_base_clear': base_clear}
    latch = sif['latch']; lq = str(latch['q']); lqb = str(latch['qb'])
    dffs = [{'q': qn, 'qb': qbn, 'd_expr': ex, 'clock': scl, 'reset': counter_reset, 'cell': 'DFFR'} for qn, qbn, ex in zip(q, qb, next_expr)]
    complements = {**dict(zip(q, qb)), **dict(zip(rq, rqb)), **dict(zip(fq, fqb))}
    ins = []
    for n in [*rq, *rqb, *fq, *fqb, *inc_inputs, *q, *qb, counter_reset, scl, sdn, global_reset, clear_net, *aux_inputs, set_net]: 
        if n not in ins: 
            ins.append(n)
    outs = []
    for n in [*q, qb[1] if len(qb)>1 else qb[0], lq]: 
        if n not in outs: 
            outs.append(n)
    return {
      'version': 'bio2rtl-neutral-shared-counter-latch-contract-v1', 
      'component_class': 'shared_counter_and_event_latch', 
      'selector_sha256': component_recipe_keys(root)['shared_counter_and_event_latch'], 
      'neutral_interface_binding': sif, 'control_encoding_plan': control_encoding_plan, 
      'interface_inputs': ins, 'interface_outputs': outs, 'complements': complements, 
      'combinational_outputs': comb, 'dffs': dffs, 'primitives': [], 
      'latches': [{'implementation_class': 'cross_coupled_nor', 'q': lq, 'qb': lqb, 
                  'reset_inputs': ['shared_latch_base_clear', 'shared_sampled_latch_clear'], 
                  'set_inputs': [set_net]}], 
      'derivation': {
        'kind': 'fresh-shared-counter-operation-proof+control-encoding+phase40-event-latch-semantics+generic-retiming-binding', 
        'shared_counter_certificate_sha256': _sha(g/'shared_counter.json'), 
        'counter_operation_codes': {k: [list(x) for x in sorted(v)] for k, v in opcodes.items()}, 
        'event_latch_semantic_source': lsrc, 'set_event': set_event, 'clear_event': clear_event, 
        'protocol_signal_names_embedded_in_lowering_code': False, 
      }, 
    }

def derive_event_detector_contract(root: Path)->dict: 
    """Derive scheduler edge-detection support from semantic detector descriptors.

    Supported B20 Ver.1 event forms here are POLLING_PHASE_COMPLETION and
    QUALIFIED_INPUT_EDGE over GPIO_INPUT qualifiers.  Concrete legacy net spellings
    are supplied only by a content-addressed interface binding; detector logic is
    derived from event kind/edge/input-bit/qualifier semantics.
    """
    from .neutral_bindings import resolve_event_interface_binding
    from .project_inputs import load_config
    from .recipe_keys import component_recipe_keys
    root = Path(root); phase = _load(root/'build/semantic/phase40.ir.json')
    iface = resolve_event_interface_binding(root)
    if not iface: 
        raise ValueError('no neutral event-interface binding for this scheduler detector shape')
    _, project = load_config(root)
    bitnet = _project_gpio_core_inputs(root)
    global_reset = 'reset'
    detectors = list(phase.get('scheduler_detectors', []))
    unsupported = [d for d in detectors if d.get('kind') not in ('QUALIFIED_INPUT_EDGE', 'POLLING_PHASE_COMPLETION')]
    if unsupported: 
        raise ValueError(f'unsupported scheduler detector kinds: {[d.get("kind") for d in unsupported]}')
    for d in detectors: 
        if int(d['input_bit']) not in bitnet: 
            raise ValueError(f'no project core-input binding for scheduler GPIO bit {d["input_bit"]}')
        for q in d.get('qualifiers', []): 
            if q.get('source')!='GPIO_INPUT': 
                raise ValueError(f'unsupported detector qualifier source {q}')
            if int(q['bit']) not in bitnet: 
                raise ValueError(f'no project core-input binding for qualifier GPIO bit {q["bit"]}')

    primitives = []; comb = {}; delayed = {}
    qedges = [d for d in detectors if d.get('kind') == 'QUALIFIED_INPUT_EDGE']
    delay_cfg = iface.get('qualified_edge_delay', {})
    stages = int(delay_cfg.get('stages', 0))
    if qedges and stages<=0: 
        raise ValueError('qualified edge detectors require a positive delay-stage policy')
    for bit in sorted({int(d['input_bit']) for d in qedges}): 
        prev = bitnet[bit]
        for i in range(stages): 
            out = f'evt_b{bit}_d{i+1}'
            primitives.append({'kind': str(delay_cfg['primitive_kind']), 
              'implementation_class': str(delay_cfg['implementation_class']), 
              'input': prev, 'output': out, 'input_expr': V(prev), 'semantic': 'temporal_delay_preserved'})
            prev = out
        delayed[bit] = prev

    # Explicitly requested input complements are generic interface conveniences used
    # by later neutral components; they are not selected by protocol names.
    for b, n in iface.get('input_complements', {}).items(): 
        bit = int(b); comb[str(n)] = N(V(bitnet[bit]))
    for b, n in iface.get('clock_complements', {}).items(): 
        bit = int(b); src = bitnet[bit]
        ci = iface.get('clock_inverter', {})
        primitives.append({'kind': str(ci['primitive_kind']), 'implementation_class': str(ci['implementation_class']), 
          'input': src, 'output': str(n), 'input_expr': V(src), 'semantic': 'clock_path_inversion_and_drive_preserved'})

    event_nets = {str(k): str(v) for k, v in iface.get('event_nets', {}).items()}
    for d in qedges: 
        eid = str(d['event_id'])
        if eid not in event_nets: 
            raise ValueError(f'no neutral output binding for scheduler event {eid}')
        bit = int(d['input_bit']); cur = bitnet[bit]; past = delayed[bit]
        # Synthesize the qualified conjunction in De-Morgan form.  This exposes
        # available input/clock complements to NOR mapping and avoids protocol-specific
        # gate choices while remaining technology-cost friendly.
        false_terms = []
        ccomp = {int(k): str(v) for k, v in iface.get('clock_complements', {}).items()}
        ccomp.update({int(k): str(v) for k, v in iface.get('input_complements', {}).items()})
        for q in d.get('qualifiers', []): 
            qb = int(q['bit']); qn = bitnet[qb]
            false_terms.append(V(ccomp[qb]) if int(q['level']) and qb in ccomp else (N(V(qn)) if int(q['level']) else V(qn)))
        edge = str(d.get('edge', '')).upper()
        if edge == 'FALL': 
            false_terms += [N(V(past)), V(cur)]
        elif edge == 'RISE': 
            false_terms += [V(ccomp[bit]) if bit in ccomp else N(V(cur)), V(past)]
        else: 
            raise ValueError(f'unsupported qualified edge {edge}')
        comb[event_nets[eid]] = N(O(*false_terms))

    # Reset groups encode architecture-level event reset policy.  Each group is a
    # Boolean OR of the project reset and a declared set of scheduler event outputs.
    for rg in iface.get('reset_groups', []): 
        evs = [str(x) for x in rg.get('events', [])]
        missing = [x for x in evs if x not in event_nets]
        if missing: 
            raise ValueError(f'reset group references unbound events {missing}')
        comb[str(rg['net'])] = O(V(global_reset), *(V(event_nets[x]) for x in evs))

    inputs = []
    for b in sorted({int(d['input_bit']) for d in detectors}|{int(q['bit']) for d in detectors for q in d.get('qualifiers', [])}): 
        n = bitnet[b]
        if n not in inputs: 
            inputs.append(n)
    if global_reset not in inputs: 
        inputs.insert(0, global_reset)
    outputs = []
    for n in list(iface.get('clock_complements', {}).values())+list(iface.get('input_complements', {}).values())+list(event_nets.values())+[str(x['net']) for x in iface.get('reset_groups', [])]: 
        if n not in outputs: 
            outputs.append(n)
    key = component_recipe_keys(root)['event_detectors']
    return {
      'version': 'bio2rtl-neutral-event-detector-contract-v1', 
      'component_class': 'event_detectors', 'selector_sha256': key, 
      'neutral_interface_binding': iface, 
      'interface_inputs': inputs, 'interface_outputs': outputs, 
      'combinational_outputs': comb, 'dffs': [], 'latches': [], 'primitives': primitives, 
      'derivation': {'kind': 'scheduler-detector-semantics+project-io-binding+generic-event-primitive-policy', 
        'phase40_sha256': _sha(root/'build/semantic/phase40.ir.json'), 
        'scheduler_detector_count': len(detectors), 'qualified_edge_count': len(qedges), 
        'protocol_signal_names_embedded_in_lowering_code': False}, 
    }



def derive_natural_event_state_contract(root: Path)->dict: 
    """Lower conservative EVENT_ONLY natural storage without inventing a clock.

    Each retained state entry is clocked only by the canonical scheduler event pulse
    proven in the architecture IR.  Guarded recurrences are implemented on D as
    ``enable ? next : Q`` by exhaustive minimization over the phase predicate basis;
    no free-running clock or synthetic polling phase is introduced.
    """
    from .natural_storage_contracts import _eval_outcome
    from .neutral_bindings import resolve_event_interface_binding
    from .predicate_projection import project_predicate_expression
    from .proof_bitset import expr_vars
    from .project_inputs import load_config
    root = Path(root)
    phase = _load(root/'build/semantic/phase40.ir.json')
    arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json')
    states = [x for x in arch.get('physical_state', [])
            if x.get('proof') == 'phase_ir_natural_storage' and x.get('clock_domain') == 'EVENT_ONLY']
    if not states: 
        raise ValueError('no natural EVENT_ONLY state is applicable')
    iface = resolve_event_interface_binding(root)
    if not iface: 
        raise ValueError('no scheduler event interface for natural EVENT_ONLY state')
    event_nets = {str(k): str(v) for k, v in iface.get('event_nets', {}).items()}
    _, cfg = load_config(root)
    reset_net = 'reset'
    phase_basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    storage = {str(x['register']): x for x in phase.get('storage_optimization', {}).get('register_storage', [])}
    aregs = {str(x['id']): x for x in phase.get('architectural_registers', [])}
    comb = {}; dffs = []; interface_inputs = {reset_net}; interface_outputs = []; proof_rows = []
    inline_basis = {}

    def sname(x): 
        import re
        return re.sub(r'[^A-Za-z0-9_]', '_', str(x))

    for entry in states: 
        reg = str(entry['semantic_source']); bits = list(map(int, entry.get('semantic_bits', [])))
        if not bits: 
            bits = list(range(int(entry.get('bits', 0))))
        dom = entry.get('natural_update_domain') or {}
        active = sorted(map(str, dom.get('active_event_classes', [])))
        if len(active)!=1: 
            raise ValueError(f'natural EVENT_ONLY state {reg} requires exactly one canonical active event, got {active}')
        event = active[0]
        if event not in event_nets: 
            raise ValueError(f'event {event} has no neutral event net')
        clock = event_nets[event]; interface_inputs.add(clock)
        st = storage.get(reg)
        if not st: 
            raise ValueError(f'missing storage row for {reg}')
        sw = int(st.get('semantic_width') or (max(bits)+1))
        rules = [r for r in phase.get('update_rules', [])
               if str(r.get('target')) == reg and event in str(r.get('event_class', '')).split('+')]
        basis = sorted({str(e['basis']) for r in rules for e in r.get('enable', [])})
        # Materialize only proof-backed physical predicate projections.  A guarded
        # recurrence that cannot be projected fails closed rather than exposing an
        # invented external control input.
        bvars = []
        for bid in basis: 
            if bid not in phase_basis: 
                raise ValueError(f'unknown predicate basis {bid}')
            pe = project_predicate_expression(root, phase_basis[bid], bid)
            if pe is None: 
                raise ValueError(f'cannot physically project guarded event predicate {bid} for {reg}')
            bn = 'basis_'+sname(bid); bvars.append(bn); interface_inputs.update(map(str, expr_vars(pe)))
            # A predicate that is exactly an existing state/output net is an alias,
            # not a new combinational driver.  Inline it into D expressions rather
            # than attempting to rename the state net to the predicate name.
            if isinstance(pe, list) and len(pe) == 2 and pe[0] == 'VAR': 
                inline_basis[bn] = pe
            else: 
                comb[bn] = pe
        qvars = [f'nat_{sname(reg)}_b{b}' for b in bits]
        qnvars = [q+'_n' for q in qvars]
        vars = qvars+bvars
        ones = [[] for _ in bits]; conflicts = []; row_count = 0
        const0 = set(map(int, st.get('constant_zero_bits', []))); const1 = set(map(int, st.get('constant_one_bits', [])))
        # NARROW_ZERO_EXTEND is a proof-backed storage contract: semantic bits
        # above the retained natural width are exactly zero, not unknown state.
        # This matters for recurrences such as a right shift where the highest
        # retained D bit reads the first omitted semantic bit.
        if str(st.get('storage_kind')) == 'NARROW_ZERO_EXTEND': 
            kept = int(st.get('storage_bits', len(bits)))
            const0.update(range(kept, sw))
        unknown = [b for b in range(sw) if b not in bits and b not in const0 and b not in const1]
        fixed1 = sum(1<<b for b in const1)
        # Use two extreme fills for unknown non-retained bits.  Equality of the
        # projected retained result proves independence for bitwise/linear outcomes;
        # unsupported ambiguous outcomes fail below.
        for qb in itertools.product((0, 1), repeat = len(bits)): 
            base = fixed1|sum(int(v)<<b for v, b in zip(qb, bits))
            variants = [base]
            if unknown: 
                variants.append(base|sum(1<<b for b in unknown))
            for bb in itertools.product((0, 1), repeat = len(basis)): 
                benv = {bid: int(bb[i]) for i, bid in enumerate(basis)}
                applicable = []
                for r in rules: 
                    if all(benv[str(e['basis'])] == int(bool(e['polarity'])) for e in r.get('enable', [])): 
                        applicable.append(r)
                projected = set()
                for cur in variants: 
                    vals = [cur] if not applicable else [_eval_outcome(r['outcome'], reg, cur, sw) for r in applicable]
                    projected.update(tuple((v>>b)&1 for b in bits) for v in vals)
                if len(projected)!=1: 
                    conflicts.append({'state': qb, 'basis': benv, 'rules': [r.get('rule_id') for r in applicable], 
                                      'projected': sorted(projected)})
                    continue
                nxt = next(iter(projected)); key = tuple(qb)+tuple(bb); row_count+=1
                for i, y in enumerate(nxt): 
                    if y: 
                        ones[i].append(key)
        if conflicts: 
            raise ValueError(f'natural EVENT_ONLY recurrence conflict {reg}: {conflicts[:4]}')
        dexprs = [minimize_truth_table(vars, o, []) for o in ones]
        def inline_expr(expr):
            if isinstance(expr, (list, tuple)):
                if len(expr) == 2 and expr[0] == 'VAR' and str(expr[1]) in inline_basis:
                    return inline_expr(inline_basis[str(expr[1])])
                values = [inline_expr(value) for value in expr]
                return tuple(values) if isinstance(expr, tuple) else values
            return expr
        dexprs = [inline_expr(e) for e in dexprs]
        reset = aregs.get(reg, {}).get('reset', ['CONST', 0])
        if not (isinstance(reset, list) and len(reset) == 2 and reset[0] == 'CONST'): 
            raise ValueError(f'non-constant architectural reset unsupported for natural event state {reg}: {reset}')
        rv = int(reset[1])
        for bit, logical_q, logical_qn, dexpr in zip(bits, qvars, qnvars, dexprs): 
            rbit = (rv>>bit)&1
            if rbit: 
                dffs.append({'q': logical_qn, 'qb': logical_q, 'd_expr': N(dexpr), 'clock': clock, 'reset': reset_net, 
                             'cell': 'DFFR', 'semantic_source': reg, 'semantic_bit': bit, 'logical_state_net': logical_q, 
                             'reset_logical_level': 1})
            else: 
                dffs.append({'q': logical_q, 'qb': logical_qn, 'd_expr': dexpr, 'clock': clock, 'reset': reset_net, 
                             'cell': 'DFFR', 'semantic_source': reg, 'semantic_bit': bit, 'logical_state_net': logical_q, 
                             'reset_logical_level': 0})
            interface_outputs.extend([logical_q, logical_qn])
        proof_rows.append({'semantic_source': reg, 'semantic_bits': bits, 'event': event, 'clock_net': clock, 
                           'basis': basis, 'abstract_truth_rows': row_count, 'reset_value': rv})
    key = component_recipe_keys(root).get('natural_event_state')
    if key is None: 
        raise ValueError('natural_event_state selector absent')
    # q/qb feedback nets are produced by this component and intentionally appear as
    # Boolean variables inside its D functions; they are not external boundary inputs.
    # Predicate projection may discover one of those feedback nets while building a
    # basis expression, so remove all locally produced state nets only after every
    # state entry has been collected.
    interface_inputs.difference_update(interface_outputs)
    return {
      'version': 'bio2rtl-neutral-natural-event-state-v1', 'component_class': 'natural_event_state', 
      'selector_sha256': key, 'neutral_interface_binding': {'event_nets': event_nets, 'reset': reset_net}, 
      'interface_inputs': sorted(interface_inputs), 'interface_outputs': list(dict.fromkeys(interface_outputs)), 
      'combinational_outputs': comb, 'dffs': dffs, 'latches': [], 'primitives': [], 
      'derivation': {'kind': 'phase40-natural-event-recurrence', 'states': proof_rows, 
                    'no_free_running_clock': True, 'no_synthetic_polling_phase': True, 
                    'phase40_sha256': _sha(root/'build/semantic/phase40.ir.json')}, 
    }

def derive_phase_factorized_control_contract(root: Path, control_encoding_plan: dict|None = None)->dict: 
    """Derive a technology-neutral phase-factorized control recurrence.

    No protocol signal names are embedded here.  Concrete Q/QB/clock/reset nets and
    branch discriminator nets come from a content-addressed neutral-interface binding.
    The semantic certificate supplies only state equivalence classes and transitions.
    """
    from .neutral_bindings import resolve_control_interface_binding
    root = Path(root); g = root/'build/generated_certificates'
    ctrl = _load(g/'control_factorization.json')
    if ctrl.get('status')!='PASS': 
        raise ValueError('control factorization certificate not PASS')
    plan = control_encoding_plan
    if not plan: 
        raise ValueError('no physical control encoding plan supplied by mapper')
    iface = resolve_control_interface_binding(root)
    if not iface: 
        raise ValueError('no neutral control-interface binding for this semantic control shape')
    banks = iface['banks']
    rb = int(plan['bits']['rise']); fb = int(plan['bits']['fall'])
    if len(banks['rise']['q'])!=rb or len(banks['rise']['qb'])!=rb or len(banks['fall']['q'])!=fb or len(banks['fall']['qb'])!=fb: 
        raise ValueError(f'control binding width mismatch plan={plan["bits"]} binding rise={len(banks["rise"]["q"])} fall={len(banks["fall"]["q"])}')
    codes = _control_state_physical_codes(ctrl, plan)
    rel = []; seen = set()
    for r in ctrl.get('semantic_transition_relation', []): 
        ev = r['event']
        if ev not in ('PHEVT_RISE', 'PHEVT_FALL'): 
            continue
        k = (ev, int(r['source_state']), int(r['next_state']))
        if k not in seen: 
            seen.add(k)
            rel.append(k)
    bysrc = {}
    for ev, s, n in rel: 
        bysrc.setdefault((ev, s), set()).add(n)
    ambiguous = {k: sorted(v) for k, v in bysrc.items() if len(v)>1}

    rise_q = [str(x) for x in banks['rise']['q']]; fall_q = [str(x) for x in banks['fall']['q']]
    rise_qb = [str(x) for x in banks['rise']['qb']]; fall_qb = [str(x) for x in banks['fall']['qb']]
    rise_roles = list(iface.get('branch_roles', {}).get('rise', [])); fall_roles = list(iface.get('branch_roles', {}).get('fall', []))
    role_exprs = _role_expr_map(iface)
    role_order = [str(r['id']) for r in rise_roles+fall_roles]
    # Truth minimization uses abstract role variables; they are substituted by the freshly
    # discovered physical expressions only after next-state logic is minimized.
    vars = rise_q+fall_q+role_order
    spec = {name: {} for name in rise_q+fall_q}
    def put(out, bits, y): 
        old = spec[out].get(bits)
        if old is not None and old!=int(y): 
            raise ValueError(f'control truth conflict {out} {bits}: {old} vs {y}')
        spec[out][bits] = int(y)

    for ev, s, n in rel: 
        sr, sf = codes[s]; nr, nf = codes[n]
        current = _code_bits(sr, rb)+_code_bits(sf, fb)
        if ev == 'PHEVT_RISE': 
            rcomb = _role_combinations(rise_roles, nr, (ev, s) in ambiguous, s)
            fcomb = _role_combinations(fall_roles, None, False, s)
        else: 
            rcomb = _role_combinations(rise_roles, None, False, s)
            fcomb = _role_combinations(fall_roles, nf, (ev, s) in ambiguous, s)
        for rr in rcomb: 
            for ff in fcomb: 
                roles = {**rr, **ff}
                bits = current+tuple(int(roles[x]) for x in role_order)
                if ev == 'PHEVT_RISE': 
                    for i, q in enumerate(rise_q): 
                        put(q, bits, (nr>>i)&1)
                else: 
                    for i, q in enumerate(fall_q): 
                        put(q, bits, (nf>>i)&1)
    allrows = list(itertools.product((0, 1), repeat = len(vars)))
    # Keep all equally minimal covers here.  Per-output minimization can miss a
    # smaller *joint* circuit when two outputs can share a product term.
    # Candidate generation is purely from the discovered care/don't-care rows.
    expr_candidates = {}
    legacy_exprs = {}
    for out, d in spec.items(): 
        ones = [k for k, v in d.items() if v]
        dc = [k for k in allrows if k not in d]
        legacy_exprs[out] = minimize_truth_table(vars, ones, dc)
        expr_candidates[out] = minimize_truth_table_candidates(vars, ones, dc, max_candidates = 32)
    exprs = dict(legacy_exprs)
    complements = {**dict(zip(rise_q, rise_qb)), **dict(zip(fall_q, fall_qb))}
    def subst(e): 
        if not isinstance(e, tuple): 
            return e
        if e[0] == 'NOT' and isinstance(e[1], tuple) and e[1][0] == 'VAR' and e[1][1] in complements: 
            return V(complements[e[1][1]])
        if e[0] in ('CONST', 'VAR'): 
            return e
        if e[0] in ('AND', 'OR'): 
            return (e[0], tuple(subst(x) for x in e[1]))
        return tuple([e[0], *(subst(x) if isinstance(x, tuple) else x for x in e[1:])])
    # Preserve genuinely computed branch discriminators as named shared DAG nodes, but
    # do not materialize zero-cost aliases as fake component outputs.  A discovered role
    # may simply name an already existing physical predicate net (e.g. ROLE := source_net).
    # Such aliases are recursively inlined into D expressions.  This keeps the neutral
    # contract technology-independent and avoids inventing BUF cells solely to rename a net.
    exprs = {k: subst(v) for k, v in exprs.items()}

    role_alias = {}
    role_helpers = {}
    for rid in role_order: 
        rexp = subst(role_exprs[str(rid)])
        if isinstance(rexp, tuple) and len(rexp) == 2 and rexp[0] == 'VAR': 
            role_alias[str(rid)] = rexp
        else: 
            role_helpers[str(rid)] = rexp

    def inline_role_aliases(e): 
        e = _tupleize_local(e)
        if not isinstance(e, tuple): 
            return e
        if e[0] == 'VAR' and str(e[1]) in role_alias: 
            return inline_role_aliases(role_alias[str(e[1])])
        if e[0] in ('CONST', 'VAR'): 
            return e
        if e[0] in ('AND', 'OR'): 
            return (e[0], tuple(inline_role_aliases(x) for x in e[1]))
        return tuple([e[0], *(inline_role_aliases(x) if isinstance(x, tuple) else x for x in e[1:])])

    exprs = {k: subst(inline_role_aliases(v)) for k, v in exprs.items()}
    helpers = {k: inline_role_aliases(v) for k, v in role_helpers.items()}
    # Interface helper expressions are defined from semantic roles; after alias
    # elimination they are expressed only in real physical/derived nets.
    for h in iface.get('helpers', []): 
        helpers[str(h['name'])] = inline_role_aliases(_helper_expr(h, role_exprs))
    # A helper can itself collapse to a direct alias.  Inline it into all consumers and
    # omit it from the physical output set as well.
    helper_alias = {k: v for k, v in helpers.items() if isinstance(v, tuple) and len(v) == 2 and v[0] == 'VAR'}
    if helper_alias: 
        def inline_helper_aliases(e): 
            e = _tupleize_local(e)
            if not isinstance(e, tuple): 
                return e
            if e[0] == 'VAR' and str(e[1]) in helper_alias: 
                return inline_helper_aliases(helper_alias[str(e[1])])
            if e[0] in ('CONST', 'VAR'): 
                return e
            if e[0] in ('AND', 'OR'): 
                return (e[0], tuple(inline_helper_aliases(x) for x in e[1]))
            return tuple([e[0], *(inline_helper_aliases(x) if isinstance(x, tuple) else x for x in e[1:])])
        exprs = {k: subst(inline_helper_aliases(v)) for k, v in exprs.items()}
        helpers = {k: inline_helper_aliases(v) for k, v in helpers.items() if k not in helper_alias}

    # Score all minimum-cover alternatives as one bank so shared terms can win.
    def normalize_candidate(expr): 
        expr = subst(inline_role_aliases(expr))
        if helper_alias: 
            expr = subst(inline_helper_aliases(expr))
        return expr

    norm_candidates = {
        name: sorted({normalize_candidate(expr) for expr in candidates}, key = repr)
        for name, candidates in expr_candidates.items()
    }
    combo_count = 1
    for candidates in norm_candidates.values(): 
        combo_count *= max(1, len(candidates))

    joint_selection = {
        'candidate_combinations': combo_count, 
        'used': False, 
        'reason': 'fallback', 
    }
    if combo_count <= 4096 and len(norm_candidates) > 1: 
        from .boolean_contract import map_boolean_sequential_contract
        import itertools as _it

        outs = list(spec)
        try: 
            area_raw = _load(root/'technology/tr1um_cell_area.json')
            area_table = dict(area_raw.get('cell_area_um2', {}))
        except Exception: 
            area_table = {}

        legacy_norm = {name: normalize_candidate(legacy_exprs[name]) for name in outs}

        def subexpr_repr_set(expr): 
            expr = _tupleize_local(expr)
            result = set()

            def visit(node): 
                if not isinstance(node, tuple): 
                    return
                result.add(repr(node))
                if node[0] in ('AND', 'OR'): 
                    for child in node[1]: 
                        visit(child)
                else: 
                    for child in node[1:]: 
                        visit(child)

            visit(expr)
            return result

        legacy_sets = {name: subexpr_repr_set(expr) for name, expr in legacy_norm.items()}
        best = None
        for choice in _it.product(*(norm_candidates[name] for name in outs)): 
            dffs_for_score = []
            for q, qb, expr in zip(rise_q + fall_q, rise_qb + fall_qb, choice): 
                bank = 'rise' if q in rise_q else 'fall'
                dffs_for_score.append({
                    'q': q, 
                    'qb': qb, 
                    'd_expr': expr, 
                    'clock': str(banks[bank]['clock']), 
                    'reset': str(iface['reset']), 
                    'cell': 'DFFR', 
                })
            score_contract = {
                'component_class': 'phase_factorized_control_joint_score', 
                'interface_inputs': [], 
                'interface_outputs': [], 
                'combinational_outputs': helpers, 
                'dffs': dffs_for_score, 
                'complements': {
                    **dict(zip(rise_q, rise_qb)), 
                    **dict(zip(rise_qb, rise_q)), 
                    **dict(zip(fall_q, fall_qb)), 
                    **dict(zip(fall_qb, fall_q)), 
                }, 
            }
            cells = map_boolean_sequential_contract(
                score_contract, area_table, 
                component = 'phase_factorized_control_joint_score', name_prefix = 'j', 
            )
            area_score = sum(float(area_table.get(cell['type'], 0.0)) for cell in cells)
            changed = sum(expr != legacy_norm[name] for name, expr in zip(outs, choice))
            edit = sum(
                len(subexpr_repr_set(expr) ^ legacy_sets[name])
                for name, expr in zip(outs, choice)
            )
            score = (len(cells), area_score, changed, edit, tuple(map(repr, choice)))
            if best is None or score < best[0]: 
                best = (score, choice)

        if best is not None: 
            exprs = {name: expr for name, expr in zip(outs, best[1])}
            joint_selection = {
                'candidate_combinations': combo_count, 
                'used': True, 
                'mapped_cell_count': best[0][0], 
                'mapped_area_tiebreak_um2': best[0][1], 
                'changed_outputs_vs_legacy': best[0][2], 
                'structural_edit_distance_vs_legacy': best[0][3], 
                'selected_expression_repr': {name: repr(exprs[name]) for name in outs}, 
            }
    key = component_recipe_keys(root)['phase_factorized_control']
    interface_inputs = []
    role_inputs = sorted(set().union(*(_neutral_expr_vars(role_exprs[x]) for x in role_order))) if role_order else []
    for n in [*role_inputs, str(iface['reset']), str(banks['rise']['clock']), str(banks['fall']['clock']), *rise_q, *rise_qb, *fall_q, *fall_qb]: 
        if n not in interface_inputs: 
            interface_inputs.append(n)
    interface_outputs = []
    for n in [*rise_q, *rise_qb, *fall_q, *fall_qb, *helpers.keys()]: 
        if n not in interface_outputs: 
            interface_outputs.append(n)
    dffs = []
    for bank, qv, qbv in [('rise', rise_q, rise_qb), ('fall', fall_q, fall_qb)]: 
        for q, qb in zip(qv, qbv): 
            dffs.append({'q': q, 'qb': qb, 'd_expr': exprs[q], 'clock': str(banks[bank]['clock']), 'reset': str(iface['reset']), 'cell': 'DFFR'})
    contract = {
      'version': 'bio2rtl-neutral-boolean-sequential-contract-v3-generic-control', 
      'component_class': 'phase_factorized_control', 'selector_sha256': key, 
      'control_encoding_plan': plan, 'neutral_interface_binding': iface, 
      'interface_inputs': interface_inputs, 'interface_outputs': interface_outputs, 
      'combinational_outputs': helpers, 'dffs': dffs, 
      'derivation': {
        'kind': 'fresh-control-transition-relation+generic-branch-role-binding+truth-minimization', 
        'control_certificate_sha256': _sha(g/'control_factorization.json'), 
        'control_encoding_plan': plan, 
        'semantic_transition_rows_used': len(rel), 
        'ambiguous_families': [{'event': ev, 'source_state': s, 'next_states': ns} for (ev, s), ns in sorted(ambiguous.items())], 
        'branch_roles': iface.get('branch_roles', {}), 
        'semantic_register_names_used_as_selection_criteria': False, 
        'protocol_signal_names_embedded_in_lowering_code': False, 
        'joint_minimum_cover_selection': joint_selection, 
      }, 
    }
    return contract


def derive_open_drain_oe_contract(root: Path, control_encoding_plan: dict|None = None)->dict: 
    from .neutral_bindings import resolve_control_interface_binding, resolve_oe_interface_binding
    root = Path(root); g = root/'build/generated_certificates'
    oe = _load(g/'oe_recurrence.json'); ctrl = _load(g/'control_factorization.json')
    plan = control_encoding_plan
    if not plan: 
        raise ValueError('no physical control encoding plan supplied by mapper')
    if oe.get('status')!='PASS' or oe.get('counterexamples'): 
        raise ValueError('OE recurrence certificate not PASS')
    cif = resolve_control_interface_binding(root); oif = resolve_oe_interface_binding(root)
    if not cif or not oif: 
        raise ValueError('no neutral OE/control interface binding for current selectors')
    banks = cif['banks']; rise_q = [str(x) for x in banks['rise']['q']]; fall_q = [str(x) for x in banks['fall']['q']]
    rise_qb = [str(x) for x in banks['rise']['qb']]; fall_qb = [str(x) for x in banks['fall']['qb']]
    rows = {int(x['state']): x for x in ctrl['state_rows']}
    rise_index = {int(st): i for i, grp in enumerate(ctrl['rise_components']) for st in grp}
    fall_index = {int(st): i for i, grp in enumerate(ctrl['fall_components']) for st in grp}
    rise_codes = [int(x) for x in plan['rise_component_codes']]; fall_codes = [int(x) for x in plan['fall_component_codes']]
    oq = str(oif['state']['q']); oqb = str(oif['state']['qb']); data_net = str(oif['data_predicate']['net'])
    count_spec = oif['count_zero']; count_role = '__bio2rtl_count_zero__'
    count_expr = _tupleize_local(count_spec['expression']) if count_spec.get('expression') is not None else None
    count_net = str(count_spec['net']) if count_expr is None else count_role
    vars = rise_q+fall_q+[oq, count_net, data_net]
    ones = []; zeros = []; specified = {}
    table = oe['truth_table_by_control_state']
    for sk, entries in table.items(): 
        st = int(sk); rc = rise_codes[rise_index[st]]; fc = fall_codes[fall_index[st]]
        cbase = _code_bits(rc, len(rise_q))+_code_bits(fc, len(fall_q))
        for e in entries: 
            cz = int(e['count_zero'])
            cv = cz if str(oif['count_zero'].get('polarity', 'direct')) == 'direct' else 1-cz
            dp = int(e['data_predicate'])
            dv = dp if str(oif['data_predicate'].get('polarity', 'direct')) == 'direct' else 1-dp
            bits = cbase+(int(e['oe']), cv, dv); y = int(e['next_oe'])
            if bits in specified and specified[bits]!=y: 
                raise ValueError(f'conflicting OE truth rows {bits}: {specified[bits]} vs {y}')
            specified[bits] = y
            (ones if y else zeros).append(bits)
    allrows = list(itertools.product((0, 1), repeat = len(vars))); dc = [x for x in allrows if x not in specified]
    expr = minimize_truth_table(vars, ones, dc)
    complements = {**dict(zip(rise_q, rise_qb)), **dict(zip(fall_q, fall_qb)), oq: oqb}
    def subst(e): 
        if not isinstance(e, tuple): 
            return e
        if e[0] == 'VAR' and count_expr is not None and e[1] == count_role: 
            return count_expr
        if e[0] == 'NOT' and isinstance(e[1], tuple) and e[1][0] == 'VAR' and e[1][1] in complements: 
            return V(complements[e[1][1]])
        if e[0] in ('CONST', 'VAR'): 
            return e
        if e[0] in ('AND', 'OR'): 
            return (e[0], tuple(subst(x) for x in e[1]))
        return tuple([e[0], *(subst(x) if isinstance(x, tuple) else x for x in e[1:])])
    expr = subst(expr)
    key = component_recipe_keys(root)['open_drain_oe_recurrence']
    ins = []
    count_inputs = sorted(_neutral_expr_vars(count_expr)) if count_expr is not None else [count_net]
    for n in [*rise_q, *rise_qb, *fall_q, *fall_qb, oq, *count_inputs, str(oif['reset']), str(oif['clock']), data_net]: 
        if n not in ins: 
            ins.append(n)
    next_out = str(oif.get('next_comb_output', f'{oq}_d'))
    contract = {
      'version': 'bio2rtl-neutral-boolean-sequential-contract-v3-generic-oe', 
      'component_class': 'open_drain_oe_recurrence', 'selector_sha256': key, 
      'neutral_interface_binding': oif, 
      'interface_inputs': ins, 'interface_outputs': [oq], 
      'combinational_outputs': {next_out: expr}, 
      'dffs': [{'q': oq, 'qb': oqb, 'd_expr': expr, 'clock': str(oif['clock']), 'reset': str(oif['reset']), 'cell': 'DFFR'}], 
      'derivation': {
        'kind': 'fresh-proof-truth-table-minimization+generic-interface-binding', 
        'oe_certificate_sha256': _sha(g/'oe_recurrence.json'), 'control_certificate_sha256': _sha(g/'control_factorization.json'), 
        'control_encoding_plan': plan, 'specified_truth_rows': len(specified), 'dontcare_rows': len(dc), 
        'semantic_register_names_used_as_selection_criteria': False, 'protocol_signal_names_embedded_in_lowering_code': False, 
      }, 
    }
    return contract



def _resolve_reset_net_for_events(root: Path, events: list[str])->str: 
    """Resolve an asynchronous semantic event-set to a physical reset net.

    The lowering never names a protocol event.  The project event-interface binding
    declares which physical reset net is the OR/qualified realization of each semantic
    event set; an exact set match is required so we fail closed rather than silently
    broadening reset behavior.
    """
    from .neutral_bindings import resolve_event_interface_binding
    iface = resolve_event_interface_binding(root)
    if not iface: 
        raise ValueError('no neutral event-interface binding available for async reset resolution')
    wanted = set(map(str, events))
    matches = []
    for row in iface.get('reset_groups', []): 
        if set(map(str, row.get('events', []))) == wanted: 
            matches.append(str(row['net']))
    if len(matches)!=1: 
        raise ValueError(f'async reset event set {sorted(wanted)} resolves to {matches}, expected exactly one physical reset net')
    return matches[0]


def derive_direct_state_contract(root: Path)->dict: 
    """Lower a proof-backed event-resettable modulo-state recurrence generically.

    The recurrence certificate supplies only semantic width/event behavior.  Physical
    state net names, clock, and the Boolean predicate that denotes ADVANCE are supplied
    by a content-addressed neutral interface binding.  No semantic register or protocol
    signal name participates in the lowering algorithm.
    """
    from .neutral_bindings import resolve_direct_state_interface_binding
    root = Path(root); g = root/'build/generated_certificates'
    cert = _load(g/'direct_state_recurrence.json')
    if cert.get('status')!='PASS' or cert.get('counterexamples'): 
        raise ValueError('direct-state recurrence certificate not PASS')
    plan = resolve_direct_state_interface_binding(root)
    if not plan: 
        raise ValueError('no neutral direct-state interface binding for current component selector')
    width = int(cert['width']); bits = list(plan.get('state_bits', []))
    if width<=0 or len(bits)!=width: 
        raise ValueError(f'direct-state width mismatch proof={width} binding={len(bits)}')
    if cert.get('recurrence', {}).get('phase_edge')!='advance ? (state + 1) mod 2^width : state': 
        raise ValueError('unsupported direct-state phase recurrence class')
    q = [str(x['net']) for x in bits]; qb = [str(x['complement']) for x in bits]
    adv = _tupleize_local(plan['advance_expression'])
    # Generic ripple modulo increment: bit i toggles iff ADVANCE and every lower bit is 1.
    toggle = adv; dffs = []
    for i, (qn, qbn) in enumerate(zip(q, qb)): 
        dexpr = X(V(qn), toggle)
        dffs.append({'q': qn, 'qb': qbn, 'd_expr': dexpr, 'clock': str(plan['clock']), 'reset': '__RESOLVE__', 'cell': 'DFFR', 
                     'semantic_state_bit': i, 'recurrence_role': 'modulo_increment'})
        toggle = A(toggle, V(qn))
    reset_events = [str(x) for x in cert.get('async_reset_events', [])]
    if not reset_events: 
        raise ValueError('direct-state generic contract currently requires proof-backed async zero-reset events')
    reset_net = _resolve_reset_net_for_events(root, reset_events)
    for d in dffs: 
        d['reset'] = reset_net
    ins = []
    for n in [*map(str, plan.get('advance_inputs', [])), str(plan['clock']), reset_net]: 
        if n not in ins: 
            ins.append(n)
    complements = {}
    for a, b in zip(q, qb): 
        complements[a] = b
        complements[b] = a
    return {
      'version': 'bio2rtl-neutral-direct-state-contract-v1', 
      'component_class': 'direct_protocol_state', 
      'selector_sha256': component_recipe_keys(root)['direct_protocol_state'], 
      'neutral_interface_binding': plan, 
      'interface_inputs': ins, 
      'interface_outputs': [x for pair in zip(q, qb) for x in pair], 
      'combinational_outputs': {}, 
      'dffs': dffs, 
      'complements': complements, 
      'derivation': {
        'kind': 'fresh-proof-event-resettable-modulo-state-recurrence+generic-interface-binding', 
        'direct_state_certificate_sha256': _sha(g/'direct_state_recurrence.json'), 
        'semantic_width': width, 
        'phase_event': str(cert['phase_event']), 
        'async_reset_events': reset_events, 
        'resolved_reset_net': reset_net, 
        'semantic_register_names_used_as_selection_criteria': False, 
        'protocol_signal_names_embedded_in_lowering_code': False, 
      }, 
    }


def _tupleize_local(x): 
    if isinstance(x, list): 
        return tuple(_tupleize_local(v) for v in x)
    if isinstance(x, dict): 
        return {k: _tupleize_local(v) for k, v in x.items()}
    return x


def _neutral_expr_vars(e, out = None): 
    out = set() if out is None else out
    if not isinstance(e, (tuple, list)) or not e: 
        return out
    if e[0] == 'VAR': 
        out.add(str(e[1]))
        return out
    if e[0] in ('AND', 'OR'): 
        for x in e[1]: 
            _neutral_expr_vars(x, out)
    else: 
        for x in e[1:]: 
            if isinstance(x, (tuple, list)): 
                _neutral_expr_vars(x, out)
    return out


def derive_load_hold_bank_contract(root: Path)->dict: 
    """Lower any number of proof/interface-defined synchronous load/hold register banks.

    Each bank declares a Boolean load predicate and one load-data expression per bit.
    The lowering is independent of protocol, register names, bank count, and bank width.
    Q/QB orientation is explicit so complementary physical outputs remain free.
    """
    from .neutral_bindings import resolve_load_hold_bank_binding
    root = Path(root); plan = resolve_load_hold_bank_binding(root)
    if not plan: 
        raise ValueError('no neutral load/hold-bank binding for current selector')
    clock = str(plan['clock']); reset = str(plan['reset']); dffs = []; inputs = []; outputs = []; complements = {}
    for bank in plan.get('banks', []): 
        load = _tupleize_local(bank['load_expression'])
        for bit in bank.get('bits', []): 
            q = str(bit['q']); qb = str(bit['qb']); data = _tupleize_local(bit['load_data'])
            dexpr = M(load, V(q), data)
            dffs.append({'q': q, 'qb': qb, 'd_expr': dexpr, 'clock': clock, 'reset': reset, 'cell': 'DFFR', 
                         'bank_role': str(bank.get('role', 'load_hold'))})
            complements[q] = qb; complements[qb] = q
            for n in (q, qb): 
                if n not in outputs: 
                    outputs.append(n)
            for n in sorted(_neutral_expr_vars(load)|_neutral_expr_vars(data)): 
                if n!=q and n not in inputs: 
                    inputs.append(n)
    for n in (clock, reset): 
        if n not in inputs: 
            inputs.append(n)
    return {
      'version': 'bio2rtl-neutral-load-hold-bank-contract-v1', 
      'component_class': 'selector_snapshot_gpio_payload', 
      'selector_sha256': component_recipe_keys(root)['selector_snapshot_gpio_payload'], 
      'neutral_interface_binding': plan, 
      'interface_inputs': inputs, 'interface_outputs': outputs, 
      'combinational_outputs': {}, 'dffs': dffs, 'complements': complements, 
      'derivation': {
        'kind': 'generic-synchronous-load-hold-bank-recurrence+content-addressed-interface-binding', 
        'bank_count': len(plan.get('banks', [])), 
        'state_bits': len(dffs), 
        'physical_recipe_used_to_generate_contract': False, 
        'protocol_signal_names_embedded_in_lowering_code': False, 
      }, 
    }




def derive_natural_async_latch_contract(root: Path)->dict: 
    """Lower a proof-backed detector-free asynchronous SET/RESET/HOLD bit."""
    root = Path(root); arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json')
    phase = _load(root/'build/semantic/phase40.ir.json')
    accepted_proofs = {'phase_ir_detector_free_async_recurrence', 'phase_ir_event_free_hidden_state_exact_quotient'}
    entries = [x for x in arch.get('non_dff_state', []) if x.get('proof') in accepted_proofs]
    if len(entries)!=1: 
        raise ValueError(f'natural async lowering currently requires one latch entry, got {len(entries)}')
    e = entries[0]; rec = e.get('async_recurrence') or {}
    if rec.get('proof_model') not in {'FULL_INPUT_CUBE_SET_RESET_HOLD_NO_TOGGLE', 'EXACT_REACHABLE_EVENT_FREE_HIDDEN_STATE_QUOTIENT_SET_RESET_HOLD_NO_TOGGLE'}: 
        raise ValueError('async recurrence proof model missing')
    if int(rec.get('reset_value', 0))!=0: 
        raise ValueError('natural async latch reset=1 requires explicit polarity realization')
    source = str(e['semantic_source']); bits = list(map(int, e.get('semantic_bits', [])))
    if len(bits)!=1: 
        raise ValueError('natural async latch must be one semantic bit')
    bit = bits[0]
    areg = next((x for x in phase.get('architectural_registers', []) if str(x.get('id')) == source), {})
    prov = str(areg.get('provenance', ''))
    prefix = 'gdata' if prov == 'gpio_data' else ('gdir' if prov == 'gpio_direction' else source.lower()+'_b')
    q = f'{prefix}{bit}'; qb = f'{q}_n'
    bitnets = _project_gpio_core_inputs(root)
    ibits = list(map(int, rec.get('input_bits', [])))
    missing = [b for b in ibits if b not in bitnets]
    if missing: 
        raise ValueError(f'async recurrence GPIO inputs have no project core binding: {missing}')
    vars = [bitnets[b] for b in ibits]
    rows = rec.get('truth_table', []); bykey = {tuple(int(r['inputs'][str(b)]) for b in ibits): str(r['action']) for r in rows}
    allrows = list(itertools.product((0, 1), repeat = len(vars)))
    if set(bykey)!=set(allrows): 
        raise ValueError('async recurrence input cube is not total')
    set_ones = [r for r in allrows if bykey[r] == 'SET']; rst_ones = [r for r in allrows if bykey[r] == 'RESET']
    set_expr = minimize_truth_table(vars, set_ones, []); rst_expr = minimize_truth_table(vars, rst_ones, [])
    from .project_inputs import load_config
    _, cfg = load_config(root); reset = 'reset'
    set_net = f'{q}_async_set'; comb = {set_net: A(set_expr, N(V(reset)))}
    reset_inputs = [reset]
    if rst_ones: 
        rst_net = f'{q}_async_reset'; comb[rst_net] = rst_expr; reset_inputs.append(rst_net)
    return {
      'version': 'bio2rtl-neutral-natural-async-latch-v1', 'component_class': 'natural_async_latch', 
      'selector_sha256': component_recipe_keys(root)['natural_async_latch'], 
      'interface_inputs': vars+[reset], 'interface_outputs': [q, qb], 
      'combinational_outputs': comb, 'dffs': [], 'primitives': [], 
      'latches': [{'implementation_class': 'cross_coupled_nor', 'q': q, 'qb': qb, 
                  'set_inputs': [set_net], 'reset_inputs': reset_inputs}], 
      'derivation': {'kind': ('exact-reachable-hidden-state-quotient-set-reset-hold-recurrence' if rec.get('hidden_sources') else 'full-input-cube-detector-free-set-reset-hold-recurrence'), 
                    'semantic_source': source, 'semantic_bit': bit, 'truth_rows': len(rows), 
                    'event_free_used_as_clock': False, 'artificial_clock_created': False, 
                    'reset_dominates_set': True, 'protocol_signal_names_embedded_in_lowering_code': False}, 
    }

def derive_static_quiescent_gpio_contract(root: Path)->dict: 
    """Lower proof-constant startup GPIO state to literal core assignments.

    Only GPIO numbers declared by project ``[[io]]`` are exposed.  The contract
    contains no state, primitive, clock, or reset dependency.
    """
    from .project_inputs import load_config
    root = Path(root); _, cfg = load_config(root)
    phase = _load(root/'build/semantic/phase40.ir.json')
    legal = _load(root/'build/semantic/legal_product.json')
    sm = {str(x.get('register')): x for x in phase.get('storage_optimization', {}).get('register_storage', [])
        if x.get('kind') == 'GPIO' and x.get('storage_kind') == 'CONST'}
    data = sm.get('G_DATA'); dire = sm.get('G_DIR')
    if data is None and dire is None: 
        raise ValueError('STATIC_QUIESCENT has no constant GPIO storage')
    comb = {}; outs = []
    for io in cfg.get('io', []): 
        if 'gpio' not in io: 
            continue
        bit = int(io['gpio'])
        for prefix, row in [('gdata', data), ('gdir', dire)]: 
            if row is None: 
                continue
            val = (int(row.get('constant_value', 0))>>bit)&1
            n = f'{prefix}{bit}'; nb = f'{n}_n'
            comb[n] = ('CONST', val); comb[nb] = ('CONST', 1-val)
            outs.extend([n, nb])
    if not outs: 
        raise ValueError('STATIC_QUIESCENT project declares no GPIO outputs')
    return {
      'version': 'bio2rtl-neutral-static-quiescent-gpio-v1', 
      'component_class': 'static_quiescent_gpio', 
      'selector_sha256': component_recipe_keys(root)['static_quiescent_gpio'], 
      'interface_inputs': [], 'interface_outputs': outs, 
      'combinational_outputs': comb, 'dffs': [], 'latches': [], 'primitives': [], 
      'derivation': {'kind': 'proof-constant-gpio', 'proof_model': str(legal.get('proof_model')), 
                    'artificial_clock_created': False, 'state_bits': 0, 
                    'protocol_signal_names_embedded_in_lowering_code': False}, 
    }

def derive_bound_combinational_contract(root: Path, component_class: str)->dict: 
    """Instantiate a technology-neutral combinational component from a bound Boolean plan."""
    from .neutral_bindings import resolve_comb_output_binding
    root = Path(root); plan = resolve_comb_output_binding(root, component_class)
    if not plan: 
        raise ValueError(f'no neutral combinational-output binding for {component_class}')
    outs = {str(k): _tupleize_local(v) for k, v in plan.get('outputs', {}).items()}
    helpers = {str(k): _tupleize_local(v) for k, v in plan.get('helpers', {}).items()}
    if set(outs)!=set(map(str, plan.get('interface_outputs', []))): 
        raise ValueError(f'{component_class} output expression set does not match interface outputs')
    overlap = set(helpers)&set(outs)
    if overlap: 
        raise ValueError(f'{component_class} helper/output name overlap: {sorted(overlap)}')
    comb = {**helpers, **outs}
    return {
      'version': 'bio2rtl-neutral-bound-combinational-contract-v1', 
      'component_class': component_class, 
      'selector_sha256': component_recipe_keys(root)[component_class], 
      'neutral_interface_binding': plan, 
      'interface_inputs': list(map(str, plan.get('interface_inputs', []))), 
      'interface_outputs': list(map(str, plan.get('interface_outputs', []))), 
      'combinational_outputs': comb, 'dffs': [], 'complements': {str(k): str(v) for k, v in plan.get('complements', {}).items()}, 
      'internal_helpers': sorted(helpers), 
      'derivation': {
        'kind': 'generic-bound-Boolean-DAG-contract', 
        'physical_recipe_used_to_generate_contract': False, 
        'protocol_signal_names_embedded_in_lowering_code': False, 
      }, 
    }

def generate_semantic_neutral_contracts(root: Path, control_encoding_plan: dict|None = None)->dict: 
    """Generate all currently applicable proof-backed neutral components.

    Applicability is discovered by semantic_component_manifest rather than by requiring
    a fixed nine-component benchmark shape.  Generator functions remain a registry of
    generic transform classes; missing/N_A transforms simply do not appear.
    """
    from .semantic_component_manifest import emit_semantic_component_manifest
    root = Path(root); out = root/'build/semantic_neutral_contracts'; out.mkdir(parents = True, exist_ok = True)
    manifest = emit_semantic_component_manifest(root)
    registry = {
      'scheduler_event_detector': lambda: derive_event_detector_contract(root), 
      'static_quiescent_gpio': lambda: derive_static_quiescent_gpio_contract(root), 
      'natural_event_state': lambda: derive_natural_event_state_contract(root), 
      'natural_async_latch': lambda: derive_natural_async_latch_contract(root), 
      'factorized_control': lambda: derive_phase_factorized_control_contract(root, control_encoding_plan), 
      'bound_boolean_dag': None, 
      'shared_counter_latch': lambda: derive_shared_counter_latch_contract(root, control_encoding_plan), 
      'quotient_state_bank': lambda: derive_shift_quotient_contract(root), 
      'modulo_state_recurrence': lambda: derive_direct_state_contract(root), 
      'load_hold_banks': lambda: derive_load_hold_bank_contract(root), 
      'single_state_recurrence': lambda: derive_open_drain_oe_contract(root, control_encoding_plan), 
    }
    contracts = {}
    # Remove stale contracts from a previous architecture shape before regeneration.
    for p in out.glob('*.json'): 
        p.unlink()
    for item in manifest.get('components', []): 
        name = str(item['component_class']); gen = str(item['generator'])
        if gen == 'bound_boolean_dag': 
            d = derive_bound_combinational_contract(root, name)
        else: 
            fn = registry.get(gen)
            if fn is None: 
                raise ValueError(f'no neutral contract generator registered for {gen!r}')
            d = fn()
        if str(d.get('component_class'))!=name: 
            raise ValueError(f'component manifest/generator mismatch {name!r} != {d.get("component_class")!r}')
        d['manifest_generator'] = gen
        d['manifest_claims'] = {k: item.get(k, []) for k in ('claims_physical_roles', 'claims_proofs', 'claims_non_dff_roles')}
        contracts[name] = d; (out/f'{name}.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    # Generic dead stateless-component elimination.  Legacy/migration Boolean DAGs are
    # allowed to participate in discovery, but a combinational component whose outputs feed
    # neither another component nor the declared physical-support boundary must not survive
    # into the mapped architecture.  Iterate because removing one dead DAG may make an
    # upstream DAG dead as well.
    from .project_inputs import load_config
    from .neutral_graph import _core_boundary
    _, cfg = load_config(root); _, support_out = _core_boundary(root, cfg)
    from .physical_interface import resolve_core_interface
    ps = resolve_core_interface(root, write_report = False)
    aliases = {str(k): str(v) for k, v in (ps.get('core_aliases') or {}).items()}
    boundary_required = set(map(str, support_out))|set(aliases.values())
    pruned = []
    while True: 
        removed = None
        for name, c in sorted(contracts.items()): 
            if c.get('dffs') or c.get('latches') or c.get('primitives'): 
                continue
            # Proof-constant GPIO roles are compiler facts, not physical state.  If
            # their literal nets feed neither another semantic component nor the
            # inferred package boundary, they must be pruned like any other dead
            # stateless component.  Physical constant-output/direction ties are
            # realized directly by the support recipe resolver.
            produced = set(map(str, c.get('interface_outputs', [])))|set(map(str, (c.get('combinational_outputs') or {}).keys()))
            required = set(boundary_required)
            for other, oc in contracts.items(): 
                if other!=name: 
                    required.update(map(str, oc.get('interface_inputs', [])))
            if produced.isdisjoint(required): 
                removed = name; break
        if removed is None: 
            break
        pruned.append(removed); contracts.pop(removed, None)
        fp = out/f'{removed}.json'
        if fp.exists(): 
            fp.unlink()
        manifest['components'] = [x for x in manifest.get('components', []) if str(x.get('component_class'))!=removed]
    if pruned: 
        manifest['dead_stateless_components_pruned'] = pruned
        (root/'build/semantic_component_manifest.json').write_text(json.dumps(manifest, indent = 2, sort_keys = True)+'\n')
    rep = {'version': 'bio2rtl-semantic-neutral-contract-generation-v2', 'status': 'PASS' if manifest.get('status') == 'PASS' else manifest.get('status'), 
         'components': sorted(contracts), 'component_count': len(contracts), 'fixed_component_count_required': False, 
         'dead_stateless_components_pruned': pruned, 
         'manifest_status': manifest.get('status')}
    (root/'build/stage9b_semantic_contract_report.json').write_text(json.dumps(rep, indent = 2, sort_keys = True)+'\n')
    return rep

