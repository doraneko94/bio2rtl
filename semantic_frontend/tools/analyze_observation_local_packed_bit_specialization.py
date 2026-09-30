#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, hashlib, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_reachability import _eval_expr
from tools.analyze_phase_local_state_elision import refs

NONPHYSICAL = {"CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED"}


def build_derived_rows(ir): 
 out = {}
 for sp in (ir.get('storage_optimization') or {}).get('register_storage', []): 
  if str(sp.get('storage_kind')) == 'DERIVED_EXPR' and sp.get('derived_expression') is not None: 
   out[str(sp['register'])] = sp['derived_expression']
 d = (ir.get('storage_optimization') or {}).get('derived_state_elimination')
 if isinstance(d, dict) and d.get('target') and d.get('expression'): 
  out.setdefault(str(d['target']), d['expression'])
 return out

def augment_derived(regs, derived, sched, gpio): 
 regs = dict(regs)
 pending = dict(derived)
 while pending: 
  changed = False
  for rid, ex in list(pending.items()): 
   if refs(ex).issubset(regs): 
    regs[rid] = int(_eval_expr(ex, regs, sched, gpio))
    del pending[rid]
    changed = True
  if not changed: 
      break
 return regs

def sha256(p: Path)->str: 
 h = hashlib.sha256()
 h.update(p.read_bytes())
 return h.hexdigest()

def possible_bit(expr, bit, storage): 
 if not isinstance(expr, list) or not expr: 
     return {0, 1}
 tag = expr[0]
 if tag == 'CONST': 
     return {(int(expr[1])>>bit)&1}
 if tag == 'REG': 
  rid = str(expr[1])
  row = storage.get(rid, {})
  vals = row.get('reachable_values_upper_bound')
  if vals is not None: 
      return {(int(v)>>bit)&1 for v in vals}
  sw = int(row.get('semantic_width', 32))
  return {0} if bit>=sw else {0, 1}
 if tag == 'GPIO_INPUT' or tag == 'SCHED_REG': 
     return {0, 1}
 if tag == 'OP': 
  op = str(expr[1])
  args = expr[2]
  if op in ('SHL', 'SHR') and len(args) == 2 and isinstance(args[1], list) and args[1][0] == 'CONST': 
   n = int(args[1][1])
   sb = bit-n if op == 'SHL' else bit+n
   return {0} if sb<0 else possible_bit(args[0], sb, storage)
  if op in ('AND', 'OR', 'XOR') and len(args) == 2: 
   A = possible_bit(args[0], bit, storage)
   B = possible_bit(args[1], bit, storage)
   if op == 'AND': 
       return {a&b for a in A for b in B}
   if op == 'OR': 
       return {a|b for a in A for b in B}
   return {a^b for a in A for b in B}
  return {0, 1}
 return {0, 1}

def main(): 
 ap = argparse.ArgumentParser()
 ap.add_argument('--ir', type = Path, required = True)
 ap.add_argument('--quotient', type = Path, required = True)
 ap.add_argument('--direct-table', type = Path, required = True)
 ap.add_argument('--legal-product', type = Path, required = True)
 ap.add_argument('--output', type = Path, required = True)
 ap.add_argument('--report', type = Path, required = True)
 a = ap.parse_args()
 ir = json.load(open(a.ir))
 q = json.load(open(a.quotient))
 tab = json.load(open(a.direct_table))
 lp = json.load(open(a.legal_product))
 if lp.get('proof_result')!='PASS': 
     raise SystemExit('FAIL fresh legal product is not PASS')
 tracked = list(map(str, q['tracked_registers']))
 ti = {r: i for i, r in enumerate(tracked)}
 reps = {int(c['code']): tuple(map(int, c['representative'])) for c in q['classes']}
 storage = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
 derived = build_derived_rows(ir)
 physical = [r for r in tracked if str(storage.get(r, {}).get('storage_kind')) not in NONPHYSICAL and int(storage.get(r, {}).get('storage_bits', 0))>0]
 # Fresh legal class/event graph from the product cache.  Product tuple
 # schemas are generic; derive field positions from edge_tuple rather than the
 # historical I2C fixed indices.
 edge_rows = lp.get('unique_edges', lp.get('edges', []))
 edge_tuple = list(map(str, lp.get('edge_tuple', [])))
 required_edge = {'class', 'event', 'next_class'}
 if not required_edge.issubset(edge_tuple): 
  raise SystemExit('not applicable: legal product has no class/event/next_class edge schema')
 ei = {name: edge_tuple.index(name) for name in required_edge}
 class_edges = sorted(set((int(e[ei['class']]), str(e[ei['event']]), int(e[ei['next_class']])) for e in edge_rows))
 outgoing = collections.defaultdict(list)
 for ce in class_edges: 
     outgoing[ce[0]].append(ce)

 # This optimization needs a framed/polling product with a discovered shared
 # counter pair, but it must not depend on protocol signal/register spellings.
 state_names = list(map(str, lp.get('state_tuple', [])))
 state_tuple = set(state_names)
 si = {n: i for i, n in enumerate(state_names)}
 topo = dict(lp.get('topology') or {})
 data_field = 'data' if 'data' in state_tuple else ('sda' if 'sda' in state_tuple else None)
 shared_field = 'shared_counter' if 'shared_counter' in state_tuple else ('PCOUNT' if 'PCOUNT' in state_tuple else None)
 proof_path = a.legal_product.parent/'counter_ownership_proof.json'
 counter_proof = json.load(open(proof_path)) if proof_path.exists() else {}
 pair = counter_proof.get('discovered_pair') or {}
 inc_reg = str(pair.get('inc_register', 'P06' if 'P06' in state_tuple else ''))
 dec_reg = str(pair.get('dec_register', 'P10' if 'P10' in state_tuple else ''))
 framed_fields = {'class', 'phase', 'busy', inc_reg, dec_reg}
 if data_field: 
     framed_fields.add(data_field)
 if shared_field: 
     framed_fields.add(shared_field)
 if (not data_field) or (not shared_field) or (not inc_reg) or (not dec_reg) or not framed_fields.issubset(state_tuple): 
  raise SystemExit('not applicable: no structurally discovered framed polling observation product')
 phase_source = str(topo.get('phase_source', 'PH00'))
 hist_source = str(topo.get('data_history_source', 'H00'))
 phase_bit = int(topo.get('phase_input_bit', 16))
 data_bit = int(topo.get('data_input_bit', 17))
 active = int(topo.get('active_phase_level', 1))
 # Full legal edges retain environmental source/post values.  Use them rather
 # than reconstructing a protocol-specific bus event by name.
 full_by_class_event = collections.defaultdict(list)
 for e in edge_rows: 
     full_by_class_event[(int(e[edge_tuple.index('class')]), str(e[edge_tuple.index('event')]))].append(e)
 def product_env(e): 
  ph = int(e[edge_tuple.index('phase')])
  dat = int(e[edge_tuple.index(data_field)])
  nph = int(e[edge_tuple.index('next_phase')])
  ndat = int(e[edge_tuple.index('next_'+data_field)])
  sampled = ndat if ph!=active and nph == active else dat
  sched = {phase_source: ph, hist_source: sampled}
  gpio = ((nph&1)<<phase_bit)|((ndat&1)<<data_bit)
  return sched, gpio

 # Candidate packed registers: only semantically stored bits are considered.
 packed = []
 for rid, row in storage.items(): 
  if str(row.get('storage_kind')) == 'PACKED_MASK_BITS' and row.get('stored_bits'): 
   packed.append((rid, [int(x) for x in row['stored_bits']]))

 def cval(code, rid): 
     return int(reps[int(code)][ti[rid]])
 def atom_eval(atom, code): 
     return cval(code, atom['register']) == int(atom['value'])
 def f_eval(f, code): 
  if f['kind'] == 'ATOM': 
      return atom_eval(f['atom'], code)
  x = atom_eval(f['left'], code)
  y = atom_eval(f['right'], code)
  return (x and y) if f['kind'] == 'AND' else (x or y)
 def f_text(f): 
  if f['kind'] == 'ATOM': 
      return f"({f['atom']['register']}=={f['atom']['value']})"
  return f"(({f['left']['register']}=={f['left']['value']}) {f['kind']} ({f['right']['register']}=={f['right']['value']}))"

 results = []
 for rid, bits in packed: 
  # Discover direct-FSM guard consumers of this semantic register.
  reader_rows = []
  reader_classes_all = set()
  for cs, evs in tab['rows'].items(): 
   for ev, ents in evs.items(): 
    for ent in ents: 
     if any(rid in refs(g.get('expression')) for g in ent.get('guard', [])): 
      reader_rows.append((int(cs), str(ev), ent))
      reader_classes_all.add(int(cs))
  if not reader_rows: 
      continue

  # Observation contexts: legal product source states where all non-target guard
  # terms that are concretely known from canonical tracked/counter/derived state pass.
  obs_classes = set()
  obs_instances = set()
  for st in lp['states']: 
   c = int(st[si['class']])
   incv = int(st[si[inc_reg]])
   decv = int(st[si[dec_reg]])
   sharedv = int(st[si[shared_field]])
   for rc, ev, ent in reader_rows: 
    if rc!=c: 
        continue
    # Match legal environmental rows for this exact product source state.
    legal_envs = []
    for e in full_by_class_event.get((c, ev), []): 
     if all(int(e[edge_tuple.index(n)]) == int(st[si[n]]) for n in state_names if n in edge_tuple): 
      legal_envs.append(e)
    if not legal_envs: 
        continue
    env_ok = False
    for e in legal_envs: 
     sched, gpio = product_env(e)
     regs = {r: cval(c, r) for r in tracked}
     regs.update({inc_reg: incv, dec_reg: decv})
     regs = augment_derived(regs, derived, sched, gpio)
     ok = True
     for g in ent.get('guard', []): 
      rr = refs(g.get('expression'))
      if rid in rr: 
          continue
      if rr.issubset(regs.keys()): 
       if bool(_eval_expr(g['expression'], regs, sched, gpio))!=bool(g['polarity']): 
           ok = False
           break
      elif not rr: 
       if bool(_eval_expr(g['expression'], regs, sched, gpio))!=bool(g['polarity']): 
           ok = False
           break
      # Unknown state refs are intentionally dropped -> conservative widening.
     if ok: 
         env_ok = True
         break
    if env_ok: 
     obs_classes.add(c)
     obs_instances.add((c, decv, sharedv))

  # Equality atoms from other physical tracked state; bounded alternative synthesis.
  atoms = []
  for ar in physical: 
   vals = sorted({cval(c, ar) for c in obs_classes}) if obs_classes else []
   for v in vals: 
       atoms.append({'register': ar, 'value': v})
  forms = [{'kind': 'ATOM', 'atom': x} for x in atoms]
  for i, x in enumerate(atoms): 
   for y in atoms[i+1:]: 
    forms.append({'kind': 'AND', 'left': x, 'right': y})
    forms.append({'kind': 'OR', 'left': x, 'right': y})

  for bit in bits: 
   # Map transition IDs to abstract next-bit values using IR update semantics.
   tid_effect = {}
   updating = []
   for rule in ir['update_rules']: 
    if str(rule.get('target'))!=rid: 
        continue
    vals = sorted(possible_bit(rule['outcome'], bit, storage))
    for tid in rule.get('source_transition_ids', []): 
        tid_effect[str(tid)] = tuple(vals)
    updating.append({'rule_id': str(rule['rule_id']), 'source_transition_ids': list(map(str, rule.get('source_transition_ids', []))), 'possible_bit_values': vals})
   trans = collections.defaultdict(set)
   for c, ev, nc in class_edges: 
    for ent in tab['rows'].get(str(c), {}).get(ev, []): 
     if not any(int(x['code']) == nc for x in ent.get('next_map', [])): 
         continue
     tid = str(ent['transition_id'])
     trans[(c, ev, nc)].add(tid_effect.get(tid, ('HOLD',)))
   reset = (int(tab['reset_code']), 0)
   seen = {reset}
   qq = collections.deque([reset])
   while qq: 
    c, f = qq.popleft()
    for _, ev, nc in outgoing.get(c, []): 
     for eff in trans[(c, ev, nc)]: 
      vals = {f} if eff == ('HOLD',) else set(map(int, eff))
      for nf in vals: 
       ns = (nc, nf)
       if ns not in seen: 
           seen.add(ns)
           qq.append(ns)

   best = None
   checked = 0
   for form in forms: 
    checked+=1
    good = True
    for c in obs_classes: 
     want = bool(f_eval(form, c))
     flags = {f for cc, f in seen if cc == c}
     if not flags or any(bool(f)!=want for f in flags): 
         good = False
         break
    if not good: 
        continue
    regs = {form['atom']['register']} if form['kind'] == 'ATOM' else {form['left']['register'], form['right']['register']}
    rank = ({'ATOM': 0, 'AND': 1, 'OR': 2}[form['kind']], len(regs), f_text(form))
    if best is None or rank<best[0]: 
        best = (rank, form)
   row = {'register': rid, 'semantic_bit': bit, 'abstract_reachable_class_flag_states': len(seen), 'all_guard_reader_classes': sorted(reader_classes_all), 'observation_classes': sorted(obs_classes), 'observation_product_instances': len(obs_instances), 'formulas_checked': checked, 'update_bit_abstraction': updating}
   if best is None: 
       row['classification'] = 'NO_BOUNDED_FORMULA'
   else: 
    row.update({'classification': 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION', 'formula': best[1], 'formula_text': f_text(best[1]), 'read_context_mismatches': 0})
   results.append(row)

 payload = {'version': 'observation-local-packed-bit-specialization-v1-generic', 'inputs': {'ir_sha256': sha256(a.ir), 'quotient_sha256': sha256(a.quotient), 'direct_table_sha256': sha256(a.direct_table), 'legal_product_sha256': sha256(a.legal_product)}, 'proof_model': 'CONSERVATIVE_CLASS_FLAG_REACHABILITY_PLUS_GENERIC_LEGAL_PRODUCT_CONSUMER_CONTEXT', 'legal_product': {'states': len(lp['states']), 'raw_edges': lp.get('raw_edge_count'), 'class_event_edges': len(class_edges)}, 'results': results, 'notes': ['Packed-bit reachability is reconstructed from current IR source_transition_ids and current direct-FSM class edges.', 'Unknown non-target guard state is dropped when discovering observation contexts, conservatively widening them.', 'Alternative formulas are synthesized from other physical tracked-register equality atoms with bounded one/two-atom search.']}
 a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
 lines = ['BIO2RTL GENERIC OBSERVATION-LOCAL PACKED-BIT SPECIALIZATION', '='*96]
 for r in results: 
     lines.append(f"{r['register']}[{r['semantic_bit']}]: states={r['abstract_reachable_class_flag_states']} obs={r['observation_classes']} {r['classification']}"+(f" -> {r.get('formula_text')}" if r.get('formula_text') else ''))
 lines.append('RESULT: PASS' if any(r['classification'].startswith('PASS') for r in results) else 'RESULT: NO CANDIDATE')
 a.report.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
if __name__ == '__main__': 
    main()
