#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, hashlib
from .boolean_contract import extract_boolean_sequential_contract, _tupleize
from .proof_bitset import exact_equivalence, expr_vars
from .semantic_liveness import analyze_semantic_liveness, prune_contracts_for_liveness


def load(p): 
    return json.loads(Path(p).read_text())
def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def tuple_expr(x): 
    e = _tupleize(x)
    def norm(z): 
        if not isinstance(z, tuple) or not z: 
            return z
        if z[0] == 'VAR' and str(z[1]) in ("1'b0", "1'b1", '0', '1'): 
            return ('CONST', 1 if str(z[1]) in ("1'b1", '1') else 0)
        if z[0] in ('AND', 'OR'): 
            return (z[0], tuple(norm(y) for y in z[1]))
        return tuple([z[0], *(norm(y) if isinstance(y, tuple) else y for y in z[1:])])
    return norm(e)

def expand_named(e, comb, stack = ()): 
    e = tuple_expr(e)
    if not isinstance(e, tuple) or not e: 
        return e
    if e[0] == 'VAR' and str(e[1]) in comb: 
        n = str(e[1])
        if n in stack: 
            raise ValueError(f'comb expansion cycle {stack+(n,)}')
        return expand_named(comb[n], comb, stack+(n,))
    if e[0] in ('AND', 'OR'): 
        return (e[0], tuple(expand_named(x, comb, stack) for x in e[1]))
    return tuple([e[0], *(expand_named(x, comb, stack) if isinstance(x, tuple) else x for x in e[1:])])

def normalize_complements(e, qb_to_q): 
    e = tuple_expr(e)
    if not isinstance(e, tuple) or not e: 
        return e
    if e[0] == 'VAR' and str(e[1]) in qb_to_q: 
        return ('NOT', ('VAR', qb_to_q[str(e[1])]))
    if e[0] in ('AND', 'OR'): 
        return (e[0], tuple(normalize_complements(x, qb_to_q) for x in e[1]))
    return tuple([e[0], *(normalize_complements(x, qb_to_q) if isinstance(x, tuple) else x for x in e[1:])])

def eq(a, b, qb_to_q, max_vars = 20): 
    return exact_equivalence(normalize_complements(a, qb_to_q), normalize_complements(b, qb_to_q), max_vars = max_vars)

def merge_contracts(root: Path, contracts: list[dict] | None = None):
    cs = contracts or [load(p) for p in sorted((root/'build/semantic_neutral_contracts').glob('*.json'))]
    comb = {}
    dffs = []
    prims = []
    latches = []
    declared = set()
    outs = set()
    for c in cs: 
        for k, v in c.get('combinational_outputs', {}).items(): 
            if k in comb and comb[k]!=v: 
                raise ValueError(f'comb collision {k}')
            comb[k] = v
        dffs+=c.get('dffs', [])
        prims+=c.get('primitives', [])
        latches+=c.get('latches', [])
        declared.update(map(str, c.get('interface_inputs', [])))
        outs.update(map(str, c.get('interface_outputs', [])))
    produced = set(comb)
    for d in dffs: 
        produced.update((str(d['q']), str(d['qb'])))
    for p in prims: 
        produced.add(str(p['output']))
    for l in latches: 
        produced.update((str(l['q']), str(l['qb'])))
    external = sorted(declared-produced)
    return {'contracts': cs, 'comb': comb, 'dffs': dffs, 'primitives': prims, 'latches': latches, 'external': external, 'outputs': sorted(outs)}

def verify_global_semantic_map(root: Path, mapfile: Path, *, output_name: str = 'GLOBAL_SEMANTIC_MAP_PROOF.json')->dict: 
    root = Path(root).resolve()
    mp = Path(mapfile)
    mp = mp if mp.is_absolute() else root/mp
    raw = merge_contracts(root)
    graph_path = root / 'build' / 'neutral_component_graph.json'
    if not graph_path.exists():
        raise FileNotFoundError(f'missing neutral component graph for semantic liveness proof: {graph_path}')
    graph = load(graph_path)
    liveness = analyze_semantic_liveness(raw['contracts'], graph)
    live_contracts = prune_contracts_for_liveness(raw['contracts'], liveness)
    merged = merge_contracts(root, live_contracts)
    exp = load(mp)
    cells = exp['cells']
    comb = merged['comb']
    dffs = merged['dffs']
    prims = merged['primitives']
    latches = merged['latches']
    iface_out = sorted(set(merged['outputs'])|set(comb))
    recipe = {'component_class': 'global_semantic_core', 'interface_inputs': merged['external'], 'interface_outputs': iface_out, 'cells': cells}
    clock_nets = {str(p['output']) for p in prims if p.get('kind') == 'clock_inverter'}
    got = extract_boolean_sequential_contract(recipe, allow_primitives = True, preserve_clock_nets = clock_nets)
    amap = {str(a['lhs']): str(a['rhs']) for a in exp.get('assignments', []) if isinstance(a, dict) and 'lhs' in a and 'rhs' in a}
    for n, rhs in amap.items(): 
        if rhs == "1'b0": 
            got.setdefault('combinational_outputs', {})[n] = ('CONST', 0)
        elif rhs == "1'b1": 
            got.setdefault('combinational_outputs', {})[n] = ('CONST', 1)
        else: 
            got.setdefault('combinational_outputs', {})[n] = ('VAR', rhs)
    qb_to_q = {str(d['qb']): str(d['q']) for d in dffs}
    qb_to_q.update({str(l['qb']): str(l['q']) for l in latches})
    failures = []
    checks = 0
    details = []
    gd = {str(x['q']): x for x in got.get('dffs', [])}
    td = {str(x['q']): x for x in dffs}
    if set(gd)!=set(td): 
        failures.append(f'DFF Q set mismatch got={sorted(gd)} target={sorted(td)}')
    for q in sorted(set(gd)&set(td)): 
        g, t = gd[q], td[q]
        if (g.get('clock'), g.get('reset'), g.get('qb'))!=(t.get('clock'), t.get('reset'), t.get('qb')): 
            failures.append(f'{q}: clock/reset/QB mismatch')
        te = expand_named(t['d_expr'], comb)
        ge = g['d_expr']
        r = eq(te, ge, qb_to_q)
        checks+=r['checks']
        details.append({'kind': 'dff', 'net': q, **r})
        if not r['equivalent']: 
            failures.append(f'{q}: recurrence mismatch {r.get("counterexample")}')
    # Compare all named combinational roles. Extraction expands mapped gates into expressions.
    gc = got.get('combinational_outputs', {})
    for n, t in sorted(comb.items()): 
        if n not in gc: 
            failures.append(f'{n}: mapped combinational output missing')
            continue
        r = eq(expand_named(t, comb), gc[n], qb_to_q)
        checks+=r['checks']
        details.append({'kind': 'comb', 'net': n, **r})
        if not r['equivalent']: 
            failures.append(f'{n}: comb mismatch {r.get("counterexample")}')
    # Primitive topology and driver expressions, keyed by kind/class/output.
    def pk(p): 
        return (str(p.get('kind')), str(p.get('implementation_class')), str(p.get('output')))
    gp = {pk(p): p for p in got.get('primitives', [])}
    tp = {pk(p): p for p in prims}
    if set(gp)!=set(tp): 
        failures.append(f'primitive set mismatch got={sorted(gp)} target={sorted(tp)}')
    for k in sorted(set(gp)&set(tp)): 
        g, t = gp[k], tp[k]
        if str(g.get('input'))!=str(t.get('input')): 
            failures.append(f'primitive {k}: input net mismatch')
        r = eq(expand_named(t.get('input_expr', ['VAR', t['input']]), comb), g.get('input_expr', ['VAR', g['input']]), qb_to_q)
        checks+=r['checks']
        details.append({'kind': 'primitive', 'net': k[2], **r})
        if not r['equivalent']: 
            failures.append(f'primitive {k}: driver mismatch')
    # Latch state and set/reset driver semantics.
    def lk(l): 
        return (str(l.get('q')), str(l.get('qb')))
    gl = {lk(l): l for l in got.get('latches', [])}
    tl = {lk(l): l for l in latches}
    if set(gl)!=set(tl): 
        failures.append(f'latch set mismatch got={sorted(gl)} target={sorted(tl)}')
    for k in sorted(set(gl)&set(tl)): 
        g, t = gl[k], tl[k]
        for side in ('set', 'reset'): 
            # Compare each named primitive input driver; names are physical primitive boundary.
            ge = g.get(side+'_input_exprs', {})
            # Semantic latch contracts may intentionally preserve only primitive-boundary
            # input names; their Boolean drivers live in the merged named combinational DAG.
            # Treat that as the target expression rather than requiring an inline duplicate.
            names = set(map(str, t.get(side+'_inputs', [])))
            te = {n: (t.get(side+'_input_exprs', {}) or {}).get(n, comb.get(n, ['VAR', n])) for n in names}
            if set(ge)!=set(te): 
                failures.append(f'latch {k} {side} input set mismatch got={sorted(ge)} target={sorted(te)}')
            for n in sorted(set(ge)&set(te)): 
                r = eq(expand_named(te[n], comb), ge[n], qb_to_q)
                checks+=r['checks']
                details.append({'kind': 'latch_'+side, 'net': n, **r})
                if not r['equivalent']: 
                    failures.append(f'latch {k} {side} {n} mismatch {r.get("counterexample")}')
    contract_files = sorted((root/'build/semantic_neutral_contracts').glob('*.json'))
    result = {
        'version': 'bio2rtl-global-semantic-map-proof-v3',
        'status': 'PASS' if not failures else 'FAIL',
        'cells': len(cells),
        'dff_count': len(dffs),
        'comb_role_count': len(comb),
        'primitive_count': len(prims),
        'latch_count': len(latches),
        'truth_checks': checks,
        'failures': failures,
        'details': details,
        'semantic_liveness': liveness,
        'mapped_from_neutral_contracts_only': True,
        'mapped_candidate_file': str(mp.relative_to(root)) if mp.is_relative_to(root) else str(mp),
        'mapped_candidate_sha256': sha(mp),
        'neutral_contract_sha256': {str(x.relative_to(root)): sha(x) for x in contract_files},
    }
    (root/'build'/output_name).write_text(json.dumps(result, indent = 2, sort_keys = True)+'\n')
    return result

