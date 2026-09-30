from __future__ import annotations

"""Proof-oriented expression simplification for Dedicated Event IR.

This pass removes software/ISA-width artifacts from already recovered Dedicated
Event hardware expressions without changing event partitioning, update guards,
storage, or source-transition identity.

The simplifier is intentionally generic:

* it uses only Dedicated Event expression structure, declared register widths,
  and proof-backed per-register value domains already attached by the storage
  analysis;
* it never examines protocol names, GPIO numbers, BB identities, source stack
  names, or transition IDs;
* all range-dependent rewrites are intended to be followed by
  :mod:`dedicated_event_expression_minimization_verifier`, which exhaustively
  checks every rewritten expression over the Cartesian over-approximation used
  to justify storage48.

The important class of rewrites is bit-precise recovery.  BIO arithmetic is
32-bit because it ran on a CPU, but a recovered hardware predicate may depend
on one bit of a 4-bit counter or one sampled GPIO bit.  Keeping 32-bit masks,
shifts, and comparisons in the final RTL needlessly reconstructs CPU-width
logic after the CFG has already been removed.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

MASK32 = 0xFFFFFFFF


def _u32(value: int) -> int: 
    return int(value) & MASK32


def _is_const(expr: Any, value: int | None = None) -> bool: 
    return (
        isinstance(expr, list)
        and len(expr) >= 2
        and expr[0] == "CONST"
        and (value is None or _u32(int(expr[1])) == _u32(value))
    )


def _is_reg(expr: Any) -> bool: 
    return isinstance(expr, list) and len(expr) >= 2 and expr[0] == "REG"


def _is_op(expr: Any, name: str | None = None) -> bool: 
    return (
        isinstance(expr, list)
        and len(expr) >= 3
        and expr[0] == "OP"
        and (name is None or str(expr[1]) == name)
    )


def _low_mask_width(value: int) -> int | None: 
    value = _u32(value)
    if value == 0: 
        return 0
    if value & (value + 1): 
        return None
    return value.bit_length()


def _collect_domains(ir: dict[str, Any]) -> tuple[dict[str, int], dict[str, list[int]]]: 
    widths = {str(r["id"]): int(r["width"]) for r in ir["architectural_registers"]}
    domains: dict[str, list[int]] = {}
    plan = ir.get("storage_optimization") or {}
    for row in plan.get("register_storage", []): 
        rid = str(row["register"])
        values = row.get("reachable_values_upper_bound")
        if values is None: 
            values = row.get("reachable_values")
        if values is not None: 
            domains[rid] = sorted({_u32(int(v)) for v in values})
    for rid, width in widths.items(): 
        if rid not in domains and width <= 12: 
            domains[rid] = list(range(1 << width))
    return widths, domains


def _may_one_mask(expr: Any, widths: dict[str, int], domains: dict[str, list[int]]) -> int: 
    """Conservative bit mask: every possibly-one result bit is set."""
    if not isinstance(expr, list) or not expr: 
        return MASK32
    tag = expr[0]
    if tag == "CONST": 
        return _u32(int(expr[1]))
    if tag == "REG": 
        rid = str(expr[1])
        vals = domains.get(rid)
        if vals is not None: 
            out = 0
            for v in vals: 
                out |= _u32(v)
            return out
        width = int(widths.get(rid, 32))
        return MASK32 if width >= 32 else (1 << width) - 1
    if tag == "SCHED_REG": 
        return 1
    if tag == "GPIO_INPUT": 
        return MASK32
    if tag == "BIT_VALUE": 
        bit = int(expr[2])
        return 1 if (_may_one_mask(expr[1], widths, domains) >> bit) & 1 else 0
    if tag == "EQ_CONST" or tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        return 1
    if tag == "OP": 
        op = str(expr[1])
        args = expr[2]
        if len(args) != 2: 
            return MASK32
        a, b = args
        ma = _may_one_mask(a, widths, domains)
        mb = _may_one_mask(b, widths, domains)
        if op == "AND": 
            return ma & mb
        if op in ("OR", "XOR"): 
            return ma | mb
        if op == "SHL" and _is_const(b): 
            return _u32(ma << (_u32(int(b[1])) & 31))
        if op == "SHR" and _is_const(b): 
            return ma >> (_u32(int(b[1])) & 31)
        # Carries/variable shifts are intentionally conservative.
        return MASK32
    return MASK32


def _simplify_once(expr: Any, widths: dict[str, int], domains: dict[str, list[int]]) -> Any: 
    if not isinstance(expr, list) or not expr: 
        return deepcopy(expr)
    tag = expr[0]
    if tag in ("CONST", "REG", "SCHED_REG", "GPIO_INPUT"): 
        return deepcopy(expr)

    if tag == "BIT_VALUE": 
        base = _simplify(expr[1], widths, domains)
        bit = int(expr[2])
        if _is_const(base): 
            return ["CONST", (_u32(int(base[1])) >> bit) & 1]
        if _is_reg(base) and bit >= int(widths.get(str(base[1]), 32)): 
            return ["CONST", 0]
        if isinstance(base, list) and base: 
            if base[0] == "BIT_VALUE": 
                return base if bit == 0 else ["CONST", 0]
            if _is_op(base): 
                op = str(base[1])
                a, b = base[2]
                if op in ("AND", "OR", "XOR"): 
                    return _simplify(
                        ["OP", op, [["BIT_VALUE", a, bit], ["BIT_VALUE", b, bit]]], 
                        widths, 
                        domains, 
                    )
                if op == "SHL" and _is_const(b): 
                    shift = _u32(int(b[1])) & 31
                    if bit < shift: 
                        return ["CONST", 0]
                    return _simplify(["BIT_VALUE", a, bit - shift], widths, domains)
                if op == "SHR" and _is_const(b): 
                    shift = _u32(int(b[1])) & 31
                    if bit + shift >= 32: 
                        return ["CONST", 0]
                    return _simplify(["BIT_VALUE", a, bit + shift], widths, domains)
        return ["BIT_VALUE", base, bit]

    if tag == "EQ_CONST": 
        return _simplify(["EQ", expr[1], ["CONST", int(expr[2])]], widths, domains)

    if tag == "OP": 
        op = str(expr[1])
        args = [_simplify(x, widths, domains) for x in expr[2]]
        if len(args) != 2: 
            return ["OP", op, args]
        a, b = args

        if _is_const(a) and _is_const(b): 
            av, bv = _u32(int(a[1])), _u32(int(b[1]))
            if op == "AND": 
                result = av & bv
            elif op == "OR": 
                result = av | bv
            elif op == "XOR": 
                result = av ^ bv
            elif op == "ADD": 
                result = _u32(av + bv)
            elif op == "SUB": 
                result = _u32(av - bv)
            elif op == "SHL": 
                result = _u32(av << (bv & 31))
            elif op == "SHR": 
                result = av >> (bv & 31)
            else: 
                return ["OP", op, args]
            return ["CONST", result]

        if op == "AND": 
            if _is_const(a, 0) or _is_const(b, 0): 
                return ["CONST", 0]
            if _is_const(a, MASK32): 
                return b
            if _is_const(b, MASK32): 
                return a
            if _is_const(a) and not _is_const(b): 
                a, b = b, a
            if _is_const(b): 
                mask = _u32(int(b[1]))
                if _is_reg(a): 
                    width = int(widths.get(str(a[1]), 32))
                    full = MASK32 if width >= 32 else (1 << width) - 1
                    if (mask & full) == full: 
                        return a
                if mask == 1: 
                    return _simplify(["BIT_VALUE", a, 0], widths, domains)
                if isinstance(a, list) and a and a[0] == "BIT_VALUE": 
                    return a if mask & 1 else ["CONST", 0]
                if _is_op(a, "AND") and _is_const(a[2][1]): 
                    return _simplify(
                        ["OP", "AND", [a[2][0], ["CONST", _u32(int(a[2][1][1])) & mask]]], 
                        widths, 
                        domains, 
                    )
                loww = _low_mask_width(mask)
                if loww is not None and 0 < loww <= 8 and _is_op(a, "OR"): 
                    return _simplify(
                        ["OP", "OR", [
                            ["OP", "AND", [a[2][0], ["CONST", mask]]], 
                            ["OP", "AND", [a[2][1], ["CONST", mask]]], 
                        ]], widths, domains, 
                    )
                if loww is not None and _is_op(a, "SHL") and _is_const(a[2][1]): 
                    shift = _u32(int(a[2][1][1])) & 31
                    if shift >= loww: 
                        return ["CONST", 0]
                    inner_mask = (1 << (loww - shift)) - 1
                    return _simplify(
                        ["OP", "SHL", [
                            ["OP", "AND", [a[2][0], ["CONST", inner_mask]]], 
                            ["CONST", shift], 
                        ]], widths, domains, 
                    )
            return ["OP", "AND", [a, b]]

        if op == "OR": 
            if _is_const(a, 0): 
                return b
            if _is_const(b, 0): 
                return a
            if _is_const(a) and not _is_const(b): 
                a, b = b, a
            if _is_const(b) and _is_op(a, "OR") and _is_const(a[2][1]): 
                return _simplify(
                    ["OP", "OR", [a[2][0], ["CONST", _u32(int(a[2][1][1])) | _u32(int(b[1]))]]], 
                    widths, 
                    domains, 
                )
            return ["OP", "OR", [a, b]]

        if op == "XOR": 
            if _is_const(a, 0): 
                return b
            if _is_const(b, 0): 
                return a
            return ["OP", "XOR", [a, b]]

        if op == "ADD": 
            if _is_const(a, 0): 
                return b
            if _is_const(b, 0): 
                return a
            if _is_const(b, MASK32) or (_is_const(b) and int(b[1]) == -1): 
                return _simplify(["OP", "SUB", [a, ["CONST", 1]]], widths, domains)
            if _is_const(a, MASK32) or (_is_const(a) and int(a[1]) == -1): 
                return _simplify(["OP", "SUB", [b, ["CONST", 1]]], widths, domains)
            return ["OP", "ADD", [a, b]]

        if op == "SUB": 
            if _is_const(b, 0): 
                return a
            return ["OP", "SUB", [a, b]]

        if op in ("SHL", "SHR"): 
            if _is_const(b): 
                shift = _u32(int(b[1])) & 31
                if shift == 0: 
                    return a
                # (x & (1<<b)) >> b  -> x[b]
                if op == "SHR" and _is_op(a, "AND") and _is_const(a[2][1]): 
                    mask = _u32(int(a[2][1][1]))
                    if mask == (1 << shift): 
                        return _simplify(["BIT_VALUE", a[2][0], shift], widths, domains)
                # u32(x << A) >> B, B>=A: exact slice-like rewrite.
                if op == "SHR" and _is_op(a, "SHL") and _is_const(a[2][1]): 
                    left = _u32(int(a[2][1][1])) & 31
                    if shift >= left: 
                        remaining = 32 - shift
                        mask = MASK32 if remaining >= 32 else (1 << remaining) - 1
                        return _simplify(
                            ["OP", "AND", [
                                ["OP", "SHR", [a[2][0], ["CONST", shift - left]]], 
                                ["CONST", mask], 
                            ]], widths, domains, 
                        )
                return ["OP", op, [a, ["CONST", shift]]]
            return ["OP", op, [a, b]]

        return ["OP", op, [a, b]]

    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        a = _simplify(expr[1], widths, domains)
        b = _simplify(expr[2], widths, domains)
        if _is_const(a) and _is_const(b): 
            av, bv = _u32(int(a[1])), _u32(int(b[1]))
            if tag == "EQ": 
                result = av == bv
            elif tag == "NE": 
                result = av != bv
            elif tag == "ULT": 
                result = av < bv
            elif tag == "UGE": 
                result = av >= bv
            else: 
                sa = av - (1 << 32) if av & (1 << 31) else av
                sb = bv - (1 << 32) if bv & (1 << 31) else bv
                result = sa < sb
                if tag == "NOT_SLT": 
                    result = not result
            return ["CONST", int(result)]

        # CPU masks that cover a narrow recovered register are semantically idle.
        for which in (0, 1): 
            x = a if which == 0 else b
            if _is_op(x, "AND") and _is_const(x[2][1]) and _is_reg(x[2][0]): 
                reg, mask_expr = x[2]
                width = int(widths.get(str(reg[1]), 32))
                full = MASK32 if width >= 32 else (1 << width) - 1
                if (_u32(int(mask_expr[1])) & full) == full: 
                    if which == 0: 
                        a = reg
                    else: 
                        b = reg

        # (narrow_reg + 1) == C -> narrow_reg == C-1, when no 32-bit wrap can satisfy C.
        if tag == "EQ" and _is_op(a, "ADD") and _is_reg(a[2][0]) and _is_const(a[2][1]) and _is_const(b): 
            reg, inc = a[2]
            width = int(widths.get(str(reg[1]), 32))
            cv = _u32(int(b[1]))
            if _u32(int(inc[1])) == 1 and 1 <= cv <= (1 << min(width, 31)): 
                return _simplify(["EQ", reg, ["CONST", cv - 1]], widths, domains)

        # Signed comparison is unsigned when the proof domain cannot reach bit31.
        if tag in ("SLT", "NOT_SLT"): 
            if (_may_one_mask(a, widths, domains) | _may_one_mask(b, widths, domains)) & 0x80000000 == 0: 
                tag = "ULT" if tag == "SLT" else "UGE"

        # x < 2^k can become a single bit test when all higher x bits are proven zero.
        if tag == "ULT" and _is_const(b): 
            threshold = _u32(int(b[1]))
            if threshold and (threshold & (threshold - 1)) == 0: 
                k = threshold.bit_length() - 1
                maybe = _may_one_mask(a, widths, domains)
                if maybe & ~((1 << (k + 1)) - 1) == 0: 
                    return _simplify(["EQ", ["BIT_VALUE", a, k], ["CONST", 0]], widths, domains)

        # (2^k-1) < x can become the top relevant bit when higher bits are proven zero.
        if tag == "ULT" and _is_const(a): 
            cv = _u32(int(a[1]))
            threshold = cv + 1
            if threshold and (threshold & (threshold - 1)) == 0: 
                k = threshold.bit_length() - 1
                maybe = _may_one_mask(b, widths, domains)
                if maybe & ~((1 << (k + 1)) - 1) == 0: 
                    return _simplify(["EQ", ["BIT_VALUE", b, k], ["CONST", 1]], widths, domains)

        # Equality of a non-overflowing constant shift can be compared before the shift.
        if tag == "EQ" and _is_op(a, "SHL") and _is_const(a[2][1]) and _is_const(b): 
            base, shift_expr = a[2]
            shift = _u32(int(shift_expr[1])) & 31
            cv = _u32(int(b[1]))
            maybe = _may_one_mask(base, widths, domains)
            if shift and (maybe >> (32 - shift)) == 0: 
                if cv & ((1 << shift) - 1): 
                    return ["CONST", 0]
                return _simplify(["EQ", base, ["CONST", cv >> shift]], widths, domains)

        return [tag, a, b]

    return deepcopy(expr)


def _simplify(expr: Any, widths: dict[str, int], domains: dict[str, list[int]]) -> Any: 
    current = deepcopy(expr)
    for _ in range(64): 
        nxt = _simplify_once(current, widths, domains)
        if nxt == current: 
            return nxt
        current = nxt
    raise RuntimeError(f"Dedicated expression simplification did not converge: {expr!r}")


def _expr_node_count(expr: Any) -> int: 
    if not isinstance(expr, list) or not expr: 
        return 0
    count = 1
    if expr[0] == "OP": 
        return count + sum(_expr_node_count(x) for x in expr[2])
    if expr[0] == "BIT_VALUE": 
        return count + _expr_node_count(expr[1])
    if expr[0] == "EQ_CONST": 
        return count + _expr_node_count(expr[1])
    if expr[0] in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        return count + _expr_node_count(expr[1]) + _expr_node_count(expr[2])
    return count


@dataclass
class ExpressionMinimizationStats: 
    predicate_expressions: int
    predicates_changed: int
    predicate_nodes_before: int
    predicate_nodes_after: int
    materialized_rules: int
    outcome_expressions_changed: int
    distinct_outcomes_before: int
    distinct_outcomes_after: int
    outcome_nodes_before: int
    outcome_nodes_after: int
    startup_expressions_changed: int


def minimize_dedicated_event_expressions(ir: dict[str, Any]) -> tuple[dict[str, Any], ExpressionMinimizationStats]: 
    widths, domains = _collect_domains(ir)
    out = deepcopy(ir)

    pred_before = 0
    pred_after = 0
    pred_changed = 0
    for row in out.get("predicate_basis", []): 
        original = row["expression"]
        simplified = _simplify(original, widths, domains)
        pred_before += _expr_node_count(original)
        pred_after += _expr_node_count(simplified)
        pred_changed += int(simplified != original)
        row["expression"] = simplified

    rule_count = 0
    outcome_changed = 0
    outcome_before = 0
    outcome_after = 0
    distinct_before: set[str] = set()
    distinct_after: set[str] = set()
    for row in out.get("update_rules", []): 
        if not row.get("materialize"): 
            continue
        rule_count += 1
        original = row["outcome"]
        simplified = _simplify(original, widths, domains)
        outcome_before += _expr_node_count(original)
        outcome_after += _expr_node_count(simplified)
        outcome_changed += int(simplified != original)
        distinct_before.add(repr(original))
        distinct_after.add(repr(simplified))
        row["outcome"] = simplified

    startup_changed = 0
    for group in out.get("startup", {}).get("startup_run_predicates", []): 
        for item in group: 
            original = item["expression"]
            simplified = _simplify(original, widths, domains)
            startup_changed += int(simplified != original)
            item["expression"] = simplified

    stats = ExpressionMinimizationStats(
        predicate_expressions = len(out.get("predicate_basis", [])), 
        predicates_changed = pred_changed, 
        predicate_nodes_before = pred_before, 
        predicate_nodes_after = pred_after, 
        materialized_rules = rule_count, 
        outcome_expressions_changed = outcome_changed, 
        distinct_outcomes_before = len(distinct_before), 
        distinct_outcomes_after = len(distinct_after), 
        outcome_nodes_before = outcome_before, 
        outcome_nodes_after = outcome_after, 
        startup_expressions_changed = startup_changed, 
    )
    out["expression_minimization"] = {
        "version": 1, 
        "proof_model": "CARTESIAN_OVERAPPROX_EXHAUSTIVE_EQUIVALENCE_REQUIRED", 
        "semantic_ir_parent": "DEDICATED_EVENT_GUARDMIN_STORAGE48", 
        "policy": [
            "remove CPU-width masks only when recovered register width makes them semantically idle", 
            "recover GPIO bit extraction and bit-level comparisons from mask/shift idioms", 
            "normalize exact modulo-2^32 arithmetic identities", 
            "use proof-backed value domains only for sign/range simplifications", 
            "do not change event classes, guards, storage, scheduler, or transition coverage", 
        ], 
        "stats": asdict(stats), 
    }
    return out, stats
