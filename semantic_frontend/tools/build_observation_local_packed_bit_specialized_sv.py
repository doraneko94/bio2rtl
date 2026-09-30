#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

def sha(p): 
 h = hashlib.sha256()
 h.update(p.read_bytes())
 return h.hexdigest()
def atom_sv(a, widths): 
 r = str(a['register'])
 v = int(a['value'])
 w = int(widths[r])
 return f"(r_{r} == {w}'d{v})"
def form_sv(f, widths): 
 if f['kind'] == 'ATOM': 
     return atom_sv(f['atom'], widths)
 op = '&' if f['kind'] == 'AND' else '|'
 return f"({atom_sv(f['left'],widths)} {op} {atom_sv(f['right'],widths)})"

def main(): 
 ap = argparse.ArgumentParser()
 ap.add_argument('--source-sv', type = Path, required = True)
 ap.add_argument('--ir', type = Path, required = True)
 ap.add_argument('--analysis', type = Path, required = True)
 ap.add_argument('--output-sv', type = Path, required = True)
 ap.add_argument('--metadata', type = Path, required = True)
 ap.add_argument('--report', type = Path, required = True)
 a = ap.parse_args()
 text = a.source_sv.read_text()
 ir = json.load(open(a.ir))
 ana = json.load(open(a.analysis))
 widths = {str(x['id']): int(x['width']) for x in ir['architectural_registers']}
 sp = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
 srcm = re.search(r'// Physical storage plan: 56 -> (\d+) bits[^\n]*', text)
 if ana.get('n_a') or not any(r.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION' for r in ana.get('results', [])): 
  srcbits = int(srcm.group(1)) if srcm else 0
  a.output_sv.parent.mkdir(parents = True, exist_ok = True)
  a.output_sv.write_text(text)
  meta = {'version': 'observation-local-packed-bit-specialized-sv-v1', 'n_a': True, 'source_storage_bits': srcbits, 'candidate_storage_bits': srcbits, 'applied': []}
  a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
  lines = ['BIO2RTL OBSERVATION-LOCAL PACKED-BIT SPECIALIZED SV', '='*96, 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: CANDIDATE GENERATED']
  a.report.write_text('\n'.join(lines)+'\n')
  print('\n'.join(lines))
  return 0
 if not srcm: 
     raise SystemExit('FAIL storage comment missing')
 srcbits = int(srcm.group(1))
 applied = []
 saved = 0
 byreg = {}
 for r in ana['results']: 
  if r.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION': 
      byreg.setdefault(str(r['register']), []).append(r)
 for rid, rows in sorted(byreg.items()): 
  spec = sp[rid]
  stored = list(map(int, spec.get('stored_bits', [])))
  semw = int(spec['semantic_width'])
  oldn = len(stored)
  remove_bits = {int(r['semantic_bit']): r for r in rows}
  remove_idx = [i for i, b in enumerate(stored) if b in remove_bits]
  if not remove_idx: 
      continue
  # Only suffix-packed removal is accepted here so packed indices remain stable.
  if remove_idx!=list(range(oldn-len(remove_idx), oldn)): 
      raise SystemExit(f'FAIL non-suffix packed removal unsupported {rid}: {remove_idx}')
  newstored = stored[:oldn-len(remove_idx)]
  newn = len(newstored)
  # Kept next-state bits must not depend on removed packed indices.
  for i in range(newn): 
   m = re.search(rf'^assign d_{re.escape(rid)}_packed\[{i}\] = (.*?);$', text, re.M)
   if not m: 
       raise SystemExit(f'FAIL missing kept d_{rid}_packed[{i}]')
   for j in remove_idx: 
    if re.search(rf'\br_{re.escape(rid)}_packed\[{j}\]', m.group(1)): 
        raise SystemExit(f'FAIL kept bit reads removed packed index {j}')
  # Rebuild semantic alias from kept packed bits plus proven replacement formulas.
  terms = []
  for i, b in enumerate(newstored): 
      terms.append(f"({{{semw-1}'b0, r_{rid}_packed[{i}]}} << {b})")
  replacements = {}
  for b, row in remove_bits.items(): 
   rhs = form_sv(row['formula'], widths)
   replacements[b] = rhs
   terms.append(f"({{{semw-1}'b0, {rhs}}} << {b})")
  old_alias = re.search(rf'^wire \[{semw-1}:0\] r_{re.escape(rid)} = .*?;$', text, re.M)
  if not old_alias: 
      raise SystemExit(f'FAIL semantic alias missing {rid}')
  text = text[:old_alias.start()]+f"wire [{semw-1}:0] r_{rid} = "+' | '.join(terms)+';'+text[old_alias.end():]
  text, n = re.subn(rf'logic \[{oldn-1}:0\] r_{re.escape(rid)}_packed;', f'logic [{newn-1}:0] r_{rid}_packed;', text, count = 1)
  if n!=1: 
      raise SystemExit(f'FAIL packed declaration {rid}')
  text, n = re.subn(rf'wire \[{oldn-1}:0\] d_{re.escape(rid)}_packed;', f'wire [{newn-1}:0] d_{rid}_packed;', text, count = 1)
  if n!=1: 
      raise SystemExit(f'FAIL packed d declaration {rid}')
  for j in sorted(remove_idx, reverse = True): 
   text, n = re.subn(rf'^assign d_{re.escape(rid)}_packed\[{j}\] = .*?;\n', '', text, count = 1, flags = re.M)
   if n!=1: 
       raise SystemExit(f'FAIL remove d packed index {j}')
  text, n = re.subn(rf'(r_{re.escape(rid)}_packed\s*<=\s*){oldn}\'d0;', rf"\g<1>{newn}'d0;", text, count = 1)
  if n!=1: 
      raise SystemExit(f'FAIL packed reset {rid}')
  for j in remove_idx: 
   if re.search(rf'\br_{re.escape(rid)}_packed\[{j}\]', text): 
       raise SystemExit(f'FAIL removed packed index remains {rid}[{j}]')
  saved+=len(remove_idx)
  applied.append({'register': rid, 'removed_semantic_bits': sorted(remove_bits), 'old_stored_bits': stored, 'new_stored_bits': newstored, 'replacements': replacements})
 if not applied: 
     raise SystemExit('FAIL no proven packed-bit specialization applied')
 dst = srcbits-saved
 text = re.sub(r'// Physical storage plan: 56 -> \d+ bits[^\n]*', f'// Physical storage plan: 56 -> {dst} bits via proof-backed observation-local packed-bit specialization.', text, count = 1)
 a.output_sv.write_text(text)
 meta = {'version': 'observation-local-packed-bit-specialized-sv-v1', 'source_storage_bits': srcbits, 'candidate_storage_bits': dst, 'source_sv_sha256': sha(a.source_sv), 'ir_sha256': sha(a.ir), 'analysis_sha256': sha(a.analysis), 'applied': applied}
 a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
 lines = ['BIO2RTL OBSERVATION-LOCAL PACKED-BIT SPECIALIZED SV', '='*96, f'storage: {srcbits} -> {dst} bits']
 for x in applied: 
     lines.append(f"{x['register']}: {x['old_stored_bits']} -> {x['new_stored_bits']} replacements={x['replacements']}")
 lines.append('RESULT: CANDIDATE GENERATED')
 a.report.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
if __name__ == '__main__': 
    main()
