from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import itertools, json
from .truth_minimize import minimize_truth_table
from .semantic_contracts import _control_state_physical_codes
from .recipe_keys import resolve_cached_control_encoding_plan


def _load(p: Path): 
    return json.loads(Path(p).read_text())

def _expr_refs(x): 
    out = set()
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG': 
            return {str(x[1])}
        for y in x: 
            out |= _expr_refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            out |= _expr_refs(y)
    return out

def _canon_expr(x): 
    if isinstance(x, list): 
        return tuple(_canon_expr(v) for v in x)
    if isinstance(x, dict): 
        return tuple(sorted((str(k), _canon_expr(v)) for k, v in x.items()))
    return x

def _basis_maps(phase: dict): 
    by_id = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    by_expr = defaultdict(list)
    for pid, e in by_id.items(): 
        by_expr[_canon_expr(e)].append(pid)
    return by_id, by_expr

def _edge_entries(tab: dict, e: list, ei: dict[str, int]): 
    c = int(e[ei['class']]); nc = int(e[ei['next_class']]); ev = str(e[ei['event']])
    return [x for x in tab.get('rows', {}).get(str(c), tab.get('rows', {}).get(c, {})).get(ev, [])
            if any(int(nm['code']) == nc for nm in x.get('next_map', []))]

def infer_guard_basis_values(phase: dict, tab: dict, e: list, ei: dict[str, int])->dict[str, int]: 
    """Recover basis truth values implied by the direct-FSM branch selected by a legal edge.

    This recovers semantic information even when the source register was subsequently
    quotient/eliminated (for example a shift-register match predicate).  Values are emitted
    only when every matching direct-FSM entry agrees, so ambiguity is fail-closed.
    """
    _, by_expr = _basis_maps(phase); vals = defaultdict(set)
    for ent in _edge_entries(tab, e, ei): 
        for g in ent.get('guard', []): 
            for pid in by_expr.get(_canon_expr(g.get('expression')), []): 
                vals[pid].add(int(bool(g.get('polarity'))))
    return {pid: next(iter(v)) for pid, v in vals.items() if len(v) == 1}

def _control_maps(ctrl: dict): 
    regs = list(map(str, ctrl.get('control_registers', [])))
    patt = {tuple(map(int, r['pattern'])): int(r['state']) for r in ctrl.get('state_rows', [])}
    return regs, patt

def legal_edge_samples(root: Path, event: str|None = None)->list[dict]: 
    """Return deterministic legal-edge samples with representative semantic and guard values.

    Legal-product tuple spellings are intentionally schema-neutral here.  Phase labels,
    sampled input values, and scalar fields are interpreted through recovered topology
    rather than legacy I2C field names such as ``sda``/``event_sda``.
    """
    from .legal_product_schema import edge_index, phase_label, event_sample, scalar_source_fields, data_fields, busy_fields
    root = Path(root); phase = _load(root/'build/semantic/phase40.ir.json');q = _load(root/'build/semantic/behavioral_quotient.json')
    lp = _load(root/'build/semantic/legal_product.json');tab = _load(root/'build/semantic/directfsm_table.json');ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    tracked = list(map(str, q['tracked_registers'])); reps = {int(c['code']): dict(zip(tracked, map(int, c['representative']))) for c in q['classes']}
    ei = edge_index(lp); regs, cmap = _control_maps(ctrl); scalar_names = scalar_source_fields(lp)
    data_cur, data_next = data_fields(lp); busy_cur, busy_next = busy_fields(lp)
    out = []
    for e in lp.get('unique_edges', []): 
        ev = str(e[ei['event']])
        if event is not None and ev!=str(event): 
            continue
        c = int(e[ei['class']]);nc = int(e[ei['next_class']]); sem = dict(reps[c]); nsem = dict(reps[nc])
        for n in scalar_names: 
            if n in ei and isinstance(e[ei[n]], (bool, int)): 
                sem[str(n)] = int(e[ei[n]])
            nn = 'next_'+str(n)
            if nn in ei and isinstance(e[ei[nn]], (bool, int)): 
                nsem[str(n)] = int(e[ei[nn]])
        cv = None;ncv = None
        p = tuple(int(sem[r]) for r in regs)
        np = tuple(int(nsem[r]) for r in regs)
        if p in cmap: 
            cv = int(cmap[p])
        if np in cmap: 
            ncv = int(cmap[np])
        edge_inputs = {}
        if data_cur in ei: 
            edge_inputs['data'] = int(e[ei[data_cur]])
        if data_next in ei: 
            edge_inputs['next_data'] = int(e[ei[data_next]])
        if busy_cur in ei: 
            edge_inputs['busy'] = int(e[ei[busy_cur]])
        if busy_next in ei: 
            edge_inputs['next_busy'] = int(e[ei[busy_next]])
        out.append({'class': c, 'next_class': nc, 'event': ev, 
                    'phase': phase_label(lp, e[ei['phase']]), 'next_phase': phase_label(lp, e[ei['next_phase']]), 
                    'event_sample': event_sample(lp, e, ei), 'edge_inputs': edge_inputs, 'semantic': sem, 'next_semantic': nsem, 
                    'control_state': cv, 'next_control_state': ncv, 
                    'guard_basis_values': infer_guard_basis_values(phase, tab, e, ei)})
    return out

def _control_physical_bits(root: Path, control_state: int)->dict[str, int]: 
    root = Path(root);ctrl = _load(root/'build/generated_certificates/control_factorization.json');plan = resolve_cached_control_encoding_plan(root)
    if not plan: 
        raise ValueError('control encoding plan unavailable')
    codes = _control_state_physical_codes(ctrl, plan);rc, fc = codes[int(control_state)]
    c = root/'build/semantic_neutral_contracts/phase_factorized_control.json'
    if c.exists(): 
        bind = _load(c).get('neutral_interface_binding', {})
    else: 
        from .neutral_bindings import resolve_control_interface_binding
        bind = resolve_control_interface_binding(root, include_roles = False) or {}
    rq = list(map(str, bind.get('banks', {}).get('rise', {}).get('q', [])))
    fq = list(map(str, bind.get('banks', {}).get('fall', {}).get('q', [])))
    if not rq and not fq: 
        raise ValueError('factorized control physical interface unavailable')
    return {n: (rc>>i)&1 for i, n in enumerate(rq)}|{n: (fc>>i)&1 for i, n in enumerate(fq)}


def _eval_semantic_expr(e, sem: dict): 
    """Evaluate the phase40 expression subset directly from a legal semantic row.

    Returns None when the row intentionally omits a source (e.g. eliminated scheduler/input
    state).  Callers then fall back to direct-FSM selected-guard evidence and finally treat the
    value as a wildcard.  This makes role discovery fail-closed without requiring protocol names.
    """
    if not isinstance(e, list) or not e: 
        return None
    op = e[0]
    if op == 'CONST': 
        return int(e[1])
    if op in ('REG', 'SCHED_REG'): 
        return sem.get(str(e[1]))
    if op == 'GPIO_INPUT': 
        return sem.get('GPIO_INPUT')
    if op == 'BIT_VALUE': 
        a = _eval_semantic_expr(e[1], sem)
        return None if a is None else ((int(a)>>int(e[2]))&1)
    if op == 'EQ': 
        a = _eval_semantic_expr(e[1], sem); b = _eval_semantic_expr(e[2], sem)
        return None if a is None or b is None else int(a == b)
    if op == 'OP': 
        name = str(e[1]); xs = [_eval_semantic_expr(x, sem) for x in e[2]]
        if any(x is None for x in xs): 
            return None
        if name == 'AND': 
            z = int(xs[0])
            for x in xs[1:]: 
                z &= int(x)
            return z
        if name == 'OR': 
            z = 0
            for x in xs: 
                z |= int(x)
            return z
        if name == 'XOR': 
            z = 0
            for x in xs: 
                z ^= int(x)
            return z
        if name == 'ADD': 
            return sum(map(int, xs))
        if name == 'SUB': 
            return int(xs[0])-int(xs[1])
        if name == 'SHR': 
            return int(xs[0])>>int(xs[1])
    return None


def _substitute_vars(expr, repl: dict[str, object]): 
    if not isinstance(expr, (list, tuple)) or not expr: 
        return expr
    op = expr[0]
    if op == 'VAR': 
        return repl.get(str(expr[1]), list(expr) if isinstance(expr, tuple) else expr)
    if op in ('AND', 'OR'): 
        return [op, [_substitute_vars(x, repl) for x in expr[1]]]
    return [op]+[_substitute_vars(x, repl) if isinstance(x, (list, tuple)) else x for x in expr[1:]]


def discover_projected_predicate_role(root: Path, *, role: str, event: str, target_fn, 
                                      phase: str|None = 'L', max_features: int = 6, 
                                      candidate_predicates: list[str]|None = None)->dict: 
    """Exact legal-domain synthesis of a Boolean architecture role.

    Candidate atoms are phase40 predicate-basis entries that already have proof-backed
    semantic->physical projections.  A legal edge may leave an atom unspecified; such atoms are
    expanded as wildcards.  We search feature subsets in increasing size and accept only a subset
    for which no physical valuation is assigned both role values.  Unreachable valuations become
    don't-cares for exact minimization.
    """
    root = Path(root)
    phase_ir = _load(root/'build/semantic/phase40.ir.json')
    basis = {str(x['id']): x['expression'] for x in phase_ir.get('predicate_basis', [])}
    proj_path = root/'build/generated_certificates/semantic_predicate_projection.json'
    if not proj_path.exists(): 
        try: 
            from .predicate_projection import project_predicate_basis
            proj = project_predicate_basis(root); proj_path.write_text(json.dumps(proj, indent = 2, sort_keys = True)+'\n')
        except Exception as exc: 
            return {'version': 'bio2rtl-projected-role-v1', 'status': 'FAIL', 'role': role, 'reason': f'predicate projection unavailable: {exc}'}
    proj = _load(proj_path)
    projected = {str(x['predicate_id']): x['physical_expression'] for x in proj.get('projected', [])}
    candidates = sorted(set(candidate_predicates or projected) & set(projected) & set(basis))
    rows = []
    for row in legal_edge_samples(root, event): 
        if phase is not None and str(row['phase'])!=str(phase): 
            continue
        try: 
            target = int(bool(target_fn(row)))
        except Exception: 
            continue
        vals = {}
        for pid in candidates: 
            v = _eval_semantic_expr(basis[pid], row['semantic'])
            if v is None: 
                v = row.get('guard_basis_values', {}).get(pid)
            vals[pid] = None if v is None else int(bool(v))
        rows.append((vals, target, row))
    if not rows: 
        return {'version': 'bio2rtl-projected-role-v1', 'status': 'N_A', 'role': role, 'reason': 'no legal rows'}

    def table_for(sub): 
        seen = {}; conflicts = []; expanded = 0
        for vals, target, row in rows: 
            unknown = [p for p in sub if vals[p] is None]
            for bits in itertools.product((0, 1), repeat = len(unknown)): 
                d = {p: vals[p] for p in sub}; d.update(zip(unknown, bits))
                key = tuple(int(d[p]) for p in sub); expanded+=1
                if key in seen and seen[key]!=target: 
                    conflicts.append({'key': list(key), 'old': seen[key], 'new': target, 'class': int(row['class'])})
                    if len(conflicts)>=16: 
                        return None, conflicts, expanded
                seen[key] = target
        return seen, conflicts, expanded

    chosen = None; table = None; expanded = 0
    upto = min(int(max_features), len(candidates))
    for k in range(0, upto+1): 
        for sub in itertools.combinations(candidates, k): 
            t, conf, ex = table_for(sub)
            if t is not None and not conf: 
                chosen = list(sub); table = t; expanded = ex; break
        if chosen is not None: 
            break
    if chosen is None: 
        return {'version': 'bio2rtl-projected-role-v1', 'status': 'FAIL', 'role': role, 
                'reason': f'no conflict-free projected predicate subset through {upto} features', 
                'candidate_predicates': candidates, 'legal_rows': len(rows)}
    allrows = set(itertools.product((0, 1), repeat = len(chosen)))
    ones = [k for k, v in table.items() if v]; dcs = allrows-set(table)
    abstract = minimize_truth_table(chosen, ones, dcs)
    physical = _substitute_vars(abstract, {p: projected[p] for p in chosen})
    # Validate minimized abstract expression on every cared valuation.
    from .boolean_mapper import eval_expr
    from .boolean_contract import _tupleize
    bad = []
    for key, want in table.items(): 
        got = int(eval_expr(_tupleize(abstract), dict(zip(chosen, key))))
        if got!=want: 
            bad.append({'key': list(key), 'want': want, 'got': got})
    return {
      'version': 'bio2rtl-projected-role-v1', 'status': 'PASS' if not bad else 'FAIL', 'role': role, 
      'event': event, 'phase': phase, 'chosen_predicates': chosen, 'abstract_expression': abstract, 
      'physical_expression': physical, 'legal_rows': len(rows), 'expanded_partial_rows': expanded, 
      'unique_cared_rows': len(table), 'on_rows': sum(table.values()), 'dontcare_rows': len(dcs), 
      'counterexamples': bad[:16], 
      'authority': ['phase40 predicate basis', 'semantic predicate projection', 'direct-FSM selected guards', 'legal product'], 
      'binding_cache_used': False, 'protocol_names_used_for_discovery': False, 
    }


def discover_direct_state_advance_role(root: Path)->dict: 
    root = Path(root); p = root/'build/generated_certificates/direct_state_recurrence.json'
    if not p.exists(): 
        return {'version': 'bio2rtl-projected-role-v1', 'status': 'N_A', 'role': 'direct_state_advance', 'reason': 'no direct-state certificate'}
    cert = _load(p)
    if cert.get('status')!='PASS': 
        return {'version': 'bio2rtl-projected-role-v1', 'status': 'N_A', 'role': 'direct_state_advance', 'reason': 'direct-state proof not PASS'}
    src = str(cert['semantic_source']); width = int(cert['width']); mask = (1<<width)-1
    def target(row): 
        a = int(row['semantic'][src]); b = int(row['next_semantic'][src])
        return int(b == ((a+1)&mask) and b!=a)
    return discover_projected_predicate_role(root, role = 'direct_state_advance', event = str(cert['phase_event']), 
                                             phase = 'L', target_fn = target, max_features = 6)


def emit_direct_state_advance_role(root: Path)->dict: 
    root = Path(root);d = discover_direct_state_advance_role(root)
    p = root/'build/generated_certificates/direct_state_advance_role.json';p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n');return d


def discover_shared_counter_increment_role(root: Path)->dict: 
    """Discover the proof-backed increment enable for a shared counter.

    The target is the increment-source semantic counter advancing by one on a legal rising edge.
    Already-proved architecture roles may participate as candidate atoms.  All static projection
    metadata is resolved once before legal-row enumeration so B20 proof search remains practical.
    """
    root = Path(root); cp = root/'build/generated_certificates/shared_counter.json'
    if not cp.exists(): 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'N_A', 'reason': 'no shared-counter proof'}
    cert = _load(cp)
    if cert.get('status')!='PASS' or cert.get('counterexamples'): 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'N_A', 'reason': 'shared-counter proof not PASS'}
    src = str((cert.get('semantic_sources') or {}).get('increment_source', ''))
    width = int(cert['counter_width']); mask = (1<<width)-1
    if not src: 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'increment semantic source absent'}
    shift = discover_shift_clock_role(root)
    if shift.get('status')!='PASS': 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'base legal-domain phase role unavailable'}
    proj = _load(root/'build/generated_certificates/semantic_predicate_projection.json')
    bypid = {str(x['predicate_id']): x['physical_expression'] for x in proj.get('projected', [])}
    phase = _load(root/'build/semantic/phase40.ir.json'); basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    term_pid = None
    for pid, e in basis.items(): 
        if pid not in bypid: 
            continue
        if isinstance(e, list) and len(e) == 3 and e[0] == 'EQ' and e[1] == ['REG', src] and e[2] == ['CONST', mask]: 
            term_pid = pid; break
    if term_pid is None: 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'no projected terminal predicate for increment source'}

    # Resolve factorized-control physical code and event-latch projection once.
    ctrl = _load(root/'build/generated_certificates/control_factorization.json'); plan = resolve_cached_control_encoding_plan(root)
    if not plan: 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'control encoding plan unavailable'}
    codes = _control_state_physical_codes(ctrl, plan)
    cpath = root/'build/semantic_neutral_contracts/phase_factorized_control.json'
    if cpath.exists(): 
        bind = _load(cpath).get('neutral_interface_binding', {})
    else: 
        try: 
            from .neutral_bindings import resolve_control_interface_binding
            bind = resolve_control_interface_binding(root, include_roles = False) or {}
        except Exception as exc: 
            return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': f'control physical projection unavailable: {exc}'}
    rq = list(map(str, bind.get('banks', {}).get('rise', {}).get('q', []))); fq = list(map(str, bind.get('banks', {}).get('fall', {}).get('q', [])))
    if not rq and not fq: 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'factorized control physical interface unavailable'}
    from .semantic_projection import non_dff_state_pair
    lp = non_dff_state_pair(root)
    lsrc, latch_q, _latch_qb = lp if lp else ('', '', '')
    from .boolean_mapper import eval_expr
    from .boolean_contract import _tupleize
    features = ['phase_role', term_pid]; seen = {}; conflicts = []; expanded = 0; legal = 0
    for row in legal_edge_samples(root, 'PHEVT_RISE'): 
        if row['phase']!='L' or row['control_state'] is None: 
            continue
        sem = row['semantic']
        if src not in sem or src not in row['next_semantic']: 
            continue
        legal+=1
        rc, fc = codes[int(row['control_state'])]; cenv = {}
        for i, n in enumerate(rq): 
            cenv[n] = (rc>>i)&1
        for i, n in enumerate(fq): 
            cenv[n] = (fc>>i)&1
        if lsrc and latch_q and lsrc in sem: 
            cenv[latch_q] = int(sem[lsrc])&1
        try: 
            phase_val = int(eval_expr(_tupleize(shift['expression']), cenv))
        except Exception: 
            continue
        tv = _eval_semantic_expr(basis[term_pid], sem)
        if tv is None: 
            tv = row.get('guard_basis_values', {}).get(term_pid)
        vals = {'phase_role': phase_val, term_pid: None if tv is None else int(bool(tv))}
        a = int(sem[src]); b = int(row['next_semantic'][src]); target = int(a!=mask and b == ((a+1)&mask))
        unknown = [p for p in features if vals[p] is None]
        for bits in itertools.product((0, 1), repeat = len(unknown)): 
            d = dict(vals); d.update(zip(unknown, bits)); key = tuple(d[p] for p in features); expanded+=1
            if key in seen and seen[key]!=target: 
                conflicts.append({'key': list(key), 'old': seen[key], 'new': target, 'class': int(row['class'])})
            seen[key] = target
    if conflicts: 
        return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'FAIL', 'reason': 'increment role conflict', 'counterexamples': conflicts[:16]}
    allrows = set(itertools.product((0, 1), repeat = len(features))); ones = [k for k, v in seen.items() if v]; dcs = allrows-set(seen)
    abstract = minimize_truth_table(features, ones, dcs)
    physical = _substitute_vars(abstract, {'phase_role': shift['expression'], term_pid: bypid[term_pid]})
    return {'version': 'bio2rtl-shared-counter-increment-role-v1', 'status': 'PASS', 'role': 'shared_counter_increment_enable', 
            'semantic_source': src, 'event': 'PHEVT_RISE', 'chosen_features': features, 'abstract_expression': abstract, 
            'physical_expression': physical, 'legal_rows': legal, 'expanded_partial_rows': expanded, 'unique_cared_rows': len(seen), 
            'dontcare_rows': len(dcs), 'counterexamples': [], 
            'authority': ['shared-counter recurrence proof', 'legal-domain phase role', 'semantic predicate projection', 'legal product'], 
            'binding_cache_used': False, 'protocol_names_used_for_discovery': False}


def emit_shared_counter_increment_role(root: Path)->dict: 
    root = Path(root); d = discover_shared_counter_increment_role(root)
    p = root/'build/generated_certificates/shared_counter_increment_role.json'; p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n'); return d



def _project_control_guard_conjunction(root: Path, guards: list[dict], basis: dict)->dict: 
    """Project a conjunction of semantic control-register predicates onto factorized control bits.

    Individual original control registers need not survive factorization.  The conjunction is
    synthesized only on reachable semantic control states; unreachable physical control codes are
    exact don't-cares.  This is the generic projection needed by semantic update-rule guards.
    """
    root = Path(root); ctrl = _load(root/'build/generated_certificates/control_factorization.json')
    regs = list(map(str, ctrl.get('control_registers', []))); rset = set(regs)
    parsed = []
    for g in guards: 
        pid = str(g['basis']); e = basis.get(pid)
        if not (isinstance(e, list) and len(e) == 3 and e[0] == 'EQ'): 
            return {'status': 'FAIL', 'reason': f'non-equality control guard {pid}'}
        lhs, rhs = e[1], e[2]
        if isinstance(lhs, list) and lhs[:1] == ['CONST']: 
            lhs, rhs = rhs, lhs
        if not (isinstance(lhs, list) and len(lhs) == 2 and lhs[0] == 'REG' and str(lhs[1]) in rset and isinstance(rhs, list) and rhs[:1] == ['CONST']): 
            return {'status': 'FAIL', 'reason': f'guard {pid} is not over a factorized control register'}
        parsed.append((pid, str(lhs[1]), int(rhs[1]), bool(g.get('polarity'))))
    plan = resolve_cached_control_encoding_plan(root)
    if not plan: 
        return {'status': 'FAIL', 'reason': 'control encoding plan unavailable'}
    codes = _control_state_physical_codes(ctrl, plan)
    # Physical bit spellings are architecture/interface naming policy.
    cpath = root/'build/semantic_neutral_contracts/phase_factorized_control.json'
    if cpath.exists(): 
        c = _load(cpath); banks = c.get('neutral_interface_binding', {}).get('banks', {})
        rq = list(map(str, banks.get('rise', {}).get('q', []))); fq = list(map(str, banks.get('fall', {}).get('q', [])))
    else: 
        # During early generation derive the bank names from the neutral interface plan.
        from .neutral_bindings import _cache
        iface = None; wanted = __import__('bio2rtl.recipe_keys', fromlist = ['control_semantic_selector']).control_semantic_selector(root)
        for ent in _cache(root).get('entries', []): 
            if ent.get('kind') == 'phase_factorized_control' and ent.get('semantic_selector') == wanted: 
                iface = ent['plan'];break
        if iface is None: 
            return {'status': 'FAIL', 'reason': 'control bank naming unavailable'}
        rq = list(map(str, iface['banks']['rise']['q']));fq = list(map(str, iface['banks']['fall']['q']))
    names = rq+fq; seen = {}; conflicts = []
    for row in ctrl.get('state_rows', []): 
        st = int(row['state']); patt = list(map(int, row['pattern'])); sem = dict(zip(regs, patt))
        target = 1
        for _pid, reg, val, pol in parsed: 
            z = int(int(sem[reg]) == val); target &= z if pol else 1-z
        rc, fc = codes[st]; key = tuple((rc>>i)&1 for i in range(len(rq)))+tuple((fc>>i)&1 for i in range(len(fq)))
        if key in seen and seen[key]!=target: 
            conflicts.append({'key': list(key), 'old': seen[key], 'new': target, 'state': st})
        seen[key] = target
    if conflicts: 
        return {'status': 'FAIL', 'reason': 'control guard not a function of factorized code', 'counterexamples': conflicts[:16]}
    allrows = set(itertools.product((0, 1), repeat = len(names)));ones = [k for k, v in seen.items() if v];dcs = allrows-set(seen)
    expr = minimize_truth_table(names, ones, dcs)
    return {'status': 'PASS', 'expression': expr, 'physical_variables': names, 'semantic_guards': [x[0] for x in parsed], 
            'reachable_codes': len(seen), 'dontcare_codes': len(dcs), 'counterexamples': []}


def discover_event_latch_residual_clear_role(root: Path)->dict: 
    """Discover a minimal safe asynchronous clear for a proof-backed 1-bit event latch.

    The semantic clear rule is a conjunction of predicate-basis literals.  When the latch is
    already zero, asserting clear is observationally/idempotently irrelevant, so those rows are
    don't-cares.  On live-latch legal LOW-phase rows we search the smallest subset of *projectable*
    rule literals whose three-valued short-circuit conjunction exactly equals the full semantic
    guard.  The resulting role is qualified by clock-low because it realizes a pre-rising-edge
    asynchronous clear.
    """
    root = Path(root)
    from .semantic_projection import non_dff_state_pair, global_clock_reset
    lp = non_dff_state_pair(root)
    if not lp: 
        return {'version': 'bio2rtl-latch-residual-clear-role-v3', 'status': 'N_A', 'reason': 'no unique 1-bit non-DFF state'}
    lsrc, lq, lqb = lp
    phase = _load(root/'build/semantic/phase40.ir.json'); basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    rules = [r for r in phase.get('update_rules', []) if str(r.get('target')) == lsrc and str(r.get('event_class')) == 'PHEVT_RISE' and r.get('materialize') and r.get('outcome') == ['CONST', 0]]
    if len(rules)!=1: 
        return {'version': 'bio2rtl-latch-residual-clear-role-v3', 'status': 'FAIL', 'reason': f'unique phase clear rule not found ({len(rules)})'}
    rule = rules[0]
    pp = root/'build/generated_certificates/semantic_predicate_projection.json'
    if not pp.exists(): 
        from .predicate_projection import project_predicate_basis
        pp.write_text(json.dumps(project_predicate_basis(root), indent = 2, sort_keys = True)+'\n')
    proj = _load(pp); bypid = {str(x['predicate_id']): x['physical_expression'] for x in proj.get('projected', [])}
    guards = [{'pid': str(g['basis']), 'polarity': bool(g.get('polarity'))} for g in rule.get('enable', [])]
    candidates = [g['pid'] for g in guards if g['pid'] in bypid and not (basis.get(g['pid']) == ['EQ', ['REG', lsrc], ['CONST', 0]])]
    pol = {g['pid']: g['polarity'] for g in guards}
    def lit(row, pid): 
        v = _eval_semantic_expr(basis[pid], row['semantic'])
        if v is None: 
            v = row.get('guard_basis_values', {}).get(pid)
        if v is None: 
            return None
        z = int(bool(v));return z if pol[pid] else 1-z
    def conj(vals): 
        if 0 in vals: 
            return 0
        if all(v == 1 for v in vals): 
            return 1
        return None
    rows = []; full_undetermined = 0
    for row in legal_edge_samples(root, 'PHEVT_RISE'): 
        if row['phase']!='L': 
            continue
        # Clearing an already-clear latch is a proof-backed idempotent relaxation.
        if int(row['semantic'].get(lsrc, 0)) == 0: 
            continue
        vals = {g['pid']: lit(row, g['pid']) for g in guards}
        target = conj(list(vals.values()))
        if target is None: 
            full_undetermined+=1;continue
        rows.append((vals, target, row))
    if not rows or full_undetermined: 
        return {'version': 'bio2rtl-latch-residual-clear-role-v3', 'status': 'FAIL', 'reason': 'semantic clear guard not fully determined on live-latch legal rows', 'legal_rows': len(rows), 'undetermined_rows': full_undetermined}
    chosen = None
    for k in range(len(candidates)+1): 
        sols = []
        for sub in itertools.combinations(candidates, k): 
            ok = True
            for vals, target, row in rows: 
                got = conj([vals[p] for p in sub])
                if got is None or got!=target: 
                    ok = False
                    break
            if ok: 
                sols.append(sub)
        if sols: 
            sols.sort();chosen = list(sols[0]);break
    if chosen is None: 
        return {'version': 'bio2rtl-latch-residual-clear-role-v3', 'status': 'FAIL', 'reason': 'no exact projected guard subset'}
    terms = []
    for pid in chosen: 
        pe = bypid[pid];terms.append(pe if pol[pid] else ['NOT', pe])
    semantic_role = ['AND', terms] if len(terms)>1 else (terms[0] if terms else ['CONST', 1])
    clock, _reset = global_clock_reset(root)
    physical = ['AND', [['NOT', ['VAR', clock]], semantic_role]]
    return {'version': 'bio2rtl-latch-residual-clear-role-v3', 'status': 'PASS', 'role': 'event_latch_residual_clear', 
            'semantic_source': lsrc, 'semantic_rule_id': str(rule.get('rule_id')), 'event': 'PHEVT_RISE', 'phase': 'L', 
            'chosen_predicates': chosen, 'semantic_role_expression': semantic_role, 'physical_expression': physical, 
            'clock_low_qualification': clock, 'live_latch_legal_rows': len(rows), 'semantic_guard_on_rows': sum(t for _, t, _ in rows), 
            'idempotent_clear_dontcare_policy': 'current_latch_zero', 'counterexamples': [], 
            'authority': ['phase40 latch update rule', 'semantic predicate projection', 'legal product', 'idempotent clear relaxation'], 
            'binding_cache_used': False, 'protocol_names_used_for_discovery': False}

def emit_event_latch_residual_clear_role(root: Path)->dict: 
    root = Path(root);d = discover_event_latch_residual_clear_role(root)
    p = root/'build/generated_certificates/event_latch_residual_clear_role.json';p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n');return d

def discover_shift_clock_role(root: Path)->dict: 
    """Discover a minimal legal-domain clock-enable for the proof-backed shift quotient.

    The target is the actual semantic shift-rule enable on legal pre-rising states.  We search
    over the factorized control state, directly projected modulo-state bits, and event latch.
    Unreachable physical valuations are don't-cares.  No recovered-predicate DAG or protocol
    signal name participates in discovery.
    """
    root = Path(root);phase = _load(root/'build/semantic/phase40.ir.json');cert = _load(root/'build/generated_certificates/shift_quotient.json')
    if cert.get('status')!='PASS': 
        return {'version': 'bio2rtl-legal-role-shift-clock-v1', 'status': 'N_A', 'reason': 'shift proof not PASS'}
    sreg = str(cert['shift_register']);rules = [r for r in phase.get('update_rules', []) if str(r.get('target')) == sreg and str(r.get('event_class')) == 'PHEVT_RISE' and str(r.get('operation')) == 'GENERIC_EXPR']
    if len(rules)!=1: 
        return {'version': 'bio2rtl-legal-role-shift-clock-v1', 'status': 'N_A', 'reason': f'unique shift rise rule not found ({len(rules)})'}
    rule = rules[0];basis = {str(x['id']): x['expression'] for x in phase.get('predicate_basis', [])}
    # The direct-state and latch bits are architecture projections.  Semantic control is mapped
    # through the factorized encoding.  Current benchmark-independent generic projection handles
    # these sources; future sources can be added without changing this role search.
    from .semantic_projection import semantic_state_pairs
    features = []
    # Full factorized control code.  During post-architecture maturation the final
    # neutral control contract does not exist yet, so resolve only the bank interface.
    cpath = root/'build/semantic_neutral_contracts/phase_factorized_control.json'
    if cpath.exists(): 
        cb = _load(cpath).get('neutral_interface_binding', {}).get('banks', {})
    else: 
        from .neutral_bindings import resolve_control_interface_binding
        ci = resolve_control_interface_binding(root, include_roles = False) or {}
        cb = ci.get('banks', {})
    control_names = list(map(str, cb.get('rise', {}).get('q', [])))+list(map(str, cb.get('fall', {}).get('q', [])))
    if not control_names: 
        return {'version': 'bio2rtl-legal-role-shift-clock-v1', 'status': 'N_A', 'reason': 'factorized control interface unavailable'}
    features.extend(control_names)
    # Project every architectural-state source actually referenced by this rule's
    # enable predicates.  This replaces the historical fixed direct-state/selector names.
    enable_sources = set()
    for atom in rule.get('enable', []): 
        pid = str(atom.get('basis', '')); enable_sources |= _expr_refs(basis.get(pid))
    source_pairs = {}
    for src in sorted(enable_sources): 
        pp = semantic_state_pairs(root, src, None)
        if pp: 
            source_pairs[src] = pp
            features.extend(q for _, q, _ in pp)
    # Event-latch semantic source comes from architecture recovery.  The physical Q spelling
    # is interface/naming policy, resolved directly from the content-addressed latch plan rather
    # than from an already-generated shared-counter contract (which would create a generation
    # cycle when the shared-counter increment role itself depends on this phase role).
    from .semantic_projection import non_dff_state_pair
    lp = non_dff_state_pair(root)
    latch_src, latch_q, _latch_qb = lp if lp else ('', '', '')
    if latch_src and latch_q: 
        features.append(latch_q)
    else: 
        latch_src = ''
    # deterministic unique feature order
    features = list(dict.fromkeys(features))
    samples = [];conf = []
    # Use LOW-phase legal states because this value gates the following rising clock edge.
    for row in legal_edge_samples(root, 'PHEVT_RISE'): 
        if row['phase']!='L' or row['control_state'] is None: 
            continue
        sem = row['semantic']; env = _control_physical_bits(root, row['control_state'])
        ok = True
        for src, pairs in source_pairs.items(): 
            if src not in sem: 
                ok = False
                break
            val = int(sem[src])
            for bit, q, qb in pairs: 
                env[str(q)] = (val>>int(bit))&1
        if not ok: 
            continue
        if latch_src: 
            if latch_src not in sem: 
                continue
            env[latch_q] = int(sem[latch_src])&1
        # Evaluate the semantic enable. Only equality predicates over available semantic state are
        # needed here; if a guard was eliminated from the representative state, its selected direct
        # FSM branch value is used instead.
        target = 1
        for g in rule.get('enable', []): 
            pid = str(g['basis']); val = None;e = basis[pid]
            try: 
                if e[0] == 'EQ' and e[2][0] == 'CONST': 
                    lhs = e[1];c = int(e[2][1])
                    if lhs[0] == 'REG' and str(lhs[1]) in sem: 
                        val = int(int(sem[str(lhs[1])]) == c)
                    elif lhs[0] == 'BIT_VALUE' and lhs[1][0] == 'REG' and str(lhs[1][1]) in sem: 
                        val = int(((int(sem[str(lhs[1][1])])>>int(lhs[2]))&1) == c)
                if val is None: 
                    val = row['guard_basis_values'].get(pid)
            except Exception: 
                val = None
            if val is None: 
                ok = False;break
            target &= int(bool(val) == bool(g['polarity']))
        if not ok: 
            continue
        key = tuple(int(env[n]) for n in features)
        samples.append((key, int(target), row))
    seen = {}
    for key, t, row in samples: 
        if key in seen and seen[key]!=t: 
            conf.append({'physical_key': list(key), 'old': seen[key], 'new': t, 'class': row['class']})
        seen[key] = t
    if conf: 
        return {'version': 'bio2rtl-legal-role-shift-clock-v1', 'status': 'FAIL', 'reason': 'role not a function of projected physical features', 'counterexamples': conf[:16]}
    allrows = set(itertools.product((0, 1), repeat = len(features)));ones = [k for k, v in seen.items() if v];dcs = allrows-set(seen)
    expr = minimize_truth_table(features, ones, dcs)
    # Re-evaluate minimized expression on every cared row.
    from .boolean_mapper import eval_expr
    from .boolean_contract import _tupleize
    bad = []
    for key, t, row in samples: 
        env = dict(zip(features, key));got = int(eval_expr(_tupleize(expr), env))
        if got!=t: 
            bad.append({'class': row['class'], 'got': got, 'expected': t, 'physical_key': list(key)})
    return {'version': 'bio2rtl-legal-role-shift-clock-v1', 'status': 'PASS' if not bad else 'FAIL', 'role': 'shift_clock_enable', 
            'semantic_source': sreg, 'event': 'PHEVT_RISE', 'physical_variables': features, 'expression': expr, 
            'legal_rows': len(samples), 'unique_physical_rows': len(seen), 'on_rows': len(ones), 'dontcare_rows': len(dcs), 
            'counterexamples': bad[:16], 'semantic_rule_id': str(rule.get('rule_id')), 
            'authority': ['phase40.update_rules', 'directfsm_table selected guard polarities', 'behavioral_quotient representatives', 'legal_product', 'control_factorization', 'semantic architecture contracts'], 
            'recovered_predicate_binding_cache_used': False, 'protocol_names_used_for_discovery': False}

def emit_shift_clock_role(root: Path)->dict: 
    root = Path(root);d = discover_shift_clock_role(root);p = root/'build/generated_certificates/shift_clock_role.json';p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n');return d


def _shared_counter_physical_bits_from_naming_plan(root: Path)->list[tuple[str, str]]: 
    """Resolve shared-counter Q/QB spellings without requiring the generated component.

    This is naming/retiming policy only.  It intentionally avoids
    resolve_shared_counter_interface_binding(), because that resolver itself asks for the
    increment role and would create a generation cycle while legal roles are being discovered.
    """
    root = Path(root)
    from .recipe_keys import component_recipe_keys
    wanted = component_recipe_keys(root).get('shared_counter_and_event_latch')
    p = root/'technology'/'neutral_shared_counter_interface_binding_cache_v1.json'
    if not p.exists(): 
        return []
    for ent in _load(p).get('entries', []): 
        if ent.get('component_selector') == wanted: 
            return [(str(x['net']), str(x['complement'])) for x in (ent.get('plan', {}).get('counter_bits', []) or [])]
    return []


def _shared_counter_phase_eq_candidates(root: Path, phase_name: str)->list[dict]: 
    """Create semantic-count equality atoms with proof-backed physical expressions.

    The shared-counter index projection states semantic_count = physical_count + offset
    modulo 2**width at a phase readpoint.  Therefore semantic_count==k has a direct Q/QB
    equality expression.  This is a generic architecture projection, not a protocol rule.
    """
    root = Path(root); p = root/'build/generated_certificates/shared_counter_index_projection.json'
    if not p.exists(): 
        return []
    d = _load(p)
    if d.get('status')!='PASS' or d.get('counterexamples'): 
        return []
    rel = (d.get('relations_by_phase', {}) or {}).get(str(phase_name))
    if not rel: 
        return []
    width = int(d['physical_counter_width']); mod = 1<<width; off = int(rel['offset_mod'])%mod
    source = str(d['semantic_count_source']); bits = _shared_counter_physical_bits_from_naming_plan(root)
    if len(bits)!=width: 
        return []
    out = []
    for sem_value in range(mod): 
        phys = (sem_value-off)%mod; terms = []
        for i, (q, qb) in enumerate(bits): 
            terms.append(['VAR', q if ((phys>>i)&1) else qb])
        expr = terms[0] if len(terms) == 1 else ['AND', terms]
        out.append({'id': f'SHARED_{source}_EQ_{sem_value}_{phase_name}', 
                    'kind': 'semantic_state_eq', 'semantic_source': source, 'semantic_value': sem_value, 
                    'phase': str(phase_name), 'physical_expression': expr})
    return out


def discover_control_branch_roles(root: Path, max_features: int = 6)->dict: 
    """Discover Boolean branch discriminators for ambiguous factorized-control families.

    For every (event, semantic source-control-state) with more than one legal next control
    state, synthesize only the *varying physical target-code bits*.  Candidate atoms come from
    proof-backed semantic predicate projections plus phase-specific shared-counter equality
    projections.  No historical branch net name is consulted.
    """
    root = Path(root); cp = root/'build/generated_certificates/control_factorization.json'
    if not cp.exists(): 
        return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'N_A', 'reason': 'no control factorization proof'}
    ctrl = _load(cp)
    if ctrl.get('status')!='PASS': 
        return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'N_A', 'reason': 'control proof not PASS'}
    plan = resolve_cached_control_encoding_plan(root)
    if not plan: 
        return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'FAIL', 'reason': 'no control encoding plan'}
    codes = _control_state_physical_codes(ctrl, plan)
    phase_ir = _load(root/'build/semantic/phase40.ir.json'); basis = {str(x['id']): x['expression'] for x in phase_ir.get('predicate_basis', [])}
    pp = root/'build/generated_certificates/semantic_predicate_projection.json'
    if not pp.exists(): 
        from .predicate_projection import project_predicate_basis
        z = project_predicate_basis(root); pp.write_text(json.dumps(z, indent = 2, sort_keys = True)+'\n')
    proj = _load(pp); projected = {str(x['predicate_id']): x['physical_expression'] for x in proj.get('projected', [])}
    # Ambiguous semantic control families.
    nxt = defaultdict(set)
    for r in ctrl.get('semantic_transition_relation', []): 
        ev = str(r.get('event'))
        if ev in ('PHEVT_RISE', 'PHEVT_FALL'): 
            nxt[(ev, int(r['source_state']))].add(int(r['next_state']))
    fams = [(ev, s, sorted(ns)) for (ev, s), ns in sorted(nxt.items()) if len(ns)>1]
    roles = {'rise': [], 'fall': []}; proofs = []
    for ev, src, next_states in fams: 
        bank = 'rise' if ev == 'PHEVT_RISE' else 'fall'; code_index = 0 if bank == 'rise' else 1
        width = int(plan['bits'][bank]); target_codes = {int(codes[n][code_index]) for n in next_states}
        varying = [i for i in range(width) if len({(c>>i)&1 for c in target_codes})>1]
        phase_name = 'L' if ev == 'PHEVT_RISE' else 'H'
        candidates = []
        for pid, pexpr in sorted(projected.items()): 
            if pid in basis: 
                candidates.append({'id': pid, 'kind': 'basis', 'physical_expression': pexpr})
        candidates.extend(_shared_counter_phase_eq_candidates(root, phase_name))
        rows = [r for r in legal_edge_samples(root, ev) if r['phase'] == phase_name and r['control_state'] == src]
        if not rows: 
            return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'FAIL', 'reason': f'no legal rows for ambiguous family {ev}/{src}'}
        for bit in varying: 
            rv = []
            for row in rows: 
                vals = {}
                for c in candidates: 
                    if c['kind'] == 'basis': 
                        v = _eval_semantic_expr(basis[c['id']], row['semantic'])
                        if v is None: 
                            v = row.get('guard_basis_values', {}).get(c['id'])
                    else: 
                        sv = c['semantic_source']; v = None if sv not in row['semantic'] else int(int(row['semantic'][sv]) == int(c['semantic_value']))
                    vals[c['id']] = None if v is None else int(bool(v))
                target = (int(codes[int(row['next_control_state'])][code_index])>>bit)&1
                rv.append((vals, target, row))
            ids = [c['id'] for c in candidates]; byid = {c['id']: c for c in candidates}; chosen = None
            for k in range(0, min(int(max_features), len(ids))+1): 
                sols = []
                for sub in itertools.combinations(ids, k): 
                    seen = {};conf = False;expanded = 0
                    for vals, target, row in rv: 
                        unknown = [p for p in sub if vals[p] is None]
                        for bs in itertools.product((0, 1), repeat = len(unknown)): 
                            d = {p: vals[p] for p in sub};d.update(zip(unknown, bs));key = tuple(int(d[p]) for p in sub);expanded+=1
                            if key in seen and seen[key]!=target: 
                                conf = True
                                break
                            seen[key] = target
                        if conf: 
                            break
                    if not conf: 
                        allrows = set(itertools.product((0, 1), repeat = len(sub)));ones = [x for x, v in seen.items() if v];dcs = allrows-set(seen)
                        ex = minimize_truth_table(list(sub), ones, dcs)
                        pex = _substitute_vars(ex, {p: byid[p]['physical_expression'] for p in sub})
                        sols.append((sub, seen, dcs, ex, pex, expanded))
                if sols: 
                    # deterministic tie-break by feature ids then expression representation
                    sols.sort(key = lambda x: (tuple(x[0]), repr(x[4])));chosen = sols[0];break
            if chosen is None: 
                return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'FAIL', 'reason': f'no discriminator <= {max_features} features for {ev}/{src}/bit{bit}'}
            sub, seen, dcs, abstract, physical, expanded = chosen
            rid = f'{bank}_s{src}_target_bit{bit}'
            role = {'id': rid, 'target_code_bit': int(bit), 'source_states': [int(src)], 
                  'expression': physical, 'semantic_features': list(sub), 
                  'qualified_outside_ambiguous_family': False}
            roles[bank].append(role)
            proofs.append({'event': ev, 'bank': bank, 'source_state': int(src), 'next_states': next_states, 
                           'target_code_bit': int(bit), 'target_codes': sorted(target_codes), 'chosen_features': list(sub), 
                           'abstract_expression': abstract, 'physical_expression': physical, 'legal_rows': len(rv), 
                           'unique_cared_rows': len(seen), 'dontcare_rows': len(dcs), 'expanded_rows': expanded, 
                           'counterexamples': []})
    import hashlib
    def sh(p): 
        return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None
    return {'version': 'bio2rtl-control-branch-role-discovery-v1', 'status': 'PASS', 'branch_roles': roles, 
            'ambiguous_families': [{'event': ev, 'source_state': s, 'next_states': ns} for ev, s, ns in fams], 
            'proofs': proofs, 'authority': ['control_factorization semantic transition relation', 'legal_product', 'semantic_predicate_projection', 'shared_counter_index_projection'], 
            'proof_anchor': {
              'control_factorization_sha256': sh(cp), 
              'legal_product_sha256': sh(root/'build/semantic/legal_product.json'), 
              'semantic_predicate_projection_sha256': sh(pp), 
              'shared_counter_index_projection_sha256': sh(root/'build/generated_certificates/shared_counter_index_projection.json'), 
            }, 
            'binding_cache_used_for_semantics': False, 'protocol_names_used_for_discovery': False}


def emit_control_branch_roles(root: Path)->dict: 
    root = Path(root);d = discover_control_branch_roles(root);p = root/'build/generated_certificates/control_branch_roles.json';p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n');return d
