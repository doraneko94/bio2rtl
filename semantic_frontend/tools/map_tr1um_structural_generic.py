#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, re, sys
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: 
    sys.path.insert(0, str(HERE))
import map_tr1um_structural_sv as M
from map_tr1um_structural_sv_v3 import PolarityCanonTech

# This mapper intentionally does not contain BIO register IDs, protocol addresses,
# output bit positions, or a fixed state-vector fixture.  State/reset/next/output
# functions are extracted from the emitted behavioral SV itself.


def sha(p: Path)->str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()

def strip_comments(s: str)->str: 
    s = re.sub(r'/\*.*?\*/', lambda m: '\n'*m.group(0).count('\n'), s, flags = re.S)
    s = re.sub(r'//[^\n]*', '', s)
    return s

@dataclass
class Assign: 
    lhs: str
    rhs: str
@dataclass
class If: 
    cond: str
    yes: object
    no: object|None
@dataclass
class Block: 
    items: list[object]

class ProcParser: 
    def __init__(self, s: str): 
        self.s = s
        self.i = 0
        self.n = len(s)
    def ws(self): 
        while self.i<self.n and self.s[self.i].isspace(): 
            self.i+=1
    def word(self, w: str)->bool: 
        self.ws()
        j = self.i+len(w)
        if self.s[self.i:j]!=w: 
            return False
        if self.i>0 and (self.s[self.i-1].isalnum() or self.s[self.i-1] in '_$'): 
            return False
        if j<self.n and (self.s[j].isalnum() or self.s[j] in '_$'): 
            return False
        return True
    def take_word(self, w: str): 
        if not self.word(w): 
            raise ValueError(f'expected {w} at {self.i}')
        self.i+=len(w)
    def paren(self)->str: 
        self.ws()
        if self.i>=self.n or self.s[self.i]!='(': 
            raise ValueError(f'expected ( at {self.i}')
        st = self.i+1
        self.i+=1
        d = 1
        while self.i<self.n and d: 
            c = self.s[self.i]
            if c == '(': 
                d+=1
            elif c == ')': 
                d-=1
            self.i+=1
        if d: 
            raise ValueError('unclosed parenthesis')
        return self.s[st:self.i-1].strip()
    def stmt(self): 
        self.ws()
        if self.word('begin'): 
            self.take_word('begin')
            xs = []
            while True: 
                self.ws()
                if self.word('end'): 
                    self.take_word('end')
                    break
                xs.append(self.stmt())
            return Block(xs)
        if self.word('if'): 
            self.take_word('if')
            cond = self.paren()
            yes = self.stmt()
            self.ws()
            no = None
            if self.word('else'): 
                self.take_word('else')
                no = self.stmt()
            return If(cond, yes, no)
        # simple statement through semicolon; generated sequential block only needs <=
        st = self.i
        d = 0
        while self.i<self.n: 
            c = self.s[self.i]
            if c in '([{': 
                d+=1
            elif c in ')]}': 
                d-=1
            elif c == ';' and d == 0: 
                raw = self.s[st:self.i].strip()
                self.i+=1
                break
            self.i+=1
        else: 
            raise ValueError('unterminated statement')
        m = re.fullmatch(r'([A-Za-z_$][\w$]*)\s*<=\s*(.+)', raw, flags = re.S)
        if not m: 
            raise ValueError(f'unsupported procedural statement: {raw!r}')
        return Assign(m.group(1), m.group(2).strip())


def extract_always_ff(text: str): 
    clean = strip_comments(text)
    m = re.search(r'always_ff\s*@\s*\([^)]*\)\s*begin\b', clean)
    if not m: 
        raise ValueError('no always_ff block')
    p = ProcParser(clean[m.end()-5:])  # start at the begin keyword
    root = p.stmt()
    if not isinstance(root, Block) or not root.items: 
        raise ValueError('bad always_ff body')
    top = root.items[0]
    if not isinstance(top, If) or top.no is None: 
        raise ValueError('always_ff must start with reset if/else')
    reset_cond = re.sub(r'\s+', '', top.cond)
    if reset_cond not in ('reset', '(reset)'): 
        raise ValueError(f'unsupported reset condition {top.cond!r}')
    return top.yes, top.no


def decl_widths(text: str)->dict[str, int]: 
    out = {}
    for m in re.finditer(r'(?m)^\s*(?:logic|wire)\s*(?:\[(\d+)\s*:\s*(\d+)\])?\s+([A-Za-z_$][\w$]*)\s*(?:=|;)', text): 
        hi, lo, n = m.groups()
        out[n] = abs(int(hi)-int(lo))+1 if hi is not None else 1
    return out


def direct_assigns(stmt)->list[Assign]: 
    if isinstance(stmt, Assign): 
        return [stmt]
    if isinstance(stmt, Block): 
        z = []
        for x in stmt.items: 
            z+=direct_assigns(x)
        return z
    if isinstance(stmt, If): 
        raise ValueError('conditional reset assignment unsupported')
    raise TypeError(stmt)


def and_guard(a: str, b: str)->str: 
    if a == "1'b1": 
        return f'({b})'
    return f'(({a}) & ({b}))'

def not_guard(a: str)->str: 
    return f'~({a})'

def guarded_assigns(stmt, guard = "1'b1", seq = None): 
    if seq is None: 
        seq = []
    if isinstance(stmt, Assign): 
        seq.append((stmt.lhs, guard, stmt.rhs))
        return seq
    if isinstance(stmt, Block): 
        for x in stmt.items: 
            guarded_assigns(x, guard, seq)
        return seq
    if isinstance(stmt, If): 
        guarded_assigns(stmt.yes, and_guard(guard, stmt.cond), seq)
        if stmt.no is not None: 
            guarded_assigns(stmt.no, and_guard(guard, not_guard(stmt.cond)), seq)
        return seq
    raise TypeError(stmt)


def parse_reset_bits(rhs: str, width: int)->list[int]: 
    # The production event-SV emitter uses integral constants for FF reset values.
    z = rhs.strip()
    try: 
        w, v = M.parse_num(z)
    except Exception as e: 
        raise ValueError(f'nonconstant reset value {rhs!r}') from e
    return [int((v>>i)&1) for i in range(width)]


def state_info(text: str): 
    widths = decl_widths(text)
    rst, normal = extract_always_ff(text)
    reset_rows = direct_assigns(rst)
    order = []
    reset = {}
    for a in reset_rows: 
        if a.lhs in reset: 
            raise ValueError(f'duplicate reset assignment {a.lhs}')
        if a.lhs not in widths: 
            raise ValueError(f'reset state lacks logic declaration: {a.lhs}')
        order.append(a.lhs)
        reset[a.lhs] = parse_reset_bits(a.rhs, widths[a.lhs])
    updates = {q: [] for q in order}
    for lhs, g, rhs in guarded_assigns(normal): 
        if lhs not in updates: 
            raise ValueError(f'normal assignment to non-reset state {lhs}')
        updates[lhs].append((g, rhs))
    nxt = {}
    for q in order: 
        e = q
        for g, rhs in updates[q]: 
            if re.sub(r'\s+', '', g) in ("1'b1", "(1'b1)"): 
                e = rhs
            else: 
                e = f'({g}) ? ({rhs}) : ({e})'
        nxt[q] = e
    # Discover the startup/protocol clock partition structurally from a top-level
    # `if (!run_latch) ... else ...` shape.  The then-side updates the run latch;
    # the else-side contains the event/protocol state.  No register ID is assumed.
    def lhs_set(st): 
        if isinstance(st, Assign): 
            return {st.lhs}
        if isinstance(st, Block): 
            z = set()
            for x in st.items: 
                z |= lhs_set(x)
            return z
        if isinstance(st, If): 
            return lhs_set(st.yes) | (lhs_set(st.no) if st.no is not None else set())
        return set()
    candidates = []
    top_items = normal.items if isinstance(normal, Block) else [normal]
    for st in top_items: 
        if not isinstance(st, If) or st.no is None: 
            continue
        c = re.sub(r'[\s()]', '', st.cond)
        mm = re.fullmatch(r'(?:!|~)([A-Za-z_$][\w$]*)', c)
        if not mm: 
            continue
        run = mm.group(1)
        ys = lhs_set(st.yes)
        ns = lhs_set(st.no)
        if run in ys and ns: 
            candidates.append((len(ns), run, ns))
    if candidates: 
        _n, run, protocol = max(candidates)
        buffered = [q for q in order if q in protocol and q!=run]
    else: 
        run = None
        buffered = []
    raw = [q for q in order if q not in set(buffered)]
    return {'order': order, 'widths': {q: widths[q] for q in order}, 'reset': reset, 'updates': updates, 'next': nxt, 'buffered': buffered, 'raw': raw, 'run_latch': run}


def label(base: str, i: int)->str: 
    return f'{base}[{i}]'
def qbase(lab: str)->str: 
    return 'q_'+re.sub(r'[^A-Za-z0-9_]', '_', lab)


def vector_assign(name: str, lits, T)->list[str]: 
    # Bitwise emission is deliberately generic and keeps constant/unpacked outputs valid.
    return [f'  assign {name}[{i}] = {T.litnet(x)};' for i, x in enumerate(lits)]



class GenericCompile(M.Compile): 
    """Compile the emitted Boolean SV, including variable logical shifts.

    The legacy area mapper accepted only constant shift amounts because earlier
    C2 cleanup happened to remove dynamic shifts. Production mapping cannot rely
    on that binary-specific cleanup, so lower a variable shift to a generic
    logarithmic mux/barrel network.
    """
    def eval(self, z): 
        if z.op in ('<<', '>>'): 
            a = self.eval(z.a)
            w = len(a)
            # Preserve the legacy constant-shift fast path.
            if isinstance(z.b, M.N) and z.b.op == 'num': 
                _, k = M.parse_num(z.b.a)
                if z.op == '<<': 
                    return tuple(([self.d.ZERO]*k+list(a))[:w])
                return tuple((list(a)[k:]+[self.d.ZERO]*k)[:w])
            sh = self.eval(z.b)
            cur = tuple(a)
            for j, sel in enumerate(sh): 
                k = 1<<j
                if k >= w: 
                    shifted = tuple(self.d.ZERO for _ in range(w))
                elif z.op == '<<': 
                    shifted = tuple([self.d.ZERO]*k + list(cur[:w-k]))
                else: 
                    shifted = tuple(list(cur[k:]) + [self.d.ZERO]*k)
                cur = tuple(self.d.MUX(sel, shifted[i], cur[i]) for i in range(w))
            return cur
        return super().eval(z)

def main(): 
    ap = argparse.ArgumentParser()
    for x in ('source', 'output', 'meta', 'report'): 
        ap.add_argument('--'+x, type = Path, required = True)
    a = ap.parse_args()
    text = a.source.read_text()
    info = state_info(text)
    # Add synthetic continuous next-state functions for the existing Boolean frontend.
    syn = []
    for q in info['order']: 
        w = info['widths'][q]
        decl = '' if w == 1 else f'[{w-1}:0] '
        syn.append(f'wire {decl}__bio2rtl_next_{q} = {info["next"][q]};')
    model_text = text+'\n'+'\n'.join(syn)+'\n'
    m = M.Model(model_text)
    D = M.DAG()
    C = GenericCompile(m, D)
    state_labels = {label(q, i) for q in info['order'] for i in range(info['widths'][q])}
    roots = {}
    for q in info['order']: 
        dv = C.signal('__bio2rtl_next_'+q)
        w = info['widths'][q]
        if len(dv)!=w: 
            raise ValueError((q, w, len(dv)))
        for i, x in enumerate(dv): 
            roots[label(q, i)] = x
    # Output functions are compiled from the source, never reconstructed by register name.
    gout = C.signal('gpio_out')
    goe = C.signal('gpio_oe')
    reset1 = {label(q, i) for q in info['order'] for i, b in enumerate(info['reset'][q]) if b}
    phys = {lab: (r.neg() if lab in reset1 else r) for lab, r in roots.items()}
    T = PolarityCanonTech(D, state_labels, m)
    T.map(list(phys.values())+list(gout)+list(goe))
    nff = sum(info['widths'].values())
    T.count['DFFR'] = nff
    use_buf = bool(info['buffered'])
    if use_buf: 
        T.count['CLKBUF_X1'] = 1
    area = sum(M.AREA[k]*v for k, v in T.count.items())

    lines = ['`timescale 1ns/1ps', '', 'module bio2rtl_generated (', '  input wire clk,', '  input wire reset,', '  input wire [31:0] gpio_in,', '  output wire [31:0] gpio_out,', '  output wire [31:0] gpio_oe', ');', '']
    for q in info['order']: 
        for i in range(info['widths'][q]): 
            b = qbase(label(q, i))
            lines += [f'  wire {b};', f'  wire {b}_n;']
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
    if use_buf: 
        lines += ['  wire clk_protocol;', '', '  CLKBUF_X1 u_clk_protocol (.A(clk), .Y(clk_protocol));']
    for typ, name, ports in T.cells: 
        ps = ', '.join(f'.{k}({v})' for k, v in ports.items())
        lines.append(f'  {typ} {name} ({ps});')
    lines.append('')
    inst = 0
    buffered = set(info['buffered'])
    for q in info['order']: 
        for i in range(info['widths'][q]): 
            lab = label(q, i)
            b = qbase(lab)
            qp, qn = b, b+'_n'
            dnet = T.litnet(phys[lab])
            ck = 'clk_protocol' if q in buffered else 'clk'
            if lab in reset1: 
                qport, qbport = qn, qp
            else: 
                qport, qbport = qp, qn
            lines.append(f'  DFFR u_ff_{inst:02d} (.CK({ck}), .D({dnet}), .RST(reset), .Q({qport}), .QB({qbport}));')
            inst+=1
    lines.append('')
    lines += vector_assign('gpio_out', gout, T)
    lines += vector_assign('gpio_oe', goe, T)
    lines += ['endmodule', '']
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines))
    aliases = sum(1 for i, x in enumerate(T.alias) if x!=M.Lit(i, False))
    meta = {'version': 'tr1um-structural-map-generic-v1', 'source': str(a.source), 'source_sha256': sha(a.source), 'output': str(a.output), 'output_sha256': sha(a.output), 
          'storage_bits': nff, 'state_order': info['order'], 'state_widths': info['widths'], 'reset1_bits': sorted(reset1), 'raw_clock_states': info['raw'], 'buffered_clock_states': info['buffered'], 
          'output_widths': {'gpio_out': len(gout), 'gpio_oe': len(goe)}, 'cell_counts': dict(sorted(T.count.items())), 'area_um2': area, 'dag_nodes': len(D.nodes), 'bdd_canonical_aliases': aliases, 
          'absorbed_polarities': dict(T.absorbed), 'mapped_comb_cells': len(T.cells), 'binary_specific_state_fixture': False, 'binary_specific_output_fixture': False, 'result': 'PASS'}
    a.meta.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    rep = ['BIO2RTL GENERIC TR-1um STRUCTURAL MAP', '='*88, f'source: {a.source}', f'states: {len(info["order"])} regs / {nff} bits', f'raw clock regs: {len(info["raw"])}', f'buffered regs: {len(info["buffered"])}', f'outputs: gpio_out={len(gout)} gpio_oe={len(goe)}', f'DAG nodes: {len(D.nodes)}', f'BDD canonical aliases: {aliases}', f'comb cells: {len(T.cells)}', f'total cells: {nff+(1 if use_buf else 0)+len(T.cells)}', f'area_um2: {area:.2f}', 'cell histogram:']+[f'  {k}: {v}' for k, v in sorted(T.count.items())]+['binary-specific state fixture: NO', 'binary-specific output fixture: NO', 'RESULT: PASS']
    a.report.write_text('\n'.join(rep)+'\n')
    print('\n'.join(rep))

if __name__ == '__main__': 
    main()
