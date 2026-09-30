#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, collections, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_reachability import _eval_expr


def refs(x): 
    out = set()
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG': 
            out.add(str(x[1]))
            return out
        for y in x: 
            out |= refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            out |= refs(y)
    return out

def aux_refs(x): 
    out = set()
    if isinstance(x, list): 
        if x: 
            tag = x[0]
            if tag == 'SCHED_REG' and len(x)>=2: 
                out.add(('SCHED_REG', str(x[1])))
                return out
            if tag == 'GPIO_INPUT': 
                out.add(('GPIO_INPUT', 'GPIO_INPUT'))
                return out
        for y in x: 
            out |= aux_refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            out |= aux_refs(y)
    return out

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--quotient', type = Path, required = True)
    ap.add_argument('--legal-product', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    args = ap.parse_args()
    ir = json.loads(args.ir.read_text())
    quo = json.loads(args.quotient.read_text())
    lp = json.loads(args.legal_product.read_text())
    if lp.get('proof_result')!='PASS': 
        raise SystemExit('FAIL legal product is not PASS')
    tracked = list(quo['tracked_registers'])
    ti = {r: i for i, r in enumerate(tracked)}
    classes = {int(c['code']): [tuple(map(int, s)) for s in c['states']] for c in quo['classes']}
    pred = {str(p['id']): p['expression'] for p in ir['predicate_basis']}
    pref = {k: refs(v) for k, v in pred.items()}
    paux = {k: aux_refs(v) for k, v in pred.items()}
    arch = {str(x['id']): x for x in ir['architectural_registers']}
    # The legal-product schema names the class and event columns; no protocol event spelling is embedded here.
    eidx = {name: i for i, name in enumerate(lp['edge_tuple'])}
    source_by_event = collections.defaultdict(set)
    reachable_classes = set()
    for row in lp['edges']: 
        code = int(row[eidx['class']])
        ev = str(row[eidx['event']])
        source_by_event[ev].add(code)
        reachable_classes.add(code)
    def evalpred(pid, st): 
        regs = {r: st[ti[r]] for r in tracked}
        return bool(_eval_expr(pred[pid], regs, {}, 0))
    outcome_readers = collections.defaultdict(list)
    for rule in ir['update_rules']: 
        for r in refs(rule.get('outcome')): 
            if str(rule.get('target'))!=r: 
                outcome_readers[r].append(str(rule['rule_id']))
    uses = collections.defaultdict(list)
    for rule in ir['update_rules']: 
        if not rule.get('materialize'): 
            continue
        for e in rule.get('enable', []): 
            uses[str(e['basis'])].append(rule)
    candidates = []
    for rid in tracked: 
        spec = arch.get(rid)
        if not spec or int(spec.get('width', 0))!=1: 
            continue
        pids = [pid for pid, rs in pref.items() if rid in rs]
        if not pids or outcome_readers.get(rid): 
            continue
        replacements = {}
        proof_rows = []
        all_ok = True
        for pid in pids: 
            ruses = uses.get(pid, [])
            if not ruses: 
                continue
            cands = [('CONST', False, None), ('CONST', True, None)]
            for qid, qrefs in pref.items(): 
                if qid == pid or rid in qrefs or not qrefs.issubset(set(tracked)) or paux.get(qid): 
                    continue
                cands.append(('PRED', False, qid))
                cands.append(('PRED', True, qid))
            survivors = []
            for kind, inv, qid in cands: 
                checked = 0
                mismatch = 0
                peruse = []
                for rule in ruses: 
                    ev = str(rule['event_class'])
                    codes = source_by_event.get(ev, set())
                    rc = rm = 0
                    parent = []
                    for e in rule.get('enable', []): 
                        if str(e['basis']) == pid: 
                            continue
                        bid = str(e['basis'])
                        if pref.get(bid, set()).issubset(set(tracked)) and not paux.get(bid): 
                            parent.append((bid, bool(e['polarity'])))
                    for code in codes: 
                        for st in classes[code]: 
                            if any(evalpred(b, st)!=pol for b, pol in parent): 
                                continue
                            got = evalpred(pid, st)
                            want = bool(inv) if kind == 'CONST' else (not evalpred(qid, st) if inv else evalpred(qid, st))
                            checked+=1
                            rc+=1
                            if got!=want: 
                                mismatch+=1
                                rm+=1
                    peruse.append({'rule_id': rule['rule_id'], 'event_class': ev, 'checked': rc, 'mismatch': rm, 'tracked_parent': parent})
                if checked>0 and mismatch == 0: 
                    survivors.append((kind, inv, qid, checked, peruse))
            if not survivors: 
                all_ok = False
                proof_rows.append({'predicate': pid, 'status': 'NO_REPLACEMENT'})
                continue
            survivors.sort(key = lambda x: (0 if x[0] == 'CONST' else 1, 0 if x[2] is None else len(pref[x[2]]), str(x[2]), x[1]))
            kind, inv, qid, checked, peruse = survivors[0]
            repl = {'kind': kind, 'invert': bool(inv), 'predicate': qid, 'constant': (int(inv) if kind == 'CONST' else None)}
            replacements[pid] = repl
            proof_rows.append({'predicate': pid, 'status': 'PASS', 'replacement': repl, 'checked': checked, 'uses': peruse, 'alternatives': len(survivors)})
        if all_ok and replacements: 
            candidates.append({'register': rid, 'predicate_replacements': replacements, 'proof': proof_rows, 'outcome_readers': []})
    payload = {'version': 'phase-local-state-elision-v2-external-legal-product', 'legal_product': {'version': lp.get('version'), 'states': len(lp.get('states', [])), 'edges': len(lp.get('edges', [])), 'reachable_classes': len(reachable_classes), 'by_event': {k: len(v) for k, v in sorted(source_by_event.items())}}, 'tracked_registers': tracked, 'candidates': candidates, 'notes': ['Legal event contexts are consumed from a separately proofed product artifact; event names are not encoded by this analyzer.', 'Non-tracked parent guards are dropped, conservatively widening proof context.', 'A candidate is emitted only when every materialized predicate consumer has a zero-mismatch replacement and the register has no non-self outcome readers.']}
    args.output.parent.mkdir(parents = True, exist_ok = True)
    args.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
    print('legal states', len(lp.get('states', [])), 'classes', len(reachable_classes), 'edges', len(lp.get('edges', [])))
    for c in candidates: 
        print('CANDIDATE', c['register'], c['predicate_replacements'])
        for p in c['proof']: 
            print(' ', p['predicate'], p['status'], p.get('checked'))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
