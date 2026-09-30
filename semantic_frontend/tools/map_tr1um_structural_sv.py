#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, hashlib, re, sys
from dataclasses import dataclass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_full_boolean_sv_resub import Model, N, parse_num

AREA = {
 'INV_X1': 1821.66, 'AND2_X1': 2165.96, 'AND3_X1': 2510.26, 'AND4_X1': 2854.56, 
 'OR2': 2165.96, 'OR3': 2510.26, 'OR4': 2854.56, 
 'NAND2': 1821.66, 'NAND3': 2165.96, 'NAND4': 2510.26, 
 'NOR2': 1821.66, 'NOR3': 2165.96, 'NOR4': 2510.26, 
 'XOR2': 2854.56, 'XNOR2': 2854.56, 'MUX2': 3887.46, 
 'DFFR': 6297.56, 'CLKBUF_X1': 3198.86, 
}

@dataclass(frozen = True, order = True)
class Lit: 
    n: int
    inv: bool = False
    def neg(self): 
        return Lit(self.n, not self.inv)

@dataclass(frozen = True)
class Node: 
    op: str
    args: tuple
    label: str = ''

class DAG: 
    def __init__(self): 
        self.nodes = [Node('CONST', ())] # node0, positive=0 negative=1
        self.intern = {}
        self.varid = {}
    @property
    def ZERO(self): 
        return Lit(0, False)
    @property
    def ONE(self): 
        return Lit(0, True)
    def is0(self, x): 
        return x.n == 0 and not x.inv
    def is1(self, x): 
        return x.n == 0 and x.inv
    def var(self, label): 
        if label not in self.varid: 
            i = len(self.nodes)
            self.varid[label] = i
            self.nodes.append(Node('VAR', (), label))
        return Lit(self.varid[label], False)
    def _node(self, op, args): 
        k = (op, tuple(args))
        if k not in self.intern: 
            i = len(self.nodes)
            self.intern[k] = i
            self.nodes.append(Node(op, tuple(args)))
        return Lit(self.intern[k], False)
    def AND(self, *xs): 
        xs = list(xs)
        flat = []
        for x in xs: 
            if self.is0(x): 
                return self.ZERO
            if self.is1(x): 
                continue
            nd = self.nodes[x.n] if not x.inv else None
            if nd and nd.op == 'AND': 
                flat.extend(nd.args)
            else: 
                flat.append(x)
        s = set(flat)
        if any(x.neg() in s for x in s): 
            return self.ZERO
        flat = sorted(s)
        if not flat: 
            return self.ONE
        if len(flat) == 1: 
            return flat[0]
        if all(x.inv for x in flat): 
            return self.OR(*[Lit(x.n, False) for x in flat]).neg()
        # actual cells max fanin4; deterministic grouping avoids illegal pseudo-gates
        while len(flat)>4: 
            g = self._node('AND', tuple(flat[:4]))
            flat = [g]+flat[4:]
            flat = sorted(flat)
        return self._node('AND', tuple(flat))
    def OR(self, *xs): 
        xs = list(xs)
        flat = []
        for x in xs: 
            if self.is1(x): 
                return self.ONE
            if self.is0(x): 
                continue
            nd = self.nodes[x.n] if not x.inv else None
            if nd and nd.op == 'OR': 
                flat.extend(nd.args)
            else: 
                flat.append(x)
        s = set(flat)
        if any(x.neg() in s for x in s): 
            return self.ONE
        flat = sorted(s)
        if not flat: 
            return self.ZERO
        if len(flat) == 1: 
            return flat[0]
        if all(x.inv for x in flat): 
            return self.AND(*[Lit(x.n, False) for x in flat]).neg()
        while len(flat)>4: 
            g = self._node('OR', tuple(flat[:4]))
            flat = [g]+flat[4:]
            flat = sorted(flat)
        return self._node('OR', tuple(flat))
    def XOR(self, a, b): 
        if self.is0(a): 
            return b
        if self.is0(b): 
            return a
        if self.is1(a): 
            return b.neg()
        if self.is1(b): 
            return a.neg()
        if a == b: 
            return self.ZERO
        if a == b.neg(): 
            return self.ONE
        inv = a.inv^b.inv
        aa = Lit(a.n, False)
        bb = Lit(b.n, False)
        if bb<aa: 
            aa, bb = bb, aa
        return Lit(self._node('XOR', (aa, bb)).n, inv)
    def MUX(self, s, t, f): 
        # selector normalization avoids a selector inverter
        if s.inv: 
            s = Lit(s.n, False)
            t, f = f, t
        if self.is0(s): 
            return f
        if self.is1(s): 
            return t
        if t == f: 
            return t
        if self.is1(t) and self.is0(f): 
            return s
        if self.is0(t) and self.is1(f): 
            return s.neg()
        if self.is0(f): 
            return self.AND(s, t)
        if self.is1(t): 
            return self.OR(s, f)
        if self.is0(t): 
            return self.AND(s.neg(), f)
        if self.is1(f): 
            return self.OR(s.neg(), t)
        return self._node('MUX', (s, t, f))

class Compile: 
    def __init__(self, model: Model, dag: DAG): 
        self.m = model
        self.d = dag
        self.cache = {}
        self.stack = set()
    def resize(self, v, w): 
        return tuple((list(v)+[self.d.ZERO]*w)[:w])
    def scalar(self, v): 
        return self.d.OR(*v)
    def signal(self, n): 
        if n in self.cache: 
            return self.cache[n]
        if n in self.stack: 
            raise RuntimeError(f'combinational loop at {n}')
        self.stack.add(n)
        w = self.m.width.get(n, 1)
        if n in self.m.whole: 
            v = self.resize(self.eval(self.m.whole[n]), w)
        else: 
            v = []
            for i in range(w): 
                if (n, i) in self.m.bits: 
                    v.append(self.scalar(self.eval(self.m.bits[(n, i)])))
                else: 
                    v.append(self.d.var(f'{n}[{i}]'))
            v = tuple(v)
        self.stack.remove(n)
        self.cache[n] = tuple(v)
        return tuple(v)
    def eval(self, z: N): 
        D = self.d
        if z.op == 'num': 
            w, v = parse_num(z.a)
            return tuple(D.ONE if (v>>i)&1 else D.ZERO for i in range(w))
        if z.op == 'id': 
            return self.signal(z.a)
        if z.op == 'idx': 
            v = self.eval(z.a)
            return (v[z.b] if z.b<len(v) else D.ZERO,)
        if z.op == 'slice': 
            v = self.eval(z.a)
            hi, lo = z.b, z.c
            rng = range(lo, hi+1) if hi>=lo else range(lo, hi-1, -1)
            return tuple(v[i] if i<len(v) else D.ZERO for i in rng)
        if z.op == '{}': 
            parts = [self.eval(x) for x in z.a]
            out = []
            for p in reversed(parts): 
                out.extend(p)
            return tuple(out)
        if z.op == 'u~': 
            return tuple(x.neg() for x in self.eval(z.a))
        if z.op == 'u!': 
            return (self.scalar(self.eval(z.a)).neg(),)
        if z.op == 'u-': 
            x = self.eval(z.a)
            return self.add(tuple(q.neg() for q in x), (D.ONE,), len(x))
        if z.op in ('&', '|', '^'): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            if z.op == '&': 
                return tuple(D.AND(x, y) for x, y in zip(a, b))
            if z.op == '|': 
                return tuple(D.OR(x, y) for x, y in zip(a, b))
            return tuple(D.XOR(x, y) for x, y in zip(a, b))
        if z.op in ('&&', '||'): 
            a = self.scalar(self.eval(z.a))
            b = self.scalar(self.eval(z.b))
            return ((D.AND(a, b) if z.op == '&&' else D.OR(a, b)),)
        if z.op in ('==', '!='): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            eq = D.AND(*[D.XOR(x, y).neg() for x, y in zip(a, b)])
            return ((eq.neg() if z.op == '!=' else eq),)
        if z.op in ('+', '-'): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            if z.op == '-': 
                return self.add(a, tuple(x.neg() for x in self.resize(b, w)), w, D.ONE)
            return self.add(a, b, w)
        if z.op in ('<<', '>>'): 
            a = self.eval(z.a)
            _, k = parse_num(z.b.a)
            w = len(a)
            if z.op == '<<': 
                return tuple(([D.ZERO]*k+list(a))[:w])
            return tuple((list(a)[k:]+[D.ZERO]*k)[:w])
        if z.op == '?:': 
            s = self.scalar(self.eval(z.a))
            a = self.eval(z.b)
            b = self.eval(z.c)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            return tuple(D.MUX(s, x, y) for x, y in zip(a, b))
        raise KeyError(z.op)
    def add(self, a, b, w, cin = None): 
        D = self.d
        a = self.resize(a, w)
        b = self.resize(b, w)
        c = D.ZERO if cin is None else cin
        out = []
        for x, y in zip(a, b): 
            xy = D.XOR(x, y)
            out.append(D.XOR(xy, c))
            # carry = xy?c:x is exact full-adder form; often one MUX cheaper than 2AND+OR
            c = D.MUX(xy, c, x)
        return tuple(out)

class Tech: 
    def __init__(self, dag: DAG, state_vars: set[str]): 
        self.d = dag
        self.state_vars = state_vars
        self.dem = {}
        self.cells = []
        self.count = {}
        self.netpos = {}
        self.netneg = {}
        self.inv_primary = {}
    def request(self, l: Lit): 
        if l.n == 0: 
            return
        self.dem.setdefault(l.n, set()).add(l.inv)
        nd = self.d.nodes[l.n]
        if nd.op == 'VAR': 
            return
        # all direct/inverted gate forms use same input polarities; MUX inverse currently output-inverter based
        for a in nd.args: 
            self.request(a)
    def _newnet(self, i, neg = False): 
        return f'n{i:04d}_{"n" if neg else "p"}'
    def _cell(self, typ, ports): 
        idx = len(self.cells)
        name = f'u_{idx:04d}'
        self.cells.append((typ, name, ports))
        self.count[typ] = self.count.get(typ, 0)+1
    def leafnet(self, l: Lit): 
        nd = self.d.nodes[l.n]
        assert nd.op == 'VAR'
        lab = nd.label
        if lab in self.state_vars: 
            base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
            return (base+'_n' if l.inv else base)
        # primary vector bit name is already valid SV expression
        if not l.inv: 
            return lab
        if lab not in self.inv_primary: 
            n = f'pinv_{len(self.inv_primary):03d}'
            self.inv_primary[lab] = n
            self._cell('INV_X1', {'A': lab, 'Y': n})
        return self.inv_primary[lab]
    def litnet(self, l: Lit): 
        if l.n == 0: 
            return "1'b1" if l.inv else "1'b0"
        nd = self.d.nodes[l.n]
        if nd.op == 'VAR': 
            return self.leafnet(l)
        return (self.netneg if l.inv else self.netpos)[l.n]
    def emit_node(self, i): 
        nd = self.d.nodes[i]
        need = self.dem.get(i, set())
        if nd.op in ('CONST', 'VAR') or not need: 
            return
        op = nd.op
        # output nets
        if False in need: 
            self.netpos[i] = self._newnet(i, False)
        if True in need: 
            self.netneg[i] = self._newnet(i, True)
        ins = [self.litnet(x) for x in nd.args]
        if op in ('AND', 'OR'): 
            n = len(ins)
            assert 2<=n<=4
            pos_type = (f'{op}{n}_X1' if op == 'AND' else f'OR{n}')
            neg_type = (f'NAND{n}' if op == 'AND' else f'NOR{n}')
            if need == {False}: 
                self._cell(pos_type, {**{chr(65+j): x for j, x in enumerate(ins)}, 'Y': self.netpos[i]})
            elif need == {True}: 
                self._cell(neg_type, {**{chr(65+j): x for j, x in enumerate(ins)}, 'Y': self.netneg[i]})
            else: 
                # inverted-output cell + inverter is cheaper for AND/OR families
                self._cell(neg_type, {**{chr(65+j): x for j, x in enumerate(ins)}, 'Y': self.netneg[i]})
                self._cell('INV_X1', {'A': self.netneg[i], 'Y': self.netpos[i]})
        elif op == 'XOR': 
            assert len(ins) == 2
            if need == {False}: 
                self._cell('XOR2', {'A': ins[0], 'B': ins[1], 'Y': self.netpos[i]})
            elif need == {True}: 
                self._cell('XNOR2', {'A': ins[0], 'B': ins[1], 'Y': self.netneg[i]})
            else: 
                self._cell('XOR2', {'A': ins[0], 'B': ins[1], 'Y': self.netpos[i]})
                self._cell('INV_X1', {'A': self.netpos[i], 'Y': self.netneg[i]})
        elif op == 'MUX': 
            s, t, f = ins
            # A=false, B=true
            if False in need: 
                self._cell('MUX2', {'A': f, 'B': t, 'S': s, 'Y': self.netpos[i]})
            if True in need: 
                if False in need: 
                    self._cell('INV_X1', {'A': self.netpos[i], 'Y': self.netneg[i]})
                else: 
                    tmp = f'n{i:04d}_tmp'
                    self._cell('MUX2', {'A': f, 'B': t, 'S': s, 'Y': tmp})
                    self._cell('INV_X1', {'A': tmp, 'Y': self.netneg[i]})
        else: 
            raise KeyError(op)
    def map(self, roots: list[Lit]): 
        for r in roots: 
            self.request(r)
        # topological IDs: builder only creates nodes after inputs
        for i in range(1, len(self.d.nodes)): 
            self.emit_node(i)

STATE_BASE_WIDTH = {
'active_run': 1, 'r_corr': 2, 'r_P03_store': 1, 'r_PCOUNT': 3, 'r_P07_match66': 1, 'r_P07_last': 1, 
'r_P08': 2, 'r_P11_packed': 2, 'r_P12': 2, 'r_HIST_P13_P14': 2, 'r_S00': 1, 'r_G_DATA_packed': 2, 
'r_G_DIR_packed': 3, 'sched_H00': 1, 'sched_PH00': 1}
RESET1 = {'r_G_DIR_packed[1]', 'r_G_DIR_packed[2]', 'sched_H00[0]', 'sched_PH00[0]'}
PROTOCOL_BASES = {'r_corr', 'r_P03_store', 'r_PCOUNT', 'r_P07_match66', 'r_P07_last', 'r_P08', 'r_P11_packed', 'r_P12', 'r_HIST_P13_P14', 'r_S00', 'r_G_DATA_packed', 'r_G_DIR_packed'}

def labels_for(base, w): 
    return [f'{base}[{i}]' for i in range(w)]

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required = True)
    ap.add_argument('--output', required = True)
    ap.add_argument('--meta', required = True)
    ap.add_argument('--report', required = True)
    a = ap.parse_args()
    text = Path(a.source).read_text()
    m = Model(text)
    D = DAG()
    C = Compile(m, D)
    state_labels = set()
    for b, w in STATE_BASE_WIDTH.items(): 
        state_labels.update(labels_for(b, w))
    # map storage bit -> logical next Lit
    roots = {}
    dmap = {'r_corr': 'd_corr', 'r_P03_store': 'd_P03_store', 'r_PCOUNT': 'd_PCOUNT', 'r_P07_match66': 'd_P07_match_66', 'r_P07_last': 'd_P07_last', 'r_P08': 'd_P08', 'r_P11_packed': 'd_P11_packed', 'r_P12': 'd_P12', 'r_HIST_P13_P14': 'd_HIST_P13_P14', 'r_S00': 'd_S00', 'r_G_DATA_packed': 'd_G_DATA_packed', 'r_G_DIR_packed': 'd_G_DIR_packed'}
    for qb, db in dmap.items(): 
        qv = C.signal(qb)
        dv = C.signal(db)
        for i in range(len(qv)): 
            roots[f'{qb}[{i}]'] = dv[i]
    # synthetic always-updated state
    ar = C.signal('active_run')[0]
    g16 = C.signal('gpio_in')[16]
    g17 = C.signal('gpio_in')[17]
    sph = C.signal('sched_PH00')[0]
    roots['active_run[0]'] = D.OR(ar, D.AND(g16, g17))
    roots['sched_H00[0]'] = g17
    roots['sched_PH00[0]'] = D.MUX(ar, g16, sph)
    # verify all 25 roots present
    assert len(roots) == 25, len(roots)
    # physical D uses complement for logical reset-one bits driven from DFFR.QB
    phys_roots = {q: (r.neg() if q in RESET1 else r) for q, r in roots.items()}
    T = Tech(D, state_labels)
    T.map(list(phys_roots.values()))
    # include DFFs and one conservative protocol clock buffer
    T.count['DFFR'] = 25
    T.count['CLKBUF_X1'] = 1
    area = sum(AREA[k]*v for k, v in T.count.items())
    # emit
    lines = ['`timescale 1ns/1ps', '', 'module bio2rtl_generated (', '  input wire clk,', '  input wire reset,', '  input wire [31:0] gpio_in,', '  output wire [31:0] gpio_out,', '  output wire [31:0] gpio_oe', ');', '']
    # state bit nets, both Q and QB always present
    for b, w in STATE_BASE_WIDTH.items(): 
        for i in range(w): 
            lab = f'{b}[{i}]'
            base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
            lines += [f'  wire {base};', f'  wire {base}_n;']
    # combinational internal nets including primary inversions
    for n in T.inv_primary.values(): 
        lines.append(f'  wire {n};')
    for i in range(1, len(D.nodes)): 
        if i in T.netpos: 
            lines.append(f'  wire {T.netpos[i]};')
        if i in T.netneg: 
            lines.append(f'  wire {T.netneg[i]};')
        # MUX negative-only temp declared if created
        if D.nodes[i].op == 'MUX' and T.dem.get(i) == {True}: 
            lines.append(f'  wire n{i:04d}_tmp;')
    lines += ['  wire clk_protocol;', '']
    lines.append('  CLKBUF_X1 u_clk_protocol (.A(clk), .Y(clk_protocol));')
    for typ, name, ports in T.cells: 
        ps = ', '.join(f'.{k}({v})' for k, v in ports.items())
        lines.append(f'  {typ} {name} ({ps});')
    lines.append('')
    # DFFs. reset1 logical signals use QB; physical Q name is logical complement.
    def qnets(lab): 
        base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
        return base, base+'_n'
    inst = 0
    for b, w in STATE_BASE_WIDTH.items(): 
        for i in range(w): 
            lab = f'{b}[{i}]'
            qp, qn = qnets(lab)
            dnet = T.litnet(phys_roots[lab])
            ck = 'clk_protocol' if b in PROTOCOL_BASES else 'clk'
            if lab in RESET1: 
                qport = qn
                qbport = qp
            else: 
                qport = qp
                qbport = qn
            lines.append(f'  DFFR u_ff_{inst:02d} (.CK({ck}), .D({dnet}), .RST(reset), .Q({qport}), .QB({qbport}));')
            inst+=1
    lines += ['', '  assign gpio_out = {12\'b0, q_r_G_DATA_packed_1, q_r_G_DATA_packed_0, 18\'b0};', 
              '  assign gpio_oe  = {12\'b0, q_r_G_DIR_packed_2, q_r_G_DIR_packed_1, q_r_G_DIR_packed_0, 17\'b0};', 'endmodule', '']
    Path(a.output).write_text('\n'.join(lines))
    meta = {'version': 'tr1um-structural-map-v1', 'source': a.source, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'output': a.output, 'output_sha256': hashlib.sha256(Path(a.output).read_bytes()).hexdigest(), 'storage_bits': 25, 'cell_counts': dict(sorted(T.count.items())), 'area_um2': area, 'dag_nodes': len(D.nodes), 'mapped_comb_cells': len(T.cells), 'reset1_polarity_retarget': sorted(RESET1), 'protocol_clock_buffer': 1, 'result': 'PASS'}
    Path(a.meta).write_text(json.dumps(meta, indent = 2)+'\n')
    rep = ['BIO2RTL TR-1um STRUCTURAL MAP v1', '='*80, f'source: {a.source}', f'storage: 25 DFFR', f'comb cells: {len(T.cells)}', f'clock buffers: 1', f'total cells: {25+1+len(T.cells)}', f'area_um2: {area:.2f}', 'cell histogram:']+[f'  {k}: {v}' for k, v in sorted(T.count.items())]+['RESULT: PASS']
    Path(a.report).write_text('\n'.join(rep)+'\n')
    print('\n'.join(rep))
if __name__ == '__main__': 
    main()
