#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, hashlib
from pathlib import Path

def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()
PAT = r'(?:act_A\d+|en_E\d+|pred_B\d+)'
# Generated decode declarations/assignments are intentionally one-line pairs.
def main(): 
 ap = argparse.ArgumentParser()
 for n in ['source', 'output', 'meta', 'report']: 
     ap.add_argument('--'+n, type = Path, required = True)
 a = ap.parse_args()
 s = a.source.read_text()
 removed = []
 rounds = []
 while True: 
  names = sorted(set(re.findall(r'\b'+PAT+r'\b', s)))
  counts = {n: len(re.findall(r'\b'+re.escape(n)+r'\b', s)) for n in names}
  dead = [n for n in names if counts[n] == 2 and re.search(r'^logic\s+'+re.escape(n)+r'\s*;\s*$', s, re.M) and re.search(r'^assign\s+'+re.escape(n)+r'\s*=.*?;\s*$', s, re.M)]
  if not dead: 
      break
  rounds.append(dead[:])
  for n in dead: 
   s = re.sub(r'^logic\s+'+re.escape(n)+r'\s*;\s*\n', '', s, flags = re.M)
   s = re.sub(r'^assign\s+'+re.escape(n)+r'\s*=.*?;\s*\n', '', s, flags = re.M)
   removed.append(n)
 a.output.write_text(s)
 left = {p: len(set(re.findall(r'\b'+p+r'\d+\b', s))) for p in ['act_A', 'en_E', 'pred_B']}
 meta = {'version': 'generated-decode-dce-v1', 'source_sha256': sha(a.source), 'output_sha256': sha(a.output), 'rounds': len(rounds), 'removed_count': len(removed), 'removed': removed, 'remaining': left, 'result': 'PASS'}
 a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
 rep = (f"BIO2RTL GENERATED DECODE DCE\n{'='*88}\nsource bytes: {a.source.stat().st_size}\noutput bytes: {a.output.stat().st_size}\nrounds: {len(rounds)}\nremoved: {len(removed)}\nremaining: {left}\nRESULT: PASS\n")
 a.report.write_text(rep)
 print(rep, end = '')
if __name__ == '__main__': 
    main()
