#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, itertools, json, re
from pathlib import Path


def sha(p: Path) -> str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()


def split_or_actions(s: str) -> list[str]: 
    xs = [x.strip() for x in s.split('|')]
    if not xs or any(not re.fullmatch(r'[A-Za-z_$][\w$]*', x) for x in xs): 
        return []
    return xs


def parse_equation(rhs: str, rname: str, bit: int): 
    # Canonical emitter form:
    # (r[i] & ~(a | b | c)) | ((b & data) | (c & data))
    pat = rf'^\({re.escape(rname)}\[{bit}\]\s*&\s*~\((.*?)\)\)\s*\|\s*\((.*)\)$'
    m = re.fullmatch(pat, rhs.strip())
    if not m: 
        return None
    updates = split_or_actions(m.group(1))
    if not updates: 
        return None
    body = m.group(2).strip()
    terms = re.findall(r'\(\s*([A-Za-z_$][\w$]*)\s*&\s*([^()]+?)\s*\)', body)
    if not terms: 
        return None
    # Ensure the body contains only ORs of the matched simple action/data terms.
    consumed = re.sub(r'\(\s*[A-Za-z_$][\w$]*\s*&\s*[^()]+?\s*\)', 'TERM', body)
    if not re.fullmatch(r'TERM(?:\s*\|\s*TERM)*', consumed): 
        return None
    acts = [a for a, _ in terms]
    if any(a not in updates for a in acts): 
        return None
    datas = [d.strip() for _, d in terms]
    if len(set(datas))!=1: 
        return None
    return {'updates': updates, 'shift_actions': acts, 'data': datas[0]}


def b(v): 
    return bool(v)


def prove_exact(update_actions, shift_actions): 
    acts = sorted(set(update_actions))
    sset = set(shift_actions)
    cset = set(acts)-sset
    bad = []
    cases = 0
    for vals in itertools.product((0, 1), repeat = len(acts)): 
        env = dict(zip(acts, vals))
        for old in (0, 1): 
            for data in (0, 1): 
                cases+=1
                upd = any(env[a] for a in acts)
                shift = any(env[a] for a in sset)
                clear = any(env[a] for a in cset)
                old_expr = (b(old) and not upd) or any(env[a] and b(data) for a in sset)
                new_expr = b(data) if shift else (False if clear else b(old))
                if old_expr!=new_expr: 
                    bad.append({'actions': env, 'old': old, 'data': data, 'old_expr': int(old_expr), 'new_expr': int(new_expr)})
                    return cases, bad
    return cases, bad


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--meta', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    s = a.source.read_text()
    original = s

    decls = []
    for m in re.finditer(r'(?m)^logic\s+\[(\d+):0\]\s+(r_([A-Za-z0-9_$]+))\s*;\s*$', s): 
        width = int(m.group(1))+1
        rname = m.group(2)
        rid = m.group(3)
        if width<2: 
            continue
        dname = 'd_'+rid
        if not re.search(rf'(?m)^wire\s+\[{width-1}:0\]\s+{re.escape(dname)}\s*;\s*$', s): 
            continue
        eq = []
        ok = True
        for i in range(width): 
            mm = re.search(rf'(?m)^assign\s+{re.escape(dname)}\[{i}\]\s*=\s*(.*);\s*$', s)
            if not mm: 
                ok = False
                break
            p = parse_equation(mm.group(1), rname, i)
            if p is None: 
                ok = False
                break
            eq.append((mm, p))
        if not ok: 
            continue
        updates = eq[0][1]['updates']
        shifts = eq[0][1]['shift_actions']
        if any(x['updates']!=updates or x['shift_actions']!=shifts for _, x in eq): 
            continue
        # Shift-register data relationship: bit0 is an arbitrary scalar input;
        # every higher bit receives the previous retained bit.
        input_expr = eq[0][1]['data']
        if any(eq[i][1]['data'].replace(' ', '') != f'{rname}[{i-1}]' for i in range(1, width)): 
            continue
        cases, bad = prove_exact(updates, shifts)
        if bad: 
            continue
        clear = [x for x in updates if x not in set(shifts)]
        if not clear: 
            continue
        decls.append({'rid': rid, 'rname': rname, 'dname': dname, 'width': width, 'input_expr': input_expr, 
                      'update_actions': updates, 'shift_actions': shifts, 'clear_actions': clear, 
                      'proof_cases': cases, 'equations': eq})

    if not decls: 
        a.output.parent.mkdir(parents = True, exist_ok = True)
        a.output.write_text(s)
        meta = {'version': 'generic-shift-clear-recurrence-v1', 'n_a': True, 'source_sha256': sha(a.source), 'output_sha256': sha(a.output), 'applied': []}
        a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL GENERIC SHIFT/CLEAR RECURRENCE', '='*96, 'OPTIONAL RESULT: N/A', 'identity transform: PASS', 'RESULT: PASS']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0

    applied = []
    # Apply from bottom to top only via exact line replacement; declarations are inserted at each D block.
    for c in decls: 
        rid = c['rid']
        rname = c['rname']
        dname = c['dname']
        width = c['width']
        stem = 'obs_'+re.sub(r'[^A-Za-z0-9_$]', '_', rid).lower()
        shift_wire = stem+'_shift'
        clear_wire = stem+'_clear'
        shift_rhs = ' | '.join(c['shift_actions'])
        clear_rhs = ' | '.join(c['clear_actions'])
        ddecl = re.search(rf'(?m)^wire\s+\[{width-1}:0\]\s+{re.escape(dname)}\s*;\s*$', s)
        if not ddecl: 
            raise SystemExit(f'FAIL d declaration vanished for {rid}')
        block = (f'// Exact generic shift/clear recurrence factorization for {rid}.\n'
               f'wire {shift_wire} = {shift_rhs};\n'
               f'wire {clear_wire} = {clear_rhs};\n')
        s = s[:ddecl.start()]+block+s[ddecl.start():]
        for i in range(width): 
            data = c['input_expr'] if i == 0 else f'{rname}[{i-1}]'
            pat = rf'(?m)^assign\s+{re.escape(dname)}\[{i}\]\s*=\s*.*;\s*$'
            repl = f"assign {dname}[{i}] = {shift_wire} ? {data} : ({clear_wire} ? 1'b0 : {rname}[{i}]);"
            s, n = re.subn(pat, repl, s, count = 1)
            if n!=1: 
                raise SystemExit(f'FAIL equation replacement {rid}[{i}]')
        applied.append({k: v for k, v in c.items() if k!='equations'} | {'shift_wire': shift_wire, 'clear_wire': clear_wire})

    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(s)
    meta = {'version': 'generic-shift-clear-recurrence-factor-v1', 'source_sha256': sha(a.source), 
          'output_sha256': sha(a.output), 'candidates': applied, 'candidate_count': len(applied), 
          'proof_model': 'EXHAUSTIVE_BOOLEAN_IDENTITY_OVER_INDEPENDENT_UPDATE_ACTIONS_OLD_BIT_AND_SHIFT_DATA', 
          'result': 'PASS'}
    a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL GENERIC SHIFT/CLEAR RECURRENCE FACTOR', '='*96, 
           f'source bytes : {len(original.encode())}', f'output bytes : {len(s.encode())}', f'candidates   : {len(applied)}']
    for c in applied: 
        lines += [f"{c['rid']}: width={c['width']} shift={c['shift_actions']} clear={c['clear_actions']} proof_cases={c['proof_cases']}"]
    lines += ['No register ID, protocol constant, address, event ID, or action ID is selected by the pass.', 'RESULT: PASS']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__ == '__main__': 
    main()
