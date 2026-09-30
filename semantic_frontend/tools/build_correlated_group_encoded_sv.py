#!/usr/bin/env python3
from __future__ import annotations
import argparse, itertools, json, re
from pathlib import Path

def qm_sop(n: int, ones: set[int], dcs: set[int]): 
    if not ones: 
        return "1'b0"
    zeros = set(range(1<<n))-ones-dcs
    cubes = []
    for vals in itertools.product((-1, 0, 1), repeat = n): # -1 dc, 0,1; bit order MSB..LSB
        covered = []
        for m in range(1<<n): 
            bits = [(m>>(n-1-i))&1 for i in range(n)]
            if all(v == -1 or bits[i] == v for i, v in enumerate(vals)): 
                covered.append(m)
        cov = set(covered)
        if cov & zeros: 
            continue
        if not (cov & ones): 
            continue
        cubes.append((vals, cov & ones, sum(v!=-1 for v in vals)))
    best = None
    for r in range(1, len(cubes)+1): 
        for combo in itertools.combinations(range(len(cubes)), r): 
            cov = set().union(*(cubes[i][1] for i in combo))
            if not ones<=cov: 
                continue
            lits = sum(cubes[i][2] for i in combo)
            score = (lits, r, tuple(cubes[i][0] for i in combo))
            if best is None or score<best[0]: 
                best = (score, combo)
        if best is not None and best[0][1] == r: 
            break
    if best is None: 
        raise RuntimeError('no SOP')
    terms = []
    for i in best[1]: 
        vals = cubes[i][0]
        ls = []
        for j, v in enumerate(vals): 
            if v == -1: 
                continue
            sig = f'r_corr[{n-1-j}]'
            ls.append(sig if v else f'~{sig}')
        terms.append(' & '.join(ls) if ls else "1'b1")
    return ' | '.join(f'({x})' for x in terms)

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-sv', type = Path, required = True)
    ap.add_argument('--storage-ir', type = Path, required = True)
    ap.add_argument('--analysis', type = Path, required = True)
    ap.add_argument('--recommendation', choices = ['max_savings', 'best_efficiency'], required = True)
    ap.add_argument('--output-sv', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    text = a.source_sv.read_text()
    ir = json.loads(a.storage_ir.read_text())
    ana = json.loads(a.analysis.read_text())
    if ana.get('n_a') or not ana.get('candidates'): 
        bits = int(ir.get('storage_optimization', {}).get('natural_storage_bits', 0))
        a.output_sv.parent.mkdir(parents = True, exist_ok = True)
        a.output_sv.write_text(text)
        meta = {'version': 'correlated-state-group-encoding-v1', 'n_a': True, 'recommendation': a.recommendation, 'registers': [], 
              'source_storage_bits': bits, 'candidate_storage_bits': bits, 'proof_model': ana.get('proof_model'), 
              'reason': ana.get('n_a_reason', 'no candidate')}
        a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL CORRELATED STATE-GROUP ENCODED CANDIDATE', '='*96, 'OPTIONAL RESULT          : N/A', f'storage                 : {bits} -> {bits} bits', 'identity transform       : PASS', 'RESULT: CANDIDATE GENERATED']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    c = ana['recommendations'][a.recommendation]
    regs = list(map(str, c['registers']))
    codew = int(c['code_bits'])
    plans = {str(x['register']): x for x in ir['storage_optimization']['register_storage']}
    arch = {str(x['id']): x for x in ir['architectural_registers']}
    startup = {str(x['register']): int(x['value'][1]) for x in ir['startup']['register_values']}
    # Validate selected group is one physical bit per semantic control.
    for r in regs: 
        p = plans[r]
        if int(p['storage_bits'])!=1 or str(p['storage_kind']) not in {'DIRECT', 'NARROW_ZERO_EXTEND'}: 
            raise SystemExit(f'FAIL unsupported group member {r}: {p}')
    # Code table and decoder functions.
    rows = c['rows']
    used = {int(x['code']) for x in rows}
    dcs = set(range(1<<codew))-used
    row_by_pattern = {tuple(map(int, x['pattern'])): int(x['code']) for x in rows}
    dec_expr = {}
    for ri, r in enumerate(regs): 
        ones = {int(x['code']) for x in rows if int(x['pattern'][ri])}
        dec_expr[r] = qm_sop(codew, ones, dcs)
    # Physical signal names and semantic alias declarations.
    physical = {}
    nextphys = {}
    decl_old = {}
    decl_new = {}
    for r in regs: 
        p = plans[r]
        sk = str(p['storage_kind'])
        semw = int(arch[r]['width'])
        if sk == 'DIRECT': 
            physical[r] = f'r_{r}'
            nextphys[r] = f'd_{r}'
            decl_old[r] = f'logic [{semw-1}:0] r_{r};'
            if semw!=1: 
                raise SystemExit(f'FAIL direct group member width !=1: {r}')
            decl_new[r] = f"wire [0:0] r_{r} = {dec_expr[r]};"
        else: 
            physical[r] = f'r_{r}_store'
            nextphys[r] = f'd_{r}_store'
            sw = int(p['storage_bits'])
            if sw!=1: 
                raise SystemExit(f'FAIL narrow group member storage !=1: {r}')
            old = f"logic [0:0] r_{r}_store;\nwire [{semw-1}:0] r_{r} = {{{semw-1}'d0, r_{r}_store}};"
            new = f"wire [0:0] r_{r}_store = {dec_expr[r]};\nwire [{semw-1}:0] r_{r} = {{{semw-1}'d0, r_{r}_store}};"
            decl_old[r] = old
            decl_new[r] = new
    # Insert code storage after active_run declaration.
    tag = 'logic active_run;'
    if tag not in text: 
        raise SystemExit('FAIL active_run declaration missing')
    text = text.replace(tag, tag+f"\n// Proof-backed correlated state-group encoding ({a.recommendation}).\nlogic [{codew-1}:0] r_corr;\nwire [{codew-1}:0] d_corr;", 1)
    for r in regs: 
        if decl_old[r] not in text: 
            raise SystemExit(f'FAIL declaration pattern missing for {r}: {decl_old[r]!r}')
        text = text.replace(decl_old[r], decl_new[r], 1)
    # Encoder equations from proof-derived OR features. Feature 0 is MSB.
    enc_lines = []
    for fi, feat in enumerate(c['features']): 
        bit = codew-1-fi
        terms = []
        for rid in feat['registers']: 
            sig = nextphys[str(rid)]
            terms.append(f'{sig}[0]')
        enc_lines.append(f"assign d_corr[{bit}] = "+' | '.join(terms)+';')
    # Insert encoder immediately before GPIO output assignments, after all d_* equations.
    marker = 'assign gpio_out = r_G_DATA;'
    if marker not in text: 
        raise SystemExit('FAIL gpio output marker missing')
    text = text.replace(marker, '// Correlated-group next-code encoder over proof-valid next patterns.\n'+'\n'.join(enc_lines)+'\n\n'+marker, 1)
    # Reset/update: remove original group flops and replace with r_corr.
    reset_pattern = tuple(startup[r] for r in regs)
    if reset_pattern not in row_by_pattern: 
        raise SystemExit(f'FAIL reset group pattern outside proof domain: {reset_pattern}')
    reset_code = row_by_pattern[reset_pattern]
    reset_lines = []
    update_lines = []
    for r in regs: 
        p = plans[r]
        sk = str(p['storage_kind'])
        if sk == 'DIRECT': 
            semw = int(arch[r]['width'])
            reset_lines.append(f"        r_{r} <= {semw}'d{startup[r] & ((1<<semw)-1)};")
            update_lines.append(f"            r_{r} <= d_{r};")
        else: 
            reset_lines.append(f"        r_{r}_store <= 1'd{startup[r] & 1};")
            update_lines.append(f"            r_{r}_store <= d_{r}_store;")
    # Insert group reset before first removed reset and update before first removed update.
    first = min(text.find(x) for x in reset_lines if x in text)
    if first<0: 
        raise SystemExit('FAIL reset lines absent')
    for x in reset_lines: 
        if x not in text: 
            raise SystemExit(f'FAIL reset line missing {x}')
        text = text.replace(x, '', 1)
    insert = ' '*8+f"r_corr <= {codew}'d{reset_code};\n"
    # after active_run reset line is deterministic and avoids position sensitivity
    reset_anchor = "        active_run <= 1'b0;\n"
    text = text.replace(reset_anchor, reset_anchor+insert, 1)
    for x in update_lines: 
        if x not in text: 
            raise SystemExit(f'FAIL update line missing {x}')
        text = text.replace(x, '', 1)
    upd_anchor = '        end else begin\n'
    # Need the inner active_run else, not outer reset else. Use last occurrence before sequential tail.
    idx = text.rfind(upd_anchor)
    if idx<0: 
        raise SystemExit('FAIL update anchor absent')
    idx2 = idx+len(upd_anchor)
    text = text[:idx2]+"            r_corr <= d_corr;\n"+text[idx2:]
    # Provenance comment and storage number update.
    oldbits = int(ir['storage_optimization']['natural_storage_bits'])
    newbits = oldbits-int(c['saved_bits'])
    text, n = re.subn(r'// Physical storage plan: 56 -> \d+ bits[^\n]*', f'// Physical storage plan: 56 -> {newbits} bits via proof-backed correlated-group encoding.', text, count = 1)
    if n!=1: 
        raise SystemExit('FAIL physical storage comment missing')
    a.output_sv.parent.mkdir(parents = True, exist_ok = True)
    a.output_sv.write_text(text)
    meta = {'version': 'correlated-state-group-encoding-v1', 'recommendation': a.recommendation, 'registers': regs, 'source_storage_bits': oldbits, 'candidate_storage_bits': newbits, 'code_bits': codew, 'rows': rows, 'features': c['features'], 'decoder_expressions': dec_expr, 'reset_pattern': list(reset_pattern), 'reset_code': reset_code, 'proof_model': ana['proof_model']}
    a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL CORRELATED STATE-GROUP ENCODED CANDIDATE', '='*96, f'recommendation          : {a.recommendation}', f"registers               : {','.join(regs)}", f'storage                 : {oldbits} -> {newbits} bits', f'group storage           : {len(regs)} -> {codew} bits', f'proof patterns          : {len(rows)}', f'encoder features        : {c["features"]}', 'decoder:']+[f'  {r} = {dec_expr[r]}' for r in regs]+['', 'No binary-specific transition fixture is used; the group and code are derived from the proof-backed joint-control domain.', 'RESULT: CANDIDATE GENERATED']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
if __name__ == '__main__': 
    main()
