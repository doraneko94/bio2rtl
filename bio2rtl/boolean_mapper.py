from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import ast, itertools, math

Expr = tuple

def C(v: int)->Expr: 
    return ('CONST', int(bool(v)))
def V(n: str)->Expr: 
    return ('VAR', n)
def N(a: Expr)->Expr: 
    return ('NOT', a)
def A(*xs: Expr)->Expr: 
    return ('AND', tuple(xs))
def O(*xs: Expr)->Expr: 
    return ('OR', tuple(xs))
def X(a: Expr, b: Expr)->Expr: 
    return ('XOR', a, b)
def XN(a: Expr, b: Expr)->Expr: 
    return ('XNOR', a, b)
def M(s: Expr, a: Expr, b: Expr)->Expr: 
    return ('MUX', s, a, b)

def canon(e: Expr)->Expr: 
    op = e[0]
    if op in ('CONST', 'VAR'): 
        return e
    if op == 'NOT': 
        a = canon(e[1])
        if a[0] == 'CONST': 
            return C(1-a[1])
        if a[0] == 'NOT': 
            return canon(a[1])
        return ('NOT', a)
    if op in ('AND', 'OR'): 
        vals = []
        for z in e[1]: 
            z = canon(z)
            if z[0] == op: 
                vals.extend(z[1])
            else: 
                vals.append(z)
        kill = 0 if op == 'AND' else 1
        ident = 1-kill
        if any(z == C(kill) for z in vals): 
            return C(kill)
        vals = [z for z in vals if z!=C(ident)]
        # idempotence
        vals = sorted(set(vals), key = repr)
        if not vals: 
            return C(ident)
        if len(vals) == 1: 
            return vals[0]
        # simple absorption a | (a&b) and a & (a|b)
        ss = set(vals)
        other = 'AND' if op == 'OR' else 'OR'
        vals = [z for z in vals if not (z[0] == other and any(q in ss for q in z[1]))]
        if len(vals) == 1: 
            return vals[0]
        return (op, tuple(vals))
    if op in ('XOR', 'XNOR'): 
        a, b = canon(e[1]), canon(e[2])
        if repr(a)>repr(b): 
            a, b = b, a
        if a == b: 
            return C(0 if op == 'XOR' else 1)
        if a[0] == 'CONST': 
            if op == 'XOR': 
                return N(b) if a[1] else b
            return b if a[1] else N(b)
        return (op, a, b)
    if op == 'MUX': 
        s, a, b = canon(e[1]), canon(e[2]), canon(e[3])
        if a == b: 
            return a
        if s[0] == 'CONST': 
            return b if s[1] else a
        return ('MUX', s, a, b)
    raise ValueError(op)

def parse_bool(text: str)->Expr: 
    node = ast.parse(text, mode = 'eval').body
    def cv(n): 
        if isinstance(n, ast.Name): 
            return V(n.id)
        if isinstance(n, ast.Constant) and n.value in (0, 1, False, True): 
            return C(int(n.value))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.Invert, ast.Not)): 
            return N(cv(n.operand))
        if isinstance(n, ast.BinOp): 
            if isinstance(n.op, ast.BitAnd): 
                return A(cv(n.left), cv(n.right))
            if isinstance(n.op, ast.BitOr): 
                return O(cv(n.left), cv(n.right))
            if isinstance(n.op, ast.BitXor): 
                return X(cv(n.left), cv(n.right))
        if isinstance(n, ast.BoolOp): 
            if isinstance(n.op, ast.And): 
                return A(*(cv(x) for x in n.values))
            if isinstance(n.op, ast.Or): 
                return O(*(cv(x) for x in n.values))
        raise ValueError(ast.dump(n))
    return canon(cv(node))

@dataclass
class Cell: 
    typ: str
    out: str
    pins: dict[str, str]

class BooleanMapper: 
    def __init__(self, area: dict[str, float], complements: dict[str, str]|None = None): 
        self.area = area
        self.complements = complements or {}
        self.cells: list[Cell] = []
        self.memo = {}
        self.n = 0
    def _new(self, p = 'n'): 
        self.n+=1
        return f'{p}_{self.n:04d}'
    def _cell(self, typ: str, out: str|None = None, **pins): 
        out = out or self._new('n')
        self.cells.append(Cell(typ, out, {**pins, 'Y': out}))
        return out
    def _inv(self, net): 
        return self._cell('INV_X1', A = net)
    def map(self, e: Expr, neg: bool = False)->str: 
        e = canon(e)
        key = (e, neg)
        if key in self.memo: 
            return self.memo[key]
        op = e[0]
        if op == 'CONST': 
            net = "1'b%d"%(e[1]^int(neg))
        elif op == 'VAR': 
            if not neg: 
                net = e[1]
            elif e[1] in self.complements: 
                net = self.complements[e[1]]
            else: 
                net = self._inv(e[1])
        elif op == 'NOT': 
            net = self.map(e[1], not neg)
        elif op in ('AND', 'OR'): 
            args = list(e[1])
            base = op
            # Reuse an already-mapped associative subexpression when possible.
            # Example: after mapping (a&b), map (a&b&c) as AND2(prev,c) rather
            # than an independent AND3.  This preserves flattened canonical
            # expressions while recovering DAG common subexpressions.
            reused = None
            if not neg and len(args)>=3: 
                aset = set(args)
                best = None
                for (me, mneg), mnet in self.memo.items(): 
                    if mneg or not isinstance(me, tuple) or not me or me[0]!=op: 
                        continue
                    sub = set(me[1])
                    if len(sub)>=2 and sub < aset: 
                        rem = [x for x in args if x not in sub]
                        if best is None or len(sub)>best[0]: 
                            best = (len(sub), mnet, rem)
                if best and 1<=len(best[2])<=3: 
                    ins = [best[1]]+[self.map(x, False) for x in best[2]]
                    k = len(ins)
                    typ = (f'AND{k}_X1' if base == 'AND' else f'OR{k}')
                    reused = self._cell(typ, **{chr(65+i): x for i, x in enumerate(ins)})
            if reused is not None: 
                net = reused
            # desired negative maps directly to NAND/NOR when fanin <=4.
            elif len(args)<=4: 
                ins = [self.map(x, False) for x in args]
                if base == 'AND': 
                    typ = ('NAND' if neg else 'AND')+str(len(ins))+('_X1' if not neg else '')
                else: 
                    typ = ('NOR' if neg else 'OR')+str(len(ins))
                # normalize 2-input AND naming; OR uses OR2/3/4, NAND/NOR use NAND2 etc.
                if typ == 'AND2_X1' or typ in self.area: 
                    pass
                else: 
                    # Some techs name AND3_X1/AND4_X1, while NAND/NOR omit suffix.
                    if base == 'AND' and not neg: 
                        typ = f'AND{len(ins)}_X1'
                net = self._cell(typ, **{chr(65+i): x for i, x in enumerate(ins)})
            else: 
                # Area-aware associative reduction. Build positive tree, invert at end only if needed.
                cur = [self.map(x, False) for x in args]
                while len(cur)>1: 
                    k = min(4, len(cur))
                    chunk = cur[:k]
                    cur = cur[k:]
                    typ = (f'AND{k}_X1' if base == 'AND' else f'OR{k}')
                    cur.append(self._cell(typ, **{chr(65+i): x for i, x in enumerate(chunk)}))
                net = self._inv(cur[0]) if neg else cur[0]
        elif op in ('XOR', 'XNOR'): 
            a = self.map(e[1])
            b = self.map(e[2])
            want_xnor = (op == 'XNOR')^neg
            net = self._cell('XNOR2' if want_xnor else 'XOR2', A = a, B = b)
        elif op == 'MUX': 
            s = self.map(e[1])
            a = self.map(e[2], neg)
            b = self.map(e[3], neg)
            net = self._cell('MUX2', A = a, B = b, S = s)
        else: 
            raise ValueError(op)
        self.memo[key] = net
        return net
    def total_area(self): 
        return sum(self.area[c.typ] for c in self.cells)

def eval_expr(e: Expr, env: dict[str, int])->int: 
    e = canon(e)
    op = e[0]
    if op == 'CONST': 
        return e[1]
    if op == 'VAR': 
        return int(bool(env[e[1]]))
    if op == 'NOT': 
        return 1-eval_expr(e[1], env)
    if op == 'AND': 
        return int(all(eval_expr(x, env) for x in e[1]))
    if op == 'OR': 
        return int(any(eval_expr(x, env) for x in e[1]))
    if op == 'XOR': 
        return eval_expr(e[1], env)^eval_expr(e[2], env)
    if op == 'XNOR': 
        return 1-(eval_expr(e[1], env)^eval_expr(e[2], env))
    if op == 'MUX': 
        return eval_expr(e[3], env) if eval_expr(e[1], env) else eval_expr(e[2], env)
    raise ValueError(op)
