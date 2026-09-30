from __future__ import annotations
from .boolean_contract import _tupleize


def expr_vars(e, out = None): 
    out = set() if out is None else out
    e = _tupleize(e)
    if not isinstance(e, tuple) or not e: 
        return out
    op = e[0]
    if op == 'VAR': 
        out.add(str(e[1]))
        return out
    if op in ('AND', 'OR'): 
        for x in e[1]: 
            expr_vars(x, out)
    else: 
        for x in e[1:]: 
            if isinstance(x, tuple): 
                expr_vars(x, out)
    return out


def _var_pattern(j: int, total: int)->int: 
    w = 1<<j
    period = w<<1
    reps = total//period
    block = ((1<<w)-1)<<w
    if reps == 0: 
        return 0
    geom = ((1<<(period*reps))-1)//((1<<period)-1)
    return block*geom


def truth_bits(e, variables: list[str], max_vars: int = 20)->tuple[int, int]: 
    e = _tupleize(e)
    n = len(variables)
    if n>max_vars: 
        raise ValueError(f'bit-parallel truth support {n} exceeds bound {max_vars}')
    total = 1<<n
    mask = (1<<total)-1
    pats = {str(v): _var_pattern(i, total) for i, v in enumerate(variables)}
    memo = {}
    def E(x): 
        x = _tupleize(x)
        if x in memo: 
            return memo[x]
        op = x[0]
        if op == 'CONST': 
            y = mask if int(x[1]) else 0
        elif op == 'VAR': 
            y = pats[str(x[1])]
        elif op == 'NOT': 
            y = (~E(x[1]))&mask
        elif op == 'AND': 
            y = mask
            for z in x[1]: 
                y &= E(z)
        elif op == 'OR': 
            y = 0
            for z in x[1]: 
                y |= E(z)
        elif op == 'XOR': 
            y = E(x[1])^E(x[2])
        elif op == 'XNOR': 
            y = (~(E(x[1])^E(x[2])))&mask
        elif op == 'MUX': 
            s, a, b = E(x[1]), E(x[2]), E(x[3])
            y = (((~s)&a)|(s&b))&mask
        else: 
            raise ValueError(f'unsupported proof expression op {op}')
        memo[x] = y
        return y
    return E(e), total


def exact_equivalence(a, b, max_vars: int = 20)->dict: 
    vars_ = sorted(expr_vars(a)|expr_vars(b))
    av, n = truth_bits(a, vars_, max_vars = max_vars)
    bv, n2 = truth_bits(b, vars_, max_vars = max_vars)
    assert n == n2
    result = {'equivalent': av == bv, 'variables': vars_, 'checks': n}
    if av!=bv: 
        diff = av^bv
        bit = (diff&-diff).bit_length()-1
        env = {v: (bit>>i)&1 for i, v in enumerate(vars_)}
        result['counterexample'] = {'env': env, 'a': (av>>bit)&1, 'b': (bv>>bit)&1}
    return result
