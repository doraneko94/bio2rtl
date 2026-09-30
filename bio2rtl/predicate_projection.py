from __future__ import annotations
from pathlib import Path
import itertools, json
from .semantic_projection import (
    V, N, A, O, _load, semantic_state_pairs, 
    external_input_bit, discover_load_hold_binding, 
)
from .truth_minimize import minimize_truth_table


def _phase_basis(root: Path) -> dict[str, list]: 
    d = _load(Path(root)/'build/semantic/phase40.ir.json')
    return {str(x['id']): x['expression'] for x in d.get('predicate_basis', [])}


def _const(e): 
    return int(e[1]) if isinstance(e, list) and len(e) == 2 and e[0] == 'CONST' else None


def _reg(e): 
    return str(e[1]) if isinstance(e, list) and len(e) == 2 and e[0] == 'REG' else None


def _shift_predicate_expr(root: Path, predicate_id: str): 
    root = Path(root)
    cert_path = root/'build/generated_certificates/shift_quotient.json'
    if not cert_path.exists(): 
        return None
    cert = _load(cert_path)
    if cert.get('status')!='PASS' or predicate_id not in list(map(str, cert.get('observation_predicates', []))): 
        return None
    # The technology-aware encoding remains an encoding plan, but predicate semantics come
    # solely from the quotient proof.  This function supports every observation predicate in
    # the proof, not a benchmark-specific subset.
    from .neutral_bindings import resolve_shift_interface_binding
    plan = resolve_shift_interface_binding(root)
    if not plan: 
        return None
    n = int(cert['quotient_state_count'])
    enc = list(map(int, plan['encoding']))
    bits = [str(x['net']) for x in plan['state_bits']]
    if len(enc)!=n or sorted(enc)!=list(range(n)): 
        raise ValueError('invalid shift encoding')
    pred_order = list(map(str, cert.get('observation_predicates', [])))
    oi = pred_order.index(predicate_id)
    classes = {int(x['state']): x for x in cert.get('quotient_classes', [])}
    ones = []
    for st, code in enumerate(enc): 
        if int(classes[st]['observation_vector'][oi]): 
            ones.append(tuple((code>>i)&1 for i in range(len(bits))))
    return minimize_truth_table(bits, ones, [])


def _state_pairs(root: Path, source: str, banks: list[dict]): 
    # semantic_state_pairs handles counter/direct-state sources.  Final auto load/hold
    # plans already carry explicit Q/QB orientation; consume that directly rather than
    # reconstructing an earlier bank-shape object.
    pairs = semantic_state_pairs(root, source, None)
    if pairs: 
        return pairs
    for b in banks: 
        if str(b.get('semantic_source'))!=str(source): 
            continue
        rows = []
        for x in b.get('bits', []): 
            bit = int(x['semantic_bit'])
            q = str(x['q'])
            qb = str(x['qb'])
            if str(x.get('semantic_on', 'q')) == 'qb': 
                rows.append((bit, qb, q))
            else: 
                rows.append((bit, q, qb))
        return sorted(rows)
    return []


def _eq_projection(root: Path, source: str, value: int, banks: list[dict]): 
    pairs = _state_pairs(root, source, banks)
    if not pairs: 
        return None
    # Sparse semantic bit slices are only equality-projectable when every bit below the
    # represented width is present.  This intentionally refuses partial GPIO words.
    ids = [b for b, _, _ in pairs]
    if ids!=list(range(len(ids))): 
        return None
    if value<0 or value>=(1<<len(ids)): 
        return None
    terms = []
    for bit, q, qb in pairs: 
        terms.append(V(q if ((value>>bit)&1) else qb))
    return A(*terms)


def _bit_projection(root: Path, source: str, bit: int, expected: int, banks: list[dict]): 
    pairs = _state_pairs(root, source, banks)
    if pairs: 
        d = {int(b): (str(q), str(qb)) for b, q, qb in pairs}
        if int(bit) not in d: 
            return None
        q, qb = d[int(bit)]
        return V(q if int(expected) else qb)
    return None




def _control_reg_eq_projection(root: Path, source: str, value: int): 
    root = Path(root)
    cp = root/'build/generated_certificates/control_factorization.json'
    if not cp.exists(): 
        return None
    ctrl = _load(cp)
    regs = list(map(str, ctrl.get('control_registers', [])))
    if str(source) not in regs: 
        return None
    bi = regs.index(str(source))
    cc = root/'build/semantic_neutral_contracts/phase_factorized_control.json'
    if cc.exists(): 
        contract = _load(cc)
        plan = contract.get('control_encoding_plan') or contract.get('derivation', {}).get('control_encoding_plan')
        bind = contract.get('neutral_interface_binding', {})
    else: 
        try: 
            from .recipe_keys import resolve_cached_control_encoding_plan
            from .neutral_bindings import resolve_control_interface_binding
            plan = resolve_cached_control_encoding_plan(root)
            bind = resolve_control_interface_binding(root, include_roles = False) or {}
        except (FileNotFoundError, KeyError, ValueError): 
            plan = None
            bind = {}
    if not plan or not bind: 
        return None
    from .semantic_contracts import _control_state_physical_codes
    codes = _control_state_physical_codes(ctrl, plan)
    rq = list(map(str, bind.get('banks', {}).get('rise', {}).get('q', [])))
    fq = list(map(str, bind.get('banks', {}).get('fall', {}).get('q', [])))
    names = rq+fq
    if not names: 
        return None
    rb = len(rq)
    fb = len(fq)
    seen = {}
    for r in ctrl.get('state_rows', []): 
        st = int(r['state'])
        rc, fc = codes[st]
        key = tuple((rc>>i)&1 for i in range(rb))+tuple((fc>>i)&1 for i in range(fb))
        got = int(int(r['pattern'][bi]) == int(value))
        if key in seen and seen[key]!=got: 
            return None
        seen[key] = got
    allrows = set(itertools.product((0, 1), repeat = len(names)))
    ones = [k for k, v in seen.items() if v]
    dcs = allrows-set(seen)
    return minimize_truth_table(names, ones, dcs)


def _latch_eq_projection(root: Path, source: str, value: int): 
    from .semantic_projection import non_dff_state_pair
    z = non_dff_state_pair(Path(root), str(source))
    if not z: 
        return None
    _src, q, qb = z
    return V(q if int(value) else qb)

def _canonical_formula_projection(root: Path, formula: dict, banks: list[dict]): 
    """Project a proof-backed canonical-elimination formula recursively."""
    if not isinstance(formula, dict): 
        return None
    k = str(formula.get('kind', '')).upper()
    if k == 'EQ': 
        reg = str(formula.get('register', ''))
        val = int(formula.get('value', 0))
        z = _eq_projection(Path(root), reg, val, banks)
        if z is not None: 
            return z
        z = _control_reg_eq_projection(Path(root), reg, val)
        if z is not None: 
            return z
        z = _latch_eq_projection(Path(root), reg, val)
        if z is not None: 
            return z
        return None
    if k in ('AND', 'OR'): 
        a = _canonical_formula_projection(root, formula.get('left'), banks)
        b = _canonical_formula_projection(root, formula.get('right'), banks)
        if a is None or b is None: 
            return None
        return A(a, b) if k == 'AND' else O(a, b)
    if k == 'NOT': 
        a = _canonical_formula_projection(root, formula.get('arg') or formula.get('operand'), banks)
        return None if a is None else N(a)
    return None


def _canonical_eliminated_eq_projection(root: Path, source: str, value: int, banks: list[dict]): 
    p = Path(root)/'build/generated_certificates/canonical_elimination.json'
    if not p.exists(): 
        return None
    d = _load(p)
    r = d.get('result', {})
    if d.get('status')!='PASS' or r.get('classification')!='PASS_CANONICAL_ELIMINATION': 
        return None
    if str(r.get('register'))!=str(source): 
        return None
    if int(r.get('source_storage_bits', 1))!=1 or int(value) not in (0, 1): 
        return None
    e = _canonical_formula_projection(Path(root), r.get('formula', {}), banks)
    if e is None: 
        return None
    return e if int(value) == 1 else N(e)


def _derived_eq_projection(root: Path, source: str, value: int, banks: list[dict]): 
    """Project proof-backed eliminated state relations before falling back to role synthesis."""
    root = Path(root)
    z = _canonical_eliminated_eq_projection(root, source, value, banks)
    if z is not None: 
        return z
    p = root/'build/generated_certificates/storage_relations.json'
    if not p.exists(): 
        return None
    d = _load(p)
    # Discover any proof-backed one-bit derived NONZERO relation by structure, never by
    # semantic register spelling or legacy certificate key.
    pat = __import__('re').compile(r'^\s*([A-Za-z_]\w*)\s*=\s*1\s+iff\s+([A-Za-z_]\w*)\s*!=\s*0\s*$')
    for rel in d.values(): 
        if not isinstance(rel, dict) or rel.get('status')!='PASS' or rel.get('counterexamples'): 
            continue
        m = pat.match(str(rel.get('relation', '')))
        if not m or str(source)!=m.group(1): 
            continue
        z = _eq_projection(root, m.group(2), 0, banks)
        if z is None: 
            return None
        return z if int(value) == 0 else N(z)
    return None

def _mirror_bit_projection(root: Path, source: str, bit: int, expected: int, banks: list[dict]): 
    root = Path(root)
    p = root/'build/generated_certificates/storage_relations.json'
    if not p.exists(): 
        return None
    d = _load(p)
    import re
    pat = re.compile(r'^\s*([A-Za-z_]\w*)\s*=\s*(~)?([A-Za-z_]\w*)\[(\d+):(\d+)\]\s*$')
    for text in d.get('gpio_mirror_fusion', {}).get('relations', []): 
        m = pat.match(str(text))
        if not m or m.group(1)!=str(source): 
            continue
        hi, lo = int(m.group(4)), int(m.group(5))
        width = hi-lo+1
        if int(bit)<0 or int(bit)>=width: 
            return None
        src = m.group(3)
        sb = lo+int(bit)
        inv = bool(m.group(2))
        pairs = _state_pairs(root, src, banks)
        mp = {int(b): (q, qb) for b, q, qb in pairs}
        if sb not in mp: 
            return None
        q, qb = mp[sb]  # q means semantic-one for src, qb semantic-zero.
        want_src = int(expected) ^ int(inv)
        return V(q if want_src else qb)
    return None


def _generic_state_expr_projection(root: Path, expr, banks): 
    """Truth-synthesize a pure architectural-state predicate.

    This is a generic fallback for guard shapes such as
    BIT_VALUE(REG + CONST, bit) == CONST.  It intentionally accepts only pure
    state expressions (no GPIO/SCHED inputs) and only when every referenced
    semantic register has a proof-backed physical bit projection.  Missing bits
    are filled only from phase40's proven constant-bit annotations.
    """
    sources = []
    def walk(e): 
        if not isinstance(e, list) or not e: 
            return True
        tag = e[0]
        if tag == 'REG': 
            src = str(e[1])
            if src not in sources: 
                sources.append(src)
            return True
        if tag in ('CONST',): 
            return True
        if tag in ('GPIO_INPUT', 'SCHED_REG', 'GPIO_SAMPLE', 'GPIO_VALUE'): 
            return False
        for x in e[1:]: 
            if isinstance(x, list): 
                # OP argument lists are lists-of-expressions rather than tagged expressions.
                if x and isinstance(x[0], list): 
                    if not all(walk(y) for y in x): 
                        return False
                elif not walk(x): 
                    return False
        return True
    if not walk(expr) or not sources: 
        return None
    phase = _load(Path(root)/'build/semantic/phase40.ir.json')
    storage = {str(x['register']): x for x in phase.get('storage_optimization', {}).get('register_storage', [])}
    projections = {}
    vars = []
    for src in sources: 
        pairs = semantic_state_pairs(Path(root), src, banks)
        if not pairs: 
            return None
        pairs = sorted(pairs)
        projections[src] = pairs
        vars.extend(str(q) for _, q, _ in pairs)
    if len(vars)>10 or len(set(vars))!=len(vars): 
        return None

    def state_value(src, env): 
        st = storage.get(src, {})
        v = 0
        for bit, q, qb in projections[src]: 
            if int(env[str(q)]): 
                v|=1<<int(bit)
        for bit in map(int, st.get('constant_one_bits', [])): 
            v|=1<<bit
        return v & 0xffffffff

    def ev(e, env): 
        if not isinstance(e, list) or not e: 
            raise ValueError
        tag = e[0]
        if tag == 'CONST': 
            return int(e[1]) & 0xffffffff
        if tag == 'REG': 
            return state_value(str(e[1]), env)
        if tag == 'BIT_VALUE': 
            return (ev(e[1], env)>>int(e[2]))&1
        if tag in ('EQ', 'NE'): 
            z = int(ev(e[1], env) == ev(e[2], env))
            return z if tag == 'EQ' else 1-z
        if tag == 'OP': 
            op = str(e[1])
            args = e[2] if isinstance(e[2], list) else []
            vals = [ev(x, env) for x in args]
            if op == 'ADD': 
                return sum(vals)&0xffffffff
            if op == 'SUB': 
                z = vals[0]
                for x in vals[1:]: 
                    z-=x
                return z&0xffffffff
            if op == 'AND': 
                z = 0xffffffff
                for x in vals: 
                    z&=x
                return z&0xffffffff
            if op == 'OR': 
                z = 0
                for x in vals: 
                    z|=x
                return z&0xffffffff
            if op == 'XOR': 
                z = 0
                for x in vals: 
                    z^=x
                return z&0xffffffff
            if op == 'SHR' and len(vals) == 2: 
                return (vals[0]>>vals[1])&0xffffffff
            if op == 'SHL' and len(vals) == 2: 
                return (vals[0]<<vals[1])&0xffffffff
        raise ValueError
    ones = []
    try: 
        for row in itertools.product((0, 1), repeat = len(vars)): 
            env = dict(zip(vars, row))
            if ev(expr, env)&1: 
                ones.append(row)
    except ValueError: 
        return None
    return minimize_truth_table(vars, ones, [])

def project_predicate_expression(root: Path, expr, predicate_id: str|None = None): 
    """Project a supported semantic predicate-basis expression into optimized physical nets.

    Returns a technology-neutral Boolean expression or None when the current generic
    projection machinery does not yet cover the semantic expression.  No protocol names or
    physical recipe fragments are consulted.
    """
    root = Path(root)
    banks = (discover_load_hold_binding(root) or {}).get('banks', [])
    if predicate_id: 
        sx = _shift_predicate_expr(root, str(predicate_id))
        if sx is not None: 
            return sx
    if not (isinstance(expr, list) and len(expr) == 3 and expr[0] == 'EQ'): 
        return None
    lhs, rhs = expr[1], expr[2]
    # Normalize CONST to RHS where possible.
    if _const(lhs) is not None and _const(rhs) is None: 
        lhs, rhs = rhs, lhs
    c = _const(rhs)
    if c is None: 
        return None
    src = _reg(lhs)
    if src is not None: 
        z = _eq_projection(root, src, c, banks)
        if z is not None: 
            return z
        z = _control_reg_eq_projection(root, src, c)
        if z is not None: 
            return z
        z = _latch_eq_projection(root, src, c)
        if z is not None: 
            return z
        z = _derived_eq_projection(root, src, c, banks)
        if z is not None: 
            return z
        return None
    # BIT_VALUE(REG, bit) == const
    if isinstance(lhs, list) and len(lhs) == 3 and lhs[0] == 'BIT_VALUE': 
        inner, bit = lhs[1], int(lhs[2])
        src = _reg(inner)
        if src is not None: 
            z = _bit_projection(root, src, bit, c, banks)
            if z is not None: 
                return z
            z = _mirror_bit_projection(root, src, bit, c, banks)
            if z is not None: 
                return z
            return None
        # BIT_VALUE(GPIO_INPUT, bit) == const
        if inner == ['GPIO_INPUT']: 
            try: 
                n = external_input_bit(root, bit)
            except ValueError: 
                return None
            return V(n) if c else N(V(n))
    # (GPIO_INPUT & single_bit_mask) == 0/nonzero
    if isinstance(lhs, list) and len(lhs) == 3 and lhs[0] == 'OP' and lhs[1] == 'AND': 
        args = lhs[2]
        if isinstance(args, list) and len(args) == 2 and args[0] == ['GPIO_INPUT']: 
            mask = _const(args[1])
            if mask and mask&(mask-1) == 0 and c == 0: 
                bit = mask.bit_length()-1
                try: 
                    n = external_input_bit(root, bit)
                except ValueError: 
                    return None
                return N(V(n))
    # Last generic fallback: synthesize any pure proof-projected architectural
    # state predicate over a bounded bit cube.
    return _generic_state_expr_projection(root, expr, banks)


def project_predicate_basis(root: Path)->dict: 
    root = Path(root)
    basis = _phase_basis(root)
    rows = []
    unsupported = []
    for pid, e in sorted(basis.items()): 
        pe = project_predicate_expression(root, e, pid)
        if pe is None: 
            unsupported.append(pid)
        else: 
            rows.append({'predicate_id': pid, 'semantic_expression': e, 'physical_expression': pe})
    return {
      'version': 'bio2rtl-semantic-predicate-projection-v1', 
      'status': 'PASS', 
      'projected': rows, 'projected_count': len(rows), 'unsupported': unsupported, 
      'physical_recipe_used': False, 'predicate_binding_cache_used': False, 
    }
