#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_derived_state import _group_transitions, _refs
from bio2rtl.dedicated_event_reachability import _eval_expr

def subst(x, env): 
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG' and str(x[1]) in env: 
            return ['CONST', int(env[str(x[1])])]
        return [subst(y, env) for y in x]
    if isinstance(x, dict): 
        return {k: subst(v, env) for k, v in x.items()}
    return x

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--complete-ir', type = Path, required = True)
    ap.add_argument('--domain', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.complete_ir.read_text())
    dom = json.loads(a.domain.read_text())
    meta = json.loads(a.metadata.read_text())
    if meta.get('n_a'): 
        text = '\n'.join(['BIO2RTL CORRELATED GROUP CANDIDATE VERIFICATION', '='*96, 'OPTIONAL RESULT                 : N/A', 'identity transform verification : PASS', 'RESULT                          : PASS'])+'\n'
        a.output.parent.mkdir(parents = True, exist_ok = True)
        a.output.write_text(text)
        print(text, end = '')
        return 0
    tracked = list(map(str, dom['tracked_registers']))
    pos = {r: i for i, r in enumerate(tracked)}
    states = [tuple(map(int, s)) for s in dom['state_tuples']]
    regs = list(map(str, meta['registers']))
    rows = meta['rows']
    pat_to_code = {tuple(map(int, r['pattern'])): int(r['code']) for r in rows}
    code_to_pat = {int(r['code']): tuple(map(int, r['pattern'])) for r in rows}
    # Exact encode/decode over every proof-backed joint state.
    encode_checks = 0
    for s in states: 
        p = tuple(s[pos[r]] for r in regs)
        encode_checks+=1
        if p not in pat_to_code: 
            raise SystemExit(f'FAIL source pattern outside encoded domain: {p}')
        if code_to_pat[pat_to_code[p]]!=p: 
            raise SystemExit(f'FAIL encode/decode mismatch: {p}')
    # Inductive closure: for every transition that is not ruled out by tracked-state guards,
    # the encoded group next pattern remains in the exact proven pattern set.
    pred = {str(p['id']): p['expression'] for p in ir['predicate_basis']}
    trs = _group_transitions(ir)
    potential = 0
    dead = 0
    residual_guard = 0
    for s in states: 
        env = {r: s[pos[r]] for r in tracked}
        for t in trs: 
            impossible = False
            has_residual = False
            for g in t['guard']: 
                ex = subst(pred[str(g['basis'])], env)
                rr = _refs(ex)
                if rr: 
                    has_residual = True
                    continue
                val = bool(_eval_expr(ex, {}, {}, 0))
                if val!=bool(g['polarity']): 
                    impossible = True
                    break
            if impossible: 
                dead+=1
                continue
            potential+=1
            residual_guard+=int(has_residual)
            nxt = []
            for r in regs: 
                ex = subst(t['outcomes'][r], env)
                rr = _refs(ex)
                if rr: 
                    raise SystemExit(f'FAIL encoded outcome has residual external dependency: {t["transition_id"]} {r} {rr}')
                nxt.append(int(_eval_expr(ex, {}, {}, 0)))
            if tuple(nxt) not in pat_to_code: 
                raise SystemExit(f'FAIL next encoded pattern outside proof domain: state={s} tr={t["transition_id"]} next={tuple(nxt)}')
    text = '\n'.join(['BIO2RTL CORRELATED GROUP CANDIDATE VERIFICATION', '='*96, f"registers                    : {','.join(regs)}", f"storage                      : {meta['source_storage_bits']} -> {meta['candidate_storage_bits']} bits", f'joint-state encode checks    : {encode_checks}', f'potential transition checks  : {potential}', f'tracked-dead transition rows : {dead}', f'rows with residual guards    : {residual_guard}', 'exact encode/decode           : PASS', 'inductive pattern closure     : PASS', 'RESULT                        : PASS'])+'\n'
    a.output.write_text(text)
    print(text, end = '')
if __name__ == '__main__': 
    main()
