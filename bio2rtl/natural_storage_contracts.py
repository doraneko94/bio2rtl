from __future__ import annotations
from pathlib import Path
import itertools, json, math
from .boolean_mapper import V, N
from .truth_minimize import minimize_truth_table
from .recover_seedless import _natural_realization_domain


def _load(p): 
    return json.loads(Path(p).read_text())

def _eval_outcome(expr, reg, cur, width): 
    if not isinstance(expr, (list, tuple)) or not expr: 
        raise ValueError(expr)
    tag = expr[0]; mask = (1<<width)-1
    if tag == 'CONST': 
        return int(expr[1])&mask
    if tag == 'REG': 
        if expr[1]!=reg: 
            raise ValueError('cross-register outcome')
        return cur&mask
    if tag == 'OP': 
        op = expr[1]; args = expr[2]
        vals = [_eval_outcome(x, reg, cur, width) for x in args]
        if op == 'ADD': 
            return sum(vals)&mask
        if op == 'SUB': 
            z = vals[0]
            for x in vals[1:]: 
                z-=x
            return z&mask
        if op == 'AND': 
            z = mask
            for x in vals: 
                z&=x
            return z&mask
        if op == 'OR': 
            z = 0
            for x in vals: 
                z|=x
            return z&mask
        if op == 'XOR': 
            z = 0
            for x in vals: 
                z^=x
            return z&mask
        if op == 'SHR': 
            if len(vals)!=2: 
                raise ValueError('SHR arity')
            return (vals[0] >> vals[1]) & mask
        if op == 'SHL': 
            if len(vals)!=2: 
                raise ValueError('SHL arity')
            return (vals[0] << vals[1]) & mask
    raise ValueError('unsupported outcome '+repr(expr))



def _clock_net_for_semantic_event(root: Path, phase: dict, event: str)->str: 
    """Resolve a scheduler event to its physical clock net without protocol names."""
    root = Path(root); event = str(event)
    rows = [d for d in phase.get('scheduler_detectors', []) if str(d.get('event_id')) == event]
    if len(rows)!=1: 
        raise ValueError(f'event {event} does not have one scheduler detector')
    d = rows[0]
    if str(d.get('kind'))!='POLLING_PHASE_COMPLETION': 
        # Qualified-edge events are pulses, not raw input clocks.  Their physical
        # event net is provided by the neutral event-interface binding.
        from .neutral_bindings import resolve_event_interface_binding
        iface = resolve_event_interface_binding(root) or {}
        n = (iface.get('event_nets', {}) or {}).get(event)
        if not n: 
            raise ValueError(f'event {event} has no neutral event net')
        return str(n)
    from .semantic_projection import gpio_core_input_map
    bit = int(d['input_bit']); bitnet = gpio_core_input_map(root)
    if bit not in bitnet: 
        raise ValueError(f'no project core-input binding for scheduler GPIO bit {bit}')
    edge = str(d.get('edge', '')).upper()
    if edge == 'RISE': 
        return str(bitnet[bit])
    if edge == 'FALL': 
        from .neutral_bindings import resolve_event_interface_binding
        iface = resolve_event_interface_binding(root) or {}
        comps = {int(k): str(v) for k, v in (iface.get('clock_complements', {}) or {}).items()}
        if bit not in comps: 
            raise ValueError(f'falling scheduler event {event} has no clock complement binding for GPIO bit {bit}')
        return comps[bit]
    raise ValueError(f'unsupported scheduler event edge {edge} for {event}')

def derive_single_edge_natural_storage_contract(phase: dict, entry: dict, reset_net = 'proto_reset'): 
    """Derive a neutral DFF contract for one natural-storage entry.

    Predicate-basis atoms remain abstract Boolean inputs. This function proves that
    the per-edge rule set is total-by-hold and conflict-free over the full abstract
    basis cube. Physical predicate realization/binding is a later mapper stage.
    """
    reg = entry.get('semantic_source')
    if not isinstance(reg, str): 
        return {'status': 'UNSUPPORTED', 'reason': 'non-scalar semantic_source'}
    bits = list(entry.get('semantic_bits', range(int(entry['bits']))))
    dom = entry.get('natural_update_domain') or _natural_realization_domain(phase, reg, bits)
    if len(dom.get('phase_edges', []))!=1: 
        return {'status': 'UNSUPPORTED_MULTI_EDGE', 'semantic_source': reg, 'domain': dom}
    edge = dom['phase_edges'][0]
    storage = next(x for x in phase['storage_optimization']['register_storage'] if x['register'] == reg)
    semantic_width = int(storage.get('semantic_width') or max(bits)+1)
    rules = [r for r in phase.get('update_rules', []) if r.get('target') == reg and edge in str(r.get('event_class', '')).split('+')]
    basis = sorted({e['basis'] for r in rules for e in r.get('enable', [])})
    qvars = [f'{reg}_b{b}' for b in bits]
    vars = qvars+[f'basis_{b}' for b in basis]
    ones = [[] for _ in bits]; specified = []; conflicts = []; rows = 0
    bidx = {b: i for i, b in enumerate(basis)}
    # reconstruct only retained bits into semantic register value; unretained bits are
    # zero. This is exact when rule outcomes do not make retained bits depend on those
    # missing bits; detect violations by evaluating both all-zero and all-one fills.
    missing = [i for i in range(semantic_width) if i not in bits]
    for qb in itertools.product((0, 1), repeat = len(bits)): 
      base = sum(int(v)<<bit for v, bit in zip(qb, bits))
      variants = [base]
      if missing: 
        variants.append(base|sum(1<<i for i in missing))
      for bb in itertools.product((0, 1), repeat = len(basis)): 
        env = {b: bb[i] for b, i in bidx.items()}
        applicable = []
        for r in rules: 
            ok = all(int(env[e['basis']]) == int(bool(e['polarity'])) for e in r.get('enable', []))
            if ok: 
                applicable.append(r)
        projected = set()
        chosen_ids = []
        for cur in variants: 
            vals = []
            if not applicable: 
                vals = [cur]
            else: 
                for r in applicable: 
                    try: 
                        vals.append(_eval_outcome(r['outcome'], reg, cur, semantic_width))
                    except ValueError: 
                        return {'status': 'UNSUPPORTED_OUTCOME', 'semantic_source': reg, 'rule_id': r.get('rule_id'), 'outcome': r.get('outcome')}
            projected.update(tuple((v>>bit)&1 for bit in bits) for v in vals)
        if len(projected)!=1: 
            conflicts.append({'q': qb, 'basis': env, 'rules': [r.get('rule_id') for r in applicable], 'projected': sorted(projected)})
            continue
        nxt = next(iter(projected)); row = tuple(qb)+tuple(bb); specified.append(row); rows+=1
        for i, y in enumerate(nxt): 
            if y: 
                ones[i].append(row)
    if conflicts: 
        return {'status': 'NEEDS_FEASIBILITY_REFINEMENT', 'semantic_source': reg, 'edge': edge, 'basis': basis, 'conflict_count': len(conflicts), 'first_conflicts': conflicts[:8]}
    allrows = list(itertools.product((0, 1), repeat = len(vars)))
    # every abstract cube row is specified in this simple generator
    exprs = [minimize_truth_table(vars, o, []) for o in ones]
    clock = str(edge)  # standalone helper has no project binding; production paths use _clock_net_for_semantic_event
    dffs = []
    for bit, q, e in zip(bits, qvars, exprs): 
        dffs.append({'q': q, 'qb': q+'_n', 'd_expr': e, 'clock': clock, 'reset': reset_net, 'cell': 'DFFR', 'semantic_bit': bit})
    return {
      'version': 'bio2rtl-neutral-natural-storage-contract-v1', 'status': 'PASS', 'component_class': 'natural_storage', 
      'semantic_source': reg, 'semantic_bits': bits, 'clock_domain': edge, 'clock': clock, 
      'async_event_candidates': dom.get('async_event_candidates', []), 'basis_inputs': basis, 
      'interface_inputs': [f'basis_{b}' for b in basis]+[reset_net, clock]+qvars+[q+'_n' for q in qvars], 
      'interface_outputs': qvars+[q+'_n' for q in qvars], 
      'combinational_outputs': {}, 'dffs': dffs, 'abstract_truth_rows': rows, 
      'proof_note': 'full abstract basis cube conflict-free; HOLD used where no update rule matches', 
    }

def derive_from_files(phase_path, dhir_path): 
    phase = _load(phase_path); dhir = _load(dhir_path); out = []
    for e in dhir.get('physical_state', []): 
        if e.get('proof') == 'phase_ir_natural_storage': 
            out.append(derive_single_edge_natural_storage_contract(phase, e))
    return out

def derive_legal_product_control_conditioned_contract(root: Path, entry: dict, control_encoding_plan: dict, reset_net = 'proto_reset'): 
    """Refine a single-edge natural register through the reachable legal product.

    Applicable when the legal-product state explicitly carries the register value and
    the next value is a function of (recovered control state, current register) on the
    relevant edge. Unreachable tuples become don't-cares, avoiding impossible basis
    combinations from sparse/minimized guard IR.
    """
    root = Path(root); phase = _load(root/'build/semantic/phase40.ir.json')
    reg = entry.get('semantic_source')
    if not isinstance(reg, str): 
        return {'status': 'UNSUPPORTED', 'reason': 'non-scalar source'}
    bits = list(entry.get('semantic_bits', range(int(entry['bits']))))
    dom = entry.get('natural_update_domain') or _natural_realization_domain(phase, reg, bits)
    if len(dom.get('phase_edges', []))!=1: 
        return {'status': 'UNSUPPORTED_MULTI_EDGE', 'semantic_source': reg, 'domain': dom}
    edge = dom['phase_edges'][0]
    lp = _load(root/'build/semantic/legal_product.json'); q = _load(root/'build/semantic/behavioral_quotient.json'); ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    if reg not in lp['state_tuple'] or ('next_'+reg) not in lp['edge_tuple']: 
        return {'status': 'UNSUPPORTED_NOT_IN_LEGAL_PRODUCT', 'semantic_source': reg}
    tracked = q['tracked_registers']; cregs = ctrl['control_registers']
    try: 
        idxs = [tracked.index(r) for r in cregs]
    except ValueError: 
        return {'status': 'UNSUPPORTED_CONTROL_PROJECTION'}
    pat_to_state = {tuple(r['pattern']): int(r['state']) for r in ctrl['state_rows']}
    class_to_state = {}
    for cl in q['classes']: 
        pat = tuple(cl['representative'][i] for i in idxs)
        if pat not in pat_to_state: 
            return {'status': 'UNSUPPORTED_CONTROL_CLASS', 'class': cl['code'], 'pattern': pat}
        class_to_state[int(cl['code'])] = pat_to_state[pat]
    # physical state encoding selected by mapper
    from .semantic_contracts import _control_state_physical_codes
    pcodes = _control_state_physical_codes(ctrl, control_encoding_plan)
    ei = {n: i for i, n in enumerate(lp['edge_tuple'])}
    width = int(entry['bits']); qvars = [f'{reg}_b{i}' for i in range(width)]
    cvars = ['ctrl_r0', 'ctrl_r1', 'ctrl_f0', 'ctrl_f1']; vars = cvars+qvars
    spec = {}; transition_rows = 0
    for e in lp['unique_edges']: 
        if e[ei['event']]!=edge: 
            continue
        st = class_to_state[int(e[ei['class']])]; rc, fc = pcodes[st]
        cur = int(e[ei[reg]]); nxt = int(e[ei['next_'+reg]])
        cbits = (rc&1, (rc>>1)&1, fc&1, (fc>>1)&1)
        qbits = tuple((cur>>i)&1 for i in range(width)); key = cbits+qbits
        nbits = tuple((nxt>>i)&1 for i in range(width))
        old = spec.get(key)
        if old is not None and old!=nbits: 
            return {'status': 'NEEDS_MORE_STATE', 'semantic_source': reg, 'edge': edge, 'key': key, 'a': old, 'b': nbits}
        spec[key] = nbits; transition_rows+=1
    if not spec: 
        return {'status': 'NO_REACHABLE_EDGE', 'semantic_source': reg, 'edge': edge}
    allrows = list(itertools.product((0, 1), repeat = len(vars))); dcs = [x for x in allrows if x not in spec]
    exprs = []
    for bi in range(width): 
        ones = [k for k, v in spec.items() if v[bi]]
        exprs.append(minimize_truth_table(vars, ones, dcs))
    clock = _clock_net_for_semantic_event(root, phase, edge)
    dffs = [{'q': q, 'qb': q+'_n', 'd_expr': ex, 'clock': clock, 'reset': reset_net, 'cell': 'DFFR', 'semantic_bit': i} for i, (q, ex) in enumerate(zip(qvars, exprs))]
    return {
      'version': 'bio2rtl-neutral-natural-storage-legal-product-v1', 'status': 'PASS', 'component_class': 'natural_storage', 
      'semantic_source': reg, 'semantic_bits': bits, 'clock_domain': edge, 'clock': clock, 
      'control_encoding_plan': control_encoding_plan, 'interface_inputs': cvars+[reset_net, clock]+qvars+[q+'_n' for q in qvars], 
      'interface_outputs': qvars+[q+'_n' for q in qvars], 'combinational_outputs': {}, 'dffs': dffs, 
      'reachable_truth_rows': len(spec), 'legal_transition_rows_seen': transition_rows, 'dontcare_rows': len(dcs), 
      'derivation': 'fresh legal product + behavioral quotient control projection + mapper-selected control encoding; no physical recipe equations', 
    }

def derive_phase_split_legal_product_contract(root: Path, entry: dict, control_encoding_plan: dict, context_registers = None, reset_net = 'start_reset'): 
    """Derive two single-edge banks for a multi-phase-edge natural register.

    The logical value is intentionally *not* muxed here. Consumers must use the
    phase-specific view (low-view before rise, high-view before fall), which avoids
    creating a synthetic dual-edge clock or an edge-sensitive phase mux hazard.
    """
    root = Path(root); phase = _load(root/'build/semantic/phase40.ir.json'); reg = entry.get('semantic_source')
    bits = list(entry.get('semantic_bits', range(int(entry['bits'])))); width = len(bits)
    dom = entry.get('natural_update_domain') or _natural_realization_domain(phase, reg, bits)
    if set(dom.get('phase_edges', []))!={'PHEVT_RISE', 'PHEVT_FALL'}: 
        return {'status': 'UNSUPPORTED_NOT_RISE_FALL', 'semantic_source': reg, 'domain': dom}
    lp = _load(root/'build/semantic/legal_product.json'); q = _load(root/'build/semantic/behavioral_quotient.json'); ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    if reg not in lp['state_tuple'] or ('next_'+reg) not in lp['edge_tuple']: 
        return {'status': 'UNSUPPORTED_NOT_IN_LEGAL_PRODUCT', 'semantic_source': reg}
    tracked = q['tracked_registers']; cregs = ctrl['control_registers']; idxs = [tracked.index(r) for r in cregs]
    pat_to_state = {tuple(r['pattern']): int(r['state']) for r in ctrl['state_rows']}
    classinfo = {}
    for cl in q['classes']: 
        rep = cl['representative']; info = {'ctrl': pat_to_state[tuple(rep[i] for i in idxs)]}
        for r in tracked: 
            info[r] = rep[tracked.index(r)]
        classinfo[int(cl['code'])] = info
    from .semantic_contracts import _control_state_physical_codes
    pcodes = _control_state_physical_codes(ctrl, control_encoding_plan); ei = {n: i for i, n in enumerate(lp['edge_tuple'])}
    # discover smallest context subset among physically tracked non-control scalar regs
    if context_registers is None: 
        candidates = []
        for r in tracked: 
            if r in cregs or r == reg: 
                continue
            # Keep only low-cardinality semantic context; this is a generic search,
            # not a hard-coded protocol register list.
            vals = {classinfo[c][r] for c in classinfo}
            if len(vals)<=4: 
                candidates.append(r)
        needed_by_edge = {}
        for edge in ('PHEVT_RISE', 'PHEVT_FALL'): 
            erows = [e for e in lp['unique_edges'] if e[ei['event']] == edge]
            best = None
            for k in range(len(candidates)+1): 
                for ss in itertools.combinations(candidates, k): 
                    by = {}; ok = True
                    for e in erows: 
                        inf = classinfo[int(e[ei['class']])]
                        key = (inf['ctrl'],)+tuple(inf[x] for x in ss)+(int(e[ei[reg]]),)
                        nv = int(e[ei['next_'+reg]])
                        if key in by and by[key]!=nv: 
                            ok = False
                            break
                        by[key] = nv
                    if ok: 
                        best = list(ss)
                        break
                if best is not None: 
                    break
            if best is None: 
                return {'status': 'NEEDS_MORE_CONTEXT', 'semantic_source': reg, 'edge': edge}
            needed_by_edge[edge] = best
    else: 
        needed_by_edge = {'PHEVT_RISE': list(context_registers), 'PHEVT_FALL': list(context_registers)}
    contracts = {}
    for edge, view_in, view_out in [('PHEVT_RISE', 'low', 'high'), ('PHEVT_FALL', 'high', 'low')]: 
        clock = _clock_net_for_semantic_event(root, phase, edge)
        ctx = needed_by_edge[edge]
        cvars = ['ctrl_r0', 'ctrl_r1', 'ctrl_f0', 'ctrl_f1']
        # Context registers are represented as compact binary semantic values.
        ctxwidth = {r: max(1, (max(classinfo[c][r] for c in classinfo)).bit_length()) for r in ctx}
        ctxvars = [f'ctx_{r}_b{i}' for r in ctx for i in range(ctxwidth[r])]
        qvars = [f'{reg}_{view_in}_b{i}' for i in range(width)]
        vars = cvars+ctxvars+qvars; spec = {}; seen_edges = 0
        for e in lp['unique_edges']: 
            if e[ei['event']]!=edge: 
                continue
            inf = classinfo[int(e[ei['class']])]; rc, fc = pcodes[inf['ctrl']]
            key = [rc&1, (rc>>1)&1, fc&1, (fc>>1)&1]
            for r in ctx: 
                v = int(inf[r]); key.extend((v>>i)&1 for i in range(ctxwidth[r]))
            cur = int(e[ei[reg]]); key.extend((cur>>i)&1 for i in range(width)); key = tuple(key)
            nv = int(e[ei['next_'+reg]]); nbits = tuple((nv>>i)&1 for i in range(width))
            if key in spec and spec[key]!=nbits: 
                return {'status': 'NEEDS_MORE_CONTEXT', 'semantic_source': reg, 'edge': edge, 'key': key, 'a': spec[key], 'b': nbits}
            spec[key] = nbits; seen_edges+=1
        allrows = list(itertools.product((0, 1), repeat = len(vars))); dcs = [x for x in allrows if x not in spec]
        exprs = [minimize_truth_table(vars, [k for k, v in spec.items() if v[i]], dcs) for i in range(width)]
        dffs = []
        for i, ex in enumerate(exprs): 
            qn = f'{reg}_{view_out}_b{i}'; dffs.append({'q': qn, 'qb': qn+'_n', 'd_expr': ex, 'clock': clock, 'reset': reset_net, 'cell': 'DFFR', 'semantic_bit': bits[i]})
        contracts[edge] = {
          'version': 'bio2rtl-neutral-phase-split-storage-contract-v1', 'status': 'PASS', 'component_class': 'phase_split_storage_'+edge.lower(), 
          'semantic_source': reg, 'source_view': view_in, 'destination_view': view_out, 'context_registers': ctx, 'context_widths': ctxwidth, 
          'interface_inputs': cvars+ctxvars+qvars+[reset_net, clock]+[q+'_n' for q in qvars], 
          'interface_outputs': [f'{reg}_{view_out}_b{i}' for i in range(width)]+[f'{reg}_{view_out}_b{i}_n' for i in range(width)], 
          'combinational_outputs': {}, 'dffs': dffs, 'reachable_truth_rows': len(spec), 'legal_transition_rows_seen': seen_edges, 'dontcare_rows': len(dcs), 
        }
    return {'version': 'bio2rtl-phase-split-natural-storage-v1', 'status': 'PASS', 'semantic_source': reg, 'bits': width, 
            'context_by_edge': needed_by_edge, 'reset_net': reset_net, 'contracts': contracts, 
            'view_contract': {'LOW': 'fall_bank_Q', 'HIGH': 'rise_bank_Q', 'no_combinational_phase_mux': True}, 
            'proof_note': 'each edge bank transition is exact over fresh legal-product reachable rows; consumers must bind to phase-specific view'}

def _eval_bool_expr(expr, env): 
    if not isinstance(expr, (list, tuple)) or not expr: 
        raise ValueError(expr)
    tag = expr[0]
    if tag == 'VAR': 
        return int(bool(env[expr[1]]))
    if tag == 'NOT': 
        return 1-_eval_bool_expr(expr[1], env)
    if tag in ('AND', 'OR', 'XOR'): 
        vals = [_eval_bool_expr(x, env) for x in expr[1]]
        if tag == 'AND': 
            return int(all(vals))
        if tag == 'OR': 
            return int(any(vals))
        z = 0
        for v in vals: 
            z^=v
        return z
    if tag == 'CONST': 
        return int(bool(expr[1]))
    raise ValueError('unsupported Boolean expression '+repr(expr))

def prove_phase_split_contract_against_legal_product(root: Path, split: dict, control_encoding_plan: dict): 
    """Exhaustively validate a phase-split storage contract on every legal-product edge.

    The proof uses an induction invariant only on the *active* phase view: LOW uses
    fall-bank Q and HIGH uses rise-bank Q.  The inactive bank may be stale.  A rise
    must compute the next HIGH view from LOW; a fall computes next LOW from HIGH;
    START-like hard reset is allowed to reset both.  No SCL-controlled data mux is
    introduced or assumed.
    """
    root = Path(root); reg = split['semantic_source']; width = int(split['bits'])
    lp = _load(root/'build/semantic/legal_product.json'); q = _load(root/'build/semantic/behavioral_quotient.json')
    ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    from .semantic_contracts import _control_state_physical_codes
    tracked = q['tracked_registers']; cregs = ctrl['control_registers']; idxs = [tracked.index(r) for r in cregs]
    pat_to_state = {tuple(r['pattern']): int(r['state']) for r in ctrl['state_rows']}
    classinfo = {}
    for cl in q['classes']: 
        rep = cl['representative']; info = {'ctrl': pat_to_state[tuple(rep[i] for i in idxs)]}
        for r in tracked: 
            info[r] = rep[tracked.index(r)]
        classinfo[int(cl['code'])] = info
    pcodes = _control_state_physical_codes(ctrl, control_encoding_plan); ei = {n: i for i, n in enumerate(lp['edge_tuple'])}
    ce = []; checks = 0; phase_edges = 0; hard_resets = 0; hold_edges = 0
    for e in lp['unique_edges']: 
        cl = int(e[ei['class']]); phase = e[ei['phase']]; nphase = e[ei['next_phase']]; event = e[ei['event']]
        cur = int(e[ei[reg]]); nxt = int(e[ei['next_'+reg]]); info = classinfo[cl]
        if event in ('PHEVT_RISE', 'PHEVT_FALL'): 
            phase_edges+=1
            expected_src = 'L' if event == 'PHEVT_RISE' else 'H'; expected_dst = 'H' if event == 'PHEVT_RISE' else 'L'
            if phase!=expected_src or nphase!=expected_dst: 
                ce.append({'kind': 'phase_direction', 'event': event, 'phase': phase, 'next_phase': nphase, 'edge': e}); continue
            c = split['contracts'][event]; env = {}
            rc, fc = pcodes[info['ctrl']]
            env.update({'ctrl_r0': rc&1, 'ctrl_r1': (rc>>1)&1, 'ctrl_f0': fc&1, 'ctrl_f1': (fc>>1)&1})
            for r in c.get('context_registers', []): 
                v = int(info[r]); w = int(c['context_widths'][r])
                for i in range(w): 
                    env[f'ctx_{r}_b{i}'] = (v>>i)&1
            srcview = c['source_view']
            for i in range(width): 
                env[f'{reg}_{srcview}_b{i}'] = (cur>>i)&1
                env[f'{reg}_{srcview}_b{i}_n'] = 1-((cur>>i)&1)
            got = 0
            for i, dff in enumerate(c['dffs']): 
                got|=(_eval_bool_expr(dff['d_expr'], env)&1)<<i
            checks+=1
            if got!=nxt: 
                ce.append({'kind': 'd_function', 'event': event, 'class': cl, 'phase': phase, 'cur': cur, 'expected': nxt, 'got': got, 'edge': e})
        elif cur!=nxt: 
            # A non-phase-edge semantic change must be the discovered asynchronous reset.
            hard_resets+=1; checks+=1
            if nxt!=0 or 'HEVT001' not in str(event).split('+'): 
                ce.append({'kind': 'non_phase_change', 'event': event, 'phase': phase, 'cur': cur, 'next': nxt, 'edge': e})
        else: 
            hold_edges+=1; checks+=1
            if nphase!=phase: 
                ce.append({'kind': 'phase_changed_without_phase_edge', 'event': event, 'phase': phase, 'next_phase': nphase, 'edge': e})
    return {
      'version': 'bio2rtl-phase-split-legal-product-induction-v1', 'status': 'PASS' if not ce else 'FAIL', 
      'semantic_source': reg, 'legal_edges_checked': len(lp['unique_edges']), 'checks': checks, 
      'phase_edge_checks': phase_edges, 'hard_reset_changes': hard_resets, 'hold_edges': hold_edges, 
      'counterexamples': ce[:32], 
      'invariant': 'phase=LOW => fall-bank Q equals semantic value; phase=HIGH => rise-bank Q equals semantic value', 
      'no_synthetic_dual_edge_clock': True, 'no_combinational_phase_mux': True, 
    }
