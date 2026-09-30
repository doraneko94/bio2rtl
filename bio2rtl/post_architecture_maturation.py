from __future__ import annotations
from pathlib import Path
from collections import Counter
import hashlib, json


def _load(p: Path): 
    return json.loads(Path(p).read_text())


def _sha(p: Path) -> str: 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _status(p: Path) -> str | None: 
    try: 
        return str(_load(p).get('status'))
    except FileNotFoundError: 
        return None


def discover_direct_state_recurrence(root: Path) -> dict: 
    root = Path(root)
    arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json')
    q = _load(root/'build/semantic/behavioral_quotient.json')
    lp = _load(root/'build/semantic/legal_product.json')
    cand = [x for x in arch.get('physical_state', [])
          if x.get('role') == 'direct_state' and x.get('proof') == 'phase_ir_natural_storage']
    if len(cand)!=1: 
        return {'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'N_A', 
                'reason': f'unique direct-state natural-storage candidate absent ({len(cand)})'}
    e = cand[0]
    reg = str(e['semantic_source'])
    width = int(e['bits'])
    dom = e.get('natural_update_domain') or {}
    phase_edges = list(dom.get('phase_edges', []))
    async_events = list(dom.get('async_event_candidates', []))
    if len(phase_edges)!=1 or not async_events: 
        return {'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'N_A', 
                'semantic_source': reg, 'reason': 'candidate is not single-phase plus asynchronous-reset state'}
    pe = str(phase_edges[0])
    tracked = q.get('tracked_registers', [])
    if reg not in tracked: 
        return {'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'N_A', 
                'semantic_source': reg, 'reason': 'candidate absent from behavioral quotient'}
    if int(q.get('varying_registers_in_merged_blocks', {}).get(reg, 0)): 
        return {'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'N_A', 
                'semantic_source': reg, 'reason': 'candidate varies inside behavioral quotient classes'}
    ri = tracked.index(reg)
    reps = {int(c['code']): int(c['representative'][ri]) for c in q['classes']}
    E = lp['edge_tuple']
    ix = {n: i for i, n in enumerate(E)}
    mask = (1<<width)-1
    ops = Counter()
    ce = []
    phase_rows = []
    reset_rows = []
    for row in lp.get('unique_edges', []): 
        ev = str(row[ix['event']])
        a = reps[int(row[ix['class']])]
        b = reps[int(row[ix['next_class']])]
        if ev == pe: 
            if b == a: 
                op = 'HOLD'
            elif b == ((a+1)&mask): 
                op = 'INC_MOD'
            else: 
                op = 'OTHER'; ce.append({'kind': 'phase_recurrence', 'event': ev, 'cur': a, 'next': b, 
                                        'class': int(row[ix['class']]), 'next_class': int(row[ix['next_class']])})
            ops[op]+=1
            phase_rows.append((a, b, op))
        elif any(x in ev.split('+') for x in async_events) and b!=a: 
            ops['ASYNC_RESET']+=1
            reset_rows.append((ev, a, b))
            if b!=0: 
                ce.append({'kind': 'async_nonzero', 'event': ev, 'cur': a, 'next': b})
    if not phase_rows or not any(op == 'INC_MOD' for _, _, op in phase_rows): 
        ce.append({'kind': 'no_increment_family'})
    if not reset_rows: 
        ce.append({'kind': 'no_async_reset_change'})
    if ce: 
        return {'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'N_A', 
                'semantic_source': reg, 'reason': 'modulo/reset recurrence not proven', 'counterexamples': ce[:32]}
    return {
      'version': 'bio2rtl-direct-state-recurrence-generic-v1', 'status': 'PASS', 
      'semantic_source': reg, 'width': width, 'phase_event': pe, 'async_reset_events': async_events, 
      'operation_counts': dict(ops), 'phase_rows': len(phase_rows), 'async_reset_rows': len(reset_rows), 
      'recurrence': {'phase_edge': 'advance ? (state + 1) mod 2^width : state', 'async_events': 'state := 0'}, 
      'advance_role': 'ADVANCE_ON_PHASE_EDGE', 'counterexamples': [], 
      'proof_scope': 'EXACT BEHAVIORAL-QUOTIENT SOURCE VALUE + LEGAL-PRODUCT CLASS/NEXT-CLASS EDGES; NO PROTOCOL SIGNAL NAMES', 
      'inputs': {'architecture_ir_sha256': _sha(root/'build/architecture_ir_v16_stage3_seedless.json'), 
                'behavioral_quotient_sha256': _sha(root/'build/semantic/behavioral_quotient.json'), 
                'legal_product_sha256': _sha(root/'build/semantic/legal_product.json')}
    }


def mature_post_architecture_certificates(root: Path) -> dict: 
    root = Path(root)
    g = root/'build/generated_certificates'
    g.mkdir(parents = True, exist_ok = True)
    rows = []
    def record(name, d): 
        rows.append({'certificate': name, 'status': d.get('status'), 'reason': d.get('reason')})
        return d
    owned = ['direct_state_recurrence.json', 'observation_source_projection.json', 'load_hold_semantic_recurrence.json', 
           'semantic_predicate_projection.json', 'control_branch_roles.json', 'direct_state_advance_role.json', 
           'shift_clock_role.json', 'shared_counter_increment_role.json', 'event_latch_residual_clear_role.json']
    for n in owned: 
        p = g/n
        if p.exists(): 
            p.unlink()
    direct = discover_direct_state_recurrence(root)
    (g/'direct_state_recurrence.json').write_text(json.dumps(direct, indent = 2, sort_keys = True)+'\n')
    record('direct_state_recurrence', direct)
    from .observation_projection import emit_observation_source_projection
    try: 
        obs = emit_observation_source_projection(root)
    except (FileNotFoundError, KeyError, ValueError) as e: 
        obs = {'version': 'bio2rtl-observation-source-projection-v1', 'status': 'N_A', 'reason': str(e)}
        (g/'observation_source_projection.json').write_text(json.dumps(obs, indent = 2, sort_keys = True)+'\n')
    record('observation_source_projection', obs)
    from .load_hold_discovery import emit_load_hold_semantic_recurrence
    try: 
        lh = emit_load_hold_semantic_recurrence(root)
    except (FileNotFoundError, KeyError, ValueError) as e: 
        lh = {'version': 'bio2rtl-load-hold-semantic-recurrence-v1', 'status': 'N_A', 'reason': str(e)}
        (g/'load_hold_semantic_recurrence.json').write_text(json.dumps(lh, indent = 2, sort_keys = True)+'\n')
    record('load_hold_semantic_recurrence', lh)
    from .predicate_projection import project_predicate_basis
    try: 
        proj = project_predicate_basis(root)
    except (FileNotFoundError, KeyError, ValueError) as e: 
        arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json')
        if not any(bool(x.get('requires_phase_split_realization')) for x in arch.get('physical_state', [])): 
            raise
        # A missing optional architecture projection is expected on the conservative
        # phase-split path.  Record N_A so the mapper can rebuild directly from Phase40;
        # normal architectures retain the original fail-closed behavior above.
        proj = {'version': 'bio2rtl-semantic-predicate-projection-v1', 'status': 'N_A', 
              'reason': 'phase-split conservative fallback: '+str(e), 
              'requires_phase_split_realization': True}
    (g/'semantic_predicate_projection.json').write_text(json.dumps(proj, indent = 2, sort_keys = True)+'\n')
    record('semantic_predicate_projection', proj)
    from .legal_role_synthesis import (emit_control_branch_roles, emit_direct_state_advance_role, emit_shift_clock_role, 
                                       emit_shared_counter_increment_role, emit_event_latch_residual_clear_role)
    role_specs = [('control_branch_roles', 'control_factorization.json', emit_control_branch_roles), 
                ('direct_state_advance_role', 'direct_state_recurrence.json', emit_direct_state_advance_role), 
                ('shift_clock_role', 'shift_quotient.json', emit_shift_clock_role), 
                ('shared_counter_increment_role', 'shared_counter.json', emit_shared_counter_increment_role), 
                ('event_latch_residual_clear_role', 'shared_counter.json', emit_event_latch_residual_clear_role)]
    for name, pre, fn in role_specs: 
        if _status(g/pre)!='PASS': 
            d = {'version': 'bio2rtl-post-architecture-role-v1', 'status': 'N_A', 'reason': f'{pre} not PASS'}
            (g/f'{name}.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
        else: 
            try: 
                d = fn(root)
            except (FileNotFoundError, KeyError, ValueError) as e: 
                d = {'version': 'bio2rtl-post-architecture-role-v1', 'status': 'N_A', 'reason': str(e)}
                (g/f'{name}.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
        record(name, d)
    rep = {'version': 'bio2rtl-post-architecture-maturation-v1', 'status': 'PASS', 'certificates': rows, 
         'phase40_ir_sha256': _sha(root/'build/semantic/phase40.ir.json'), 
         'architecture_ir_sha256': _sha(root/'build/architecture_ir_v16_stage3_seedless.json'), 
         'free_running_clock_created': False, 'synthetic_polling_phase_created': False}
    (root/'build/post_architecture_maturation_report.json').write_text(json.dumps(rep, indent = 2, sort_keys = True)+'\n')
    return rep
