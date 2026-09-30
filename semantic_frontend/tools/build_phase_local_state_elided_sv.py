#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re
from pathlib import Path


def repl_expr(r): 
    kind = str(r['kind'])
    if kind == 'CONST': 
        return "1'b1" if int(r.get('constant', 0)) else "1'b0"
    if kind == 'PRED': 
        q = str(r['predicate'])
        e = f'pred_{q}'
        return f'~{e}' if bool(r.get('invert')) else e
    raise ValueError(kind)


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-sv', type = Path, required = True)
    ap.add_argument('--storage-ir', type = Path, required = True)
    ap.add_argument('--analysis', type = Path, required = True)
    ap.add_argument('--output-sv', type = Path, required = True)
    ap.add_argument('--output-ir', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    text = a.source_sv.read_text()
    ir = json.loads(a.storage_ir.read_text())
    ana = json.loads(a.analysis.read_text())
    cands = list(ana.get('candidates', []))
    if not cands: 
        a.output_sv.parent.mkdir(parents = True, exist_ok = True)
        a.output_sv.write_text(text)
        a.output_ir.write_text(json.dumps(ir, indent = 2, sort_keys = True)+'\n')
        meta = {'version': 'phase-local-state-elided-candidate-v2', 'status': 'N_A', 'source_storage_bits': int(ir['storage_optimization']['natural_storage_bits']), 'candidate_storage_bits': int(ir['storage_optimization']['natural_storage_bits']), 'removed_registers': [], 'candidates': [], 'proof_model': ana.get('legal_product', {})}
        a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL PHASE-LOCAL STATE-ELIDED CANDIDATE', '='*96, 'candidate                : N/A', 'semantic/storage rewrite : NONE', 'RESULT                   : PASS / N_A']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return
    plans = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
    selected = []
    for c in cands: 
        rid = str(c['register'])
        p = plans.get(rid)
        if not p or int(p.get('storage_bits', 0))!=1: 
            continue
        if c.get('outcome_readers'): 
            raise SystemExit(f'FAIL {rid}: outcome readers present')
        selected.append(c)
    if not selected: 
        a.output_sv.parent.mkdir(parents = True, exist_ok = True)
        a.output_sv.write_text(text)
        a.output_ir.write_text(json.dumps(ir, indent = 2, sort_keys = True)+'\n')
        meta = {'version': 'phase-local-state-elided-candidate-v2', 'status': 'N_A', 'source_storage_bits': int(ir['storage_optimization']['natural_storage_bits']), 'candidate_storage_bits': int(ir['storage_optimization']['natural_storage_bits']), 'removed_registers': [], 'candidates': [], 'proof_model': ana.get('legal_product', {})}
        a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL PHASE-LOCAL STATE-ELIDED CANDIDATE', '='*96, 'candidate                : N/A', 'semantic/storage rewrite : NONE', 'RESULT                   : PASS / N_A']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return
    # Replace only the consumer predicates proven by the phase-local analyzer.
    for c in selected: 
        for pid, r in c['predicate_replacements'].items(): 
            pat = rf'assign\s+pred_{re.escape(pid)}\s*=\s*.*?;'
            m = re.search(pat, text)
            if not m: 
                raise SystemExit(f'FAIL predicate assignment not found: {pid}')
            text = text[:m.start()]+f'assign pred_{pid} = {repl_expr(r)};'+text[m.end():]
    removed = []
    for c in selected: 
        rid = str(c['register'])
        # storage declaration
        pats = [
            rf'^logic\s+\[0:0\]\s+r_{re.escape(rid)};\s*\n', 
            rf'^wire\s+\[0:0\]\s+d_{re.escape(rid)};\s*\n', 
            rf'^assign\s+d_{re.escape(rid)}\[0\]\s*=.*?;\s*\n', 
            rf'^\s*r_{re.escape(rid)}\s*<=\s*[^;]+;\s*\n', 
            rf'^wire\s+\[[^\]]+\]\s+\w+\s*=\s*r_{re.escape(rid)};\s*\n', 
        ]
        for pat in pats: 
            text, n = re.subn(pat, '', text, flags = re.M)
        # Any remaining semantic storage reference would mean the candidate is not safely local.
        if re.search(rf'\br_{re.escape(rid)}\b', text): 
            hits = [x for x in text.splitlines() if re.search(rf'\br_{re.escape(rid)}\b', x)]
            raise SystemExit(f'FAIL remaining references for {rid}: {hits[:8]}')
        if re.search(rf'\bd_{re.escape(rid)}\b', text): 
            hits = [x for x in text.splitlines() if re.search(rf'\bd_{re.escape(rid)}\b', x)]
            raise SystemExit(f'FAIL remaining d references for {rid}: {hits[:8]}')
        removed.append(rid)
    oldbits = int(ir['storage_optimization']['natural_storage_bits'])
    newbits = oldbits-len(removed)
    # Update storage plan so downstream generic passes naturally ignore removed registers.
    outir = json.loads(json.dumps(ir))
    outir['storage_optimization']['natural_storage_bits'] = newbits
    for p in outir['storage_optimization']['register_storage']: 
        if str(p['register']) in removed: 
            p['storage_bits'] = 0
            p['storage_kind'] = 'PHASE_LOCAL_ELIDED'
            p['phase_local_elision'] = next(c for c in selected if str(c['register']) == str(p['register']))['predicate_replacements']
    outir.setdefault('notes', []).append('Phase-local state-elision annotations are proof-derived from the legal sampled-I2C consumer contexts; eliminated registers have no non-self outcome readers.')
    # Update physical-storage comment if present.
    text = re.sub(r'// Physical storage plan: \d+ -> \d+ bits[^\n]*', f'// Physical storage plan: {oldbits} -> {newbits} bits via proof-backed phase-local state elision.', text, count = 1)
    a.output_sv.parent.mkdir(parents = True, exist_ok = True)
    a.output_sv.write_text(text)
    a.output_ir.write_text(json.dumps(outir, indent = 2, sort_keys = True)+'\n')
    meta = {'version': 'phase-local-state-elided-candidate-v1', 'source_storage_bits': oldbits, 'candidate_storage_bits': newbits, 'removed_registers': removed, 'candidates': selected, 'proof_model': ana.get('legal_product', {})}
    a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL PHASE-LOCAL STATE-ELIDED CANDIDATE', '='*96, f'storage                 : {oldbits} -> {newbits} bits', f"removed registers       : {','.join(removed)}"]
    for c in selected: 
        lines.append(f"  {c['register']}: {c['predicate_replacements']}")
    lines += ['', 'No register name is selected by the builder; all removals come from the proof-backed analyzer output.', 'RESULT: CANDIDATE GENERATED']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__ == '__main__': 
    main()
