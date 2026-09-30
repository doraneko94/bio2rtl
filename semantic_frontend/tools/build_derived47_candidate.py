#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_derived_state import DerivedStateCandidate, analyze_simple_derived_state, apply_derived_state_candidate

def main()->int: 
 ap = argparse.ArgumentParser()
 ap.add_argument('--complete-ir', type = Path, required = True)
 ap.add_argument('--baseline-ir', type = Path, required = True)
 ap.add_argument('--analysis-json', type = Path)
 ap.add_argument('--output', type = Path, required = True)
 ap.add_argument('--report', type = Path, required = True)
 a = ap.parse_args()
 complete = json.loads(a.complete_ir.read_text())
 base = json.loads(a.baseline_ir.read_text())
 if a.analysis_json: 
  raw = json.loads(a.analysis_json.read_text())
  rows = raw.get('candidates', [])
  if not rows: 
   out = base
   out.setdefault('storage_optimization', {})['derived_state_elimination'] = {'status': 'N_A', 'reason': 'no proof-backed simple derived-state candidate'}
   a.output.parent.mkdir(parents = True, exist_ok = True)
   a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
   lines = ['DEDICATED EVENT DERIVED-STATE CANDIDATE', '='*88, 'candidate                  : N/A', 'semantic relation rewrite  : NONE', 'storage plan rewrite       : NONE', 'RESULT: PASS / N_A']
   a.report.write_text('\n'.join(lines)+'\n')
   print('\n'.join(lines))
   return 0
  q = rows[0]
  c = DerivedStateCandidate(
    target = str(q['target']), source = str(q['source']), 
    reachable_pairs = tuple(tuple(map(int, x)) for x in q['reachable_pairs']), 
    mapping = tuple(tuple(map(int, x)) for x in q['mapping']), 
    expression = q['expression'], expression_kind = str(q['expression_kind']), 
    removed_storage_bits = int(q['removed_storage_bits']), 
    source_width = int(q['source_width']), target_width = int(q['target_width']), 
    transition_checks = int(q['transition_checks']), iterations = int(q['iterations']), 
    outcome_readers = tuple(map(str, q.get('outcome_readers', []))), 
    predicate_users = tuple(map(str, q.get('predicate_users', []))), 
    proof_context = tuple(map(str, q.get('proof_context', []))), 
 )
 else: 
  an = analyze_simple_derived_state(complete, base)
  if not an.candidates: 
   out = base
   out.setdefault('storage_optimization', {})['derived_state_elimination'] = {'status': 'N_A', 'reason': 'no proof-backed simple derived-state candidate'}
   a.output.parent.mkdir(parents = True, exist_ok = True)
   a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
   lines = ['DEDICATED EVENT DERIVED-STATE CANDIDATE', '='*88, 'candidate                  : N/A', 'semantic relation rewrite  : NONE', 'storage plan rewrite       : NONE', 'RESULT: PASS / N_A']
   a.report.write_text('\n'.join(lines)+'\n')
   print('\n'.join(lines))
   return 0
  # Deterministic generic ranking lives in the analyzer; the first candidate is
  # the maximum-bit, minimum-expression-complexity safe candidate.
  c = an.candidates[0]
 out = apply_derived_state_candidate(base, c)
 a.output.parent.mkdir(parents = True, exist_ok = True)
 a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
 lines = ['DEDICATED EVENT DERIVED47 CANDIDATE', '='*88, 
        f'target/source              : {c.target} <- {c.source}', 
        f'expression kind            : {c.expression_kind}', 
        f'expression                 : {c.expression}', 
        f'mapping                    : {list(c.mapping)}', 
        f'proof context              : {list(c.proof_context)}', 
        f'reachable pair upper bound : {list(c.reachable_pairs)}', 
        f'removed storage bits       : {c.removed_storage_bits}', 
        f'baseline storage bits      : {base["storage_optimization"]["natural_storage_bits"]}', 
        f'candidate storage bits     : {out["storage_optimization"]["natural_storage_bits"]}', 
        f'predicate users            : {list(c.predicate_users)}', 
        f'architectural readers      : {list(c.outcome_readers)}']
 a.report.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
 return 0
if __name__ == '__main__': 
    raise SystemExit(main())
