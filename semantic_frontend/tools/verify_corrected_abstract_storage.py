#!/usr/bin/env python3
from __future__ import annotations
import argparse, copy, hashlib, json, sys
from pathlib import Path

def canon(d): 
    x = copy.deepcopy(d)
    x.pop('storage_optimization', None)
    return json.dumps(x, sort_keys = True, separators = (',', ':')).encode()

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-ir', type = Path, required = True)
    ap.add_argument('--storage-ir', type = Path, required = True)
    ap.add_argument('--expect-before', type = int)
    ap.add_argument('--expect-after', type = int)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.source_ir.read_text())
    dst = json.loads(a.storage_ir.read_text())
    p = dst.get('storage_optimization', {})
    checks = {}
    checks['semantic_ir_unchanged'] = hashlib.sha256(canon(src)).digest() == hashlib.sha256(canon(dst)).digest()
    checks['proof_model'] = p.get('proof_model') == 'FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1'
    checks['safe_storage_proof'] = bool(p.get('safe_storage_proof'))
    checks['source_transitions_preserved'] = int(p.get('source_transitions', -1)) == int(src.get('verification', {}).get('source_transitions', -2))
    checks['relation_checks_3465_or_positive'] = int(dst.get('verification', {}).get('relation_checks', 0))>0
    checks['relation_misses_zero'] = int(dst.get('verification', {}).get('relation_misses', -1)) == 0
    checks['relation_conflicts_zero'] = int(dst.get('verification', {}).get('relation_conflicts', -1)) == 0
    checks['scheduler_relation_failures_zero'] = int(dst.get('verification', {}).get('scheduler_relation_failures', -1)) == 0
    checks['scheduler_not_used_for_elimination'] = not bool(p.get('policy', {}).get('scheduler_reachability_used_for_elimination', True))
    checks['no_sparse_encoding'] = not bool(p.get('policy', {}).get('sparse_value_encoding', True))
    checks['no_derived_elimination'] = not bool(p.get('policy', {}).get('derived_state_elimination', True))
    checks['no_joint_fsm_reencoding'] = not bool(p.get('policy', {}).get('joint_fsm_reencoding', True))
    before = int(p.get('preoptimization_storage_upper_bound_bits', -1))
    after = int(p.get('natural_storage_bits', -1))
    checks['storage_not_increased'] = 0<=after<=before
    if a.expect_before is not None: 
        checks['fixture_before'] = before == a.expect_before
    if a.expect_after is not None: 
        checks['fixture_after'] = after == a.expect_after
    # Local range proof audit for every applied narrowing.
    range_errors = []
    for r in p.get('register_storage', []): 
        kind = r['storage_kind']
        bits = int(r['storage_bits'])
        vals = r.get('reachable_values_upper_bound', r.get('reachable_values', []))
        if kind == 'NARROW_ZERO_EXTEND' and any(int(v)>=(1<<bits) for v in vals): 
            range_errors.append((r['register'], 'NARROW', bits, vals))
        if kind == 'CONST' and vals and any(int(v)!=int(r['constant_value']) for v in vals): 
            range_errors.append((r['register'], 'CONST', bits, vals))
        if kind == 'PACKED_MASK_BITS': 
            stored = {int(x) for x in r.get('stored_bits', [])}
            c0 = {int(x) for x in r.get('constant_zero_bits', [])}
            c1 = {int(x) for x in r.get('constant_one_bits', [])}
            for v in vals: 
                v = int(v)
                if any((v>>b)&1 for b in c0) or any(not ((v>>b)&1) for b in c1): 
                    range_errors.append((r['register'], 'PACKED_CONST', bits, v))
                    break
                variable = {b for b in range(int(r['semantic_width'])) if (v>>b)&1 and b not in c1}
                if not variable.issubset(stored): 
                    range_errors.append((r['register'], 'PACKED_DROP', bits, v))
                    break
    checks['all_applied_ranges_fit'] = not range_errors
    lines = ['CORRECTED ABSTRACT STORAGE VERIFICATION', '='*84]
    for k, v in checks.items(): 
        lines.append(f'{k:46}: {"PASS" if v else "FAIL"}')
    lines += [f'storage: {before} -> {after} bit', f'proof_model: {p.get("proof_model")}', f'range_errors: {range_errors[:8]}', 'RESULT: '+('PASS' if all(checks.values()) else 'FAIL')]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0 if all(checks.values()) else 1
if __name__ == '__main__': 
    raise SystemExit(main())
