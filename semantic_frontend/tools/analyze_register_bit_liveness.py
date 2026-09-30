#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path


def refs_reg(x, rid): 
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG' and str(x[1]) == rid: 
            return True
        return any(refs_reg(y, rid) for y in x)
    if isinstance(x, dict): 
        return any(refs_reg(y, rid) for y in x.values())
    return False

def support(expr, rid, width): 
    """Conservative semantic source-bit support. Refines BIT_VALUE and AND-with-constant."""
    if not isinstance(expr, list): 
        return set()
    if len(expr)>=2 and expr[0] == 'REG': 
        return set(range(width)) if str(expr[1]) == rid else set()
    if expr and expr[0] == 'BIT_VALUE' and len(expr)>=3: 
        inner = expr[1]
        bit = int(expr[2])
        if isinstance(inner, list) and len(inner)>=2 and inner[0] == 'REG' and str(inner[1]) == rid: 
            return {bit}
        return support(inner, rid, width)
    if expr and expr[0] == 'OP' and len(expr)>=3: 
        op = str(expr[1])
        args = expr[2] if isinstance(expr[2], list) else []
        if op == 'AND': 
            masks = [int(a[1]) for a in args if isinstance(a, list) and len(a)>=2 and a[0] == 'CONST']
            s = set()
            for a in args: 
                if isinstance(a, list) and a and a[0] == 'CONST': 
                    continue
                s |= support(a, rid, width)
            if masks: 
                mask = (1<<width)-1
                for m in masks: 
                    mask &= m
                s = {b for b in s if (mask>>b)&1}
            return s
        # For shifts/other ops source-bit support is conservatively the union.
        s = set()
        for a in args: 
            s|=support(a, rid, width)
        return s
    s = set()
    for y in expr[1:]: 
        s|=support(y, rid, width)
    return s

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    plans = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
    results = []
    for rid, p in sorted(plans.items()): 
        if str(p.get('storage_kind'))!='DIRECT': 
            continue
        sb = int(p.get('storage_bits', 0))
        sw = int(p.get('semantic_width', sb))
        if sb<=1 or sb!=sw: 
            continue
        live = set()
        evidence = []
        for pr in ir.get('predicate_basis', []): 
            if refs_reg(pr.get('expression'), rid): 
                ss = support(pr['expression'], rid, sw)
                live|=ss
                evidence.append({'kind': 'predicate', 'id': str(pr['id']), 'bits': sorted(ss)})
        for ru in ir.get('update_rules', []): 
            if str(ru.get('target')) == rid: 
                continue
            ex = ru.get('outcome')
            if refs_reg(ex, rid): 
                ss = support(ex, rid, sw)
                live|=ss
                evidence.append({'kind': 'outcome', 'id': str(ru.get('rule_id')), 'target': str(ru.get('target')), 'bits': sorted(ss)})
        # Only high suffix removal is allowed by this pass; internal holes require packed storage.
        neww = sb
        while neww>1 and (neww-1) not in live: 
            neww-=1
        if neww<sb: 
            results.append({'register': rid, 'source_bits': sb, 'candidate_bits': neww, 'removed_high_bits': list(range(neww, sb)), 
                            'live_nonself_bits': sorted(live), 'evidence': evidence, 
                            'proof': 'SEMANTIC_NONSELF_BIT_SUPPORT_CONSERVATIVE_HIGH_SUFFIX'})
    out = {'version': 'register-bit-liveness-v1', 'candidates': results, 'notes': ['Only contiguous high suffixes of DIRECT storage are proposed.', 'Predicate support is conservative; BIT_VALUE and AND-with-constant are refined.', 'Self-update recurrence is not itself an observation and does not make a bit live; candidate builder must prove kept next-state bits do not depend on removed bits.']}
    a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    print('BIO2RTL REGISTER BIT LIVENESS')
    print('='*80)
    for r in results: 
        print(f"{r['register']}: {r['source_bits']} -> {r['candidate_bits']} live={r['live_nonself_bits']}")
    print('candidates', len(results))
if __name__ == '__main__': 
    main()
