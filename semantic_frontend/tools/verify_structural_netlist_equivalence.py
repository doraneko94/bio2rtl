#!/usr/bin/env python3
from __future__ import annotations
import argparse, re, functools, json
from pathlib import Path
TYPES = ['AND2_X1', 'AND3_X1', 'AND4_X1', 'CLKBUF_X1', 'DFFR', 'DFFS', 'INV_X1', 'MUX2', 'NAND2', 'NAND3', 'NAND4', 'NOR2', 'NOR3', 'NOR4', 'OR2', 'OR3', 'OR4', 'XNOR2', 'XOR2']
PAT = re.compile(r'(?ms)^[ \t]*('+'|'.join(TYPES)+r')[ \t]+(\S+)[ \t]*\((.*?)\)\s*;')

def parse(text): 
    drv = {}
    ffs = {}
    q = set()
    qb = {}
    for m in PAT.finditer(text): 
        typ, inst, b = m.groups()
        p = {k: v.strip() for k, v in re.findall(r'\.(\w+)\s*\(\s*([^\)]+?)\s*\)', b)}
        if typ in ('DFFR', 'DFFS'): 
            ffs[inst] = (typ, p)
            if p.get('Q'): 
                q.add(p['Q'])
            if p.get('Q') and p.get('QB'): 
                qb[p['QB']] = p['Q']
        elif p.get('Y'): 
            drv[p['Y']] = (typ, p)
    return drv, ffs, q, qb

class BDD: 
    def __init__(self, names): 
        self.names = list(names)
        self.vid = {n: i for i, n in enumerate(self.names)}
        self.nodes = [None, None]
        self.uniq = {}
        self.V = {n: self.mk(i, 0, 1) for n, i in self.vid.items()}
    def mk(self, v, l, h): 
        if l == h: 
            return l
        k = (v, l, h)
        if k not in self.uniq: 
            self.uniq[k] = len(self.nodes)
            self.nodes.append(k)
        return self.uniq[k]
    def top(self, u): 
        return 10**9 if u<2 else self.nodes[u][0]
    def cof(self, u, v, b): 
        if u<2: 
            return u
        uv, l, h = self.nodes[u]
        return (h if b else l) if uv == v else u
    @functools.lru_cache(None)
    def NOT(self, u): 
        if u<2: 
            return 1-u
        v, l, h = self.nodes[u]
        return self.mk(v, self.NOT(l), self.NOT(h))
    @functools.lru_cache(None)
    def AND(self, a, b): 
        if a == 0 or b == 0: 
            return 0
        if a == 1: 
            return b
        if b == 1: 
            return a
        if a == b: 
            return a
        if a>b: 
            a, b = b, a
        v = min(self.top(a), self.top(b))
        return self.mk(v, self.AND(self.cof(a, v, 0), self.cof(b, v, 0)), self.AND(self.cof(a, v, 1), self.cof(b, v, 1)))
    @functools.lru_cache(None)
    def OR(self, a, b): 
        return self.NOT(self.AND(self.NOT(a), self.NOT(b)))
    @functools.lru_cache(None)
    def XOR(self, a, b): 
        if a == 0: 
            return b
        if b == 0: 
            return a
        if a == 1: 
            return self.NOT(b)
        if b == 1: 
            return self.NOT(a)
        if a == b: 
            return 0
        return self.OR(self.AND(a, self.NOT(b)), self.AND(self.NOT(a), b))
    def ANDN(self, xs): 
        z = 1
        for x in xs: 
            z = self.AND(z, x)
        return z
    def ORN(self, xs): 
        z = 0
        for x in xs: 
            z = self.OR(z, x)
        return z

def leaf_names(parsed): 
    drv, ffs, q, qb = parsed
    s = set(q)
    # external combinational leaves are discovered while walking all FF D/CK inputs
    seen = set()
    def walk(n): 
        if n in seen: 
            return
        seen.add(n)
        if n in {"1'b0", "1'h0", "1'b1", "1'h1"}: 
            return
        if n in qb: 
            s.add(qb[n])
            return
        if n in q: 
            return
        if n in drv: 
            _, p = drv[n]
            for k, v in p.items(): 
                if k!='Y': 
                    walk(v)
        else: 
            s.add(n)
    for _, p in ffs.values(): 
        for k in ('D', 'CK'): 
            if p.get(k): 
                walk(p[k])
    return s

def evaluator(parsed, B): 
    drv, ffs, q, qb = parsed
    consts = {"1'b0": 0, "1'h0": 0, "1'b1": 1, "1'h1": 1}
    @functools.lru_cache(None)
    def ev(n): 
        if n in consts: 
            return consts[n]
        if n in qb: 
            return B.NOT(B.V[qb[n]])
        if n in B.V: 
            return B.V[n]
        if n not in drv: 
            raise KeyError(f'unknown leaf {n}')
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
    return ev

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--reference', required = True)
    ap.add_argument('--candidate', required = True)
    ap.add_argument('--json', required = True)
    ap.add_argument('--txt', required = True)
    a = ap.parse_args()
    A = parse(Path(a.reference).read_text())
    C = parse(Path(a.candidate).read_text())
    if set(A[1])!=set(C[1]): 
        raise SystemExit('DFF instance sets differ')
    leaves = sorted(leaf_names(A)|leaf_names(C))
    B = BDD(leaves)
    ea = evaluator(A, B)
    ec = evaluator(C, B)
    rows = []
    for inst in sorted(A[1]): 
        ta, pa = A[1][inst]
        tc, pc = C[1][inst]
        # State/output identity must be preserved by post-map optimization.
        structural = (ta == tc and pa.get('Q') == pc.get('Q') and pa.get('RST') == pc.get('RST') and (pa.get('QB') is None or pa.get('QB') == pc.get('QB')))
        dpass = ea(pa['D']) == ec(pc['D'])
        ckpass = ea(pa['CK']) == ec(pc['CK'])
        rows.append({'instance': inst, 'q': pa.get('Q'), 'structural': structural, 'd_pass': dpass, 'clock_pass': ckpass})
    ok = all(r['structural'] and r['d_pass'] and r['clock_pass'] for r in rows)
    out = {'reference': a.reference, 'candidate': a.candidate, 'variables': len(leaves), 'bdd_nodes': len(B.nodes), 'checks': len(rows), 'passes': sum(r['structural'] and r['d_pass'] and r['clock_pass'] for r in rows), 'rows': rows, 'result': 'PASS' if ok else 'FAIL'}
    Path(a.json).write_text(json.dumps(out, indent = 2)+'\n')
    lines = ['STRUCTURAL NETLIST FULL-BOOLEAN EQUIVALENCE', '='*80, f"reference: {a.reference}", f"candidate: {a.candidate}", f"variables: {len(leaves)}", f"BDD nodes: {len(B.nodes)}"]
    lines += [f"{r['instance']} Q={r['q']}: structural={'PASS' if r['structural'] else 'FAIL'} D={'PASS' if r['d_pass'] else 'FAIL'} CK={'PASS' if r['clock_pass'] else 'FAIL'}" for r in rows]
    lines += [f"CHECKS: {out['passes']}/{out['checks']}", f"RESULT: {out['result']}"]
    Path(a.txt).write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    raise SystemExit(0 if ok else 1)
if __name__ == '__main__': 
    main()
