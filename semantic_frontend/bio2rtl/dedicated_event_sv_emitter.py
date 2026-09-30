from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from typing import Any
import re


def _u32(x: int) -> int: 
    return x & 0xFFFFFFFF


def _sv_id(text: str) -> str: 
    out = re.sub(r"[^A-Za-z0-9_$]", "_", text)
    if not out or not re.match(r"[A-Za-z_$]", out[0]): 
        out = "_" + out
    return out


def _evt_id(name: str) -> str: 
    return "evt_" + _sv_id(name.lower().replace("+", "_and_"))


def _det_id(name: str) -> str: 
    return "det_" + _sv_id(name)


def _const_sv(value: int) -> str: 
    return f"32'd{_u32(int(value))}"


def _expr_sv(expr: Any) -> str: 
    tag = expr[0]
    if tag == "CONST": 
        return _const_sv(int(expr[1]))
    if tag == "REG": 
        return f"r_{_sv_id(str(expr[1]))}"
    if tag == "SCHED_REG": 
        return f"sched_{_sv_id(str(expr[1]))}"
    if tag == "GPIO_INPUT": 
        return "gpio_in"
    if tag == "BIT_VALUE": 
        base = expr[1]
        bit = int(expr[2])
        if base[0] == "REG": 
            return f"r_{_sv_id(str(base[1]))}[{bit}]"
        if base[0] == "SCHED_REG": 
            return f"sched_{_sv_id(str(base[1]))}[{bit}]"
        return f"(({_expr_sv(base)} >> 32'd{bit}) & 32'd1)"
    if tag == "EQ_CONST": 
        return f"({_expr_sv(expr[1])} == {_const_sv(int(expr[2]))})"
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        a, b = _expr_sv(expr[1]), _expr_sv(expr[2])
        if tag == "EQ": 
            return f"({a} == {b})"
        if tag == "NE": 
            return f"({a} != {b})"
        if tag == "ULT": 
            return f"($unsigned({a}) < $unsigned({b}))"
        if tag == "UGE": 
            return f"($unsigned({a}) >= $unsigned({b}))"
        if tag == "SLT": 
            return f"($signed({a}) < $signed({b}))"
        return f"~($signed({a}) < $signed({b}))"
    if tag == "OP": 
        op = str(expr[1])
        args = [_expr_sv(x) for x in expr[2]]
        symbol = {"ADD": "+", "SUB": "-", "AND": "&", "OR": "|", "XOR": "^", "SHL": "<<", "SHR": ">>"}.get(op)
        if symbol is None or len(args) != 2: 
            raise ValueError(f"unsupported Dedicated Event expression: {expr!r}")
        return f"({args[0]} {symbol} {args[1]})"
    raise ValueError(f"unsupported Dedicated Event expression: {expr!r}")




def _bit_value_raw_sv(expr: Any, reg_widths: dict[str, int]) -> str: 
    """Return the one-bit signal selected by a BIT_VALUE expression."""
    base = expr[1]; bit = int(expr[2])
    if base[0] == "REG": 
        return f"r_{_sv_id(str(base[1]))}[{bit}]"
    if base[0] == "SCHED_REG": 
        return f"sched_{_sv_id(str(base[1]))}[{bit}]"
    if base[0] == "GPIO_INPUT": 
        return f"gpio_in[{bit}]"
    return f"(({_expr_sv_precise(base, reg_widths)} >> 32'd{bit}) & 32'd1)"


def _expr_sv_precise(expr: Any, reg_widths: dict[str, int]) -> str: 
    """Bit-precise syntax for expression-minimized Dedicated IR.

    This is intentionally conservative: arithmetic remains 32-bit unless the
    expression minimizer has removed it.  The emitter only exposes already
    proven bit-level structure (direct bit selects and narrow equality
    constants) so the RTL does not rebuild mask/shift idioms around a single
    recovered hardware bit.
    """
    tag = expr[0]
    if tag == "CONST": 
        return _const_sv(int(expr[1]))
    if tag == "REG": 
        return f"r_{_sv_id(str(expr[1]))}"
    if tag == "SCHED_REG": 
        return f"sched_{_sv_id(str(expr[1]))}"
    if tag == "GPIO_INPUT": 
        return "gpio_in"
    if tag == "BIT_VALUE": 
        # Dedicated IR BIT_VALUE is an integer-valued (0/1) expression under
        # 32-bit CPU semantics.  Zero-extend it when it participates in an
        # arithmetic/bitwise expression; boolean comparisons below use the raw
        # one-bit signal directly.  This avoids Verilog's self-determined
        # one-bit shift width (e.g. bit << 1 becoming zero).
        raw = _bit_value_raw_sv(expr, reg_widths)
        return f"{{{{31{{1'b0}}}}, {raw}}}"
    if tag == "EQ_CONST": 
        return _expr_sv_precise(["EQ", expr[1], ["CONST", int(expr[2])]], reg_widths)
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        left, right = expr[1], expr[2]
        # A proven BIT_VALUE is already one bit; comparing it with 0/1 is a
        # wire or inversion, not a 32-bit comparator.
        for bit_expr, const_expr, swapped in ((left, right, False), (right, left, True)): 
            if isinstance(bit_expr, list) and bit_expr and bit_expr[0] == "BIT_VALUE" and _is_small_bool_const(const_expr): 
                bit = _bit_value_raw_sv(bit_expr, reg_widths)
                value = int(const_expr[1]) & 1
                if tag == "EQ": 
                    return bit if value else f"~({bit})"
                if tag == "NE": 
                    return f"~({bit})" if value else bit
        a = _expr_sv_precise(left, reg_widths); b = _expr_sv_precise(right, reg_widths)
        # Equality against a constant can use the recovered register width.
        if tag in ("EQ", "NE"): 
            if isinstance(left, list) and left and left[0] == "REG" and isinstance(right, list) and right and right[0] == "CONST": 
                w = int(reg_widths.get(str(left[1]), 32)); v = _u32(int(right[1]))
                if w < 32 and v < (1 << w): 
                    b = f"{w}'d{v}"
            elif isinstance(right, list) and right and right[0] == "REG" and isinstance(left, list) and left and left[0] == "CONST": 
                w = int(reg_widths.get(str(right[1]), 32)); v = _u32(int(left[1]))
                if w < 32 and v < (1 << w): 
                    a = f"{w}'d{v}"
        if tag == "EQ": 
            return f"({a} == {b})"
        if tag == "NE": 
            return f"({a} != {b})"
        if tag == "ULT": 
            return f"($unsigned({a}) < $unsigned({b}))"
        if tag == "UGE": 
            return f"($unsigned({a}) >= $unsigned({b}))"
        if tag == "SLT": 
            return f"($signed({a}) < $signed({b}))"
        return f"~($signed({a}) < $signed({b}))"
    if tag == "OP": 
        op = str(expr[1]); args = [_expr_sv_precise(x, reg_widths) for x in expr[2]]
        symbol = {"ADD": "+", "SUB": "-", "AND": "&", "OR": "|", "XOR": "^", "SHL": "<<", "SHR": ">>"}.get(op)
        if symbol is None or len(args) != 2: 
            raise ValueError(f"unsupported Dedicated Event expression: {expr!r}")
        return f"({args[0]} {symbol} {args[1]})"
    raise ValueError(f"unsupported Dedicated Event expression: {expr!r}")


def _bnot(a: str) -> str: 
    if a == "1'b0": 
        return "1'b1"
    if a == "1'b1": 
        return "1'b0"
    if a.startswith("~(") and a.endswith(")"): 
        return a[2:-1]
    return f"~({a})"

def _band(a: str, b: str) -> str: 
    if a == "1'b0" or b == "1'b0": 
        return "1'b0"
    if a == "1'b1": 
        return b
    if b == "1'b1": 
        return a
    if a == b: 
        return a
    return f"({a} & {b})"

def _bor(a: str, b: str) -> str: 
    if a == "1'b1" or b == "1'b1": 
        return "1'b1"
    if a == "1'b0": 
        return b
    if b == "1'b0": 
        return a
    if a == b: 
        return a
    return f"({a} | {b})"

def _bxor(a: str, b: str) -> str: 
    if a == "1'b0": 
        return b
    if b == "1'b0": 
        return a
    if a == "1'b1": 
        return _bnot(b)
    if b == "1'b1": 
        return _bnot(a)
    if a == b: 
        return "1'b0"
    return f"({a} ^ {b})"

def _bmajority(a: str, b: str, c: str) -> str: 
    # carry = ab | ac | bc, with simple constant folding
    return _bor(_bor(_band(a, b), _band(a, c)), _band(b, c))

def _expr_bit_sv(expr: Any, bit: int, reg_widths: dict[str, int]) -> str: 
    """Render one result bit of a 32-bit Dedicated Event expression.

    This is a semantics-preserving RTL realization optimization.  It does not
    narrow the IR expression itself: bitwise ops are projected per bit,
    constant shifts become wiring, and ADD/SUB use only the ripple prefix
    needed for the requested result bit.  Unsupported forms fall back to the
    proven 32-bit expression followed by a bit select.
    """
    if bit < 0 or bit >= 32: 
        return "1'b0"
    if not isinstance(expr, list) or not expr: 
        raise ValueError(f"malformed Dedicated Event expression: {expr!r}")
    tag = expr[0]
    if tag == "CONST": 
        return "1'b1" if ((_u32(int(expr[1])) >> bit) & 1) else "1'b0"
    if tag == "REG": 
        rid = str(expr[1]); w = int(reg_widths.get(rid, 32))
        return f"r_{_sv_id(rid)}[{bit}]" if bit < w else "1'b0"
    if tag == "SCHED_REG": 
        return f"sched_{_sv_id(str(expr[1]))}[0]" if bit == 0 else "1'b0"
    if tag == "GPIO_INPUT": 
        return f"gpio_in[{bit}]"
    if tag == "BIT_VALUE": 
        return _bit_value_raw_sv(expr, reg_widths) if bit == 0 else "1'b0"
    if tag == "OP": 
        op = str(expr[1]); args = expr[2]
        if len(args) == 2 and op in ("AND", "OR", "XOR"): 
            a = _expr_bit_sv(args[0], bit, reg_widths); b = _expr_bit_sv(args[1], bit, reg_widths)
            return {"AND": _band, "OR": _bor, "XOR": _bxor}[op](a, b)
        if len(args) == 2 and op in ("SHL", "SHR") and isinstance(args[1], list) and args[1] and args[1][0] == "CONST": 
            amount = _u32(int(args[1][1]))
            if amount >= 32: 
                return "1'b0"
            src_bit = bit - amount if op == "SHL" else bit + amount
            return _expr_bit_sv(args[0], src_bit, reg_widths) if 0 <= src_bit < 32 else "1'b0"
        if len(args) == 2 and op in ("ADD", "SUB"): 
            # 32-bit two's-complement ripple.  Only carry/borrow prefix through
            # the requested bit is emitted, never a full 32-bit adder.
            carry = "1'b0" if op == "ADD" else "1'b1"
            out = "1'b0"
            for i in range(bit + 1): 
                a = _expr_bit_sv(args[0], i, reg_widths)
                b = _expr_bit_sv(args[1], i, reg_widths)
                if op == "SUB": 
                    b = _bnot(b)
                out = _bxor(_bxor(a, b), carry)
                carry = _bmajority(a, b, carry)
            return out
    # Comparisons are normally predicates rather than stored outcomes.  Keep a
    # conservative fallback for completeness.
    rendered = _expr_sv_precise(expr, reg_widths)
    return f"((({rendered} >> 32'd{bit}) & 32'd1) != 32'd0)"


def _is_small_bool_const(expr: Any) -> bool: 
    return isinstance(expr, list) and len(expr) >= 2 and expr[0] == "CONST" and _u32(int(expr[1])) in (0, 1)

def _prefix_count_for_basis_order(rules: list[dict], basis_order: list[str]) -> int: 
    rank = {basis: i for i, basis in enumerate(basis_order)}
    prefixes: set[tuple[tuple[str, bool], ...]] = set()
    for rule in rules: 
        lits = [(str(x["basis"]), bool(x["polarity"])) for x in rule.get("enable", [])]
        lits = sorted(lits, key = lambda x: (rank.get(x[0], len(rank)), x[0], x[1]))
        seq = tuple(lits)
        for i in range(1, len(seq) + 1): 
            prefixes.add(seq[:i])
    return len(prefixes)


def _optimized_prefix_basis_order(rules: list[dict]) -> list[str]: 
    """Deterministically reduce shared conjunction-prefix nodes.

    Predicate conjunction is commutative, so changing literal order cannot
    change semantics.  Start from the historical frequency ordering, then use
    exhaustive pair-swap local improvement until no swap reduces the exact
    number of unique prefix nodes.  The search depends only on predicate IDs
    and rule cubes; it contains no protocol/state-specific knowledge.
    """
    frequency: dict[str, int] = defaultdict(int)
    for rule in rules: 
        for item in rule.get("enable", []): 
            frequency[str(item["basis"])] += 1
    order = sorted(frequency, key = lambda b: (-frequency[b], b))
    current = _prefix_count_for_basis_order(rules, order)
    while True: 
        best_count = current
        best_order: list[str] | None = None
        for i in range(len(order) - 1): 
            for j in range(i + 1, len(order)): 
                trial = list(order)
                trial[i], trial[j] = trial[j], trial[i]
                count = _prefix_count_for_basis_order(rules, trial)
                if count < best_count: 
                    best_count = count
                    best_order = trial
        if best_order is None: 
            return order
        order = best_order
        current = best_count


def _build_prefix_nodes(rules: list[dict], *, optimize_basis_order: bool = False) -> tuple[
    dict[tuple[tuple[str, bool], ...], str], 
    list[tuple[str, tuple[tuple[str, bool], ...]]], 
    dict[str, str], 
]: 
    # A conjunction is order-independent.  The default preserves the accepted
    # historical frequency ordering.  Candidate backends may request an exact
    # prefix-count local search without changing rule semantics.
    basis_frequency: dict[str, int] = defaultdict(int)
    for rule in rules: 
        for item in rule.get("enable", []): 
            basis_frequency[str(item["basis"])] += 1
    if optimize_basis_order: 
        basis_order = _optimized_prefix_basis_order(rules)
        basis_rank = {basis: i for i, basis in enumerate(basis_order)}
        order_key = lambda x: (basis_rank.get(x[0], len(basis_rank)), x[0], x[1])
    else: 
        order_key = lambda x: (-basis_frequency[x[0]], x[0], x[1])

    def canonical(enable: list[dict]) -> tuple[tuple[str, bool], ...]: 
        lits = [(str(x["basis"]), bool(x["polarity"])) for x in enable]
        return tuple(sorted(lits, key = order_key))

    prefixes = set()
    full_by_rule: dict[str, tuple[tuple[str, bool], ...]] = {}
    for rule in rules: 
        lits = canonical(rule.get("enable", []))
        full_by_rule[str(rule["rule_id"])] = lits
        for i in range(1, len(lits) + 1): 
            prefixes.add(lits[:i])
    ordered = sorted(prefixes, key = lambda p: (len(p), p))
    names = {p: f"en_E{i:03d}" for i, p in enumerate(ordered)}
    rule_conditions = {
        rid: ("1'b1" if not lits else names[lits]) for rid, lits in full_by_rule.items()
    }
    return names, [(names[p], p) for p in ordered], rule_conditions


def _startup_condition(groups: list[list[dict]], expr_fn = _expr_sv) -> str: 
    clauses = []
    for group in groups: 
        terms = []
        for p in group: 
            e = expr_fn(p["expression"])
            terms.append(e if bool(p["polarity"]) else f"~({e})")
        clauses.append("(" + (" & ".join(terms) if terms else "1'b1") + ")")
    return " | ".join(clauses) if clauses else "1'b0"


def _detector_expr(det: dict, sched_rows: dict[str, dict]) -> str: 
    bit = int(det["input_bit"])
    quals = []
    for q in det.get("qualifiers", []): 
        if q.get("source") != "GPIO_INPUT": 
            raise ValueError(f"unsupported qualifier: {q}")
        b, level = int(q["bit"]), int(q["level"])
        quals.append(f"gpio_in[{b}]" if level else f"~gpio_in[{b}]")
    if det["kind"] == "QUALIFIED_INPUT_EDGE": 
        candidates = [
            row for row in sched_rows.values()
            if row.get("role") == "EDGE_HISTORY_AUX" and int(row.get("input_bit", -1)) == bit
        ]
        if len(candidates) != 1: 
            raise ValueError(f"cannot resolve history scheduler for GPIO[{bit}]")
        h = f"sched_{_sv_id(candidates[0]['id'])}"
        if det["edge"] == "RISE": 
            core = f"(~{h} & gpio_in[{bit}])"
        elif det["edge"] == "FALL": 
            core = f"({h} & ~gpio_in[{bit}])"
        else: 
            raise ValueError(det["edge"])
    elif det["kind"] == "POLLING_PHASE_COMPLETION": 
        ph = f"sched_{_sv_id(str(det['phase_state']))}"
        before, after = int(det["phase_before"]), int(det["phase_after"])
        phase_term = ph if before else f"~{ph}"
        input_term = f"gpio_in[{bit}]" if after else f"~gpio_in[{bit}]"
        core = f"({phase_term} & {input_term})"
    else: 
        raise ValueError(f"unsupported detector kind {det['kind']}")
    if quals: 
        core = "(" + " & ".join([core] + quals) + ")"
    return f"active_run & {core}"



def _physical_storage_bits(sp: dict[str, Any], semw: int) -> list[int]: 
    """Semantic bit positions physically stored by one storage-plan row."""
    sk = str(sp["storage_kind"])
    if sk == "DIRECT": 
        return list(range(semw))
    if sk == "NARROW_ZERO_EXTEND": 
        return list(range(int(sp["storage_bits"])))
    if sk == "PACKED_MASK_BITS": 
        return [int(x) for x in sp.get("stored_bits", [])]
    if sk in ("CONST", "DERIVED_EXPR"): 
        return []
    raise ValueError(f"unsupported storage kind: {sk}")


def _build_action_groups(rules: list[dict], rule_conditions: dict[str, str]) -> tuple[list[dict], dict[str, str]]: 
    """Group target updates that share exactly the same event+guard condition.

    The grouping is semantics-neutral: it only gives one name to a condition
    that is already shared by multiple materialized update rules.
    """
    grouped: dict[tuple[str, tuple[tuple[str, bool], ...]], list[dict]] = defaultdict(list)
    for rule in rules: 
        key = (
            str(rule["event_class"]), 
            tuple((str(x["basis"]), bool(x["polarity"])) for x in rule.get("enable", [])), 
        )
        grouped[key].append(rule)
    actions: list[dict] = []
    rule_to_action: dict[str, str] = {}
    for i, (key, members) in enumerate(sorted(grouped.items(), key = lambda kv: kv[0])): 
        event, _enable = key
        # All members have the same guard cube, hence the same shared-prefix
        # condition. Use the first member as the canonical condition source.
        cond = rule_conditions[str(members[0]["rule_id"])]
        name = f"act_A{i:03d}"
        actions.append({"name": name, "event": event, "guard_condition": cond, "rules": members})
        for rule in members: 
            rule_to_action[str(rule["rule_id"])] = name
    return actions, rule_to_action



def _build_factored_condition_dag(rules: list[dict]) -> tuple[
    list[tuple[str, frozenset[tuple[str, bool]], frozenset[tuple[str, bool]]]], 
    dict[frozenset[tuple[str, bool]], str], 
    dict[str, frozenset[tuple[str, bool]]], 
]: 
    """Factor arbitrary conjunction subexpressions across rule conditions.

    Unlike a global prefix trie, this generic pair-extraction heuristic can
    share a conjunction even when the common literals occur in different
    positions in different cubes.  Every internal node is a pure AND, so the
    rewrite is semantics-neutral and independent of protocol/state names.
    """
    from collections import Counter
    full_by_rule: dict[str, frozenset[tuple[str, bool]]] = {}
    unique_conditions: set[frozenset[tuple[str, bool]]] = set()
    for rule in rules: 
        lits = frozenset((str(x["basis"]), bool(x["polarity"])) for x in rule.get("enable", []))
        full_by_rule[str(rule["rule_id"])] = lits
        unique_conditions.add(lits)

    def atom_tuple(a: frozenset[tuple[str, bool]]) -> tuple[tuple[str, bool], ...]: 
        # Never use repr(frozenset) as an ordering key: its element order
        # depends on PYTHONHASHSEED.  Canonical tuple ordering makes the
        # emitted factored DAG byte-for-byte reproducible across processes.
        return tuple(sorted(a, key = lambda x: (x[0], x[1])))

    def atom_key(a: frozenset[tuple[str, bool]]) -> tuple[int, tuple[tuple[str, bool], ...]]: 
        return (len(a), atom_tuple(a))

    exprs: list[set[frozenset[tuple[str, bool]]]] = [
        {frozenset((lit,)) for lit in cond}
        for cond in sorted(unique_conditions, key = lambda c: (len(c), atom_tuple(c)))
    ]
    node_names: dict[frozenset[tuple[str, bool]], str] = {}
    nodes: list[tuple[str, frozenset[tuple[str, bool]], frozenset[tuple[str, bool]]]] = []

    while any(len(e) > 1 for e in exprs): 
        freq: Counter[tuple[frozenset[tuple[str, bool]], frozenset[tuple[str, bool]]]] = Counter()
        for expr in exprs: 
            items = sorted(expr, key = atom_key)
            for i in range(len(items)): 
                for j in range(i + 1, len(items)): 
                    a, b = items[i], items[j]
                    if a & b: 
                        continue
                    pair = (a, b) if atom_key(a) <= atom_key(b) else (b, a)
                    freq[pair] += 1
        if freq: 
            (a, b), _ = max(
                freq.items(), 
                key = lambda kv: (
                    kv[1], 
                    len(kv[0][0]) + len(kv[0][1]), 
                    atom_tuple(kv[0][0]), 
                    atom_tuple(kv[0][1]), 
                ), 
            )
        else: 
            expr = next(e for e in exprs if len(e) > 1)
            a, b = sorted(expr, key = atom_key)[:2]
        union = frozenset(set(a) | set(b))
        if len(union) > 1 and union not in node_names: 
            name = f"fc_F{len(nodes):03d}"
            node_names[union] = name
            nodes.append((name, a, b))
        for expr in exprs: 
            if a in expr and b in expr: 
                expr.remove(a); expr.remove(b); expr.add(union)

    return nodes, node_names, full_by_rule

def emit_storage_optimized_dedicated_event_sv(
    ir: dict[str, Any], 
    *, 
    precise_expressions: bool = False, 
    optimize_prefix_order: bool = False, 
    bitwise_update_equations: bool = False, 
    event_semantic_update_equations: bool = False, 
    bitwise_outcome_lowering: bool = False, 
) -> str: 
    plan = ir.get("storage_optimization")
    if not plan: 
        raise ValueError("Dedicated Event IR has no storage_optimization annotation")
    if not plan.get("safe_storage_proof"): 
        raise ValueError("storage optimization lacks a conservative source-relation proof")

    regs = list(ir["architectural_registers"])
    reg_by_id = {r["id"]: r for r in regs}
    reg_widths = {str(r["id"]): int(r["width"]) for r in regs}
    expr_fn = (lambda e: _expr_sv_precise(e, reg_widths)) if precise_expressions else _expr_sv
    storage = {r["register"]: r for r in plan["register_storage"]}
    sched_rows = {r["id"]: r for r in ir.get("scheduler_owned_sources", [])}

    nonphysical_kinds = ("CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED")
    retained_rules = [
        r for r in ir["update_rules"]
        if r.get("materialize") and storage[r["target"]]["storage_kind"] not in nonphysical_kinds
    ]

    # Phase-local state elision is proof-backed at consumer/read contexts.
    # The IR carries predicate replacements produced by the legal-product
    # analyzer; the emitter consumes those annotations directly instead of
    # first materializing the removed register and patching the generated SV.
    phase_pred_repl: dict[str, dict[str, Any]] = {}
    for sp in storage.values(): 
        if str(sp.get("storage_kind")) != "PHASE_LOCAL_ELIDED": 
            continue
        for bid, repl in sp.get("phase_local_elision", {}).items(): 
            bid = str(bid)
            if bid in phase_pred_repl and phase_pred_repl[bid] != repl: 
                raise ValueError(f"conflicting phase-local predicate replacement for {bid}")
            phase_pred_repl[bid] = repl

    def phase_pred_expr(bid: str, visiting: set[str] | None = None) -> str: 
        if bid not in phase_pred_repl: 
            return expr_fn(pred_by_id[bid]["expression"])
        visiting = set() if visiting is None else set(visiting)
        if bid in visiting: 
            raise ValueError(f"cyclic phase-local predicate replacement at {bid}")
        visiting.add(bid)
        repl = phase_pred_repl[bid]
        kind = str(repl["kind"])
        if kind == "CONST": 
            return "1'b1" if int(repl.get("constant", 0)) else "1'b0"
        if kind == "PRED": 
            dep = str(repl["predicate"])
            raw = f"pred_{dep}"
            return f"~({raw})" if bool(repl.get("invert")) else raw
        raise ValueError(f"unsupported phase-local predicate replacement: {repl}")
    eventsem_rules: list[dict[str, Any]] = []
    eventsem_nodes = []
    eventsem_node_names = {}
    eventsem_full_by_rule = {}
    if event_semantic_update_equations: 
        meta = ir.get("event_semantic_update_relation")
        if not meta: 
            raise ValueError("event_semantic_update_equations requested without event_semantic_update_relation")
        for raw in meta.get("rules", []): 
            if storage[str(raw["target"])]["storage_kind"] in nonphysical_kinds: 
                continue
            row = deepcopy(raw)
            row["enable"] = (
                [{"basis": "P:" + str(x["basis"]), "polarity": bool(x["polarity"])} for x in raw.get("enable", [])]
                + [{"basis": "D:" + str(x["detector"]), "polarity": bool(x["polarity"])} for x in raw.get("detector_enable", [])]
            )
            eventsem_rules.append(row)
        eventsem_nodes, eventsem_node_names, eventsem_full_by_rule = _build_factored_condition_dag(eventsem_rules)
        prefix_nodes, prefix_order, rule_conditions = {}, [], {}
        used_basis = sorted({str(x["basis"]) for r in eventsem_rules for x in r.get("enable", []) if str(x["basis"]).startswith("P:")})
        used_basis = [x[2:] for x in used_basis]
    else: 
        prefix_nodes, prefix_order, rule_conditions = _build_prefix_nodes(retained_rules, optimize_basis_order = optimize_prefix_order)
        used_basis = sorted({str(x["basis"]) for r in retained_rules for x in r.get("enable", [])})
    pred_by_id = {p["id"]: p for p in ir["predicate_basis"]}
    # Replacement predicates may depend on another basis that was not otherwise
    # used by a retained physical update. Declare that transitive dependency too.
    changed = True
    while changed: 
        changed = False
        for bid in list(used_basis): 
            repl = phase_pred_repl.get(str(bid))
            if repl and str(repl.get("kind")) == "PRED": 
                dep = str(repl["predicate"])
                if dep not in used_basis: 
                    used_basis.append(dep)
                    changed = True
    used_basis = sorted(set(used_basis))

    L: list[str] = [
        "`timescale 1ns/1ps", "", 
        "module bio2rtl_generated (", 
        "    input  logic        clk,", 
        "    input  logic        reset,", 
        "    input  logic [31:0] gpio_in,", 
        "    output logic [31:0] gpio_out,", 
        "    output logic [31:0] gpio_oe", 
        ");", "", 
        "// Dedicated event hardware with proof-backed event-boundary storage minimization.", 
        f"// Physical storage plan: {plan['preoptimization_storage_upper_bound_bits']} -> {plan['natural_storage_bits']} bits.", 
        "logic active_run;", 
    ]

    # Architectural semantic views + physical storage.
    for reg in regs: 
        rid, semw = reg["id"], int(reg["width"])
        sp = storage[rid]
        sk, sb = sp["storage_kind"], int(sp["storage_bits"])
        if sk == "CONST": 
            L.append(f"wire [{semw-1}:0] r_{rid} = {semw}'d{int(sp['constant_value']) & ((1<<semw)-1)};")
        elif sk == "DERIVED_EXPR": 
            # Emitted after all physically stored semantic views are declared.
            pass
        elif sk == "PHASE_LOCAL_ELIDED": 
            # No global semantic wire is emitted. The proof guarantees every
            # surviving consumer is replaced by phase_pred_repl above.
            pass
        elif sk == "DIRECT": 
            if bitwise_update_equations: 
                L.append(f"logic [{semw-1}:0] r_{rid};")
            else: 
                L.append(f"logic [{semw-1}:0] r_{rid}, n_{rid};")
        elif sk == "NARROW_ZERO_EXTEND": 
            L.append(f"logic [{sb-1}:0] r_{rid}_store;")
            if not bitwise_update_equations: 
                L.append(f"logic [{semw-1}:0] n_{rid};")
            if semw == sb: 
                L.append(f"wire [{semw-1}:0] r_{rid} = r_{rid}_store;")
            else: 
                L.append(f"wire [{semw-1}:0] r_{rid} = {{{semw-sb}'d0, r_{rid}_store}};")
        elif sk == "PACKED_MASK_BITS": 
            bits = list(sp["stored_bits"])
            L.append(f"logic [{sb-1}:0] r_{rid}_packed;")
            if not bitwise_update_equations: 
                L.append(f"logic [{semw-1}:0] n_{rid};")
            terms = [f"({{{semw-1}'b0, r_{rid}_packed[{i}]}} << {bit})" for i, bit in enumerate(bits)]
            const_one = sum(1 << int(bit) for bit in sp.get("constant_one_bits", []))
            if const_one: 
                terms.append(f"{semw}'d{const_one & ((1 << semw) - 1)}")
            L.append(f"wire [{semw-1}:0] r_{rid} = " + (" | ".join(terms) if terms else f"{semw}'d0") + ";")
        else: 
            raise ValueError(f"unsupported storage kind: {sk}")
    # Derived architectural values are pure combinational semantic views.  No
    # transition rule is materialized for them; the invariant proof guarantees
    # that their value equals this expression at every represented boundary.
    pending = [r for r in regs if storage[r["id"]]["storage_kind"] == "DERIVED_EXPR"]
    emitted: set[str] = set()
    while pending: 
        progress = False
        for reg in list(pending): 
            rid = reg["id"]
            deps = set(storage[rid].get("derived_from", []))
            pending_ids = {x["id"] for x in pending}
            if deps & (pending_ids - {rid}): 
                continue
            semw = int(reg["width"])
            L.append(f"wire [{semw-1}:0] r_{rid} = {expr_fn(storage[rid]['derived_expression'])};")
            emitted.add(rid); pending.remove(reg); progress = True
        if not progress: 
            raise ValueError(f"cyclic derived storage dependency: {[r['id'] for r in pending]}")
    L.append("")

    for sid in sched_rows: 
        L.append(f"logic [0:0] sched_{_sv_id(sid)};")
    L.append("")

    # Event detectors.
    for det in ir["scheduler_detectors"]: 
        dn = _det_id(det["event_id"])
        L.append(f"logic {dn};")
        L.append(f"assign {dn} = {_detector_expr(det, sched_rows)};")
    L.append("")

    # Named event-class decoders are unnecessary for cross-event candidates;
    # their proven relation consumes detector truth cubes directly.
    if not event_semantic_update_equations: 
        for event in ir["event_classes"]: 
            name = _evt_id(event["event_class"])
            terms = [ _det_id(x) for x in event.get("required_detectors", []) ]
            terms += [ f"~{_det_id(x)}" for x in event.get("forbidden_detectors", []) ]
            body = " & ".join(terms) if terms else "1'b1"
            L.append(f"logic {name};")
            L.append(f"assign {name} = active_run & ({body});")
        L.append("")

    # Only predicates actually needed by retained physical updates are emitted.
    for bid in used_basis: 
        if bid not in pred_by_id: 
            raise ValueError(f"unknown predicate basis {bid}")
        L.append(f"logic pred_{bid};")
        L.append(f"assign pred_{bid} = {phase_pred_expr(bid)};")
    L.append("")

    # Shared conjunction network. Cross-event candidates use arbitrary common
    # pair factoring over detector+predicate literals; historical candidates
    # preserve the accepted prefix DAG.
    eventsem_rule_conditions: dict[str, str] = {}
    if event_semantic_update_equations: 
        def leaf_expr(atom: frozenset[tuple[str, bool]]) -> str: 
            if len(atom) != 1: 
                return eventsem_node_names[atom]
            basis, pol = next(iter(atom))
            if basis.startswith("P:"): 
                raw = f"pred_{basis[2:]}"
            elif basis.startswith("D:"): 
                raw = _det_id(basis[2:])
            else: 
                raise ValueError(basis)
            return raw if pol else f"~({raw})"
        for name, a, b in eventsem_nodes: 
            L.append(f"logic {name};")
            L.append(f"assign {name} = {leaf_expr(a)} & {leaf_expr(b)};")
        for rid, cond in eventsem_full_by_rule.items(): 
            if not cond: 
                eventsem_rule_conditions[rid] = "1'b1"
            elif len(cond) == 1: 
                eventsem_rule_conditions[rid] = leaf_expr(frozenset(cond))
            else: 
                eventsem_rule_conditions[rid] = eventsem_node_names[frozenset(cond)]
    else: 
        for en_name, prefix in prefix_order: 
            parent = prefix[:-1]
            bid, pol = prefix[-1]
            lhs = "1'b1" if not parent else prefix_nodes[parent]
            lit = f"pred_{bid}" if pol else f"~pred_{bid}"
            L.append(f"logic {en_name};")
            L.append(f"assign {en_name} = {lhs} & {lit};")
    L.append("")

    # Combinational next state.  The accepted historical emitter uses a
    # per-target priority mux.  The optional equation backend instead lowers
    # each *physical storage bit* directly to a hold/update Boolean equation.
    # It consumes exactly the same proven rule relation.
    stored_ids = [r["id"] for r in regs if storage[r["id"]]["storage_kind"] not in nonphysical_kinds]
    if not bitwise_update_equations: 
        L.append("always_comb begin")
        for rid in stored_ids: 
            L.append(f"    n_{rid} = r_{rid};")

        event_order = [e["event_class"] for e in ir["event_classes"]]
        rules_by_event_target: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for rule in retained_rules: 
            rules_by_event_target[(rule["event_class"], rule["target"])].append(rule)

        for event_name in event_order: 
            event_targets = [rid for rid in stored_ids if (event_name, rid) in rules_by_event_target]
            if not event_targets: 
                continue
            L.append(f"    if ({_evt_id(event_name)}) begin")
            for rid in event_targets: 
                rules = rules_by_event_target[(event_name, rid)]
                # Merge rules with identical outcomes into an OR of shared enables.
                groups: list[tuple[Any, list[str]]] = []
                group_index: dict[str, int] = {}
                for rule in rules: 
                    key = repr(rule["outcome"])
                    cond = rule_conditions[str(rule["rule_id"])]
                    if key not in group_index: 
                        group_index[key] = len(groups)
                        groups.append((rule["outcome"], [cond]))
                    else: 
                        groups[group_index[key]][1].append(cond)
                for gi, (outcome, conds) in enumerate(groups): 
                    cond = " | ".join(conds)
                    kw = "if" if gi == 0 else "else if"
                    L.append(f"        {kw} ({cond}) n_{rid} = {expr_fn(outcome)};")
            L.append("    end")
        L.append("end")
        L.append("")
    else: 
        if event_semantic_update_equations: 
            active_rules = eventsem_rules
            rule_to_action = {str(r["rule_id"]): eventsem_rule_conditions[str(r["rule_id"])] for r in active_rules}
            L.append(f"// Cross-event bitwise update-equation backend: {len(set(rule_to_action.values()))} shared detector+guard conditions.")
        else: 
            active_rules = retained_rules
            actions, rule_to_action = _build_action_groups(retained_rules, rule_conditions)
            L.append(f"// Bitwise update-equation backend: {len(actions)} shared event+guard actions.")
            for action in actions: 
                cond = str(action["guard_condition"])
                rhs = _evt_id(str(action["event"])) if cond == "1'b1" else f"({_evt_id(str(action['event']))} & {cond})"
                L.append(f"logic {action['name']};")
                L.append(f"assign {action['name']} = {rhs};")
        L.append("")

        rules_by_target: dict[str, list[dict]] = defaultdict(list)
        for rule in active_rules: 
            rules_by_target[str(rule["target"])].append(rule)

        def outcome_bit(outcome: Any, bit: int) -> str: 
            if bitwise_outcome_lowering: 
                return _expr_bit_sv(outcome, bit, reg_widths)
            if isinstance(outcome, list) and outcome: 
                if outcome[0] == "CONST": 
                    return "1'b1" if ((_u32(int(outcome[1])) >> bit) & 1) else "1'b0"
                if outcome[0] == "REG": 
                    src = str(outcome[1]); sw = int(reg_widths.get(src, 32))
                    return f"r_{_sv_id(src)}[{bit}]" if bit < sw else "1'b0"
                if outcome[0] == "SCHED_REG": 
                    return f"sched_{_sv_id(str(outcome[1]))}[0]" if bit == 0 else "1'b0"
                if outcome[0] == "BIT_VALUE": 
                    return _bit_value_raw_sv(outcome, reg_widths) if bit == 0 else "1'b0"
            rendered = expr_fn(outcome)
            return f"((({rendered} >> 32'd{bit}) & 32'd1) != 32'd0)"

        for rid in stored_ids: 
            sp = storage[rid]; semw = int(reg_by_id[rid]["width"]); sk = str(sp["storage_kind"])
            semantic_bits = _physical_storage_bits(sp, semw)
            if sk == "DIRECT": 
                phys_name = f"d_{rid}"
                L.append(f"wire [{len(semantic_bits)-1}:0] {phys_name};")
                q_for = lambda pi, sb: f"r_{rid}[{sb}]"
            elif sk == "NARROW_ZERO_EXTEND": 
                phys_name = f"d_{rid}_store"
                L.append(f"wire [{len(semantic_bits)-1}:0] {phys_name};")
                q_for = lambda pi, sb: f"r_{rid}_store[{pi}]"
            elif sk == "PACKED_MASK_BITS": 
                phys_name = f"d_{rid}_packed"
                L.append(f"wire [{len(semantic_bits)-1}:0] {phys_name};")
                q_for = lambda pi, sb: f"r_{rid}_packed[{pi}]"
            else: 
                raise ValueError(sk)
            target_rules = rules_by_target.get(rid, [])
            for pi, sb in enumerate(semantic_bits): 
                # The update-enable term is shared by all possible values for
                # this storage bit.  Set-value terms are ORed because the
                # relation verifier has proven cross-outcome exclusivity on
                # the represented semantic domain.
                actions_for_target = sorted({rule_to_action[str(r["rule_id"])] for r in target_rules})
                upd = " | ".join(actions_for_target) if actions_for_target else "1'b0"
                set_terms: list[str] = []
                for rule in target_rules: 
                    act = rule_to_action[str(rule["rule_id"])]
                    vb = outcome_bit(rule["outcome"], sb)
                    if vb == "1'b0": 
                        continue
                    if vb == "1'b1": 
                        set_terms.append(act)
                    else: 
                        set_terms.append(f"({act} & {vb})")
                set_expr = " | ".join(sorted(set(set_terms))) if set_terms else "1'b0"
                L.append(f"assign {phys_name}[{pi}] = ({q_for(pi, sb)} & ~({upd})) | ({set_expr});")
            L.append("")

    L.append("assign gpio_out = r_G_DATA;")
    L.append("assign gpio_oe  = r_G_DIR;")
    L.append("")

    # Sequential storage.
    L.append("always_ff @(posedge clk or posedge reset) begin")
    L.append("    if (reset) begin")
    L.append("        active_run <= 1'b0;")
    startup_vals = {x["register"]: int(x["value"][1]) for x in ir["startup"]["register_values"]}
    for rid in stored_ids: 
        sp = storage[rid]
        reset = startup_vals[rid]
        sk = sp["storage_kind"]
        if sk == "DIRECT": 
            w = int(reg_by_id[rid]["width"])
            L.append(f"        r_{rid} <= {w}'d{reset & ((1<<w)-1)};")
        elif sk == "NARROW_ZERO_EXTEND": 
            w = int(sp["storage_bits"])
            L.append(f"        r_{rid}_store <= {w}'d{reset & ((1<<w)-1)};")
        elif sk == "PACKED_MASK_BITS": 
            bits = list(sp["stored_bits"])
            packed = sum(((reset >> bit) & 1) << i for i, bit in enumerate(bits))
            L.append(f"        r_{rid}_packed <= {len(bits)}'d{packed};")
    for sid, row in sched_rows.items(): 
        raw = row["initial_value"]
        L.append(f"        sched_{_sv_id(sid)} <= 1'd{int(raw[1]) & 1};")
    L.append("    end else begin")

    # Scheduler maintenance is independent of active_run, matching baseline.
    for sid, row in sched_rows.items(): 
        sn = f"sched_{_sv_id(sid)}"
        if row["maintenance"] == "CAPTURE_GPIO_INPUT": 
            L.append(f"        {sn} <= gpio_in[{int(row['input_bit'])}];")
        elif row["maintenance"] == "EVENT_PHASE_AUTOMATON": 
            dets = [d for d in ir["scheduler_detectors"] if d.get("phase_state") == sid]
            for i, det in enumerate(dets): 
                kw = "if" if i == 0 else "else if"
                L.append(f"        {kw} ({_det_id(det['event_id'])}) {sn} <= 1'b{int(det['phase_after'])};")
        else: 
            raise ValueError(f"unsupported scheduler maintenance: {row['maintenance']}")

    start_cond = _startup_condition(ir["startup"].get("startup_run_predicates", []), expr_fn)
    L.append("        if (!active_run) begin")
    L.append(f"            if ({start_cond}) active_run <= 1'b1;")
    L.append("        end else begin")
    for rid in stored_ids: 
        sp = storage[rid]
        sk = sp["storage_kind"]
        if bitwise_update_equations: 
            if sk == "DIRECT": 
                L.append(f"            r_{rid} <= d_{rid};")
            elif sk == "NARROW_ZERO_EXTEND": 
                L.append(f"            r_{rid}_store <= d_{rid}_store;")
            elif sk == "PACKED_MASK_BITS": 
                L.append(f"            r_{rid}_packed <= d_{rid}_packed;")
        else: 
            if sk == "DIRECT": 
                L.append(f"            r_{rid} <= n_{rid};")
            elif sk == "NARROW_ZERO_EXTEND": 
                w = int(sp["storage_bits"])
                L.append(f"            r_{rid}_store <= n_{rid}[{w-1}:0];")
            elif sk == "PACKED_MASK_BITS": 
                bits = list(sp["stored_bits"])
                concat = ", ".join(f"n_{rid}[{b}]" for b in reversed(bits))
                L.append(f"            r_{rid}_packed <= {{{concat}}};")
    L.append("        end")
    L.append("    end")
    L.append("end")
    L.append("")

    # Debug/provenance aliases are generic and do not carry CFG/basic-block identity.
    for reg in regs: 
        if reg["kind"] not in ("PHYSICAL", "SEMANTIC"): 
            continue
        rid = str(reg["id"])
        if storage[rid]["storage_kind"] == "PHASE_LOCAL_ELIDED": 
            continue
        prov = _sv_id(str(reg["provenance"]))
        w = int(reg["width"])
        L.append(f"wire [{w-1}:0] r_{prov} = r_{reg['id']};")
    for sid, row in sched_rows.items(): 
        prov = _sv_id(str(row["source"]))
        L.append(f"wire [0:0] r_{prov} = sched_{_sv_id(sid)};")
    L.append("")
    L.append("endmodule")
    return "\n".join(L) + "\n"
