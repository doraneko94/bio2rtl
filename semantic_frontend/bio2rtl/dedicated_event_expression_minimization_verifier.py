from __future__ import annotations

"""Exhaustive finite-domain verifier for Dedicated Event expression minimization."""

from dataclasses import asdict, dataclass
import itertools
from typing import Any

from .dedicated_event_reachability import _eval_expr, _gpio_input_dependencies

MASK32 = 0xFFFFFFFF


def _u32(x: int) -> int: 
    return int(x) & MASK32


def _refs(expr: Any) -> tuple[set[str], set[str]]: 
    regs: set[str] = set()
    sched: set[str] = set()
    if not isinstance(expr, list) or not expr: 
        return regs, sched
    if expr[0] == "REG": 
        regs.add(str(expr[1]))
        return regs, sched
    if expr[0] == "SCHED_REG": 
        sched.add(str(expr[1]))
        return regs, sched
    children = []
    if expr[0] == "OP": 
        children = list(expr[2])
    elif expr[0] == "BIT_VALUE": 
        children = [expr[1]]
    elif expr[0] == "EQ_CONST": 
        children = [expr[1]]
    elif expr[0] in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        children = [expr[1], expr[2]]
    else: 
        children = [x for x in expr[1:] if isinstance(x, list)]
    for child in children: 
        r, s = _refs(child)
        regs |= r
        sched |= s
    return regs, sched


def _domains(ir: dict[str, Any]) -> tuple[dict[str, list[int]], dict[str, list[int]]]: 
    widths = {str(r["id"]): int(r["width"]) for r in ir["architectural_registers"]}
    regs: dict[str, list[int]] = {}
    for row in (ir.get("storage_optimization") or {}).get("register_storage", []): 
        rid = str(row["register"])
        values = row.get("reachable_values_upper_bound")
        if values is None: 
            values = row.get("reachable_values")
        if values is not None: 
            regs[rid] = sorted({_u32(v) for v in values})
    for rid, width in widths.items(): 
        if rid not in regs: 
            if width > 12: 
                raise ValueError(f"no finite proof domain for {rid} width={width}")
            regs[rid] = list(range(1 << width))
    sched = {str(r["id"]): [0, 1] for r in ir.get("scheduler_owned_sources", [])}
    return regs, sched


def _enumerate_gpio(bits: list[int]): 
    for pattern in range(1 << len(bits)): 
        value = 0
        for i, bit in enumerate(bits): 
            if (pattern >> i) & 1: 
                value |= 1 << bit
        yield value


def _check_expr(
    before: Any, 
    after: Any, 
    reg_domains: dict[str, list[int]], 
    sched_domains: dict[str, list[int]], 
    *, 
    max_combinations: int, 
) -> tuple[int, str | None]: 
    r0, s0 = _refs(before)
    r1, s1 = _refs(after)
    rnames = sorted(r0 | r1)
    snames = sorted(s0 | s1)
    gbits = sorted(_gpio_input_dependencies(before) | _gpio_input_dependencies(after))
    if len(gbits) > 14: 
        return 0, f"GPIO dependency too wide ({len(gbits)} bits): {gbits}"
    n = 1 << len(gbits)
    for name in rnames: 
        n *= len(reg_domains[name])
    for name in snames: 
        n *= len(sched_domains[name])
    if n > max_combinations: 
        return 0, f"Cartesian equivalence domain too large ({n} > {max_combinations})"
    checked = 0
    rproducts = itertools.product(*(reg_domains[n] for n in rnames)) if rnames else [()]
    for rv in rproducts: 
        regs = dict(zip(rnames, rv))
        sproducts = itertools.product(*(sched_domains[n] for n in snames)) if snames else [()]
        for sv in sproducts: 
            sched = dict(zip(snames, sv))
            for gpio in _enumerate_gpio(gbits): 
                a = _u32(_eval_expr(before, regs, sched, gpio))
                b = _u32(_eval_expr(after, regs, sched, gpio))
                checked += 1
                if a != b: 
                    return checked, (
                        f"mismatch regs={regs} sched={sched} gpio=0x{gpio:08x} "
                        f"before=0x{a:08x} after=0x{b:08x}"
                    )
    return checked, None


@dataclass
class ExpressionEquivalenceResult: 
    predicate_rows: int
    outcome_rows: int
    startup_rows: int
    changed_predicates: int
    changed_outcomes: int
    changed_startup: int
    evaluated_assignments: int
    failures: int
    failure_examples: list[str]


def verify_expression_minimization(
    source: dict[str, Any], 
    optimized: dict[str, Any], 
    *, 
    max_combinations_per_expression: int = 2_000_000, 
) -> ExpressionEquivalenceResult: 
    reg_domains, sched_domains = _domains(source)
    failures: list[str] = []
    evaluated = 0
    changed_pred = changed_out = changed_start = 0

    src_pred = {str(x["id"]): x["expression"] for x in source["predicate_basis"]}
    dst_pred = {str(x["id"]): x["expression"] for x in optimized["predicate_basis"]}
    if src_pred.keys() != dst_pred.keys(): 
        raise ValueError("predicate basis IDs changed during expression minimization")
    for pid in sorted(src_pred): 
        if src_pred[pid] == dst_pred[pid]: 
            continue
        changed_pred += 1
        n, err = _check_expr(src_pred[pid], dst_pred[pid], reg_domains, sched_domains, max_combinations = max_combinations_per_expression)
        evaluated += n
        if err: 
            failures.append(f"predicate {pid}: {err}")

    src_rules = {str(x["rule_id"]): x for x in source["update_rules"] if x.get("materialize")}
    dst_rules = {str(x["rule_id"]): x for x in optimized["update_rules"] if x.get("materialize")}
    if src_rules.keys() != dst_rules.keys(): 
        raise ValueError("materialized rule IDs changed during expression minimization")
    for rid in sorted(src_rules): 
        a, b = src_rules[rid], dst_rules[rid]
        structural = (a["event_class"], a["target"], a.get("enable"), a.get("source_transition_ids"))
        structural2 = (b["event_class"], b["target"], b.get("enable"), b.get("source_transition_ids"))
        if structural != structural2: 
            failures.append(f"rule {rid}: non-expression structure changed")
            continue
        if a["outcome"] == b["outcome"]: 
            continue
        changed_out += 1
        n, err = _check_expr(a["outcome"], b["outcome"], reg_domains, sched_domains, max_combinations = max_combinations_per_expression)
        evaluated += n
        if err: 
            failures.append(f"outcome {rid}/{a['target']}: {err}")

    sg = source.get("startup", {}).get("startup_run_predicates", [])
    dg = optimized.get("startup", {}).get("startup_run_predicates", [])
    if len(sg) != len(dg): 
        failures.append("startup group count changed")
    else: 
        for gi, (ga, gb) in enumerate(zip(sg, dg)): 
            if len(ga) != len(gb): 
                failures.append(f"startup group {gi} item count changed")
                continue
            for ii, (a, b) in enumerate(zip(ga, gb)): 
                if bool(a["polarity"]) != bool(b["polarity"]): 
                    failures.append(f"startup {gi}/{ii} polarity changed")
                    continue
                if a["expression"] == b["expression"]: 
                    continue
                changed_start += 1
                n, err = _check_expr(a["expression"], b["expression"], reg_domains, sched_domains, max_combinations = max_combinations_per_expression)
                evaluated += n
                if err: 
                    failures.append(f"startup {gi}/{ii}: {err}")

    return ExpressionEquivalenceResult(
        predicate_rows = len(src_pred), outcome_rows = len(src_rules), startup_rows = sum(len(g) for g in sg), 
        changed_predicates = changed_pred, changed_outcomes = changed_out, changed_startup = changed_start, 
        evaluated_assignments = evaluated, failures = len(failures), failure_examples = failures[:20], 
    )


def result_to_dict(result: ExpressionEquivalenceResult) -> dict[str, Any]: 
    return asdict(result)
