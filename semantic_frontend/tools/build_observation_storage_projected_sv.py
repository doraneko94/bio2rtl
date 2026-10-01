#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re
from pathlib import Path

def sha(p: Path)->str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()
def sub1(s, pat, repl, label, flags = 0): 
    z, n = re.subn(pat, repl, s, count = 1, flags = flags)
    if n!=1: 
        raise RuntimeError(f'{label}: expected one replacement, got {n}')
    return z

def decl_width(text, name): 
    m = re.search(rf'(?m)^\s*(?:logic|wire)\s*(?:\[(\d+)\s*:\s*(\d+)\])?\s+{re.escape(name)}\s*(?:=|;)', text)
    if not m: 
        return None
    return abs(int(m.group(1))-int(m.group(2)))+1 if m.group(1) is not None else 1

def main(): 
    ap = argparse.ArgumentParser()
    for x in ('source', 'analysis', 'output', 'meta', 'report'): 
        ap.add_argument('--'+x, type = Path, required = True)
    a = ap.parse_args()
    ana = json.loads(a.analysis.read_text())
    passes = [x for x in ana.get('results', []) if x.get('classification') == 'PASS_SHIFT_OBSERVATION_PROJECTION']
    if len(passes)!=1: 
        if ana.get('n_a') or len(passes) == 0: 
            s = a.source.read_text()
            a.output.parent.mkdir(parents = True, exist_ok = True)
            a.output.write_text(s)
            meta = {'version': 'generic-observation-storage-projected-sv-v1', 'n_a': True, 'source_sha256': sha(a.source), 'analysis_sha256': sha(a.analysis), 'output_sha256': sha(a.output), 'result': 'PASS', 'checks': {'identity_transform': True}}
            a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
            lines = ['BIO2RTL GENERIC OBSERVATION-STORAGE PROJECTED SV', '='*96, 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: PASS']
            a.report.write_text('\n'.join(lines)+'\n')
            print('\n'.join(lines))
            return 0
        raise SystemExit(f'FAIL expected exactly one qualifying candidate, got {len(passes)}')
    row = passes[0]
    c = row['candidate']
    rid = str(c['register'])
    w = int(c['width'])
    k = int(c['low_keep_width'])
    consts = list(map(int, c['match_constants']))
    pr = str(row['progress_register'])
    saved = int(c['saved_bits'])
    ibit = int(c['input_gpio_bit'])
    if saved<=0 or not consts: 
        raise SystemExit('FAIL non-saving/non-match candidate')
    s = a.source.read_text()
    old = s
    r = f'r_{rid}'
    d = f'd_{rid}'
    progress = f'r_{pr}'
    if decl_width(s, r)!=w: 
        raise SystemExit(f'FAIL source {r} width is not proof width {w}')
    if decl_width(s, progress) is None: 
        raise SystemExit(f'FAIL source progress proxy {progress} absent')
    # Infer shared SHIFT/CLEAR selectors from canonical bitwise shift recurrence emitted by the generic exact factor pass.
    m = re.search(rf'(?m)^assign\s+{re.escape(d)}\[0\]\s*=\s*([^?;]+?)\s*\?\s*gpio_in\[{ibit}\]\s*:\s*\(([^?;]+?)\s*\?\s*1\'b0\s*:\s*{re.escape(r)}\[0\]\s*\);', s)
    if not m: 
        raise SystemExit('FAIL cannot infer SHIFT/CLEAR selectors from source recurrence')
    shift = m.group(1).strip()
    clear = m.group(2).strip()
    # Require all higher bits to be the same shift/clear recurrence.
    for i in range(1, w): 
        pat = rf'(?m)^assign\s+{re.escape(d)}\[{i}\]\s*=\s*{re.escape(shift)}\s*\?\s*{re.escape(r)}\[{i-1}\]\s*:\s*\({re.escape(clear)}\s*\?\s*1\'b0\s*:\s*{re.escape(r)}\[{i}\]\s*\);'
        if not re.search(pat, s): 
            raise SystemExit(f'FAIL noncanonical recurrence at {d}[{i}]')
    match_names = [f'{r}_match{x}' for x in consts]
    low_name = (f'{r}_last' if k == 1 else f'{r}_low{k}') if k else None
    decl = ['// Generic observation-storage projection: shift history -> observed match/low-bit state.']
    decl += [f'logic {n};' for n in match_names]
    if k: 
        decl.append(f"logic {low_name};" if k == 1 else f"logic [{k-1}:0] {low_name};")
    if k == 0: 
        proxy = f"wire [{w-1}:0] {r} = {w}'d0;"
    elif k == w: 
        proxy = f"wire [{w-1}:0] {r} = {low_name};"
    else: 
        proxy = f"wire [{w-1}:0] {r} = {{{w-k}'d0, {low_name}}};"
    decl.append('// Semantic proxy is exact only for retained low bits; constant-match consumers are redirected below.')
    decl.append(proxy)
    s = sub1(s, rf'(?m)^logic\s+\[{w-1}:0\]\s+{re.escape(r)}\s*;\s*$', '\n'.join(decl), 'storage declaration')
    # Redirect canonical masked-equality predicate(s) to match accumulators.  The source emitter's syntax is intentionally matched semantically by mask/constant, not by predicate name.
    mask = (1<<w)-1
    replaced = []
    for const, mn in zip(consts, match_names): 
        pat = rf'(?m)^(assign\s+([A-Za-z_$][\w$]*)\s*=\s*)\(\({re.escape(r)}\s*&\s*\d+\'d{mask}\)\s*==\s*\d+\'d{const}\)\s*;\s*$'
        mm = re.search(pat, s)
        if not mm: 
            # tolerate unparenthesized canonical variant
            pat = rf'(?m)^(assign\s+([A-Za-z_$][\w$]*)\s*=\s*)\(?\s*\({re.escape(r)}\s*&\s*\d+\'d{mask}\)\s*==\s*\d+\'d{const}\s*\)?\s*;\s*$'
            mm = re.search(pat, s)
        if not mm: 
            raise SystemExit(f'FAIL cannot locate match consumer for {rid} mask={mask} const={const}')
        pname = mm.group(2)
        s = s[:mm.start()]+f'assign {pname} = {mn};'+s[mm.end():]
        replaced.append({'constant': const, 'consumer_signal': pname, 'replacement': mn})
    # Replace all d_RID assignments as one block.
    lines = []
    for const, mn in zip(consts, match_names): 
        ones = [i for i in range(w) if ((const>>(w-1-i))&1)]
        if not ones: 
            expected = "1'b0"
        elif len(ones) == w: 
            expected = "1'b1"
        else: 
            expected = ' | '.join(f'({progress} == {i})' for i in ones)
        stem = f'obs_{rid.lower()}_match_{const}'
        lines += [f'wire {stem}_expected = {expected};', f'wire {stem}_ok = ~(gpio_in[{ibit}] ^ {stem}_expected);', f'wire d_{rid}_match_{const} = {shift} ? ({stem}_ok & (({progress} == 0) | {mn})) : {mn};']
    if k: 
        if k == 1: 
            shifted = f'gpio_in[{ibit}]'
        else: 
            shifted = f'{{{low_name}[{k-2}:0], gpio_in[{ibit}]}}'
        zero = "1'b0" if k == 1 else f"{k}'d0"
        lines += [f"wire {('' if k==1 else f'[{k-1}:0] ')}d_{rid}_{'last' if k==1 else f'low{k}'} = {shift} ? {shifted} : ({clear} ? {zero} : {low_name});"]
    block_re = rf'(?m)^wire\s+\[{w-1}:0\]\s+{re.escape(d)}\s*;\n(?:assign\s+{re.escape(d)}\[\d+\]\s*=.*\n){{{w}}}'
    s = sub1(s, block_re, '\n'.join(lines)+'\n', 'D block')
    # Reset/update physical state.
    reset_state_lines = [f"        {n} <= 1'b0;" for n in match_names]
    if k:
        reset_zero = "1'b0" if k == 1 else f"{k}'d0"
        reset_state_lines.append(f"        {low_name} <= {reset_zero};")
    reset_lines = '\n'.join(reset_state_lines)
    s = sub1(s, rf'(?m)^\s*{re.escape(r)}\s*<=\s*{w}\'d0\s*;\s*$', reset_lines, 'reset')
    upd = [f"            {mn} <= d_{rid}_match_{const};" for const, mn in zip(consts, match_names)]
    if k: 
        upd.append(f"            {low_name} <= d_{rid}_{'last' if k==1 else f'low{k}'};")
    s = sub1(s, rf'(?m)^\s*{re.escape(r)}\s*<=\s*{re.escape(d)}\s*;\s*$', '\n'.join(upd), 'update')
    # Update storage-plan comment if present.
    mm = re.search(r'Physical storage plan:\s*(\d+)\s*->\s*(\d+)\s*bits', s)
    before = None
    after = None
    if mm: 
        before = int(mm.group(2))
        after = before-saved
        s = s[:mm.start()]+f'Physical storage plan: {mm.group(1)} -> {after} bits'+s[mm.end():]
    a.output.write_text(s)
    checks = {'analysis_pass': ana.get('result') == 'PASS', 'saved_bits': saved>0, 'old_physical_absent': not re.search(rf'(?m)^logic\s+\[{w-1}:0\]\s+{re.escape(r)}\s*;', s), 'old_d_absent': not re.search(rf'\b{re.escape(d)}\s*\[', s), 'match_state_present': all(f'logic {n};' in s for n in match_names), 'low_state_present': (not k or low_name in s), 'consumer_replacements': len(replaced) == len(consts)}
    meta = {'version': 'generic-observation-storage-projected-sv-v1', 'source_sha256': sha(a.source), 'analysis_sha256': sha(a.analysis), 'output_sha256': sha(a.output), 'candidate': c, 'progress_register': pr, 'shift_selector': shift, 'clear_selector': clear, 'consumer_replacements': replaced, 'source_storage_bits': before, 'candidate_storage_bits': after, 'checks': checks, 'result': 'PASS' if all(checks.values()) else 'FAIL'}
    a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    linesr = ['BIO2RTL GENERIC OBSERVATION-STORAGE PROJECTED SV', '='*96, f"register       : {rid}", f"projection     : {w} -> {c['projected_bits']} bits (save {saved})", f"progress       : {pr}", f"constants      : {consts}", f"observed bits  : {c['observed_bits']}", f"source bytes   : {len(old.encode())}", f"output bytes   : {len(s.encode())}", f"output sha256  : {sha(a.output)}", f"RESULT: {meta['result']}"]
    a.report.write_text('\n'.join(linesr)+'\n')
    print('\n'.join(linesr))
    raise SystemExit(0 if meta['result'] == 'PASS' else 2)
if __name__ == '__main__': 
    main()
