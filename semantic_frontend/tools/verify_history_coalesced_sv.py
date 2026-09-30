#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path

def main(): 
 ap = argparse.ArgumentParser()
 ap.add_argument('--sv', type = Path, required = True)
 ap.add_argument('--metadata', type = Path, required = True)
 ap.add_argument('--output', type = Path, required = True)
 a = ap.parse_args()
 t = a.sv.read_text()
 m = json.loads(a.metadata.read_text())
 if m.get('n_a'): 
  txt = '\n'.join(['BIO2RTL HISTORY-COALESCED SV STRUCTURE CHECK', '='*96, 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: PASS'])+'\n'
  a.output.write_text(txt)
  print(txt, end = '')
  return 0
 ra, rb = m['registers']
 h = m['history_register']
 w = int(m['width'])
 checks = {
  'history_storage_decl': f'logic [{w-1}:0] r_{h};' in t, 
  'first_alias': f'wire [{w-1}:0] r_{ra} = r_{h};' in t, 
  'second_alias': f'wire [{w-1}:0] r_{rb} = r_{h};' in t, 
  'old_storage_removed': f'logic [{w-1}:0] r_{ra};' not in t and f'logic [{w-1}:0] r_{rb};' not in t, 
  'merged_next_decl': f'wire [{w-1}:0] d_{h};' in t, 
  'old_next_removed': f'wire [{w-1}:0] d_{ra};' not in t and f'wire [{w-1}:0] d_{rb};' not in t, 
  'single_reset': t.count(f'r_{h} <=') == 2, # reset + active update
  'storage_comment': f"-> {m['candidate_storage_bits']} bits" in t, 
  'balanced_braces': t.count('{') == t.count('}'), 
  'balanced_parens': t.count('(') == t.count(')'), 
 }
 ok = all(checks.values())
 lines = ['BIO2RTL HISTORY-COALESCED SV STRUCTURE CHECK', '='*96]+[f'{k:32s}: {"PASS" if v else "FAIL"}' for k, v in checks.items()]+['', f'RESULT: {"PASS" if ok else "FAIL"}']
 txt = '\n'.join(lines)+'\n'
 a.output.write_text(txt)
 print(txt, end = '')
 return 0 if ok else 1
if __name__ == '__main__': 
    raise SystemExit(main())
