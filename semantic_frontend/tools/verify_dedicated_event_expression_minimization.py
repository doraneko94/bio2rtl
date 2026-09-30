#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_expression_minimization_verifier import verify_expression_minimization, result_to_dict

def main()->int: 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-ir', type = Path, required = True)
    ap.add_argument('--optimized-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.source_ir.read_text())
    dst = json.loads(a.optimized_ir.read_text())
    r = verify_expression_minimization(src, dst)
    lines = ['DEDICATED EVENT EXPRESSION EQUIVALENCE PROOF', '='*88, 
      f'predicate rows          : {r.predicate_rows}', f'outcome rows            : {r.outcome_rows}', f'startup rows            : {r.startup_rows}', 
      f'changed predicates      : {r.changed_predicates}', f'changed outcomes        : {r.changed_outcomes}', f'changed startup         : {r.changed_startup}', 
      f'evaluated assignments   : {r.evaluated_assignments}', f'failures                : {r.failures}']
    if r.failure_examples: 
        lines += ['', 'Failure examples']+[f'- {x}' for x in r.failure_examples]
    lines += [f"RESULT                  : {'PASS' if r.failures==0 else 'FAIL'}"]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(result_to_dict(r), indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines))
    return 0 if r.failures == 0 else 1
if __name__ == '__main__': 
    raise SystemExit(main())
