from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from itertools import product
from math import prod
from typing import Any

from .dedicated_event_derived_state import (
    _domain_map, 
    _group_transitions, 
    _guard_allows, 
    _outcome_values, 
    _predicate_map, 
    _refs, 
    _startup_values, 
    _use_summary, 
)
from .dedicated_event_reachability import _eval_expr
from .dedicated_event_semantic_guard_minimization import (
    _canonical_basis_aliases, 
    _predicate_variables, 
)


@dataclass
class JointControlDomainAnalysis: 
    source_transitions: int
    core_registers: tuple[str, ...]
    context_registers: tuple[str, ...]
    tracked_registers: tuple[str, ...]
    raw_domain_product: int
    reachable_states: int
    iterations: int
    transition_checks: int
    predicate_ids: tuple[str, ...]
    truth_patterns: tuple[tuple[tuple[str, bool], ...], ...]
    state_tuples: tuple[tuple[int, ...], ...]
    fallback_external_updates: int
    notes: tuple[str, ...]


def _storage_rows(ir: dict[str, Any]) -> dict[str, dict[str, Any]]: 
    return {str(x["register"]): x for x in ir["storage_optimization"]["register_storage"]}


def _recurrence_control_like(row: dict[str, Any]) -> bool: 
    classes = set((row.get("recurrence_classes") or {}).keys())
    return not bool(classes & {"COUNT", "SHIFT"})


def _select_core_registers(ir: dict[str, Any]) -> tuple[str, ...]: 
    domains = _domain_map(ir)
    rows = _storage_rows(ir)
    predicate_users, outcome_readers = _use_summary(ir)
    specs = {str(r["id"]): r for r in ir["architectural_registers"]}
    selected: list[str] = []
    for rid, spec in specs.items(): 
        if spec["kind"] == "GPIO" or rid not in domains or rid not in rows: 
            continue
        row = rows[rid]
        bits = int(row.get("storage_bits", spec["width"]))
        if bits <= 0 or bits > 2: 
            continue
        if len(domains[rid]) > 4: 
            continue
        if not _recurrence_control_like(row): 
            continue
        if outcome_readers.get(rid): 
            continue
        if not predicate_users.get(rid): 
            continue
        selected.append(rid)
    return tuple(sorted(selected))


def _select_context_registers(
    complete_ir: dict[str, Any], 
    baseline_ir: dict[str, Any], 
    core: tuple[str, ...], 
    *, 
    max_raw_domain_product: int, 
    max_context_registers: int, 
) -> tuple[str, ...]: 
    domains = _domain_map(baseline_ir)
    rows = _storage_rows(baseline_ir)
    transitions = _group_transitions(complete_ir)
    needed: Counter[str] = Counter()
    core_set = set(core)
    for tr in transitions: 
        for rid in core: 
            for kind, name in _refs(tr["outcomes"][rid]): 
                if kind == "REG" and name not in core_set: 
                    needed[name] += 1
    current = prod(len(domains[r]) for r in core) if core else 1
    chosen: list[str] = []
    for rid, _freq in sorted(needed.items(), key = lambda kv: (-kv[1], kv[0])): 
        if rid not in domains or rid not in rows: 
            continue
        bits = int(rows[rid].get("storage_bits", 99))
        if bits <= 0 or bits > 2 or len(domains[rid]) > 4: 
            continue
        if len(chosen) >= max_context_registers: 
            break
        next_product = current * len(domains[rid])
        if next_product > max_raw_domain_product: 
            continue
        chosen.append(rid)
        current = next_product
    return tuple(sorted(chosen))


def _joint_closure(
    complete_ir: dict[str, Any], 
    baseline_ir: dict[str, Any], 
    tracked: tuple[str, ...], 
    *, 
    max_reachable_states: int, 
    outcome_enumeration_limit: int, 
) -> tuple[set[tuple[int, ...]], int, int, int]: 
    """Conservative joint closure with dependency/result memoization.

    This is proof-identical to the original implementation.  Cache hits do not
    change transition_checks or fallback_external_updates; those counters remain
    logical proof-work statistics rather than evaluator-call counts.
    """
    domains = _domain_map(baseline_ir)
    startup = _startup_values(complete_ir)
    predicates = _predicate_map(complete_ir)
    transitions = _group_transitions(complete_ir)
    start = tuple(startup[r] for r in tracked)
    reachable: set[tuple[int, ...]] = {start}
    frontier: set[tuple[int, ...]] = {start}
    successor_cache: dict[tuple[int, ...], set[tuple[int, ...]]] = {}
    known = set(tracked)
    transition_checks = 0
    fallback_external_updates = 0
    iterations = 0

    # Compile reference metadata once.  The source relation contains many
    # repeated predicate/outcome expressions, so re-walking JSON expression
    # trees dominates the small 256-state reachable product otherwise.
    pred_meta: dict[str, tuple[Any, tuple[str, ...], bool]] = {}
    for pid, expr in predicates.items(): 
        rr = _refs(expr)
        regs = tuple(sorted(name for kind, name in rr if kind == "REG"))
        has_other = any(kind != "REG" for kind, _ in rr)
        pred_meta[pid] = (expr, regs, has_other)

    compiled = []
    for tr in transitions: 
        guards = []
        for item in tr["guard"]: 
            pid = str(item["basis"])
            expr, regs, has_other = pred_meta[pid]
            guards.append((pid, bool(item["polarity"]), expr, regs, has_other))
        outcomes = []
        for rid in tracked: 
            expr = tr["outcomes"][rid]
            rr = _refs(expr)
            reg_refs = tuple(sorted(name for kind, name in rr if kind == "REG"))
            external = tuple(name for name in reg_refs if name not in known)
            has_nonreg = any(kind != "REG" for kind, _ in rr)
            internal_refs = tuple(name for name in reg_refs if name in known)
            outcomes.append((rid, expr, repr(expr), internal_refs, bool(external or has_nonreg)))
        compiled.append((tr, guards, outcomes))

    pred_value_cache: dict[tuple[str, tuple[int, ...]], bool] = {}
    outcome_value_cache: dict[tuple[str, str, tuple[int, ...]], tuple[int, ...]] = {}

    while frontier: 
        iterations += 1
        discovered: set[tuple[int, ...]] = set()
        for state in frontier: 
            if state not in successor_cache: 
                current = dict(zip(tracked, state))
                succ: set[tuple[int, ...]] = set()
                for tr, guards, outcomes in compiled: 
                    allowed = True
                    for pid, polarity, expr, reg_refs, has_other in guards: 
                        # Unknown constraints are deliberately ignored exactly
                        # as in _guard_allows; this keeps the closure an over-approximation.
                        if has_other or any(r not in known for r in reg_refs): 
                            continue
                        vals_key = tuple(current[r] for r in reg_refs)
                        ck = (pid, vals_key)
                        value = pred_value_cache.get(ck)
                        if value is None: 
                            value = bool(_eval_expr(expr, current, {}, 0))
                            pred_value_cache[ck] = value
                        if value != polarity: 
                            allowed = False
                            break
                    if not allowed: 
                        continue
                    transition_checks += 1
                    next_sets: list[tuple[int, ...]] = []
                    for rid, expr, expr_key, internal_refs, is_fallback in outcomes: 
                        if is_fallback: 
                            fallback_external_updates += 1
                        ck = (expr_key, rid, tuple(current[r] for r in internal_refs))
                        vals = outcome_value_cache.get(ck)
                        if vals is None: 
                            vals = _outcome_values(
                                expr, 
                                current = current, 
                                domains = domains, 
                                target = rid, 
                                enumeration_limit = outcome_enumeration_limit, 
                            )
                            outcome_value_cache[ck] = vals
                        next_sets.append(vals)
                    combinations = prod(len(v) for v in next_sets)
                    if combinations > outcome_enumeration_limit: 
                        next_sets = [domains[r] for r in tracked]
                    for values in product(*next_sets): 
                        succ.add(tuple(int(v) for v in values))
                        if len(reachable) + len(succ) > max_reachable_states: 
                            raise RuntimeError(
                                f"joint control closure exceeded {max_reachable_states} states"
                            )
                successor_cache[state] = succ
            discovered |= successor_cache[state]
        discovered -= reachable
        if not discovered: 
            break
        reachable |= discovered
        if len(reachable) > max_reachable_states: 
            raise RuntimeError(f"joint control closure exceeded {max_reachable_states} states")
        frontier = discovered
    return reachable, iterations, transition_checks, fallback_external_updates


def analyze_joint_control_domain(
    complete_ir: dict[str, Any], 
    baseline_ir: dict[str, Any], 
    *, 
    max_raw_domain_product: int = 32768, 
    max_reachable_states: int = 32768, 
    max_context_registers: int = 3, 
    outcome_enumeration_limit: int = 4096, 
) -> JointControlDomainAnalysis: 
    domains = _domain_map(baseline_ir)
    core = _select_core_registers(baseline_ir)
    context = _select_context_registers(
        complete_ir, 
        baseline_ir, 
        core, 
        max_raw_domain_product = max_raw_domain_product, 
        max_context_registers = max_context_registers, 
    )
    tracked = tuple(sorted(set(core) | set(context)))
    if not tracked: 
        # This is an optional optimization domain, not a semantic prerequisite.
        # Programs with no small control-state cluster use the singleton identity
        # domain so downstream cached-domain tooling remains transactional and
        # byte-for-byte legacy behavior is unchanged when a real cluster exists.
        return JointControlDomainAnalysis(
            source_transitions = len(_group_transitions(complete_ir)), 
            core_registers = (), context_registers = (), tracked_registers = (), 
            raw_domain_product = 1, reachable_states = 1, iterations = 0, 
            transition_checks = 0, predicate_ids = (), truth_patterns = ((),), 
            state_tuples = ((),), fallback_external_updates = 0, 
            notes = (
                "No small control-state cluster was discovered; this optional pass is N/A and uses the singleton identity domain.", 
                "No state, event, GPIO bit, basic block, or protocol-specific special case was invented.", 
            ), 
        )
    raw_product = prod(len(domains[r]) for r in tracked)
    if raw_product > max_raw_domain_product: 
        raise ValueError(f"control-state raw product {raw_product} exceeds cap")
    states, iterations, transition_checks, fallback_external_updates = _joint_closure(
        complete_ir, 
        baseline_ir, 
        tracked, 
        max_reachable_states = max_reachable_states, 
        outcome_enumeration_limit = outcome_enumeration_limit, 
    )

    aliases = _canonical_basis_aliases(complete_ir)
    canonical_expr: dict[str, Any] = {}
    for p in complete_ir.get("predicate_basis", []): 
        bid = str(p["id"])
        canonical_expr.setdefault(aliases[bid], p["expression"])
    tracked_vars = {("REG", rid) for rid in tracked}
    pids: list[str] = []
    for pid, expr in canonical_expr.items(): 
        variables = _predicate_variables(expr)
        if variables and variables <= tracked_vars: 
            pids.append(pid)
    pids = sorted(pids)
    state_index = {rid: i for i, rid in enumerate(tracked)}
    truth_patterns: set[tuple[tuple[str, bool], ...]] = set()
    for state in states: 
        regs = {rid: int(state[state_index[rid]]) for rid in tracked}
        signature = tuple(
            (pid, bool(_eval_expr(canonical_expr[pid], regs, {}, 0))) for pid in pids
        )
        truth_patterns.add(signature)

    return JointControlDomainAnalysis(
        source_transitions = len(_group_transitions(complete_ir)), 
        core_registers = core, 
        context_registers = context, 
        tracked_registers = tracked, 
        raw_domain_product = raw_product, 
        reachable_states = len(states), 
        iterations = iterations, 
        transition_checks = transition_checks, 
        predicate_ids = tuple(pids), 
        truth_patterns = tuple(sorted(truth_patterns)), 
        state_tuples = tuple(sorted(states)), 
        fallback_external_updates = fallback_external_updates, 
        notes = (
            "Core registers are selected structurally from small proof-backed domains, non-counter/non-shift recurrence, predicate use, and absence of architectural datapath readers.", 
            "Small registers directly read by core updates are admitted only as proof context under a bounded raw-domain product.", 
            "The closure starts from reset and applies every corrected feasibility-proven transition whose tracked-register guard is satisfiable.", 
            "Unknown third-state, scheduler, and GPIO guard constraints are ignored, and unknown update dependencies are existentially enumerated or widened.", 
            "Therefore the joint tuple set over-approximates concrete reachable control state; impossible tuples may remain but concrete tuples cannot be excluded.", 
            "No protocol name, basic-block identity, transition-ID special case, source stack name, or fixed GPIO bit number participates in clustering.", 
        ), 
    )


def attach_joint_control_domain(ir: dict[str, Any], analysis: JointControlDomainAnalysis) -> dict[str, Any]: 
    from copy import deepcopy
    out = deepcopy(ir)
    out["joint_control_domain"] = {
        "version": 1, 
        "proof_model": "FSE_JOINT_CONTROL_OVERAPPROX_V1", 
        "source_transitions": analysis.source_transitions, 
        "core_registers": list(analysis.core_registers), 
        "context_registers": list(analysis.context_registers), 
        "tracked_registers": list(analysis.tracked_registers), 
        "raw_domain_product": analysis.raw_domain_product, 
        "reachable_states": analysis.reachable_states, 
        "iterations": analysis.iterations, 
        "transition_checks": analysis.transition_checks, 
        "fallback_external_updates": analysis.fallback_external_updates, 
        "predicate_ids": list(analysis.predicate_ids), 
        "truth_patterns": [[{"basis": p, "polarity": v} for p, v in sig] for sig in analysis.truth_patterns], 
        "state_tuples": [list(x) for x in analysis.state_tuples], 
        "notes": list(analysis.notes), 
    }
    return out


def analysis_to_dict(analysis: JointControlDomainAnalysis) -> dict[str, Any]: 
    return asdict(analysis)
