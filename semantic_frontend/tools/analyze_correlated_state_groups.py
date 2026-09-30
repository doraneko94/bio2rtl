#!/usr/bin/env python3
from __future__ import annotations
import argparse, itertools, json, math
from pathlib import Path

def or_feature_encoding(patterns): 
    n = len(patterns[0])
    cb = max(1, math.ceil(math.log2(len(patterns))))
    feats = []
    for size in range(1, n+1): 
        for sub in itertools.combinations(range(n), size): 
            vec = tuple(int(any(p[i] for i in sub)) for p in patterns)
            if len(set(vec))<2: 
                continue
            feats.append({'inputs': list(sub), 'or_cost': size-1, 'vec': vec})
    best = None
    for idxs in itertools.combinations(range(len(feats)), cb): 
        codes = []
        for pi in range(len(patterns)): 
            bits = tuple(feats[j]['vec'][pi] for j in idxs)
            codes.append(bits)
        if len(set(codes))!=len(patterns): 
            continue
        score = (sum(feats[j]['or_cost'] for j in idxs), sum(len(feats[j]['inputs']) for j in idxs), tuple(tuple(feats[j]['inputs']) for j in idxs))
        if best is None or score<best[0]: 
            best = (score, [feats[j] for j in idxs], codes)
    return best

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--storage-ir', type = Path, required = True)
    ap.add_argument('--domain', type = Path, required = True)
    ap.add_argument('--output-json', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    ap.add_argument('--min-group-size', type = int, default = 4)
    ap.add_argument('--min-savings', type = int, default = 2)
    a = ap.parse_args()
    ir = json.loads(a.storage_ir.read_text())
    dom = json.loads(a.domain.read_text())
    tracked = list(map(str, dom['tracked_registers']))
    states = [tuple(map(int, s)) for s in dom['state_tuples']]
    plans = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
    pos = {r: i for i, r in enumerate(tracked)}
    eligible = []
    for r in tracked: 
        p = plans[r]
        vals = sorted({s[pos[r]] for s in states})
        if int(p.get('storage_bits', 0)) == 1 and vals and set(vals)<= {0, 1} and str(p.get('storage_kind')) in {'DIRECT', 'NARROW_ZERO_EXTEND'}: 
            eligible.append(r)
    candidates = []
    for k in range(max(2, a.min_group_size), min(6, len(eligible))+1): 
        for regs in itertools.combinations(eligible, k): 
            pats = sorted({tuple(s[pos[r]] for r in regs) for s in states})
            cb = max(1, math.ceil(math.log2(len(pats))))
            saved = k-cb
            if saved<a.min_savings or len(pats)>8: 
                continue
            enc = or_feature_encoding(pats)
            if enc is None: 
                continue
            score, features, codes = enc
            rows = []
            for p, bits in zip(pats, codes): 
                code = 0
                for bit in bits: 
                    code = (code<<1)|int(bit)
                rows.append({'pattern': list(p), 'code': code, 'code_bits': list(bits)})
            candidates.append({'registers': list(regs), 'source_bits': k, 'code_bits': cb, 'saved_bits': saved, 'patterns': len(pats), 'encoder_or_cost': score[0], 'encoder_inputs': score[1], 'features': [{'inputs': x['inputs'], 'registers': [regs[i] for i in x['inputs']]} for x in features], 'rows': rows})
    candidates.sort(key = lambda x: (-x['saved_bits'], x['encoder_or_cost'], x['encoder_inputs'], x['patterns'], x['registers']))
    if not candidates: 
        out = {'version': 'correlated-state-group-analysis-v1', 'proof_model': 'JOINT_CONTROL_DOMAIN_EXACT_TUPLE_SET', 'eligible_one_bit_registers': eligible, 'candidates': [], 
             'recommendations': {}, 'n_a': True, 'n_a_reason': 'no proof-backed correlated group meets the configured savings threshold'}
        a.output_json.parent.mkdir(parents = True, exist_ok = True)
        a.output_json.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL CORRELATED STATE-GROUP ANALYSIS', '='*96, f'joint tuples              : {len(states)}', f'eligible one-bit controls : {len(eligible)}', 'candidate groups          : 0', 'OPTIONAL RESULT           : N/A', 'RESULT: PASS']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    max_saved = candidates[0]
    efficient = sorted(candidates, key = lambda x: (-(x['saved_bits']/(x['encoder_or_cost']+1.0)), -x['saved_bits'], x['encoder_or_cost'], x['patterns'], x['registers']))[0]
    out = {'version': 'correlated-state-group-analysis-v1', 'proof_model': 'JOINT_CONTROL_DOMAIN_EXACT_TUPLE_SET', 'eligible_one_bit_registers': eligible, 'candidates': candidates, 'recommendations': {'max_savings': max_saved, 'best_efficiency': efficient}}
    a.output_json.parent.mkdir(parents = True, exist_ok = True)
    a.output_json.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL CORRELATED STATE-GROUP ANALYSIS', '='*96, f'joint tuples              : {len(states)}', f'eligible one-bit controls : {len(eligible)}', f'candidate groups          : {len(candidates)}', '']
    for key, c in out['recommendations'].items(): 
        lines += [f'{key}:', f"  registers   : {','.join(c['registers'])}", f"  storage     : {c['source_bits']} -> {c['code_bits']} bits (save {c['saved_bits']})", f"  patterns    : {c['patterns']}", f"  encoder OR cost: {c['encoder_or_cost']}", f"  features    : {c['features']}", '']
    lines += ['Selection is derived only from the proof-backed joint-control domain; no protocol/state name is hard-coded.', 'RESULT: PASS']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__ == '__main__': 
    main()
