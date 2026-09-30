#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

def sha(p): 
 h = hashlib.sha256()
 h.update(p.read_bytes())
 return h.hexdigest()

def render_atom(a, widths): 
 r = str(a['register'])
 v = int(a['value'])
 w = int(widths[r])
 return f"(r_{r} == {w}'d{v})"
def render(f, widths): 
 if f['kind'] == 'ATOM': 
     return render_atom(f['atom'], widths)
 op = '&' if f['kind'] == 'AND' else '|'
 return f"({render_atom(f['left'],widths)} {op} {render_atom(f['right'],widths)})"

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
 passes = [r for r in ana['results'] if r.get('classification') == 'PASS_CANONICAL_ELIMINATION']
 if not passes: 
  srcm = re.search(r'// Physical storage plan: 56 -> (\d+) bits[^\n]*', text)
  srcbits = int(srcm.group(1)) if srcm else 0
  a.output_sv.parent.mkdir(parents = True, exist_ok = True)
  a.output_sv.write_text(text)
  meta = {'version': 'observation-local-state-specialized-sv-v1', 'n_a': True, 'source_storage_bits': srcbits, 'candidate_storage_bits': srcbits, 'applied': [], 'proof_model': ana.get('proof_model')}
  a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
  lines = ['BIO2RTL OBSERVATION-LOCAL STATE SPECIALIZED SV', '='*96, 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: CANDIDATE GENERATED']
  a.report.write_text('\n'.join(lines)+'\n')
  print('\n'.join(lines))
  return 0
 widths = {str(x['id']): int(x['width']) for x in ir['architectural_registers']}
 sp = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
 srcm = re.search(r'// Physical storage plan: 56 -> (\d+) bits[^\n]*', text)
 if not srcm: 
     raise SystemExit('FAIL storage comment missing')
 srcbits = int(srcm.group(1))
 saved = 0
 applied = []
 for row in passes: 
  rid = str(row['register'])
  spec = sp[rid]
  if int(spec['storage_bits'])!=1: 
      continue
  semw = int(spec['semantic_width'])
  sk = str(spec['storage_kind'])
  f = row['formula']
  rhs = render(f, widths)
  # Reject self references by construction/defense.
  if re.search(rf'\br_{re.escape(rid)}\b', rhs): 
      raise SystemExit(f'FAIL self-referential replacement {rid}')
  if sk == 'NARROW_ZERO_EXTEND': 
   decl = f'logic [0:0] r_{rid}_store;\nwire [{semw-1}:0] r_{rid} = {{{semw-1}\'d0, r_{rid}_store}};'
   repl = f'// Legal-product canonical specialization: physical {rid} storage removed.\nwire [{semw-1}:0] r_{rid} = {{{semw-1}\'d0, {rhs}}};'
   if decl not in text: 
       raise SystemExit(f'FAIL declaration pattern missing for {rid}')
   text = text.replace(decl, repl, 1)
   text, n = re.subn(rf'^wire \[0:0\] d_{re.escape(rid)}_store;\nassign d_{re.escape(rid)}_store\[0\] = .*?;\n', '', text, count = 1, flags = re.M)
   if n!=1: 
       raise SystemExit(f'FAIL d cone missing for {rid}')
   text, n = re.subn(rf'^\s*r_{re.escape(rid)}_store <= 1\'d0;\s*\n', '', text, count = 1, flags = re.M)
   if n!=1: 
       raise SystemExit(f'FAIL reset missing for {rid}')
   text, n = re.subn(rf'^\s*r_{re.escape(rid)}_store <= d_{re.escape(rid)}_store;\s*\n', '', text, count = 1, flags = re.M)
   if n!=1: 
       raise SystemExit(f'FAIL sequential update missing for {rid}')
  elif sk == 'DIRECT' and semw == 1: 
   decl = f'logic [0:0] r_{rid};'
   repl = f'// Legal-product canonical specialization: physical {rid} storage removed.\nwire [0:0] r_{rid} = {rhs};'
   if decl not in text: 
       raise SystemExit(f'FAIL declaration missing for {rid}')
   text = text.replace(decl, repl, 1)
   text, n = re.subn(rf'^wire \[0:0\] d_{re.escape(rid)};\nassign d_{re.escape(rid)}\[0\] = .*?;\n', '', text, count = 1, flags = re.M)
   if n!=1: 
       raise SystemExit(f'FAIL d cone missing for {rid}')
   text, n = re.subn(rf'^\s*r_{re.escape(rid)} <= 1\'d0;\s*\n', '', text, count = 1, flags = re.M)
   assert n == 1
   text, n = re.subn(rf'^\s*r_{re.escape(rid)} <= d_{re.escape(rid)};\s*\n', '', text, count = 1, flags = re.M)
   assert n == 1
  else: 
      raise SystemExit(f'FAIL unsupported storage shape {rid} {sk} semw={semw}')
  # Original physical objects must be gone.
  if re.search(rf'\br_{re.escape(rid)}_store\b|\bd_{re.escape(rid)}(?:_store)?\b', text): 
      raise SystemExit(f'FAIL removed storage residue {rid}')
  saved+=1
  applied.append({'register': rid, 'formula': row['formula_text'], 'sv_expression': rhs})
 if not applied: 
     raise SystemExit('FAIL no specialization applied')
 dst = srcbits-saved
 text = re.sub(r'// Physical storage plan: 56 -> \d+ bits[^\n]*', f'// Physical storage plan: 56 -> {dst} bits via fresh legal-product canonical observation specialization.', text, count = 1)
 a.output_sv.write_text(text)
 meta = {'version': 'observation-local-state-specialized-sv-v1', 'source_storage_bits': srcbits, 'candidate_storage_bits': dst, 'source_sv_sha256': sha(a.source_sv), 'ir_sha256': sha(a.ir), 'analysis_sha256': sha(a.analysis), 'applied': applied, 'proof_model': ana['proof_model']}
 a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
 lines = ['BIO2RTL OBSERVATION-LOCAL STATE SPECIALIZED SV', '='*96, f'storage: {srcbits} -> {dst} bits']+[f"{x['register']} := {x['formula']}" for x in applied]+['RESULT: CANDIDATE GENERATED']
 a.report.write_text('\n'.join(lines)+'\n')
 print('\n'.join(lines))
if __name__ == '__main__': 
    main()
