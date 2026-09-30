#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, itertools, json
from pathlib import Path

NONPHYSICAL = {"CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED"}

def sha256(p: Path)->str: 
 h = hashlib.sha256()
 h.update(p.read_bytes())
 return h.hexdigest()

def refs(x): 
 out = set()
 if isinstance(x, list): 
  if len(x)>=2 and x[0] == 'REG': 
      out.add(str(x[1]))
      return out
  for y in x: 
      out |= refs(y)
 elif isinstance(x, dict): 
  for y in x.values(): 
      out |= refs(y)
 return out

def main(): 
 ap = argparse.ArgumentParser()
 ap.add_argument('--ir', type = Path, required = True)
 ap.add_argument('--quotient', type = Path, required = True)
 ap.add_argument('--legal-product', type = Path, required = True)
 ap.add_argument('--output', type = Path, required = True)
 ap.add_argument('--report', type = Path, required = True)
 a = ap.parse_args()
 ir = json.load(open(a.ir))
 q = json.load(open(a.quotient))
 lp = json.load(open(a.legal_product))
 if lp.get('proof_result')!='PASS' or not lp.get('states'): 
     raise SystemExit('FAIL legal product is not a nonempty PASS artifact')
 tracked = list(map(str, q['tracked_registers']))
 ti = {r: i for i, r in enumerate(tracked)}
 reps = {int(c['code']): tuple(map(int, c['representative'])) for c in q['classes']}
 sp = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
 state_fields = list(map(str, lp.get('state_tuple', [])))
 edge_fields = list(map(str, lp.get('edge_tuple', [])))
 if 'class' not in state_fields or 'class' not in edge_fields or 'next_class' not in edge_fields: 
     raise SystemExit('FAIL legal product tuple schema missing class/next_class')
 state_class_i = state_fields.index('class')
 edge_class_i = edge_fields.index('class')
 edge_next_class_i = edge_fields.index('next_class')
 reachable_classes = sorted({int(s[state_class_i]) for s in lp['states']})
 # Consumer discovery from current semantic IR: guard/predicate/outcome readers, excluding self recurrence.
 pred_expr = {str(p['id']): p['expression'] for p in ir['predicate_basis']}
 pred_users = {}
 for rid in tracked: 
  pids = [pid for pid, e in pred_expr.items() if rid in refs(e)]
  rules = []
  for r in ir['update_rules']: 
   used = [str(x['basis']) for x in r.get('enable', []) if str(x['basis']) in pids]
   if used: 
       rules.append({'rule_id': str(r['rule_id']), 'event_class': str(r['event_class']), 'predicates': used, 'target': str(r['target'])})
  outs = [str(r['rule_id']) for r in ir['update_rules'] if str(r.get('target'))!=rid and rid in refs(r.get('outcome'))]
  pred_users[rid] = {'predicate_rules': rules, 'outcome_rules': outs}

 # Only current physical 1-bit storage represented in quotient can be eliminated by this pass.
 targets = []
 for rid in tracked: 
  row = sp.get(rid, {})
  if int(row.get('storage_bits', 99)) == 1 and str(row.get('storage_kind')) not in NONPHYSICAL: 
   targets.append(rid)

 def val(code, rid): 
     return int(reps[int(code)][ti[rid]])
 # Candidate atoms are equality tests over other currently physical tracked registers.
 physical = [r for r in tracked if str(sp.get(r, {}).get('storage_kind')) not in NONPHYSICAL and int(sp.get(r, {}).get('storage_bits', 0))>0]
 atoms_by_target = {}
 for target in targets: 
  atoms = []
  for rid in physical: 
   if rid == target: 
       continue
   vals = sorted({val(c, rid) for c in reachable_classes})
   for v in vals: 
    atoms.append({'kind': 'EQ', 'register': rid, 'value': v, 'text': f'({rid}=={v})'})
  atoms_by_target[target] = atoms

 def atom_eval(atom, code): 
     return val(code, atom['register']) == int(atom['value'])
 def formula_eval(f, code): 
  if f['kind'] == 'ATOM': 
      return atom_eval(f['atom'], code)
  x = atom_eval(f['left'], code)
  y = atom_eval(f['right'], code)
  return (x and y) if f['kind'] == 'AND' else (x or y)
 def form_text(f): 
  if f['kind'] == 'ATOM': 
      return f['atom']['text']
  return f"({f['left']['text']} {f['kind']} {f['right']['text']})"

 results = []
 edges = lp['unique_edges']
 for target in targets: 
  atoms = atoms_by_target[target]
  forms = [{'kind': 'ATOM', 'atom': x} for x in atoms]
  # deterministic bounded 2-atom search; same-register contradictory/redundant pairs are allowed but rank later.
  for i, x in enumerate(atoms): 
   for y in atoms[i+1:]: 
    forms.append({'kind': 'AND', 'left': x, 'right': y})
    forms.append({'kind': 'OR', 'left': x, 'right': y})
  best = None
  checked = 0
  for f in forms: 
   checked+=1
   cm = 0
   for st in lp['states']: 
    c = int(st[state_class_i])
    if bool(val(c, target)) != bool(formula_eval(f, c)): 
     cm = 1
     break
   if cm: 
       continue
   # Explicit source/next edge proof. This intentionally does not rely on state-set closure alone.
   srcm = nextm = 0
   for e in edges: 
    sc = int(e[edge_class_i])
    nc = int(e[edge_next_class_i])
    if bool(val(sc, target)) != bool(formula_eval(f, sc)): 
        srcm+=1
        break
    if bool(val(nc, target)) != bool(formula_eval(f, nc)): 
        nextm+=1
        break
   if srcm or nextm: 
       continue
   # Rank: one atom, then AND, then OR; fewer distinct source regs; lexical determinism.
   regs = {f['atom']['register']} if f['kind'] == 'ATOM' else {f['left']['register'], f['right']['register']}
   rank = ({'ATOM': 0, 'AND': 1, 'OR': 2}[f['kind']], len(regs), form_text(f))
   row = (rank, f)
   if best is None or row[0]<best[0]: 
       best = row
  out = {'register': target, 'source_storage_bits': 1, 'consumer_discovery': pred_users[target], 'formulas_checked': checked}
  if best is None: 
   out.update({'classification': 'NO_BOUNDED_FORMULA'})
  else: 
   f = best[1]
   out.update({'classification': 'PASS_CANONICAL_ELIMINATION', 'formula': f, 'formula_text': form_text(f), 'current_states_checked': len(lp['states']), 'current_mismatches': 0, 'edge_instances_checked': len(edges), 'source_mismatches': 0, 'next_mismatches': 0, 'reachable_classes': len(reachable_classes)})
  results.append(out)

 payload = {
  'version': 'observation-local-state-specialization-v1-bounded-generic', 
  'inputs': {'ir_sha256': sha256(a.ir), 'quotient_sha256': sha256(a.quotient), 'legal_product_sha256': sha256(a.legal_product)}, 
  'proof_model': 'SCHEMA_DRIVEN_LEGAL_PRODUCT_CANONICAL_CLASS_CURRENT_AND_NEXT', 
  'legal_product': {'states': len(lp['states']), 'raw_edges': lp.get('raw_edge_count'), 'stored_unique_edges': len(edges), 'reachable_classes': len(reachable_classes)}, 
  'search_space': 'other physical tracked register == reachable constant; one atom or two-atom AND/OR', 
  'results': results, 
 }
 a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
 lines = ['BIO2RTL GENERIC OBSERVATION-LOCAL STATE SPECIALIZATION', '='*96, f"legal product: {len(lp['states'])} states / {lp.get('raw_edge_count')} raw edges / {len(edges)} stored unique edges"]
 for r in results: 
  lines.append(f"{r['register']}: {r['classification']}"+(f" -> {r.get('formula_text')}" if r.get('formula_text') else ''))
 lines.append('RESULT: PASS' if any(r['classification'].startswith('PASS') for r in results) else 'RESULT: NO CANDIDATE')
 a.report.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
if __name__ == '__main__': 
    main()
