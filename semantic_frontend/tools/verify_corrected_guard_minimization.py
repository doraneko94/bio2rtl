#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_guard_minimization_verifier import verify_guard_minimization, result_to_dict


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-ir', type = Path, required = True)
    ap.add_argument('--minimized-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.source_ir.read_text())
    dst = json.loads(a.minimized_ir.read_text())
    r = verify_guard_minimization(src, dst)
    d = result_to_dict(r)
    lines = [
      'DEDICATED EVENT GUARD MINIMIZATION PROOF', '='*80, 
      f"source relation rows               : {r.source_relation_rows}", 
      f"source materialized rows           : {r.source_materialized_rows}", 
      f"minimized rules                    : {r.minimized_rules}", 
      f"checked source rows                : {r.checked_source_rows}", 
      f"uncovered non-HOLD rows            : {r.uncovered_nonhold_rows}", 
      f"different-outcome overlap rows     : {r.different_outcome_overlap_rows}", 
      f"HOLD rows spuriously updated       : {r.hold_rows_spuriously_updated}", 
      f"cross-outcome minimized overlaps   : {r.minimized_cross_outcome_overlap_pairs}", 
      f"semantic pass                      : {'PASS' if r.semantic_pass else 'FAIL'}", 
    ]
    if r.failure_examples: 
      lines += ['', 'Failure examples', '-'*80] + [json.dumps(x, sort_keys = True) for x in r.failure_examples]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines))
    return 0 if r.semantic_pass else 1
if __name__ == '__main__': 
    raise SystemExit(main())
