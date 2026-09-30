#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_relation_verifier import verify_dedicated_event_relation, result_to_dict


def main()->int: 
    ap = argparse.ArgumentParser(description = 'Independently verify a raw corrected Dedicated Event relation and emit a promoted immutable IR copy.')
    ap.add_argument('--fse', type = Path, required = True)
    ap.add_argument('--raw-ir', type = Path, required = True)
    ap.add_argument('--output-ir', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    f = json.loads(a.fse.read_text())
    ir = json.loads(a.raw_ir.read_text())
    r = verify_dedicated_event_relation(f, ir)
    d = result_to_dict(r)
    lines = ['CORRECTED DEDICATED EVENT RELATION VERIFICATION', '='*88, 
      f'source transitions              : {r.source_transitions}', 
      f'architectural registers         : {r.architectural_registers}', 
      f'architectural relation checks   : {r.architectural_relation_checks}', 
      f'architectural relation misses   : {r.architectural_relation_misses}', 
      f'architectural relation conflicts: {r.architectural_relation_conflicts}', 
      f'scheduler states                : {r.scheduler_states}', 
      f'scheduler relation checks       : {r.scheduler_relation_checks}', 
      f'scheduler relation failures     : {r.scheduler_relation_failures}', 
      f'scheduler unsatisfied rows      : {r.scheduler_unsatisfied_rows}', 
      f'structural pass                 : {"PASS" if r.structural_pass else "FAIL"}', 
      f'semantic pass                   : {"PASS" if r.semantic_pass else "FAIL"}', '', 
      'forbidden identity counts', '-'*88]
    lines += [f'{k:28}: {v}' for k, v in r.forbidden_identity_counts.items()]
    if r.failure_examples: 
      lines += ['', 'failure examples', '-'*88]+[json.dumps(x, sort_keys = True) for x in r.failure_examples]
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.report.write_text('\n'.join(lines)+'\n')
    a.report.with_suffix(a.report.suffix+'.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    if not (r.semantic_pass and r.structural_pass): 
      print('\n'.join(lines))
      return 1
    v = ir.setdefault('verification', {})
    v.update({'source_transitions': r.source_transitions, 'relation_checks': r.architectural_relation_checks, 
      'relation_misses': r.architectural_relation_misses, 'relation_conflicts': r.architectural_relation_conflicts, 
      'scheduler_relation_checks': r.scheduler_relation_checks, 'scheduler_relation_failures': r.scheduler_relation_failures, 
      'scheduler_unsatisfied_rows': r.scheduler_unsatisfied_rows, 'forbidden_identity_counts': r.forbidden_identity_counts, 
      'semantic_pass': r.semantic_pass, 'structural_pass': r.structural_pass})
    a.output_ir.parent.mkdir(parents = True, exist_ok = True)
    a.output_ir.write_text(json.dumps(ir, indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines))
    print(f'promoted_ir={a.output_ir}')
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
