#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from copy import deepcopy
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_derived_state import analyze_simple_derived_state
from bio2rtl.dedicated_event_reachability import _eval_expr

def semantic_view(d): 
 x = deepcopy(d)
 x.pop('storage_optimization', None)
 x.pop('version', None)
 return x

def main()->int: 
 ap = argparse.ArgumentParser()
 ap.add_argument('--complete-ir', type = Path, required = True)
 ap.add_argument('--baseline-ir', type = Path, required = True)
 ap.add_argument('--candidate-ir', type = Path, required = True)
 ap.add_argument('--output', type = Path, required = True)
 a = ap.parse_args()
 complete = json.loads(a.complete_ir.read_text())
 base = json.loads(a.baseline_ir.read_text())
 cand = json.loads(a.candidate_ir.read_text())
 errors = []
 if semantic_view(base)!=semantic_view(cand): 
     errors.append('abstract Dedicated Event semantic relation changed')
 an = analyze_simple_derived_state(complete, base)
 if not an.candidates: 
     expected = None
 else: 
     expected = an.candidates[0]
 meta = cand.get('storage_optimization', {}).get('derived_state_elimination', {})
 if expected: 
  if meta.get('target')!=expected.target or meta.get('source')!=expected.source: 
      errors.append('selected candidate differs from deterministic analysis ranking')
  if meta.get('expression')!=expected.expression: 
      errors.append('derived expression differs from analysis')
  if [tuple(x) for x in meta.get('mapping', [])]!=list(expected.mapping): 
      errors.append('derived mapping differs from analysis')
 row = {x['register']: x for x in cand['storage_optimization']['register_storage']}.get(expected.target if expected else '')
 if expected and (not row or row.get('storage_kind')!='DERIVED_EXPR' or int(row.get('storage_bits', -1))!=0): 
     errors.append('derived target is still physically stored')
 # Independently evaluate the emitted semantic expression on every source value
 # observed by the conservative invariant proof.
 expression_checks = 0
 if expected: 
  for src, dst in expected.mapping: 
   got = int(_eval_expr(expected.expression, {expected.source: int(src)}, {}, 0))
   expression_checks+=1
   if got!=dst: 
       errors.append(f'expression mismatch source={src}: got={got} expected={dst}')
 reset = {x['register']: int(x['value'][1]) for x in complete['startup']['register_values']}
 reset_ok = (True if expected is None else bool(dict(expected.mapping).get(reset[expected.source]) == reset[expected.target]))
 if expected and not reset_ok: 
     errors.append('reset does not satisfy derived invariant')
 base_bits = int(base['storage_optimization']['natural_storage_bits'])
 cand_bits = int(cand['storage_optimization']['natural_storage_bits'])
 if expected and cand_bits!=base_bits-expected.removed_storage_bits: 
     errors.append('candidate storage bit accounting mismatch')
 if expected is None: 
  if cand_bits!=base_bits: 
      errors.append('N/A candidate changed storage bit accounting')
  if meta.get('status')!='N_A': 
      errors.append('N/A candidate metadata absent')
 lines = ['DERIVED47 INDEPENDENT VERIFICATION', '='*88, 
        f'source transitions                 : {an.source_transitions}', 
        f'pair candidates checked            : {an.pair_candidates_checked}', 
        f'reproduced candidates              : {len(an.candidates)}', 
        f'selected target/source             : {meta.get("target")} <- {meta.get("source")}', 
        f'proof model                        : {meta.get("proof_model")}', 
        f'expression checks                  : {expression_checks}', 
        f'reset invariant                    : {"PASS" if reset_ok else "FAIL"}', 
        f'baseline/candidate storage bits    : {base_bits} / {cand_bits}', 
        f'semantic relation unchanged        : {"PASS" if semantic_view(base)==semantic_view(cand) else "FAIL"}', 
        f'errors                             : {len(errors)}', 
        f'RESULT: {"PASS" if not errors else "FAIL"}']
 lines += [f'ERROR: {e}' for e in errors]
 a.output.parent.mkdir(parents = True, exist_ok = True)
 a.output.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
 return 0 if not errors else 1
if __name__ == '__main__': 
    raise SystemExit(main())
