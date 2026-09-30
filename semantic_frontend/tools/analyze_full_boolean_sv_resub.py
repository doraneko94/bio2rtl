#!/usr/bin/env python3
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import argparse, re, functools, collections, itertools, json

# ---------- tiny Verilog expression parser ----------
TOK_RE = re.compile(r"\s*(?:(\d+'[bdh][0-9a-fA-F_xXzZ]+|\d+)|([A-Za-z_$][\w$]*)|(==|!=|&&|\|\||<<|>>|[~!&|^+\-?:(),{}\[\]]))")
@dataclass(frozen = True)
class N: 
    op: str
    a: object = None
    b: object = None
    c: object = None

def toks(s): 
    out = []
    i = 0
    while i<len(s): 
        m = TOK_RE.match(s, i)
        if not m: 
            raise SyntaxError(f'cannot tokenize at {s[i:i+60]!r} in {s!r}')
        out.append(m.group(1) or m.group(2) or m.group(3))
        i = m.end()
    return out

PREC = {'||': 1, '&&': 2, '|': 3, '^': 4, '&': 5, '==': 6, '!=': 6, '<<': 7, '>>': 7, '+': 8, '-': 8}
class Parser: 
    def __init__(self, s): 
        self.t = toks(s)
        self.i = 0
    def peek(self): 
        return self.t[self.i] if self.i<len(self.t) else None
    def pop(self, x = None): 
        z = self.peek()
        if x is not None and z!=x: 
            raise SyntaxError((x, z, self.t[max(0, self.i-5):self.i+5]))
        self.i+=1
        return z
    def parse(self): 
        z = self.expr(0)
        if self.peek() is not None: 
            raise SyntaxError(('extra', self.peek(), self.t[self.i:]))
        return z
    def expr(self, minp): 
        z = self.prefix()
        while True: 
            op = self.peek()
            if op == '?' and minp<=0: 
                self.pop('?')
                y = self.expr(0)
                self.pop(':')
                n = self.expr(0)
                z = N('?:', z, y, n)
                continue
            p = PREC.get(op, -1)
            if p<minp: 
                break
            self.pop()
            r = self.expr(p+1)
            z = N(op, z, r)
        return z
    def prefix(self): 
        x = self.pop()
        if x in ('~', '!', '-'): 
            return N('u'+x, self.expr(9))
        if x == '(': 
            z = self.expr(0)
            self.pop(')')
            return self.postfix(z)
        if x == '{': 
            xs = []
            if self.peek()!='}': 
                while True: 
                    xs.append(self.expr(0))
                    if self.peek()!=',': 
                        break
                    self.pop(',')
            self.pop('}')
            return self.postfix(N('{}', tuple(xs)))
        if re.match(r"\d", x): 
            z = N('num', x)
        else: 
            z = N('id', x)
        return self.postfix(z)
    def postfix(self, z): 
        while self.peek() == '[': 
            self.pop('[')
            lo = self.pop()
            if self.peek() == ':': 
                self.pop(':')
                hi = self.pop()
                self.pop(']')
                z = N('slice', z, int(lo), int(hi))
            else: 
                self.pop(']')
                z = N('idx', z, int(lo))
        return z

def parse_num(s): 
    if "'" not in s: 
        v = int(s)
        w = max(32, v.bit_length() or 1)
        return w, v
    w, rest = s.split("'", 1)
    w = int(w)
    base = rest[0].lower()
    digits = rest[1:].replace('_', '')
    if any(c.lower() in 'xz' for c in digits): 
        raise ValueError('xz unsupported')
    return w, int(digits, {'b': 2, 'd': 10, 'h': 16}[base])

# ---------- BDD ----------
class BDD: 
    def __init__(self, names): 
        self.names = list(names)
        self.vid = {n: i for i, n in enumerate(self.names)}
        self.nodes = [None, None]
        self.uniq = {}
        self.var = {n: self.mk(i, 0, 1) for n, i in self.vid.items()}
    def mk(self, v, l, h): 
        if l == h: 
            return l
        k = (v, l, h)
        q = self.uniq.get(k)
        if q is None: 
            q = len(self.nodes)
            self.uniq[k] = q
            self.nodes.append(k)
        return q
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
        if a>b: 
            a, b = b, a
        v = min(self.top(a), self.top(b))
        return self.mk(v, self.XOR(self.cof(a, v, 0), self.cof(b, v, 0)), self.XOR(self.cof(a, v, 1), self.cof(b, v, 1)))
    def reduce_or(self, xs): 
        z = 0
        for x in xs: 
            z = self.OR(z, x)
        return z
    def support(self, u): 
        out = set()
        seen = set()
        st = [u]
        while st: 
            q = st.pop()
            if q<2 or q in seen: 
                continue
            seen.add(q)
            v, l, h = self.nodes[q]
            out.add(self.names[v])
            st += [l, h]
        return frozenset(out)

# ---------- SV model ----------
DECL_RE = re.compile(r'(?m)^\s*(?:input\s+|output\s+)?(?:logic|wire)\s*(?:\[(\d+)\s*:\s*(\d+)\])?\s+([A-Za-z_$][\w$]*)\s*(?:=\s*([^;]+))?;')
PORT_RE = re.compile(r'(?m)^\s*(?:input|output)\s+(?:logic|wire)\s*(?:\[(\d+)\s*:\s*(\d+)\])?\s+([A-Za-z_$][\w$]*)\s*[,)]')
ASSIGN_RE = re.compile(r'(?ms)^\s*assign\s+([^=;]+?)\s*=\s*(.*?);')

class Model: 
    def __init__(self, text): 
        self.text = text
        self.width = {}
        self.whole = {}
        self.bits = {}
        self.deps = collections.defaultdict(set)
        # ports and regular declarations, one name per declaration is enough for generated SV
        for m in PORT_RE.finditer(text): 
            hi, lo, n = m.groups()
            w = abs(int(hi)-int(lo))+1 if hi else 1
            self.width[n] = w
        for m in DECL_RE.finditer(text): 
            hi, lo, n, init = m.groups()
            w = abs(int(hi)-int(lo))+1 if hi else 1
            self.width[n] = w
            if init: 
                try: 
                    self.whole[n] = Parser(init).parse()
                except Exception: 
                    pass
        for m in ASSIGN_RE.finditer(text): 
            lhs = m.group(1).strip()
            rhs = m.group(2).strip()
            ast = Parser(rhs).parse()
            mm = re.fullmatch(r'([A-Za-z_$][\w$]*)\[(\d+)\]', lhs)
            if mm: 
                self.bits[(mm.group(1), int(mm.group(2)))] = ast
            else: 
                self.whole[lhs] = ast
        self.assigned = set(self.whole)|{n for n, i in self.bits}
        self.primary_bits = []
        for n, w in self.width.items(): 
            if n not in self.assigned: 
                for i in range(w): 
                    self.primary_bits.append(f'{n}[{i}]')
        # also unknown undeclared identifiers become primaries discovered later
        self._leaf_names = set(self.primary_bits)
        for ast in list(self.whole.values())+list(self.bits.values()): 
            self._collect_unknown(ast)
        self.bdd = BDD(sorted(self._leaf_names))
        self._vec_cache = {}
        self._dep_cache = {}
    def _collect_unknown(self, z): 
        if not isinstance(z, N): 
            return
        if z.op == 'id': 
            n = z.a
            if n not in self.assigned: 
                w = self.width.get(n, 1)
                for i in range(w): 
                    self._leaf_names.add(f'{n}[{i}]')
        for q in (z.a, z.b, z.c): 
            if isinstance(q, N): 
                self._collect_unknown(q)
            elif isinstance(q, tuple): 
                for x in q: 
                    self._collect_unknown(x)
    def signal(self, n): 
        if n in self._vec_cache: 
            return self._vec_cache[n]
        w = self.width.get(n, 1)
        # whole assignment has priority
        if n in self.whole: 
            v = self.eval(self.whole[n])
            v = self.resize(v, w)
        else: 
            v = []
            for i in range(w): 
                if (n, i) in self.bits: 
                    v.append(self.scalar(self.eval(self.bits[(n, i)])))
                else: 
                    key = f'{n}[{i}]'
                    if key not in self.bdd.var: 
                        raise KeyError(f'unknown primary bit {key}')
                    v.append(self.bdd.var[key])
        self._vec_cache[n] = tuple(v)
        return tuple(v)
    def resize(self, v, w): 
        v = list(v)
        return tuple((v+[0]*w)[:w])
    def scalar(self, v): 
        return self.bdd.reduce_or(v)
    def eval(self, z): 
        B = self.bdd
        if z.op == 'num': 
            w, v = parse_num(z.a)
            return tuple(1 if (v>>i)&1 else 0 for i in range(w))
        if z.op == 'id': 
            return self.signal(z.a)
        if z.op == 'idx': 
            v = self.eval(z.a)
            return (v[z.b] if z.b<len(v) else 0,)
        if z.op == 'slice': 
            v = self.eval(z.a)
            hi, lo = z.b, z.c
            rng = range(lo, hi+1) if hi>=lo else range(lo, hi-1, -1)
            return tuple(v[i] if i<len(v) else 0 for i in rng)
        if z.op == '{}': 
            # Verilog concat writes first item as MSBs; vector representation is LSB-first
            parts = [self.eval(x) for x in z.a]
            out = []
            for p in reversed(parts): 
                out.extend(p)
            return tuple(out)
        if z.op == 'u~': 
            return tuple(B.NOT(x) for x in self.eval(z.a))
        if z.op == 'u!': 
            return (B.NOT(self.scalar(self.eval(z.a))),)
        if z.op == 'u-': 
            x = self.eval(z.a)
            # two's complement
            inv = [B.NOT(q) for q in x]
            return self.add(tuple(inv), (1,), len(x))
        if z.op in ('&', '|', '^'): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            fn = {'&': B.AND, '|': B.OR, '^': B.XOR}[z.op]
            return tuple(fn(x, y) for x, y in zip(a, b))
        if z.op in ('&&', '||'): 
            a = self.scalar(self.eval(z.a))
            b = self.scalar(self.eval(z.b))
            return ((B.AND(a, b) if z.op == '&&' else B.OR(a, b)),)
        if z.op in ('==', '!='): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            q = 1
            for x, y in zip(a, b): 
                q = B.AND(q, B.NOT(B.XOR(x, y)))
            if z.op == '!=': 
                q = B.NOT(q)
            return (q,)
        if z.op in ('+', '-'): 
            a = self.eval(z.a)
            b = self.eval(z.b)
            w = max(len(a), len(b))
            if z.op == '-': 
                b = tuple(B.NOT(x) for x in self.resize(b, w))
                return self.add(a, b, w, cin = 1)
            return self.add(a, b, w)
        if z.op in ('<<', '>>'): 
            a = self.eval(z.a)
            b = z.b
            w = len(a)
            if isinstance(b, N) and b.op == 'num': 
                _, k = parse_num(b.a)
                if z.op == '<<': 
                    return tuple(([0]*k+list(a))[:w])
                return tuple((list(a)[k:]+[0]*k)[:w])
            # Generic exact variable-shift lowering as a logarithmic barrel mux.
            # This removes the old dependency on binary-specific pre-simplification.
            sh = self.eval(b)
            cur = tuple(a)
            for j, sel in enumerate(sh): 
                k = 1<<j
                if k >= w: 
                    shifted = tuple(0 for _ in range(w))
                elif z.op == '<<': 
                    shifted = tuple([0]*k + list(cur[:w-k]))
                else: 
                    shifted = tuple(list(cur[k:]) + [0]*k)
                cur = tuple(B.OR(B.AND(sel, x), B.AND(B.NOT(sel), y)) for x, y in zip(shifted, cur))
            return cur
        if z.op == '?:': 
            s = self.scalar(self.eval(z.a))
            a = self.eval(z.b)
            b = self.eval(z.c)
            w = max(len(a), len(b))
            a = self.resize(a, w)
            b = self.resize(b, w)
            return tuple(B.OR(B.AND(s, x), B.AND(B.NOT(s), y)) for x, y in zip(a, b))
        raise KeyError(z.op)
    def add(self, a, b, w, cin = 0): 
        B = self.bdd
        a = self.resize(a, w)
        b = self.resize(b, w)
        c = cin
        out = []
        for x, y in zip(a, b): 
            xy = B.XOR(x, y)
            out.append(B.XOR(xy, c))
            c = B.OR(B.AND(x, y), B.AND(c, xy))
        return tuple(out)
    def function(self, label): 
        m = re.fullmatch(r'([A-Za-z_$][\w$]*)\[(\d+)\]', label)
        if m: 
            return self.signal(m.group(1))[int(m.group(2))]
        v = self.signal(label)
        if len(v)!=1: 
            raise ValueError(f'{label} is vector width {len(v)}')
        return v[0]
    def ast_deps(self, z): 
        out = set()
        def rec(q): 
            if not isinstance(q, N): 
                return
            if q.op == 'id': 
                out.add(q.a)
            elif q.op in ('idx', 'slice') and isinstance(q.a, N) and q.a.op == 'id': 
                out.add(q.a.a)
            else: 
                for x in (q.a, q.b, q.c): 
                    if isinstance(x, N): 
                        rec(x)
                    elif isinstance(x, tuple): 
                        for y in x: 
                            rec(y)
        rec(z)
        return out
    def signal_deps(self, n): 
        if n in self._dep_cache: 
            return self._dep_cache[n]
        ds = set()
        asts = []
        if n in self.whole: 
            asts = [self.whole[n]]
        else: 
            asts = [a for (x, i), a in self.bits.items() if x == n]
        for a in asts: 
            for d in self.ast_deps(a): 
                ds.add(d)
                if d!=n and d in self.assigned: 
                    ds |= self.signal_deps(d)
        self._dep_cache[n] = frozenset(ds)
        return self._dep_cache[n]

AREA = {'INV': 1821.66, 'NAND2': 1821.66, 'NOR2': 1821.66, 'AND2': 2165.96, 'OR2': 2165.96, 'AND3': 2510.26, 'OR3': 2510.26, 'NAND3': 2165.96, 'NOR3': 2165.96, 'AND4': 2854.56, 'OR4': 2854.56, 'NAND4': 2510.26, 'NOR4': 2510.26, 'XOR2': 2854.56, 'XNOR2': 2854.56, 'MUX2': 3887.46}

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('sv')
    ap.add_argument('--json')
    ap.add_argument('--max-sources', type = int, default = 250)
    args = ap.parse_args()
    text = Path(args.sv).read_text()
    m = Model(text)
    B = m.bdd
    targets = ['d_P00_store[0]', 'd_P01_store[0]', 'd_P03_store[0]', 'd_P08[0]', 'd_P08[1]']
    # scalar source labels: assigned 1-bit nets + assigned vector bits + primary bits + constants
    labels = []
    for n in sorted(m.assigned): 
        w = m.width.get(n, 1)
        if w == 1: 
            labels.append(n)
        else: 
            for i in range(w): 
                labels.append(f'{n}[{i}]')
    labels += sorted(m._leaf_names)
    funcs = {}
    for lab in labels: 
        try: 
            funcs[lab] = m.function(lab)
        except Exception: 
            pass
    # one representative per exact function, prefer semantic/simple names over d_ targets
    reps = {}
    def rank(n): 
        return (n.startswith('d_'), n.startswith('act_'), n.startswith('en_'), len(n))
    for n, f in funcs.items(): 
        if f not in reps or rank(n)<rank(reps[f]): 
            reps[f] = n
    uniq = [(f, n) for f, n in reps.items()]
    result = {'sv': str(args.sv), 'primary_vars': len(B.names), 'bdd_nodes_after_compile': None, 'targets': {}}
    op2 = [('NAND2', lambda a, b: B.NOT(B.AND(a, b))), ('NOR2', lambda a, b: B.NOT(B.OR(a, b))), ('AND2', B.AND), ('OR2', B.OR), ('XOR2', B.XOR), ('XNOR2', lambda a, b: B.NOT(B.XOR(a, b)))]
    op3 = [('AND3', lambda a, b, c: B.AND(B.AND(a, b), c)), ('OR3', lambda a, b, c: B.OR(B.OR(a, b), c)), ('NAND3', lambda a, b, c: B.NOT(B.AND(B.AND(a, b), c))), ('NOR3', lambda a, b, c: B.NOT(B.OR(B.OR(a, b), c)))]
    for t in targets: 
        try: 
            bt = m.function(t)
        except Exception as e: 
            result['targets'][t] = {'error': str(e)}
            continue
        tn = t.split('[', 1)[0]
        sup = B.support(bt)
        # exact direct aliases/inversions
        cand = []
        for f, n in uniq: 
            sn = n.split('[', 1)[0]
            if sn == tn or tn in m.signal_deps(sn): 
                continue
            fs = B.support(f)
            if not fs.issubset(sup): 
                continue
            if f == bt: 
                cand.append((0.0, 'BUF', n))
            if B.NOT(f) == bt: 
                cand.append((AREA['INV'], 'INV', n))
        # restrict source pool to target support subsets, exact unique functions, and avoid loops
        pool = []
        for f, n in uniq: 
            sn = n.split('[', 1)[0]
            if sn == tn or tn in m.signal_deps(sn): 
                continue
            if B.support(f).issubset(sup): 
                pool.append((f, n))
        # cap by simple source name ranking, but preserve all action/en/pred + primaries up to max
        pool = sorted(pool, key = lambda x: rank(x[1]))[:args.max_sources]
        for i, (fa, a) in enumerate(pool): 
            for fb, b in pool[i:]: 
                for op, fn in op2: 
                    if fn(fa, fb) == bt: 
                        cand.append((AREA[op], op, a, b))
        # bounded triples only among at most 80 candidates and only if no <=2gate result other than inv
        p3 = pool[:80]
        if not any(x[1] in ('NAND2', 'NOR2', 'AND2', 'OR2', 'XOR2', 'XNOR2', 'BUF') for x in cand): 
            for i, (fa, a) in enumerate(p3): 
                for j in range(i, len(p3)): 
                    fb, b = p3[j]
                    for k in range(j, len(p3)): 
                        fc, c = p3[k]
                        for op, fn in op3: 
                            if fn(fa, fb, fc) == bt: 
                                cand.append((AREA[op], op, a, b, c))
        # bounded MUX search: find cofactor-compatible A/B for each selector
        mux = []
        for fs, s in pool[:100]: 
            nfs = B.NOT(fs)
            As = []
            Bs = []
            for fa, a in pool: 
                if B.AND(nfs, B.XOR(bt, fa)) == 0: 
                    As.append(a)
                if B.AND(fs, B.XOR(bt, fa)) == 0: 
                    Bs.append(a)
            if As and Bs: 
                for a in As[:4]: 
                    for b in Bs[:4]: 
                        if a!=b: 
                            mux.append((AREA['MUX2'], 'MUX2', a, b, s))
        cand += mux
        cand = sorted(set(cand))
        result['targets'][t] = {'support': sorted(sup), 'support_n': len(sup), 'pool_n': len(pool), 'best': cand[:20]}
    result['bdd_nodes_after_compile'] = len(B.nodes)
    out = json.dumps(result, indent = 2)
    print(out)
    if args.json: 
        Path(args.json).write_text(out+'\n')
if __name__ == '__main__': 
    main()
