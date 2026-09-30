#!/usr/bin/env python3
from __future__ import annotations
import argparse, copy, json
from pathlib import Path


def main()->int: 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-ir', type = Path, required = True)
    ap.add_argument('--output-ir', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.source_ir.read_text())
    out = copy.deepcopy(src)
    p = out.get('storage_optimization', {})
    if p.get('proof_model')!='FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1' or not p.get('safe_storage_proof'): 
        raise SystemExit('FAIL: packed-mask elimination requires the accepted FSE Cartesian over-approximation storage proof')
    changed = []
    saved = 0
    for r in p.get('register_storage', []): 
        if r.get('kind') == 'GPIO': 
            continue
        sk = str(r.get('storage_kind'))
        semw = int(r.get('semantic_width', 0))
        old = int(r.get('storage_bits', 0))
        vals = [int(v) for v in r.get('reachable_values_upper_bound', [])]
        if sk not in {'DIRECT', 'NARROW_ZERO_EXTEND'} or old<=0 or not vals: 
            continue
        vary = []
        c0 = []
        c1 = []
        for b in range(semw): 
            bs = {(v>>b)&1 for v in vals}
            if bs == {0}: 
                c0.append(b)
            elif bs == {1}: 
                c1.append(b)
            else: 
                vary.append(b)
        # For NARROW_ZERO_EXTEND, bits above old are already eliminated. Count only
        # variables that were physically stored by the source plan.
        source_phys = set(range(semw if sk == 'DIRECT' else old))
        vary_phys = [b for b in vary if b in source_phys]
        if len(vary_phys)>=old: 
            continue
        # Any bit not stored must be proof-constant. This is a wiring-only semantic
        # reconstruction, not a sparse value code and therefore needs no decoder.
        omitted = source_phys-set(vary_phys)
        if any(b not in c0 and b not in c1 for b in omitted): 
            raise RuntimeError((r['register'], 'nonconstant omitted bit', omitted, c0, c1))
        r['storage_kind'] = 'PACKED_MASK_BITS'
        r['storage_bits'] = len(vary_phys)
        r['stored_bits'] = vary_phys
        r['constant_zero_bits'] = c0
        r['constant_one_bits'] = c1
        r['packed_mask_bit_proof'] = 'FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1_CONSTANT_SEMANTIC_BITS'
        r['source_storage_kind'] = sk
        r['source_storage_bits'] = old
        delta = old-len(vary_phys)
        saved+=delta
        changed.append({'register': r['register'], 'old_bits': old, 'new_bits': len(vary_phys), 'stored_bits': vary_phys, 'constant_zero_bits': c0, 'constant_one_bits': c1, 'saved_bits': delta, 'reachable_values_upper_bound': vals})
    old_total = int(p.get('natural_storage_bits', -1))
    if old_total<0: 
        raise SystemExit('FAIL: source storage total missing')
    p['natural_storage_bits'] = old_total-saved
    p['version'] = max(int(p.get('version', 0)), 4)
    p.setdefault('policy', {})['interior_constant_bit_elimination'] = True
    p['policy']['sparse_value_encoding'] = False
    p.setdefault('notes', []).append('Non-GPIO semantic bits proven constant over the FSE Cartesian reachable-value upper bound may be replaced by constants; remaining non-contiguous semantic bits are packed without recoding.')
    p['packed_mask_candidate'] = {'source_storage_bits': old_total, 'candidate_storage_bits': old_total-saved, 'saved_bits': saved, 'changed_registers': changed, 'semantic_recoding': False}
    a.output_ir.parent.mkdir(parents = True, exist_ok = True)
    a.output_ir.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL INTERIOR CONSTANT-BIT PACKED STORAGE CANDIDATE', '='*100, f'source storage bits    : {old_total}', f'candidate storage bits : {old_total-saved}', f'saved physical bits    : {saved}', '']
    for x in changed: 
        lines.append(f"{x['register']}: {x['old_bits']} -> {x['new_bits']} bits; stored semantic bits={x['stored_bits']}; const0={x['constant_zero_bits']}; const1={x['constant_one_bits']}; values={x['reachable_values_upper_bound']}")
    lines += ['', 'This is wiring-only bit packing, not arbitrary sparse-state encoding. No value decoder is introduced.', 'RESULT: '+('PASS' if saved>0 else 'PASS / N_A')]
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
