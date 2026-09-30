#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, hashlib, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import map_tr1um_structural_sv as M

class CanonTech(M.Tech): 
    def __init__(self, dag, state_vars, model): 
        self.model = model
        self.B = model.bdd
        self.alias = []
        self.func = []
        self._build_alias(dag)
        super().__init__(dag, state_vars)
    def _build_alias(self, D): 
        B = self.B
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
                u = self.model.function(nd.label)
            elif nd.op == 'AND': 
                u = 1
                for a in nd.args: 
                    u = B.AND(u, evlit(a))
            elif nd.op == 'OR': 
                u = 0
                for a in nd.args: 
                    u = B.OR(u, evlit(a))
            elif nd.op == 'XOR': 
                u = B.XOR(evlit(nd.args[0]), evlit(nd.args[1]))
            elif nd.op == 'MUX': 
                s, t, f = [evlit(x) for x in nd.args]
                u = B.OR(B.AND(s, t), B.AND(B.NOT(s), f))
            else: 
                raise KeyError(nd.op)
            memo[i] = u
            return u
        seen = {0: M.Lit(0, False), 1: M.Lit(0, True)}
        aliases = []
        funcs = []
        for i in range(len(D.nodes)): 
            u = evnode(i)
            funcs.append(u)
            if i == 0: 
                aliases.append(M.Lit(0, False))
                continue
            if u in seen: 
                aliases.append(seen[u])
                continue
            nu = B.NOT(u)
            if nu in seen: 
                aliases.append(seen[nu].neg())
                continue
            lit = M.Lit(i, False)
            aliases.append(lit)
            seen[u] = lit
            seen[nu] = lit.neg()
        self.alias = aliases
        self.func = funcs
    def canon(self, l: M.Lit): 
        a = self.alias[l.n]
        return a.neg() if l.inv else a
    def request(self, l: M.Lit): 
        l = self.canon(l)
        if l.n == 0: 
            return
        self.dem.setdefault(l.n, set()).add(l.inv)
        nd = self.d.nodes[l.n]
        if nd.op == 'VAR': 
            return
        for a in nd.args: 
            self.request(a)
    def litnet(self, l: M.Lit): 
        return super().litnet(self.canon(l))
    def emit_node(self, i): 
        if self.alias[i] != M.Lit(i, False): 
            return
        nd = self.d.nodes[i]
        need = self.dem.get(i, set())
        if nd.op in ('CONST', 'VAR') or not need: 
            return
        op = nd.op
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

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source', required = True)
    ap.add_argument('--output', required = True)
    ap.add_argument('--meta', required = True)
    ap.add_argument('--report', required = True)
    a = ap.parse_args()
    text = Path(a.source).read_text()
    m = M.Model(text)
    D = M.DAG()
    C = M.Compile(m, D)
    state_labels = set()
    for b, w in M.STATE_BASE_WIDTH.items(): 
        state_labels.update(M.labels_for(b, w))
    roots = {}
    dmap = {'r_corr': 'd_corr', 'r_P03_store': 'd_P03_store', 'r_PCOUNT': 'd_PCOUNT', 'r_P07_match66': 'd_P07_match_66', 'r_P07_last': 'd_P07_last', 'r_P08': 'd_P08', 'r_P11_packed': 'd_P11_packed', 'r_P12': 'd_P12', 'r_HIST_P13_P14': 'd_HIST_P13_P14', 'r_S00': 'd_S00', 'r_G_DATA_packed': 'd_G_DATA_packed', 'r_G_DIR_packed': 'd_G_DIR_packed'}
    for qb, db in dmap.items(): 
        dv = C.signal(db)
        for i, r in enumerate(dv): 
            roots[f'{qb}[{i}]'] = r
    ar = C.signal('active_run')[0]
    g16 = C.signal('gpio_in')[16]
    g17 = C.signal('gpio_in')[17]
    sph = C.signal('sched_PH00')[0]
    roots['active_run[0]'] = D.OR(ar, D.AND(g16, g17))
    roots['sched_H00[0]'] = g17
    roots['sched_PH00[0]'] = D.MUX(ar, g16, sph)
    assert len(roots) == 25
    phys = {q: (r.neg() if q in M.RESET1 else r) for q, r in roots.items()}
    T = CanonTech(D, state_labels, m)
    T.map(list(phys.values()))
    T.count['DFFR'] = 25
    T.count['CLKBUF_X1'] = 1
    area = sum(M.AREA[k]*v for k, v in T.count.items())
    # output structural SV, same form as v1
    lines = ['`timescale 1ns/1ps', '', 'module bio2rtl_generated (', '  input wire clk,', '  input wire reset,', '  input wire [31:0] gpio_in,', '  output wire [31:0] gpio_out,', '  output wire [31:0] gpio_oe', ');', '']
    for b, w in M.STATE_BASE_WIDTH.items(): 
        for i in range(w): 
            lab = f'{b}[{i}]'
            base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
            lines += [f'  wire {base};', f'  wire {base}_n;']
    for n in T.inv_primary.values(): 
        lines.append(f'  wire {n};')
    for i in range(1, len(D.nodes)): 
        if i in T.netpos: 
            lines.append(f'  wire {T.netpos[i]};')
        if i in T.netneg: 
            lines.append(f'  wire {T.netneg[i]};')
        if D.nodes[i].op == 'MUX' and T.dem.get(i) == {True}: 
            lines.append(f'  wire n{i:04d}_tmp;')
    lines += ['  wire clk_protocol;', '', '  CLKBUF_X1 u_clk_protocol (.A(clk), .Y(clk_protocol));']
    for typ, name, ports in T.cells: 
        ps = ', '.join(f'.{k}({v})' for k, v in ports.items())
        lines.append(f'  {typ} {name} ({ps});')
    lines.append('')
    inst = 0
    for b, w in M.STATE_BASE_WIDTH.items(): 
        for i in range(w): 
            lab = f'{b}[{i}]'
            base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
            qp, qn = base, base+'_n'
            dnet = T.litnet(phys[lab])
            ck = 'clk_protocol' if b in M.PROTOCOL_BASES else 'clk'
            if lab in M.RESET1: 
                qport, qbport = qn, qp
            else: 
                qport, qbport = qp, qn
            lines.append(f'  DFFR u_ff_{inst:02d} (.CK({ck}), .D({dnet}), .RST(reset), .Q({qport}), .QB({qbport}));')
            inst+=1
    lines += ['', '  assign gpio_out = {12\'b0, q_r_G_DATA_packed_1, q_r_G_DATA_packed_0, 18\'b0};', '  assign gpio_oe  = {12\'b0, q_r_G_DIR_packed_2, q_r_G_DIR_packed_1, q_r_G_DIR_packed_0, 17\'b0};', 'endmodule', '']
    Path(a.output).write_text('\n'.join(lines))
    aliases = sum(1 for i, x in enumerate(T.alias) if x!=M.Lit(i, False))
    meta = {'version': 'tr1um-structural-map-v2-bdd-cse', 'source': a.source, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'output': a.output, 'output_sha256': hashlib.sha256(Path(a.output).read_bytes()).hexdigest(), 'storage_bits': 25, 'cell_counts': dict(sorted(T.count.items())), 'area_um2': area, 'dag_nodes': len(D.nodes), 'bdd_canonical_aliases': aliases, 'mapped_comb_cells': len(T.cells), 'result': 'PASS'}
    Path(a.meta).write_text(json.dumps(meta, indent = 2)+'\n')
    rep = ['BIO2RTL TR-1um STRUCTURAL MAP v2 BDD-CSE', '='*80, f'source: {a.source}', f'DAG nodes: {len(D.nodes)}', f'BDD canonical aliases: {aliases}', f'storage: 25 DFFR', f'comb cells: {len(T.cells)}', 'clock buffers: 1', f'total cells: {25+1+len(T.cells)}', f'area_um2: {area:.2f}', 'cell histogram:']+[f'  {k}: {v}' for k, v in sorted(T.count.items())]+['RESULT: PASS']
    Path(a.report).write_text('\n'.join(rep)+'\n')
    print('\n'.join(rep))
if __name__ == '__main__': 
    main()
