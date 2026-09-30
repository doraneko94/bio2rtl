#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-sv', type = Path, required = True)
    ap.add_argument('--analysis', type = Path, required = True)
    ap.add_argument('--source-storage-bits', type = int, required = True)
    ap.add_argument('--output-sv', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    text = a.source_sv.read_text()
    ana = json.loads(a.analysis.read_text())
    applied = []
    for c in ana.get('candidates', []): 
        rid = str(c['register'])
        old = int(c['source_bits'])
        new = int(c['candidate_bits'])
        removed = list(map(int, c['removed_high_bits']))
        # Current source must still have the direct vector form analyzed.
        pat = rf'logic\s+\[{old-1}:0\]\s+r_{re.escape(rid)}\s*;'
        if not re.search(pat, text): 
            continue
        # Verify kept next-state equations do not depend on removed source bits or on whole vector.
        for b in range(new): 
            m = re.search(rf'assign\s+d_{re.escape(rid)}\[{b}\]\s*=\s*(.*?);', text, flags = re.S)
            if not m: 
                raise SystemExit(f'FAIL missing d_{rid}[{b}]')
            rhs = m.group(1)
            for rb in removed: 
                if re.search(rf'\br_{re.escape(rid)}\[{rb}\]', rhs): 
                    raise SystemExit(f'FAIL kept d_{rid}[{b}] reads removed bit {rb}')
            # A bare whole-vector reference would make bit dependence ambiguous.
            tmp = re.sub(rf'\br_{re.escape(rid)}\[\d+\]', '', rhs)
            if re.search(rf'\br_{re.escape(rid)}\b', tmp): 
                raise SystemExit(f'FAIL kept d_{rid}[{b}] has whole-vector source reference')
        text = re.sub(pat, f'logic [{new-1}:0] r_{rid};', text, count = 1)
        text, n = re.subn(rf'wire\s+\[{old-1}:0\]\s+d_{re.escape(rid)}\s*;', f'wire [{new-1}:0] d_{rid};', text, count = 1)
        if n!=1: 
            raise SystemExit(f'FAIL d_{rid} declaration')
        for rb in removed: 
            text, n = re.subn(rf'^assign\s+d_{re.escape(rid)}\[{rb}\]\s*=.*?;\s*\n', '', text, count = 1, flags = re.M)
            if n!=1: 
                raise SystemExit(f'FAIL remove d_{rid}[{rb}]')
        # Reset literal width. Reset value must be zero for suffix removal in this pass.
        text, n = re.subn(rf'(r_{re.escape(rid)}\s*<=\s*){old}\'d0\s*;', rf"\g<1>{new}'d0;", text, count = 1)
        if n!=1: 
            raise SystemExit(f'FAIL zero reset for {rid}')
        # Preserve full semantic-width debug/stack aliases by zero extension.
        alias_pat = rf'wire\s+\[{old-1}:0\]\s+(\w+)\s*=\s*r_{re.escape(rid)}\s*;'
        text = re.sub(alias_pat, lambda m: f"wire [{old-1}:0] {m.group(1)} = {{{old-new}'d0, r_{rid}}};", text)
        # Removed bit must not remain as an explicit bit select.
        for rb in removed: 
            if re.search(rf'\br_{re.escape(rid)}\[{rb}\]', text): 
                raise SystemExit(f'FAIL remaining removed bit reference {rid}[{rb}]')
        applied.append(c)
    if not applied: 
        newbits = a.source_storage_bits
        a.output_sv.parent.mkdir(parents = True, exist_ok = True)
        a.output_sv.write_text(text)
        meta = {'version': 'dead-high-bit-elided-sv-v1', 'source_storage_bits': a.source_storage_bits, 'candidate_storage_bits': newbits, 'applied': [], 
              'n_a': True, 'checks': ['no candidate applicable; identity transform']}
        a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL DEAD HIGH-BIT ELISION', '='*88, f'storage: {a.source_storage_bits} -> {newbits} bits', 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: CANDIDATE GENERATED']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    newbits = a.source_storage_bits-sum(int(c['source_bits'])-int(c['candidate_bits']) for c in applied)
    text = re.sub(r'// Physical storage plan: 56 -> \d+ bits[^\n]*', f'// Physical storage plan: 56 -> {newbits} bits via proof-backed state specialization + dead high-bit elision.', text, count = 1)
    a.output_sv.write_text(text)
    meta = {'version': 'dead-high-bit-elided-sv-v1', 'source_storage_bits': a.source_storage_bits, 'candidate_storage_bits': newbits, 'applied': applied, 'checks': ['non-self semantic bit-support', 'kept emitted next-state equations do not read removed bit', 'zero-reset high suffix', 'no explicit removed-bit references remain']}
    a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL DEAD HIGH-BIT ELISION', '='*88, f'storage: {a.source_storage_bits} -> {newbits} bits']+[f"{c['register']}: {c['source_bits']} -> {c['candidate_bits']}" for c in applied]+['RESULT: CANDIDATE GENERATED']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__ == '__main__': 
    main()
