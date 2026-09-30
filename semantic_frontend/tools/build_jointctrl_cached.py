#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_joint_control_domain import JointControlDomainAnalysis, attach_joint_control_domain
from bio2rtl.dedicated_event_semantic_guard_minimization import minimize_dedicated_event_semantic_guards


def load_analysis(p: Path) -> JointControlDomainAnalysis: 
    d = json.loads(p.read_text())
    return JointControlDomainAnalysis(
        source_transitions = int(d['source_transitions']), 
        core_registers = tuple(d['core_registers']), context_registers = tuple(d['context_registers']), 
        tracked_registers = tuple(d['tracked_registers']), raw_domain_product = int(d['raw_domain_product']), 
        reachable_states = int(d['reachable_states']), iterations = int(d['iterations']), 
        transition_checks = int(d['transition_checks']), predicate_ids = tuple(d['predicate_ids']), 
        truth_patterns = tuple(tuple((str(x[0]), bool(x[1])) for x in sig) for sig in d['truth_patterns']), 
        state_tuples = tuple(tuple(map(int, x)) for x in d['state_tuples']), 
        fallback_external_updates = int(d['fallback_external_updates']), notes = tuple(d['notes']))


def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Semantic-guard minimization using a separately frozen joint-control domain.')
    ap.add_argument('--expression-minimized-complete-ir', type = Path, required = True)
    ap.add_argument('--joint-domain', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.expression_minimized_complete_ir.read_text())
    an = load_analysis(a.joint_domain)
    proof = attach_joint_control_domain(src, an)
    dst, st = minimize_dedicated_event_semantic_guards(proof)
    dst['joint_control_domain'] = deepcopy(proof['joint_control_domain'])
    dst['semantic_guard_minimization']['joint_control_domain_used'] = True
    dst['semantic_guard_minimization']['joint_control_domain_recomputed'] = False
    dst['semantic_guard_minimization']['joint_control_domain_stage_separate'] = True
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(dst, indent = 2, sort_keys = True)+'\n')
    lines = ['DEDICATED EVENT CACHED JOINT-CONTROL GUARD MINIMIZATION', '='*96, 
           f'joint predicate ids          : {len(an.predicate_ids)}', 
           f'joint tracked registers      : {len(an.tracked_registers)}', 
           f'joint reachable tuples       : {an.reachable_states}', 
           f'complete source rows         : {st.source_rules}', 
           f'complete non-HOLD rows       : {st.source_materialized_rules}', 
           f'minimized rules              : {st.minimized_rules}', 
           f'minimized guard literals     : {st.minimized_enable_literals}', 
           f'predicate basis used         : {st.predicate_basis_used}', 
           f'cross-outcome feasible overlap: {st.semantic_cross_outcome_overlaps}', 
           'RESULT                        : PASS']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
