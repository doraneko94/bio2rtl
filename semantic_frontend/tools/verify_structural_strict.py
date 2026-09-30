#!/usr/bin/env python3
from __future__ import annotations
import argparse, functools, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: 
    sys.path.insert(0, str(HERE))
import map_tr1um_structural_sv as M
from analyze_full_boolean_sv_resub import Parser
from map_tr1um_structural_generic import state_info, qbase, label, GenericCompile
from verify_structural_netlist_equivalence import BDD

TYPES = ['AND2_X1', 'AND3_X1', 'AND4_X1', 'CLKBUF_X1', 'DFFR', 'DFFS', 'INV_X1', 'MUX2', 'NAND2', 'NAND3', 'NAND4', 'NOR2', 'NOR3', 'NOR4', 'OR2', 'OR3', 'OR4', 'XNOR2', 'XOR2']
PAT = re.compile(r'(?ms)^[ \t]*('+'|'.join(TYPES)+r')[ \t]+(\S+)[ \t]*\((.*?)\)\s*;')
ASSIGN_RE = re.compile(r'(?ms)^\s*assign\s+([^=;]+?)\s*=\s*(.*?);')
ID_RE = re.compile(r'\b[A-Za-z_$][\w$]*(?:\[\d+\])?')


def parse_cells(text): 
    drv = {}
    ffs = {}
    drivers = {}
    refs = []
    for m in PAT.finditer(text): 
        typ, inst, b = m.groups()
        p = {k: v.strip() for k, v in re.findall(r'\.(\w+)\s*\(\s*([^\)]+?)\s*\)', b)}
        if typ in ('DFFR', 'DFFS'): 
            ffs[inst] = (typ, p)
            for k in ('Q', 'QB'): 
                if p.get(k): 
                    n = p[k]
                    drivers.setdefault(n, []).append(inst+'.'+k)
            for k in ('D', 'CK', 'RST', 'SET'): 
                if p.get(k): 
                    refs.append((inst+'.'+k, p[k]))
        else: 
            y = p.get('Y')
            if y: 
                drv[y] = (typ, p)
                drivers.setdefault(y, []).append(inst+'.Y')
            for k, v in p.items(): 
                if k!='Y': 
                    refs.append((inst+'.'+k, v))
    return drv, ffs, drivers, refs



def combinational_cycle_audit(drv): 
    # Kahn topological check over combinationally-driven nets.  FF Q/QB and
    # primary inputs are leaves and therefore are intentionally excluded.
    deps = {n: set() for n in drv}
    rev = {n: set() for n in drv}
    for y, (_typ, pins) in drv.items(): 
        for k, v in pins.items(): 
            if k == 'Y': 
                continue
            v = v.strip()
            if v in drv: 
                deps[y].add(v)
                rev[v].add(y)
    indeg = {n: len(ds) for n, ds in deps.items()}
    q = [n for n, d in indeg.items() if d == 0]
    done = 0
    while q: 
        n = q.pop()
        done+=1
        for y in rev[n]: 
            indeg[y]-=1
            if indeg[y] == 0: 
                q.append(y)
    rem = sorted(n for n, d in indeg.items() if d>0)
    return {'pass': not rem, 'combinational_nets': len(drv), 'topologically_removed': done, 
            'remaining_nets': rem, 'remaining_count': len(rem)}

def source_functions(text, info, B): 
    syn = []
    for q in info['order']: 
        w = info['widths'][q]
        decl = '' if w == 1 else f'[{w-1}:0] '
        syn.append(f'wire {decl}__bio2rtl_next_{q} = {info["next"][q]};')
    model = M.Model(text+'\n'+'\n'.join(syn)+'\n')
    D = M.DAG()
    C = GenericCompile(model, D)
    memo = {}
    def evlit(l): 
        u = evnode(l.n)
        return B.NOT(u) if l.inv else u
    def evnode(i): 
        if i in memo: 
            return memo[i]
        nd = D.nodes[i]
        if nd.op == 'CONST': 
            u = 0
        elif nd.op == 'VAR': 
            if nd.label not in B.V: 
                raise KeyError('source leaf '+nd.label)
            u = B.V[nd.label]
        elif nd.op == 'AND': 
            u = 1
            for x in nd.args: 
                u = B.AND(u, evlit(x))
        elif nd.op == 'OR': 
            u = 0
            for x in nd.args: 
                u = B.OR(u, evlit(x))
        elif nd.op == 'XOR': 
            u = B.XOR(evlit(nd.args[0]), evlit(nd.args[1]))
        elif nd.op == 'MUX': 
            s, t, f = [evlit(x) for x in nd.args]
            u = B.OR(B.AND(s, t), B.AND(B.NOT(s), f))
        else: 
            raise KeyError(nd.op)
        memo[i] = u
        return u
    nxt = {}
    for q in info['order']: 
        for i, x in enumerate(C.signal('__bio2rtl_next_'+q)): 
            nxt[label(q, i)] = evlit(x)
    outs = {}
    for n in ('gpio_out', 'gpio_oe'): 
        outs[n] = [evlit(x) for x in C.signal(n)]
    return nxt, outs


def candidate_eval(text, info, B): 
    drv, ffs, drivers, refs = parse_cells(text)
    logical = {}
    for q in info['order']: 
        for i in range(info['widths'][q]): 
            lab = label(q, i)
            base = qbase(lab)
            logical[base] = (lab, False)
            logical[base+'_n'] = (lab, True)
    consts = {"1'b0": 0, "1'h0": 0, "1'd0": 0, "1'b1": 1, "1'h1": 1, "1'd1": 1}
    @functools.lru_cache(None)
    def ev(n): 
        n = n.strip()
        if n in consts: 
            return consts[n]
        if n in logical: 
            lab, inv = logical[n]
            u = B.V[lab]
            return B.NOT(u) if inv else u
        mm = re.fullmatch(r'gpio_in\[(\d+)\]', n)
        if mm: 
            return B.V[n]
        if n in ('clk', 'reset'): 
            return B.V[n]
        if n not in drv: 
            raise KeyError('undriven/unknown net '+n)
        typ, p = drv[n]
        g = lambda k: ev(p[k])
        xs = [g(k) for k in 'ABCD' if k in p]
        if typ == 'INV_X1': 
            return B.NOT(g('A'))
        if typ.startswith('AND'): 
            return B.ANDN(xs)
        if typ.startswith('NAND'): 
            return B.NOT(B.ANDN(xs))
        if typ.startswith('OR'): 
            return B.ORN(xs)
        if typ.startswith('NOR'): 
            return B.NOT(B.ORN(xs))
        if typ == 'XOR2': 
            return B.XOR(g('A'), g('B'))
        if typ == 'XNOR2': 
            return B.NOT(B.XOR(g('A'), g('B')))
        if typ == 'MUX2': 
            return B.OR(B.AND(B.NOT(g('S')), g('A')), B.AND(g('S'), g('B')))
        if typ == 'CLKBUF_X1': 
            return g('A')
        raise KeyError(typ)
    return ev, drv, ffs, drivers, refs, logical


def ast_eval(z, ev, B, widths): 
    if z.op == 'num': 
        w, v = M.parse_num(z.a)
        return tuple(1 if (v>>i)&1 else 0 for i in range(w))
    if z.op == 'id': 
        n = z.a
        w = widths.get(n, 1)
        if w == 1: 
            return (ev(n),)
        return tuple(ev(f'{n}[{i}]') for i in range(w))
    if z.op == 'idx': 
        if isinstance(z.a, M.N) and z.a.op == 'id': 
            return (ev(f'{z.a.a}[{z.b}]'),)
        v = ast_eval(z.a, ev, B, widths)
        return (v[z.b] if z.b<len(v) else 0,)
    if z.op == 'slice': 
        v = ast_eval(z.a, ev, B, widths)
        hi, lo = z.b, z.c
        rng = range(lo, hi+1) if hi>=lo else range(lo, hi-1, -1)
        return tuple(v[i] if i<len(v) else 0 for i in rng)
    if z.op == '{}': 
        parts = [ast_eval(x, ev, B, widths) for x in z.a]
        o = []
        for p in reversed(parts): 
            o.extend(p)
        return tuple(o)
    if z.op == 'u~': 
        return tuple(B.NOT(x) for x in ast_eval(z.a, ev, B, widths))
    if z.op == 'u!': 
        v = ast_eval(z.a, ev, B, widths)
        u = 0
        for x in v: 
            u = B.OR(u, x)
        return (B.NOT(u),)
    if z.op in ('&', '|', '^'): 
        a = ast_eval(z.a, ev, B, widths)
        b = ast_eval(z.b, ev, B, widths)
        w = max(len(a), len(b))
        a = tuple(list(a)+[0]*(w-len(a)))
        b = tuple(list(b)+[0]*(w-len(b)))
        fn = {'&': B.AND, '|': B.OR, '^': B.XOR}[z.op]
        return tuple(fn(x, y) for x, y in zip(a, b))
    if z.op == '?:': 
        sv = ast_eval(z.a, ev, B, widths)
        s = 0
        for x in sv: 
            s = B.OR(s, x)
        a = ast_eval(z.b, ev, B, widths)
        b = ast_eval(z.c, ev, B, widths)
        w = max(len(a), len(b))
        a = tuple(list(a)+[0]*(w-len(a)))
        b = tuple(list(b)+[0]*(w-len(b)))
        return tuple(B.OR(B.AND(s, x), B.AND(B.NOT(s), y)) for x, y in zip(a, b))
    raise KeyError('unsupported output AST '+z.op)


def output_functions(text, ev, B): 
    widths = {'gpio_out': 32, 'gpio_oe': 32}
    bits = {n: [None]*32 for n in widths}
    whole = {}
    for m in ASSIGN_RE.finditer(text): 
        lhs = m.group(1).strip()
        rhs = m.group(2).strip()
        mm = re.fullmatch(r'(gpio_out|gpio_oe)\[(\d+)\]', lhs)
        if mm: 
            n, i = mm.group(1), int(mm.group(2))
            v = ast_eval(Parser(rhs).parse(), ev, B, widths)
            bits[n][i] = v[0]
            continue
        if lhs in bits: 
            whole[lhs] = ast_eval(Parser(rhs).parse(), ev, B, widths)
    for n in bits: 
        if n in whole: 
            v = list(whole[n])+[0]*32
            bits[n] = v[:32]
        if any(x is None for x in bits[n]): 
            raise ValueError(f'incomplete output driver {n}')
    return bits


def identifiers(expr): 
    return [x for x in ID_RE.findall(expr) if not re.fullmatch(r'[bhd][0-9A-Fa-fxXzZ]+', x)]


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--behavioral', type = Path, required = True)
    ap.add_argument('--candidate', type = Path, required = True)
    ap.add_argument('--json', type = Path, required = True)
    ap.add_argument('--txt', type = Path, required = True)
    a = ap.parse_args()
    bt = a.behavioral.read_text()
    ct = a.candidate.read_text()
    info = state_info(bt)
    vars = [label(q, i) for q in info['order'] for i in range(info['widths'][q])]+[f'gpio_in[{i}]' for i in range(32)]+['clk', 'reset']
    B = BDD(sorted(set(vars)))
    src_next, src_out = source_functions(bt, info, B)
    ev, drv, ffs, drivers, refs, logical = candidate_eval(ct, info, B)
    cycle = combinational_cycle_audit(drv)
    rows = []
    used_ffs = set()
    for q in info['order']: 
        for i in range(info['widths'][q]): 
            lab = label(q, i)
            base = qbase(lab)
            matches = []
            for inst, (typ, p) in ffs.items(): 
                if p.get('Q') == base: 
                    matches.append((inst, typ, p, False))
                if p.get('QB') == base: 
                    matches.append((inst, typ, p, True))
            if len(matches)!=1: 
                rows.append({'state': lab, 'pass': False, 'reason': 'logical_state_port_match', 'matches': len(matches)})
                continue
            inst, typ, p, logical_on_qb = matches[0]
            used_ffs.add(inst)
            async_pin = 'RST' if typ == 'DFFR' else 'SET'
            async_net = p.get(async_pin)
            if typ == 'DFFR': 
                logical_reset = 1 if logical_on_qb else 0
            else: 
                logical_reset = 0 if logical_on_qb else 1
            expected_reset = info['reset'][q][i]
            expect_d = B.NOT(src_next[lab]) if logical_on_qb else src_next[lab]
            try: 
                dpass = ev(p['D']) == expect_d
                ckpass = ev(p['CK']) == B.V['clk']
                apass = (async_net == 'reset')
            except Exception as e: 
                dpass = ckpass = apass = False
                err = repr(e)
            else: 
                err = ''
            qb_present = bool(p.get('Q')) and bool(p.get('QB'))
            ok = dpass and ckpass and apass and logical_reset == expected_reset and qb_present
            rows.append({'state': lab, 'instance': inst, 'type': typ, 'logical_on_qb': logical_on_qb, 'd_pass': dpass, 'ck_pass': ckpass, 'async_pin': async_pin, 'async_net': async_net, 'async_pass': apass, 'reset_expected': expected_reset, 'reset_actual': logical_reset, 'reset_pass': logical_reset == expected_reset, 'q_qb_present': qb_present, 'error': err, 'pass': ok})
    extra_ffs = sorted(set(ffs)-used_ffs)
    # Outputs: exact Boolean equivalence against behavioral assignments.
    out_rows = []
    try: 
        cand_out = output_functions(ct, ev, B)
    except Exception as e: 
        cand_out = None
        out_error = repr(e)
    else: 
        out_error = ''
    if cand_out is not None: 
        for n in ('gpio_out', 'gpio_oe'): 
            for i, (x, y) in enumerate(zip(src_out[n], cand_out[n])): 
                out_rows.append({'output': f'{n}[{i}]', 'pass': x == y})
    # Missing/multiple driver audit over structural cell pins and output assignment identifiers.
    legal = set(drivers)|set(logical)|{f'gpio_in[{i}]' for i in range(32)}|{'gpio_in', 'clk', 'reset'}
    missing = []
    for where, n in refs: 
        if n in ("1'b0", "1'b1", "1'h0", "1'h1"): 
            continue
        if n not in legal: 
            missing.append({'where': where, 'net': n})
    for m in ASSIGN_RE.finditer(ct): 
        lhs = m.group(1).strip()
        if not (lhs.startswith('gpio_out') or lhs.startswith('gpio_oe')): 
            continue
        for n in identifiers(m.group(2)): 
            if n in ('gpio_out', 'gpio_oe'): 
                continue
            if n.startswith('gpio_in[') or n in legal: 
                continue
            missing.append({'where': 'assign '+lhs, 'net': n})
    multi = {n: ds for n, ds in drivers.items() if len(ds)!=1}
    all_states = all(r.get('pass') for r in rows) and not extra_ffs and len(rows) == sum(info['widths'].values())
    all_outputs = (cand_out is not None and len(out_rows) == 64 and all(r['pass'] for r in out_rows))
    driver_ok = not missing and not multi
    cycle_ok = cycle['pass']
    ok = all_states and all_outputs and driver_ok and cycle_ok
    out = {'version': 'bio2rtl-structural-strict-v1', 'behavioral': str(a.behavioral), 'candidate': str(a.candidate), 'state_bits': sum(info['widths'].values()), 'ff_count': len(ffs), 'state_rows': rows, 'extra_ffs': extra_ffs, 'output_rows': out_rows, 'output_error': out_error, 'missing_drivers': missing, 'multiple_drivers': multi, 'bdd_variables': len(B.names), 'bdd_nodes': len(B.nodes), 'combinational_cycle_audit': cycle, 'checks': {'state_sequential': all_states, 'output_boolean': all_outputs, 'driver_integrity': driver_ok, 'combinational_acyclic': cycle_ok}, 'result': 'PASS' if ok else 'FAIL'}
    a.json.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL STRICT STRUCTURAL VERIFIER', '='*88, f'behavioral: {a.behavioral}', f'candidate : {a.candidate}', f'state D/CK/async/reset: {sum(r.get("pass",False) for r in rows)}/{len(rows)}', f'extra FFs: {len(extra_ffs)}', f'gpio_out/gpio_oe Boolean: {sum(r["pass"] for r in out_rows)}/{len(out_rows)}', f'missing drivers: {len(missing)}', f'multiple drivers: {len(multi)}', f'combinational cycle remaining nets: {cycle["remaining_count"]}', f'BDD nodes: {len(B.nodes)}', f'RESULT: {out["result"]}']
    a.txt.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    raise SystemExit(0 if ok else 2)
if __name__ == '__main__': 
    main()
