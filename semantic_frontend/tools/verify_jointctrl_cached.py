#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_joint_control_domain import JointControlDomainAnalysis, attach_joint_control_domain
from bio2rtl.dedicated_event_semantic_guard_minimization_verifier import verify_semantic_guard_minimization


def load(p: Path) -> JointControlDomainAnalysis: 
    d = json.loads(p.read_text())
    return JointControlDomainAnalysis(
        source_transitions = int(d['source_transitions']), core_registers = tuple(d['core_registers']), 
        context_registers = tuple(d['context_registers']), tracked_registers = tuple(d['tracked_registers']), 
        raw_domain_product = int(d['raw_domain_product']), reachable_states = int(d['reachable_states']), 
        iterations = int(d['iterations']), transition_checks = int(d['transition_checks']), 
        predicate_ids = tuple(d['predicate_ids']), 
        truth_patterns = tuple(tuple((str(x[0]), bool(x[1])) for x in s) for s in d['truth_patterns']), 
        state_tuples = tuple(tuple(map(int, x)) for x in d['state_tuples']), 
        fallback_external_updates = int(d['fallback_external_updates']), notes = tuple(d['notes']))


def _norm(x): 
    return {
        'proof_model': x.get('proof_model'), 'core_registers': x.get('core_registers'), 
        'context_registers': x.get('context_registers'), 'tracked_registers': x.get('tracked_registers'), 
        'raw_domain_product': x.get('raw_domain_product'), 'reachable_states': x.get('reachable_states'), 
        'predicate_ids': x.get('predicate_ids'), 'truth_patterns': x.get('truth_patterns')}


def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Verify a cached-domain semantic-guard candidate.')
    ap.add_argument('--expression-minimized-complete-ir', type = Path, required = True)
    ap.add_argument('--joint-domain', type = Path, required = True)
    ap.add_argument('--candidate-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    complete = json.loads(a.expression_minimized_complete_ir.read_text())
    cand = json.loads(a.candidate_ir.read_text())
    an = load(a.joint_domain)
    proof = attach_joint_control_domain(complete, an)
    metadata_ok = _norm(proof['joint_control_domain']) == _norm(cand.get('joint_control_domain', {}))
    r = verify_semantic_guard_minimization(proof, cand)
    errors = []
    if not metadata_ok: 
        errors.append('candidate joint-control metadata differs from frozen-domain artifact')
    if not r.semantic_pass: 
        errors.append('semantic guard relation verification failed')
    lines = ['CACHED JOINTCTRL SEMANTIC VERIFICATION', '='*88, 
           f'joint predicates                      : {len(an.predicate_ids)}', 
           f'joint reachable tuples                : {an.reachable_states}', 
           f'checked source rows                    : {r.checked_source_rows}', 
           f'source non-HOLD rows                  : {r.source_nonhold_rows}', 
           f'minimized rules                       : {r.minimized_rules}', 
           f'uncovered non-HOLD rows               : {r.uncovered_nonhold_rows}', 
           f'different-outcome overlap rows        : {r.different_outcome_overlap_rows}', 
           f'HOLD rows spuriously updated          : {r.hold_rows_spuriously_updated}', 
           f'minimized cross-outcome overlap pairs : {r.minimized_cross_outcome_overlap_pairs}', 
           f'metadata reproduction                 : {"PASS" if metadata_ok else "FAIL"}', 
           f'errors                                : {len(errors)}', 
           f'RESULT: {"PASS" if not errors else "FAIL"}']
    if errors: 
        lines += [f'ERROR: {e}' for e in errors]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0 if not errors else 1
if __name__ == '__main__': 
    raise SystemExit(main())
