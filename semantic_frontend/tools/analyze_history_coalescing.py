#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, itertools, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_reachability import _eval_expr
from bio2rtl.dedicated_event_derived_state import _refs


def canon(x): 
    return json.dumps(x, sort_keys = True, separators = (',', ':'))

def guard_eval(rule, preds, env): 
    for g in rule.get('enable', []): 
        ex = preds[str(g['basis'])]
        refs = _refs(ex)
        if any(k!='REG' or r not in env for k, r in refs): 
            return None
        if bool(_eval_expr(ex, env, {}, 0)) != bool(g['polarity']): 
            return False
    return True

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--domain', type = Path, required = True)
    ap.add_argument('--output-json', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    dom = json.loads(a.domain.read_text())
    arch = {str(r['id']): r for r in ir['architectural_registers'] if r.get('kind') == 'PHYSICAL'}
    startup = {str(x['register']): x['value'] for x in ir['startup']['register_values']}
    rules = {rid: [x for x in ir['update_rules'] if x.get('materialize') and str(x.get('target')) == rid] for rid in arch}
    preds = {str(p['id']): p['expression'] for p in ir['predicate_basis']}
    tracked = list(map(str, dom['tracked_registers']))
    states = [dict(zip(tracked, map(int, s))) for s in dom['state_tuples']]
    candidates = []
    ids = sorted(arch)
    for ra, rb in itertools.combinations(ids, 2): 
        aa, bb = arch[ra], arch[rb]
        if int(aa['width'])!=int(bb['width']) or startup.get(ra)!=startup.get(rb): 
            continue
        if len(rules[ra])!=1 or len(rules[rb])!=1: 
            continue
        xa, xb = rules[ra][0], rules[rb][0]
        if str(xa['event_class'])!=str(xb['event_class']) or canon(xa['outcome'])!=canon(xb['outcome']): 
            continue
        # Common residual guards may depend on untracked datapath state (e.g. a counter).
        # For mutual-exclusion proof, cancel exact common guard literals and require only
        # the *differing* selector literals to be decidable from the proof-backed joint state.
        ga = {(str(g['basis']), bool(g['polarity'])) for g in xa.get('enable', [])}
        gb = {(str(g['basis']), bool(g['polarity'])) for g in xb.get('enable', [])}
        common = ga & gb
        da = ga-common
        db = gb-common
        refs_diff = set()
        for basis, pol in da|db: 
            refs_diff |= _refs(preds[basis])
        if any(k!='REG' or r not in tracked for k, r in refs_diff): 
            continue
        def diff_true(ds, env): 
            for basis, pol in ds: 
                if bool(_eval_expr(preds[basis], env, {}, 0)) != bool(pol): 
                    return False
            return True
        ena = []
        enb = []
        overlap = []
        for i, env in enumerate(states): 
            va = diff_true(da, env)
            vb = diff_true(db, env)
            if va: 
                ena.append(i)
            if vb: 
                enb.append(i)
            if va and vb: 
                overlap.append(i)
        if overlap: 
            continue
        if not ena or not enb: 
            continue
        # Read references are diagnostic and used by the downstream model proof.
        pred_reads = []
        for p in ir['predicate_basis']: 
            rr = _refs(p['expression'])
            if ('REG', ra) in rr or ('REG', rb) in rr: 
                pred_reads.append({'predicate': str(p['id']), 'reads': [r for r in (ra, rb) if ('REG', r) in rr]})
        outcome_reads = []
        for r in ir['update_rules']: 
            rr = _refs(r['outcome'])
            hits = [x for x in (ra, rb) if ('REG', x) in rr]
            if hits: 
                outcome_reads.append({'rule_id': str(r['rule_id']), 'target': str(r['target']), 'event_class': str(r['event_class']), 'reads': hits, 'enable': r.get('enable', [])})
        # Conservative read-live partition over tracked control state: ignore candidate-dependent
        # predicates and untracked datapath guards (treat them as potentially satisfiable).
        pred_bank = {}
        for p in ir['predicate_basis']: 
            rr = _refs(p['expression'])
            hits = [x for x in (ra, rb) if ('REG', x) in rr]
            if len(hits) == 1: 
                pred_bank[str(p['id'])] = hits[0]
        live = {ra: set(), rb: set()}
        for rule in ir['update_rules']: 
            if not rule.get('materialize'): 
                continue
            banks = set()
            rr = _refs(rule['outcome'])
            banks.update(x for x in (ra, rb) if ('REG', x) in rr)
            for g in rule.get('enable', []): 
                if str(g['basis']) in pred_bank: 
                    banks.add(pred_bank[str(g['basis'])])
            if not banks: 
                continue
            for i, env in enumerate(states): 
                impossible = False
                for g in rule.get('enable', []): 
                    ex = preds[str(g['basis'])]
                    refs = _refs(ex)
                    if any(('REG', x) in refs for x in (ra, rb)): 
                        continue
                    if all(k == 'REG' and r in tracked for k, r in refs): 
                        if bool(_eval_expr(ex, env, {}, 0))!=bool(g['polarity']): 
                            impossible = True
                            break
                if not impossible: 
                    for bank in banks: 
                        live[bank].add(i)
        live_overlap = live[ra]&live[rb]
        selector_sets = {ra: set(ena), rb: set(enb)}
        live_within_selector = all(live[x] <= selector_sets[x] for x in (ra, rb))
        candidates.append({
            'registers': [ra, rb], 'width': int(aa['width']), 'reset': startup[ra], 
            'event_class': str(xa['event_class']), 'outcome': xa['outcome'], 
            'rules': [str(xa['rule_id']), str(xb['rule_id'])], 
            'write_enabled_states': [len(ena), len(enb)], 'write_overlap_states': len(overlap), 
            'common_guard_literals': [{'basis': b, 'polarity': p} for b, p in sorted(common)], 
            'differing_guard_literals': [[{'basis': b, 'polarity': p} for b, p in sorted(da)], [{'basis': b, 'polarity': p} for b, p in sorted(db)]], 
            'predicate_reads': pred_reads, 'outcome_reads': outcome_reads, 
            'read_live_states': [len(live[ra]), len(live[rb])], 'read_live_overlap_states': len(live_overlap), 
            'read_live_within_write_selector': live_within_selector, 
            'saved_bits': int(aa['width']), 
        })
    # Rank largest bit saving then simplest read footprint.
    candidates.sort(key = lambda x: (-x['saved_bits'], len(x['predicate_reads'])+len(x['outcome_reads']), x['registers']))
    result = {'version': 'history-coalescing-analysis-v1', 'proof_model': dom.get('proof_model', 'proof-backed joint-control domain'), 
            'tracked_states': len(states), 'candidates': candidates, 'recommended': candidates[0] if candidates else None, 
            'n_a': not bool(candidates), 'n_a_reason': ('no proof-backed history pair' if not candidates else None)}
    a.output_json.write_text(json.dumps(result, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL HISTORY / PING-PONG STORAGE COALESCING ANALYSIS', '='*104, 
           f'proof-backed joint states : {len(states)}', f'candidates                 : {len(candidates)}']
    for i, c in enumerate(candidates[:12]): 
        lines += ['', f'[{i}] {c["registers"][0]} + {c["registers"][1]} : {c["width"]*2} -> {c["width"]} bits', 
                  f'    event/outcome         : {c["event_class"]} / identical', 
                  f'    write-enabled states  : {c["write_enabled_states"]}', 
                  f'    write-overlap states  : {c["write_overlap_states"]}', 
                  f'    predicate read sites  : {len(c["predicate_reads"])}', f'    outcome read sites    : {len(c["outcome_reads"])}', 
                  f'    read-live states      : {c["read_live_states"]}', f'    read-live overlap     : {c["read_live_overlap_states"]}', f'    live within selector  : {c["read_live_within_write_selector"]}']
    lines += ['', ('OPTIONAL RESULT: N/A' if not candidates else 'RESULT: CANDIDATE FOUND'), 'RESULT: PASS']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
