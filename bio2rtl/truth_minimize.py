from __future__ import annotations

from itertools import combinations

from .boolean_mapper import A, C, N, O, V, canon


def _covers(imp, bits): 
    return all(a is None or a == b for a, b in zip(imp, bits))


def _combine(a, b): 
    diff = 0
    out = []
    for x, y in zip(a, b): 
        if x == y: 
            out.append(x)
        elif x is None or y is None: 
            return None
        else: 
            diff += 1
            out.append(None)
            if diff > 1: 
                return None
    return tuple(out) if diff == 1 else None


def minimize_truth_table(var_names, ones, dontcares = ()): 
    """Exact small truth-table minimizer (Quine-McCluskey + minimum cover)."""
    ones = {tuple(map(int, x)) for x in ones}
    dcs = {tuple(map(int, x)) for x in dontcares}
    if not ones: 
        return C(0)
    n = len(var_names)
    if len(ones) == 1 << n: 
        return C(1)

    cur = set(ones | dcs)
    primes = set()
    while cur: 
        used = set()
        nxt = set()
        ls = sorted(cur, key = repr)
        for i, a in enumerate(ls): 
            for b in ls[i + 1:]: 
                c = _combine(a, b)
                if c is not None: 
                    used.add(a)
                    used.add(b)
                    nxt.add(c)
        primes |= {x for x in cur if x not in used}
        if not nxt: 
            break
        cur = nxt

    primes = [p for p in primes if any(_covers(p, m) for m in ones)]
    cover = {p: {m for m in ones if _covers(p, m)} for p in primes}
    uncovered = set(ones)
    chosen = []
    while True: 
        essential = None
        for m in list(uncovered): 
            ps = [p for p in primes if m in cover[p]]
            if len(ps) == 1: 
                essential = ps[0]
                break
        if essential is None: 
            break
        if essential not in chosen: 
            chosen.append(essential)
        uncovered -= cover[essential]
        primes = [p for p in primes if p != essential]

    if uncovered: 
        cand = [p for p in primes if cover[p] & uncovered]
        best = None
        for k in range(1, len(cand) + 1): 
            for ss in combinations(cand, k): 
                covered = set()
                for p in ss: 
                    covered |= cover[p]
                if uncovered <= covered: 
                    lits = sum(sum(x is not None for x in p) for p in ss)
                    score = (k, lits, tuple(map(repr, ss)))
                    if best is None or score < best[0]: 
                        best = (score, ss)
            if best is not None: 
                break
        if best is None: 
            raise RuntimeError("truth-table cover failure")
        chosen.extend(best[1])

    terms = []
    for p in chosen: 
        xs = []
        for name, bit in zip(var_names, p): 
            if bit is None: 
                continue
            xs.append(V(name) if bit else N(V(name)))
        terms.append(C(1) if not xs else xs[0] if len(xs) == 1 else A(*xs))
    return canon(terms[0] if len(terms) == 1 else O(*terms))


def minimize_truth_table_candidates(var_names, ones, dontcares = (), max_candidates = 32): 
    """Return deterministic equally minimal covers for multi-output sharing."""
    ones = {tuple(map(int, x)) for x in ones}
    dcs = {tuple(map(int, x)) for x in dontcares}
    if not ones: 
        return [C(0)]
    n = len(var_names)
    if len(ones) == 1 << n: 
        return [C(1)]

    cur = set(ones | dcs)
    primes = set()
    while cur: 
        used = set()
        nxt = set()
        ls = sorted(cur, key = repr)
        for i, a in enumerate(ls): 
            for b in ls[i + 1:]: 
                c = _combine(a, b)
                if c is not None: 
                    used.add(a)
                    used.add(b)
                    nxt.add(c)
        primes |= {x for x in cur if x not in used}
        if not nxt: 
            break
        cur = nxt

    primes = sorted((p for p in primes if any(_covers(p, m) for m in ones)), key = repr)
    cover = {p: {m for m in ones if _covers(p, m)} for p in primes}
    uncovered = set(ones)
    essential = []
    remaining = list(primes)
    while True: 
        e = None
        for m in sorted(uncovered): 
            ps = [p for p in remaining if m in cover[p]]
            if len(ps) == 1: 
                e = ps[0]
                break
        if e is None: 
            break
        if e not in essential: 
            essential.append(e)
        uncovered -= cover[e]
        remaining = [p for p in remaining if p != e]

    if not uncovered: 
        solutions = [tuple()]
    else: 
        cand = [p for p in remaining if cover[p] & uncovered]
        solutions = []
        best_pair = None
        for k in range(1, len(cand) + 1): 
            for ss in combinations(cand, k): 
                covered = set()
                for q in ss: 
                    covered |= cover[q]
                if not uncovered <= covered: 
                    continue
                lits = sum(sum(x is not None for x in q) for q in ss)
                pair = (k, lits)
                if best_pair is None: 
                    best_pair = pair
                if pair == best_pair: 
                    solutions.append(ss)
            if best_pair is not None: 
                break

    def to_expr(extra): 
        terms = []
        for q in [*essential, *extra]: 
            xs = []
            for name, bit in zip(var_names, q): 
                if bit is None: 
                    continue
                xs.append(V(name) if bit else N(V(name)))
            terms.append(C(1) if not xs else xs[0] if len(xs) == 1 else A(*xs))
        return canon(terms[0] if len(terms) == 1 else O(*terms))

    out = []
    for ss in sorted(solutions, key = repr): 
        expr = to_expr(ss)
        if expr not in out: 
            out.append(expr)
        if len(out) >= max_candidates: 
            break

    legacy = minimize_truth_table(var_names, ones, dcs)
    if legacy not in out: 
        if len(out) >= max_candidates: 
            out[-1] = legacy
        else: 
            out.append(legacy)
    return sorted(set(out), key = repr)
