#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_joint_control_domain import analyze_joint_control_domain, analysis_to_dict


def main()->int: 
    ap = argparse.ArgumentParser()
    ap.add_argument('--complete-ir', type = Path, required = True)
    ap.add_argument('--baseline-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--json-output', type = Path)
    a = ap.parse_args()
    complete = json.loads(a.complete_ir.read_text())
    baseline = json.loads(a.baseline_ir.read_text())
    an = analyze_joint_control_domain(complete, baseline)
    lines = ['DEDICATED EVENT JOINT CONTROL DOMAIN ANALYSIS', '='*92, 
           f'source transitions          : {an.source_transitions}', 
           f'core registers             : {list(an.core_registers)}', 
           f'context registers          : {list(an.context_registers)}', 
           f'tracked registers          : {list(an.tracked_registers)}', 
           f'raw domain product         : {an.raw_domain_product}', 
           f'reachable joint tuples     : {an.reachable_states}', 
           f'fixed-point iterations     : {an.iterations}', 
           f'transition checks          : {an.transition_checks}', 
           f'external-update fallbacks  : {an.fallback_external_updates}', 
           f'joint predicate ids        : {len(an.predicate_ids)}', 
           f'joint truth patterns       : {len(an.truth_patterns)}', 
           '', 'Notes:'] + [f'- {x}' for x in an.notes]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    if a.json_output: 
        a.json_output.write_text(json.dumps(analysis_to_dict(an), indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
