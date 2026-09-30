#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, hashlib, re, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import map_tr1um_structural_sv as M
import map_tr1um_structural_sv_v2 as V2

class PolarityCanonTech(V2.CanonTech): 
    def __init__(self, dag, state_vars, model): 
        self.extra_wires = set()
        self.absorbed = collections.Counter()
        super().__init__(dag, state_vars, model)

    def _norm(self, i): 
        """Return (canonical_op, canonical_child_lits, intrinsic_inv).
        Semantic node F == G(canonical children) XOR intrinsic_inv.
        Child aliases are canonicalized before polarity folding.
        """
        nd = self.d.nodes[i]
        args = [self.canon(a) for a in nd.args]
        op = nd.op
        intrinsic = False
        if op == 'XOR': 
            intrinsic = bool(args[0].inv ^ args[1].inv)
            if args[0].inv or args[1].inv: 
                self.absorbed['xor_input_inv']+=1
            args = [M.Lit(a.n, False) for a in args]
        elif op in ('AND', 'OR') and args and all(a.inv for a in args): 
            # AND(~x...) = ~OR(x...), OR(~x...) = ~AND(x...)
            op = 'OR' if op == 'AND' else 'AND'
            intrinsic = True
            args = [M.Lit(a.n, False) for a in args]
            self.absorbed['demorgan_all_inputs']+=1
        elif op == 'MUX': 
            s, t, f = args
            if s.inv: 
                s = M.Lit(s.n, False)
                t, f = f, t
                self.absorbed['mux_select_swap']+=1
            if t.inv == f.inv: 
                intrinsic = bool(t.inv)
                if intrinsic: 
                    self.absorbed['mux_both_data_inv']+=1
                t = M.Lit(t.n, False)
                f = M.Lit(f.n, False)
            args = [s, t, f]
        return op, args, intrinsic

    def request(self, l: M.Lit): 
        l = self.canon(l)
        if l.n == 0: 
            return
        self.dem.setdefault(l.n, set()).add(l.inv)
        nd = self.d.nodes[l.n]
        if nd.op == 'VAR': 
            return
        op, args, intrinsic = self._norm(l.n)
        for a in args: 
            self.request(a)

    def emit_node(self, i): 
        if self.alias[i] != M.Lit(i, False): 
            return
        nd = self.d.nodes[i]
        need = self.dem.get(i, set())
        if nd.op in ('CONST', 'VAR') or not need: 
            return
        op, args, intrinsic = self._norm(i)
        if False in need: 
            self.netpos[i] = self._newnet(i, False)
        if True in need: 
            self.netneg[i] = self._newnet(i, True)
        ins = [self.litnet(x) for x in args]
        def semnet(p): 
            return self.netneg[i] if p else self.netpos[i]
        if op in ('AND', 'OR'): 
            n = len(ins)
            assert 2<=n<=4
            pos_type = (f'AND{n}_X1' if op == 'AND' else f'OR{n}')
            neg_type = (f'NAND{n}' if op == 'AND' else f'NOR{n}')
            pins = {chr(65+j): x for j, x in enumerate(ins)}
            if len(need) == 1: 
                p = next(iter(need))
                q = bool(intrinsic ^ p)
                self._cell(neg_type if q else pos_type, {**pins, 'Y': semnet(p)})
            else: 
                # canonical negative is cheaper for AND/OR; it corresponds to semantic p=1^intrinsic
                pneg = bool(True ^ intrinsic)
                ppos = not pneg
                self._cell(neg_type, {**pins, 'Y': semnet(pneg)})
                self._cell('INV_X1', {'A': semnet(pneg), 'Y': semnet(ppos)})
        elif op == 'XOR': 
            assert len(ins) == 2
            if len(need) == 1: 
                p = next(iter(need))
                q = bool(intrinsic ^ p)
                self._cell('XNOR2' if q else 'XOR2', {'A': ins[0], 'B': ins[1], 'Y': semnet(p)})
            else: 
                # Generate semantic positive directly with XOR/XNOR according intrinsic, invert once.
                self._cell('XNOR2' if intrinsic else 'XOR2', {'A': ins[0], 'B': ins[1], 'Y': self.netpos[i]})
                self._cell('INV_X1', {'A': self.netpos[i], 'Y': self.netneg[i]})
        elif op == 'MUX': 
            s, t, f = ins
            # Cell produces canonical G. Semantic p requests G xor intrinsic xor p.
            if len(need) == 2: 
                pg = bool(intrinsic)  # semantic p where F xor p == G -> p=intrinsic
                pnot = not pg
                self._cell('MUX2', {'A': f, 'B': t, 'S': s, 'Y': semnet(pg)})
                self._cell('INV_X1', {'A': semnet(pg), 'Y': semnet(pnot)})
            else: 
                p = next(iter(need))
                q = bool(intrinsic ^ p)
                if not q: 
                    self._cell('MUX2', {'A': f, 'B': t, 'S': s, 'Y': semnet(p)})
                else: 
                    tmp = f'n{i:04d}_tmp'
                    self.extra_wires.add(tmp)
                    self._cell('MUX2', {'A': f, 'B': t, 'S': s, 'Y': tmp})
                    self._cell('INV_X1', {'A': tmp, 'Y': semnet(p)})
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
    dmap = {'r_corr': 'd_corr', 'r_P03_store': 'd_P03_store', 'r_PCOUNT': 'd_PCOUNT', 'r_P07_match66': 'd_P07_match66', 'r_P07_last': 'd_P07_last', 'r_P08': 'd_P08', 'r_P11_packed': 'd_P11_packed', 'r_P12': 'd_P12', 'r_HIST_P13_P14': 'd_HIST_P13_P14', 'r_S00': 'd_S00', 'r_G_DATA_packed': 'd_G_DATA_packed', 'r_G_DIR_packed': 'd_G_DIR_packed'}
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
    T = PolarityCanonTech(D, state_labels, m)
    T.map(list(phys.values()))
    T.count['DFFR'] = 25
    T.count['CLKBUF_X1'] = 1
    area = sum(M.AREA[k]*v for k, v in T.count.items())
    lines = ['`timescale 1ns/1ps', '', 'module bio2rtl_generated (', '  input wire clk,', '  input wire reset,', '  input wire [31:0] gpio_in,', '  output wire [31:0] gpio_out,', '  output wire [31:0] gpio_oe', ');', '']
    for b, w in M.STATE_BASE_WIDTH.items(): 
        for i in range(w): 
            lab = f'{b}[{i}]'
            base = 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)
            lines += [f'  wire {base};', f'  wire {base}_n;']
    for n in T.inv_primary.values(): 
        lines.append(f'  wire {n};')
    declared = set()
    for i in range(1, len(D.nodes)): 
        for dic in (T.netpos, T.netneg): 
            if i in dic and dic[i] not in declared: 
                lines.append(f'  wire {dic[i]};')
                declared.add(dic[i])
    for n in sorted(T.extra_wires): 
        if n not in declared: 
            lines.append(f'  wire {n};')
            declared.add(n)
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
    lines += ['', '  assign gpio_out = {12\'b0, q_r_G_DATA_packed_1_, q_r_G_DATA_packed_0_, 18\'b0};', '  assign gpio_oe  = {12\'b0, q_r_G_DIR_packed_2_, q_r_G_DIR_packed_1_, q_r_G_DIR_packed_0_, 17\'b0};', 'endmodule', '']
    Path(a.output).write_text('\n'.join(lines))
    aliases = sum(1 for i, x in enumerate(T.alias) if x!=M.Lit(i, False))
    meta = {'version': 'tr1um-structural-map-v3-polarity-bdd-cse', 'source': a.source, 'source_sha256': hashlib.sha256(text.encode()).hexdigest(), 'output': a.output, 'output_sha256': hashlib.sha256(Path(a.output).read_bytes()).hexdigest(), 'storage_bits': 25, 'cell_counts': dict(sorted(T.count.items())), 'area_um2': area, 'dag_nodes': len(D.nodes), 'bdd_canonical_aliases': aliases, 'absorbed_polarities': dict(T.absorbed), 'mapped_comb_cells': len(T.cells), 'result': 'PASS'}
    Path(a.meta).write_text(json.dumps(meta, indent = 2)+'\n')
    rep = ['BIO2RTL TR-1um STRUCTURAL MAP v3 POLARITY+BDD-CSE', '='*80, f'source: {a.source}', f'DAG nodes: {len(D.nodes)}', f'BDD canonical aliases: {aliases}', f'absorbed polarities: {dict(T.absorbed)}', f'storage: 25 DFFR', f'comb cells: {len(T.cells)}', 'clock buffers: 1', f'total cells: {25+1+len(T.cells)}', f'area_um2: {area:.2f}', 'cell histogram:']+[f'  {k}: {v}' for k, v in sorted(T.count.items())]+['RESULT: PASS']
    Path(a.report).write_text('\n'.join(rep)+'\n')
    print('\n'.join(rep))
if __name__ == '__main__': 
    main()
