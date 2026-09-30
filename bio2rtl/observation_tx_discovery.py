from __future__ import annotations
from pathlib import Path
import json
from .boolean_mapper import C, V, N, A, O, M, canon


def _load(p: Path): 
    return json.loads(Path(p).read_text())

def _as_list(x): 
    return list(x) if isinstance(x, (list, tuple)) else []

def _parse_indexed_bit_predicate(expr): 
    """Recognize EQ(BIT_VALUE(SHR(REG obs, SUB(REG count, CONST k)), bit), CONST v).

    This is semantic syntax recognition, not a register-name rule.  The returned source
    names are whatever the current semantic authority contains.
    """
    if not (isinstance(expr, list) and len(expr) == 3 and expr[0] == 'EQ'): 
        return None
    lhs, rhs = expr[1], expr[2]
    if isinstance(lhs, list) and lhs[:1] == ['CONST']: 
        lhs, rhs = rhs, lhs
    if not (isinstance(rhs, list) and len(rhs) == 2 and rhs[0] == 'CONST'): 
        return None
    compare = int(rhs[1])
    if not (isinstance(lhs, list) and len(lhs) == 3 and lhs[0] == 'BIT_VALUE'): 
        return None
    shifted, bit = lhs[1], int(lhs[2])
    if not (isinstance(shifted, list) and len(shifted) == 3 and shifted[:2] == ['OP', 'SHR']): 
        return None
    args = shifted[2]
    if not (isinstance(args, list) and len(args) == 2): 
        return None
    obs, idx = args
    if not (isinstance(obs, list) and len(obs) == 2 and obs[0] == 'REG'): 
        return None
    if not (isinstance(idx, list) and len(idx) == 3 and idx[:2] == ['OP', 'SUB']): 
        return None
    iargs = idx[2]
    if not (isinstance(iargs, list) and len(iargs) == 2): 
        return None
    cnt, sub = iargs
    if not (isinstance(cnt, list) and len(cnt) == 2 and cnt[0] == 'REG'): 
        return None
    if not (isinstance(sub, list) and len(sub) == 2 and sub[0] == 'CONST'): 
        return None
    return {'observation_register': str(obs[1]), 'count_register': str(cnt[1]), 
            'subtract': int(sub[1]), 'bit_value_bit': bit, 'compare_value': compare}

def _semantic_storage(root: Path, reg: str)->dict: 
    phase = _load(root/'build/semantic/phase40.ir.json')
    for x in phase.get('storage_optimization', {}).get('register_storage', []): 
        if str(x.get('register')) == reg: 
            return x
    for x in phase.get('architectural_registers', []): 
        if str(x.get('id')) == reg: 
            return {'register': reg, 'semantic_width': int(x.get('width', 1)), 
                    'constant_zero_bits': [], 'constant_one_bits': [], 'stored_bits': list(range(int(x.get('width', 1))))}
    raise KeyError(f'no semantic storage {reg}')

def _bank_semantic_bit_map(plan: dict, source: str)->dict[int, dict]: 
    for bank in plan.get('banks', []): 
        if str(bank.get('semantic_source'))!=source: 
            continue
        out = {}
        for b in bank.get('bits', []): 
            sem = int(b['semantic_bit']); on = str(b.get('semantic_on', 'q'))
            q, qb = str(b['q']), str(b['qb'])
            out[sem] = {'semantic': q if on == 'q' else qb, 'complement': qb if on == 'q' else q}
        return out
    raise KeyError(f'no load/hold bank for semantic source {source}')

def _selector_bits(plan: dict, selector_reg: str, width: int)->list[dict]: 
    m = _bank_semantic_bit_map(plan, selector_reg)
    return [{'q': m[i]['semantic'], 'qb': m[i]['complement']} for i in range(width)]

def _source_bit_expr(root: Path, load_plan: dict, source: dict, bit: int, obs_width: int, snapshot_cert: dict): 
    kind = str(source.get('kind'))
    if kind == 'constant': 
        return C((int(source['value'])>>bit)&1)
    lo = int(source.get('lo', 0)); hi = int(source.get('hi', -1)); span = max(0, hi-lo+1)
    if bit>=span: 
        return C(0)
    sem_bit = lo+bit
    if kind == 'register_slice': 
        m = _bank_semantic_bit_map(load_plan, str(source['source']))
        z = m[sem_bit]
        return V(z['complement'] if bool(source.get('invert')) else z['semantic'])
    if kind == 'external_input_slice': 
        # The snapshot proof is the semantic authority saying a live input source must be
        # replaced by its captured observation-local state at later serialization.
        if not snapshot_cert.get('claims', {}).get('external_input_source_requires_snapshot'): 
            raise ValueError('external observation source lacks snapshot proof')
        snap_reg = str(snapshot_cert['observation_register']); snap_bits = list(map(int, snapshot_cert['snapshot_bits']))
        if bit>=len(snap_bits): 
            return C(0)
        m = _bank_semantic_bit_map(load_plan, snap_reg)
        return V(m[snap_bits[bit]]['semantic'])
    raise ValueError(f'unsupported observation source kind {kind!r}')

def _compatible(a, b): 
    if len(a)!=len(b): 
        return False
    for x, y in zip(a, b): 
        if x is not None and y is not None and canon(x)!=canon(y): 
            return False
    return True

def _merge_constraints(a, b): 
    return [x if x is not None else y for x, y in zip(a, b)]

def _mux_simplify(sel: dict, low, high): 
    low, high = canon(low), canon(high)
    if low == high: 
        return low
    q, qb = V(sel['q']), V(sel['qb'])
    if high == C(0): 
        return canon(A(qb, low))
    if low == C(0): 
        return canon(A(q, high))
    if high == C(1): 
        return canon(O(q, low))
    if low == C(1): 
        return canon(O(qb, high))
    return canon(M(q, low, high))

def _synth_constraints(vals: list, index_bits: list[dict]): 
    """Synthesize a Boolean value over binary index bits with None = don't-care.

    Compatible low/high cofactors are merged before recursion.  This is the generic step
    that lets don't-cares remove an otherwise unnecessary selector level.
    """
    n = 1<<len(index_bits)
    if len(vals)!=n: 
        raise ValueError((len(vals), len(index_bits)))
    def rec(vs, bits): 
        defs = [x for x in vs if x is not None]
        if not defs: 
            return C(0)
        if all(canon(x) == canon(defs[0]) for x in defs): 
            return canon(defs[0])
        if not bits: 
            raise ValueError('incompatible constraints at leaf')
        h = len(vs)//2; lo, hi = vs[:h], vs[h:]
        if _compatible(lo, hi): 
            return rec(_merge_constraints(lo, hi), bits[:-1])
        a = rec(lo, bits[:-1]); b = rec(hi, bits[:-1])
        return _mux_simplify(bits[-1], a, b)
    return canon(rec(vals, index_bits))

def _selector_mux(values: list, sel_bits: list[dict]): 
    if len(values)!=(1<<len(sel_bits)): 
        raise ValueError('selector value count/width mismatch')
    # no don't-cares here, but use the same synthesis machinery
    return _synth_constraints(values, sel_bits)

def _synth_rows_with_order(rows: list[tuple[dict, object]], order: list[dict]): 
    """Shannon synthesis over semantic selection variables with don't-care merging.

    Leaves are physical payload nets/constants; only the selector/index variables are
    Shannon-expanded.  A high/low cofactor pair is merged whenever all overlapping care
    leaves agree, which is exact don't-care reduction rather than protocol knowledge.
    """
    def compat(a, b): 
        return a is None or b is None or canon(a) == canon(b)
    def rec(rs, vars_left): 
        defs = [v for _, v in rs if v is not None]
        if not defs: 
            return C(0)
        if all(canon(v) == canon(defs[0]) for v in defs): 
            return canon(defs[0])
        if not vars_left: 
            raise ValueError('incompatible cared observation leaves')
        var = vars_left[0]; rem = vars_left[1:]
        lo = [(e, v) for e, v in rs if int(e[var['name']]) == 0]
        hi = [(e, v) for e, v in rs if int(e[var['name']]) == 1]
        def key(e): 
            return tuple(int(e[x['name']]) for x in rem)
        dl = {key(e): v for e, v in lo}; dh = {key(e): v for e, v in hi}
        if set(dl) == set(dh) and all(compat(dl[k], dh[k]) for k in dl): 
            merged = []
            for k in dl: 
                v = dl[k] if dl[k] is not None else dh[k]
                e = {var['name']: 0};e.update({x['name']: z for x, z in zip(rem, k)})
                merged.append((e, v))
            return rec(merged, rem)
        return _mux_simplify(var, rec(lo, rem), rec(hi, rem))
    return canon(rec(rows, order))


def _technology_order_search(root: Path, rows: list[tuple[dict, object]], variables: list[dict], compare_value: int): 
    """Choose a Shannon variable order by mapped cell count/area.

    The semantic care-set and leaves are fixed before this step.  The search is a generic
    technology-aware factoring decision (G7/G9 territory), not a semantic special case.
    For large selection dimensions we use deterministic order only; small B20-style
    indexed observations can exhaust all permutations cheaply.
    """
    from itertools import permutations
    from .boolean_mapper import BooleanMapper
    area_path = root/'technology/tr1um_cell_area.json'
    area = _load(area_path).get('cell_area_um2', {}) if area_path.exists() else {}
    comps = {}
    for v in variables: 
        comps[str(v['q'])] = str(v['qb']); comps[str(v['qb'])] = str(v['q'])
    orders = list(permutations(variables)) if len(variables)<=7 else [tuple(variables)]
    best = None
    for order in orders: 
        selected = _synth_rows_with_order(rows, list(order))
        expr = selected if int(compare_value) == 1 else canon(N(selected))
        if area: 
            bm = BooleanMapper(area, complements = comps);bm.map(expr); cost = (len(bm.cells), bm.total_area(), tuple(v['name'] for v in order))
        else: 
            cost = (len(repr(expr)), 0.0, tuple(v['name'] for v in order))
        if best is None or cost<best[0]: 
            best = (cost, expr, order)
    return best[1], {'orders_checked': len(orders), 'selected_order': [v['name'] for v in best[2]], 
                    'mapped_cell_cost': int(best[0][0]), 'mapped_area_cost_um2': float(best[0][1])}


def _load_hold_state_projection_plan(root: Path)->dict|None: 
    """Recover only load/hold bank physical Q/QB naming from semantic recurrence.

    Unlike discover_load_hold_binding(), this does not synthesize load predicates and therefore
    does not depend on already-emitted component contracts/helpers.  Observation-output lowering
    can consequently run in any component-manifest order.
    """
    p = root/'build/generated_certificates/load_hold_semantic_recurrence.json'
    if not p.exists(): 
        return None
    cert = _load(p)
    if cert.get('status')!='PASS': 
        return None
    from .semantic_projection import load_hold_naming
    banks = []
    for b in cert.get('banks', []): 
        banks.append({'semantic_source': str(b['semantic_source']), 
                      'bits': load_hold_naming(b)})
    return {'banks': banks, 'resolved_from': 'load_hold_semantic_recurrence+naming_policy_only'}


def discover_observation_tx_binding(root: Path)->dict|None: 
    root = Path(root); g = root/'build/generated_certificates'
    needed = [g/'oe_recurrence.json', g/'shared_counter.json', g/'shared_counter_index_projection.json', 
            g/'observation_snapshot.json', g/'observation_source_projection.json', g/'observation_local_bit_elimination.json']
    if any(not p.exists() for p in needed): 
        return None
    oe, shared, idxproj, snap, srcproj, obslocal = map(_load, needed)
    if any(d.get('status')!='PASS' for d in (oe, shared, idxproj, snap, srcproj)): 
        return None
    basis = str(oe.get('data_predicate_basis', ''))
    phase = _load(root/'build/semantic/phase40.ir.json')
    pred = None
    for x in phase.get('predicate_basis', []): 
        if str(x.get('id')) == basis: 
            pred = x.get('expression')
            break
    parsed = _parse_indexed_bit_predicate(pred)
    if not parsed: 
        return None
    if parsed['bit_value_bit']!=0: 
        return None
    if str(parsed['count_register'])!=str(idxproj.get('semantic_count_source')): 
        return None
    if str(parsed['count_register'])!=str(oe.get('decrement_count_source')): 
        return None
    high = idxproj.get('relations_by_phase', {}).get('H', {})
    # At the fall-edge source (high phase), semantic_count = PC + offset.  The indexed
    # semantic predicate subtracts the same offset, hence its physical index is PC.
    if int(high.get('offset_mod', -999))!=int(parsed['subtract']): 
        return None
    width = int(idxproj['physical_counter_width']); modulus = 1<<width

    from .neutral_bindings import resolve_shared_counter_interface_binding, resolve_oe_interface_binding
    load_plan = _load_hold_state_projection_plan(root)
    scbind = resolve_shared_counter_interface_binding(root)
    oebind = resolve_oe_interface_binding(root)
    if not load_plan or not scbind or not oebind: 
        return None
    cbits = list(scbind.get('counter_bits', []))
    if len(cbits)!=width: 
        return None
    index_bits = [{'q': str(x['net']), 'qb': str(x['complement'])} for x in cbits]
    selreg = str(srcproj['selector_register']); selwidth = int(srcproj['selector_width'])
    selbits = _selector_bits(load_plan, selreg, selwidth)
    selectors = list(range(1<<selwidth))
    if sorted(map(int, srcproj.get('selector_values', [])))!=selectors: 
        return None

    obsreg = str(parsed['observation_register']); st = _semantic_storage(root, obsreg); obswidth = int(st.get('semantic_width', 1))
    src_by = {int(k): v for k, v in srcproj['source_by_selector'].items()}
    # Cross-check source-projected observation-local eliminated bits using the physical
    # selector encoding.  This is independent of the final indexed-output factoring.
    obs_bits = []
    for bit in range(obswidth): 
        vals = [_source_bit_expr(root, load_plan, src_by[s], bit, obswidth, snap) for s in selectors]
        obs_bits.append(_selector_mux(vals, selbits))
    local_checks = []
    for x in obslocal.get('results', []): 
        if str(x.get('register'))!=obsreg or x.get('classification')!='PASS_OBSERVATION_LOCAL_BIT_ELIMINATION': 
            continue
        bit = int(x['semantic_bit']); f = x.get('formula', {}); ok = False
        if f.get('kind') == 'ATOM': 
            atom = f.get('atom', {}); reg = str(atom.get('register')); val = int(atom.get('value'))
            if reg == selreg: 
                want = C(1)
                for i, sb in enumerate(selbits): 
                    want = canon(A(want, V(sb['q']) if ((val>>i)&1) else V(sb['qb'])))
                ok = canon(want) == canon(obs_bits[bit])
        local_checks.append({'bit': bit, 'status': 'PASS' if ok else 'FAIL'})
    if any(x['status']!='PASS' for x in local_checks): 
        raise ValueError(f'observation-local projection mismatch {local_checks}')

    # Prove that data_predicate is irrelevant when semantic decrement count is zero.
    data_used_only_nonzero = True
    for rows0 in oe.get('truth_table_by_control_state', {}).values(): 
        groups = {}
        for row in rows0: 
            if int(row.get('count_zero', 0))!=1: 
                continue
            key = tuple((k, row.get(k)) for k in sorted(row) if k not in ('data_predicate', 'next_oe'))
            groups.setdefault(key, {})[int(row.get('data_predicate', 0))] = int(row.get('next_oe', 0))
        if any(len(v)>1 and len(set(v.values()))>1 for v in groups.values()): 
            data_used_only_nonzero = False
    if not data_used_only_nonzero: 
        raise ValueError('data predicate is observed at count_zero')

    # Build the exact care table over only semantic selection dimensions.  Payload data
    # remain symbolic physical leaves.  The PC value corresponding to count_zero is a
    # proven don't-care for this helper and is therefore None.
    variables = []
    for i, b in enumerate(index_bits): 
        variables.append({'name': f'index_{i}', 'q': b['q'], 'qb': b['qb']})
    for i, b in enumerate(selbits): 
        variables.append({'name': f'selector_{i}', 'q': b['q'], 'qb': b['qb']})
    rows = []
    from itertools import product
    for bitsv in product((0, 1), repeat = len(variables)): 
        env = {v['name']: z for v, z in zip(variables, bitsv)}
        idx = sum(int(env[f'index_{i}'])<<i for i in range(width))
        sel = sum(int(env[f'selector_{i}'])<<i for i in range(selwidth))
        if idx == modulus-1: 
            leaf = None
        elif idx>=obswidth: 
            leaf = C(0)
        else: 
            leaf = _source_bit_expr(root, load_plan, src_by[sel], idx, obswidth, snap)
        rows.append((env, leaf))
    out_expr, order_search = _technology_order_search(root, rows, variables, parsed['compare_value'])
    outnet = str(oebind.get('data_predicate', {}).get('net') or f'pred_{basis.lower()}')

    # Deterministic interface: actual physical variables used by the synthesized output.
    def vars_of(e): 
        op = e[0]
        if op == 'VAR': 
            return {str(e[1])}
        if op == 'CONST': 
            return set()
        if op == 'NOT': 
            return vars_of(e[1])
        if op in ('AND', 'OR'): 
            return set().union(*(vars_of(z) for z in e[1])) if e[1] else set()
        if op in ('XOR', 'XNOR'): 
            return vars_of(e[1])|vars_of(e[2])
        if op == 'MUX': 
            return vars_of(e[1])|vars_of(e[2])|vars_of(e[3])
        raise ValueError(op)
    ins = sorted(vars_of(out_expr))
    return {
      'version': 'bio2rtl-auto-observation-indexed-output-plan-v1', 
      'interface_inputs': ins, 'interface_outputs': [outnet], 'outputs': {outnet: out_expr}, 'helpers': {}, 
      'complements': {str(v['q']): str(v['qb']) for v in variables}|{str(v['qb']): str(v['q']) for v in variables}, 
      'resolved_from': 'semantic_indexed_predicate+shared_counter_projection+observation_source_projection+snapshot_proof', 
      'binding_discovery_from_semantic_authority_complete': True, 
      'semantic_basis': basis, 'semantic_indexed_predicate': parsed, 
      'physical_index_relation': {'phase': 'H', 'offset_cancelled': parsed['subtract'], 'width': width, 'dontcare_index': modulus-1, 'index_bits': index_bits}, 
      'observation_projection': {'register': obsreg, 'width': obswidth, 'selector_register': selreg, 'selector_width': selwidth}, 
      'observation_local_cross_checks': local_checks, 'technology_factoring_search': order_search, 
    }
