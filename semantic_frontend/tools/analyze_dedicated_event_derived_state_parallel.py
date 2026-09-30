#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_derived_state import (
    DerivedStateCandidate, 
    _dependency_context, 
    _domain_map, 
    _group_transitions, 
    _joint_closure, 
    _predicate_map, 
    _recognize_mapping, 
    _startup_values, 
    _use_summary, 
)

_CTX = None


def init_worker(ctx): 
    global _CTX
    _CTX = ctx


def analyze_pair(pair): 
    source, target = pair
    c = _CTX
    context = _dependency_context(source, target, c['transitions'], c['predicates'], c['domains'], max_context_regs = 2, max_joint_states = 4096)
    if context is None: 
        return None
    tracked = (source, target)+context
    reachable_joint, transition_checks, iterations = _joint_closure(
        tracked, c['transitions'], c['predicates'], c['domains'], c['startup'], 
        enumeration_limit = 4096, max_joint_states = 4096)
    if len(reachable_joint)>4096: 
        return None
    reachable = {(state[0], state[1]) for state in reachable_joint}
    mapping = {}
    functional = True
    for src_val, dst_val in sorted(reachable): 
        if src_val in mapping and mapping[src_val]!=dst_val: 
            functional = False
            break
        mapping[src_val] = dst_val
    if not functional: 
        return None
    recognized = _recognize_mapping(source, mapping)
    if recognized is None: 
        return None
    kind, expr = recognized
    bits = int(c['storage_rows'][target].get('storage_bits', c['reg_specs'][target]['width']))
    if bits<=0: 
        return None
    cand = DerivedStateCandidate(
        target = target, source = source, reachable_pairs = tuple(sorted(reachable)), mapping = tuple(sorted(mapping.items())), 
        expression = expr, expression_kind = kind, removed_storage_bits = bits, 
        source_width = int(c['reg_specs'][source]['width']), target_width = int(c['reg_specs'][target]['width']), 
        transition_checks = transition_checks, iterations = iterations, 
        outcome_readers = tuple(sorted(c['out_readers'].get(target, set()))), 
        predicate_users = tuple(sorted(c['pred_users'].get(target, set()))), proof_context = context)
    return asdict(cand)


def main()->int: 
    ap = argparse.ArgumentParser(description = 'Parallel deterministic implementation of the proof-identical simple derived-state pair analysis.')
    ap.add_argument('--complete-ir', type = Path, required = True)
    ap.add_argument('--baseline-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--json-output', type = Path)
    ap.add_argument('--jobs', type = int, default = min(12, max(1, os.cpu_count() or 1)))
    a = ap.parse_args()
    complete = json.loads(a.complete_ir.read_text())
    base = json.loads(a.baseline_ir.read_text())
    transitions = _group_transitions(complete)
    domains = _domain_map(base)
    startup = _startup_values(complete)
    predicates = _predicate_map(complete)
    pred_users, out_readers = _use_summary(base)
    storage_rows = {str(x['register']): x for x in base['storage_optimization']['register_storage']}
    reg_specs = {str(r['id']): r for r in complete['architectural_registers']}
    eligible = [rid for rid, spec in reg_specs.items() if spec['kind'] not in ('GPIO',) and rid in domains and int(storage_rows.get(rid, {}).get('storage_bits', spec['width']))>0 and len(domains[rid])<=8]
    pairs = []
    for source in eligible: 
        for target in eligible: 
            if source == target: 
                continue
            if out_readers.get(target): 
                continue
            if int(storage_rows[target].get('storage_bits', reg_specs[target]['width']))>2: 
                continue
            if len(domains[source])*len(domains[target])>4096: 
                continue
            pairs.append((source, target))
    ctx = {'transitions': transitions, 'domains': domains, 'startup': startup, 'predicates': predicates, 'pred_users': pred_users, 'out_readers': out_readers, 'storage_rows': storage_rows, 'reg_specs': reg_specs}
    jobs = max(1, min(int(a.jobs), len(pairs) or 1))
    if jobs == 1: 
        init_worker(ctx)
        raw = [analyze_pair(x) for x in pairs]
    else: 
        mpc = mp.get_context('fork')
        with ProcessPoolExecutor(max_workers = jobs, mp_context = mpc, initializer = init_worker, initargs = (ctx,)) as ex: 
            raw = list(ex.map(analyze_pair, pairs, chunksize = 1))
    candidates = [x for x in raw if x is not None]
    complexity = {'IDENTITY': 0, 'BIT_SELECT': 0, 'NONZERO': 1, 'IS_ZERO': 1, 'EQ_CONST': 1, 'NE_CONST': 1, 'NOT_BIT_SELECT': 1}
    candidates.sort(key = lambda c: (-int(c['removed_storage_bits']), complexity.get(str(c['expression_kind']), 9), str(c['target']), str(c['source'])))
    notes = [
        'Pair closures are conservative over-approximations of the corrected feasibility-proven source relation.', 
        'Third-register, scheduler, and GPIO guard constraints are ignored rather than assumed.', 
        'External outcome dependencies are enumerated from proof-backed domains or widened to the full target domain.', 
        'Automatic candidates must have a recognized low-cost derivation and no architectural outcome readers.', 
        'No protocol names, basic-block identity, transition ID special cases, or fixed GPIO bit numbers participate in discovery.', 
    ]
    payload = {'source_transitions': len(transitions), 'pair_candidates_checked': len(pairs), 'candidates': candidates, 'notes': notes}
    lines = ['DEDICATED EVENT SIMPLE DERIVED-STATE ANALYSIS', '='*88, 
        f'source transitions          : {len(transitions)}', f'pair candidates checked     : {len(pairs)}', f'accepted simple candidates  : {len(candidates)}', f'parallel workers            : {jobs}']
    for i, c in enumerate(candidates): 
        lines += [f"candidate[{i}] target/source : {c['target']} <- {c['source']}", f"candidate[{i}] expression    : {c['expression_kind']} {c['expression']}", f"candidate[{i}] mapping       : {c['mapping']}", f"candidate[{i}] reachable pair: {c['reachable_pairs']}", f"candidate[{i}] proof context : {c['proof_context']}", f"candidate[{i}] removed bits  : {c['removed_storage_bits']}", f"candidate[{i}] predicate use : {c['predicate_users']}", f"candidate[{i}] outcome reader: {c['outcome_readers']}"]
    lines += ['', 'Notes:']+[f'- {x}' for x in notes]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    if a.json_output: 
        a.json_output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines))
    return 0

if __name__ == '__main__': 
    raise SystemExit(main())
