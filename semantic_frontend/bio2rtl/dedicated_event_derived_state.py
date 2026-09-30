from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, asdict
from itertools import product
from math import prod
from typing import Any

from .dedicated_event_reachability import _eval_expr


@dataclass(frozen = True)
class DerivedStateCandidate: 
    target: str
    source: str
    reachable_pairs: tuple[tuple[int, int], ...]
    mapping: tuple[tuple[int, int], ...]
    expression: list[Any]
    expression_kind: str
    removed_storage_bits: int
    source_width: int
    target_width: int
    transition_checks: int
    iterations: int
    outcome_readers: tuple[str, ...]
    predicate_users: tuple[str, ...]
    proof_context: tuple[str, ...]


@dataclass
class DerivedStateAnalysis: 
    source_transitions: int
    pair_candidates_checked: int
    candidates: list[DerivedStateCandidate]
    notes: list[str]


def _refs(expr: Any, out: set[tuple[str, str]] | None = None) -> set[tuple[str, str]]: 
    if out is None: 
        out = set()
    if isinstance(expr, list): 
        if len(expr) >= 2 and expr[0] == "REG": 
            out.add(("REG", str(expr[1])))
            return out
        if len(expr) >= 2 and expr[0] == "SCHED_REG": 
            out.add(("SCHED", str(expr[1])))
            return out
        if expr and expr[0] == "GPIO_INPUT": 
            out.add(("GPIO", "GPIO_INPUT"))
            return out
        for item in expr: 
            _refs(item, out)
    elif isinstance(expr, dict): 
        for item in expr.values(): 
            _refs(item, out)
    return out


def _group_transitions(ir: dict[str, Any]) -> list[dict[str, Any]]: 
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rule in ir["update_rules"]: 
        tids = list(rule.get("source_transition_ids", []))
        if len(tids) != 1: 
            raise ValueError(f"complete relation row must have exactly one transition id: {rule}")
        grouped[(str(rule["event_class"]), str(tids[0]))].append(rule)
    out = []
    target_count = len(ir["architectural_registers"])
    for (event, tid), rows in sorted(grouped.items()): 
        if len(rows) != target_count: 
            raise ValueError(f"transition {tid} has {len(rows)} rows, expected {target_count}")
        guards = {repr(row.get("enable", [])) for row in rows}
        if len(guards) != 1: 
            raise ValueError(f"transition {tid} has inconsistent guards")
        out.append({
            "event_class": event, 
            "transition_id": tid, 
            "guard": deepcopy(rows[0].get("enable", [])), 
            "outcomes": {str(row["target"]): deepcopy(row["outcome"]) for row in rows}, 
        })
    return out


def _domain_map(ir: dict[str, Any]) -> dict[str, tuple[int, ...]]: 
    plan = ir.get("storage_optimization") or {}
    rows = {str(x["register"]): x for x in plan.get("register_storage", [])}
    out: dict[str, tuple[int, ...]] = {}
    for reg in ir["architectural_registers"]: 
        rid = str(reg["id"])
        row = rows.get(rid, {})
        values = row.get("reachable_values_upper_bound", row.get("reachable_values"))
        if values is None: 
            width = int(reg["width"])
            if width > 10: 
                continue
            values = list(range(1 << width))
        out[rid] = tuple(sorted({int(v) for v in values}))
    return out


def _startup_values(ir: dict[str, Any]) -> dict[str, int]: 
    return {str(x["register"]): int(x["value"][1]) for x in ir["startup"]["register_values"]}


def _predicate_map(ir: dict[str, Any]) -> dict[str, Any]: 
    return {str(p["id"]): p["expression"] for p in ir["predicate_basis"]}


def _eval_known(expr: Any, regs: dict[str, int]) -> int: 
    # The common evaluator is exact once every referenced REG is populated.
    return int(_eval_expr(expr, regs, {}, 0))


def _guard_allows(
    guard: list[dict[str, Any]], 
    predicates: dict[str, Any], 
    regs: dict[str, int], 
    known_regs: set[str], 
) -> bool: 
    for item in guard: 
        expr = predicates[str(item["basis"])]
        refs = _refs(expr)
        reg_refs = {name for kind, name in refs if kind == "REG"}
        other_refs = {kind for kind, _ in refs if kind != "REG"}
        # Unknown constraints are deliberately ignored: this widens the pair
        # closure and therefore cannot create a false functional dependency.
        if other_refs or not reg_refs.issubset(known_regs): 
            continue
        value = bool(_eval_known(expr, regs))
        if value != bool(item["polarity"]): 
            return False
    return True


def _outcome_values(
    expr: Any, 
    *, 
    current: dict[str, int], 
    domains: dict[str, tuple[int, ...]], 
    target: str, 
    enumeration_limit: int, 
) -> tuple[int, ...]: 
    refs = _refs(expr)
    if any(kind != "REG" for kind, _ in refs): 
        return domains[target]
    ext = sorted({name for kind, name in refs if kind == "REG" and name not in current})
    if any(name not in domains for name in ext): 
        return domains[target]
    count = prod(len(domains[name]) for name in ext) if ext else 1
    if count > enumeration_limit: 
        return domains[target]
    values: set[int] = set()
    if not ext: 
        try: 
            values.add(_eval_known(expr, current))
        except Exception: 
            return domains[target]
    else: 
        for combo in product(*(domains[name] for name in ext)): 
            regs = dict(current)
            regs.update(zip(ext, combo))
            try: 
                values.add(_eval_known(expr, regs))
            except Exception: 
                return domains[target]
    semw = None
    # Domains are already width-clamped; intersecting makes the result safe if
    # an evaluator expression carries a wider CPU value.
    allowed = set(domains[target])
    clipped = tuple(sorted(v for v in values if v in allowed))
    return clipped if clipped else domains[target]


def _recognize_mapping(source: str, mapping: dict[int, int]) -> tuple[str, list[Any]] | None: 
    items = sorted(mapping.items())
    if not items: 
        return None
    # Identity / zero-extension.
    if all(y == x for x, y in items): 
        return "IDENTITY", ["REG", source]
    target_values = {y for _, y in items}
    if target_values <= {0, 1}: 
        if all(y == (1 if x != 0 else 0) for x, y in items): 
            return "NONZERO", ["NE", ["REG", source], ["CONST", 0]]
        if all(y == (1 if x == 0 else 0) for x, y in items): 
            return "IS_ZERO", ["EQ", ["REG", source], ["CONST", 0]]
        xs = [x for x, y in items if y == 1]
        if len(xs) == 1: 
            return "EQ_CONST", ["EQ", ["REG", source], ["CONST", xs[0]]]
        zeros = [x for x, y in items if y == 0]
        if len(zeros) == 1: 
            return "NE_CONST", ["NE", ["REG", source], ["CONST", zeros[0]]]
        max_x = max(x for x, _ in items)
        for bit in range(max(1, max_x.bit_length())): 
            if all(y == ((x >> bit) & 1) for x, y in items): 
                return "BIT_SELECT", ["BIT_VALUE", ["REG", source], bit]
            if all(y == (1 - ((x >> bit) & 1)) for x, y in items): 
                return "NOT_BIT_SELECT", ["EQ", ["BIT_VALUE", ["REG", source], bit], ["CONST", 0]]
    return None


def _use_summary(ir: dict[str, Any]) -> tuple[dict[str, set[str]], dict[str, set[str]]]: 
    predicate_users: dict[str, set[str]] = defaultdict(set)
    outcome_readers: dict[str, set[str]] = defaultdict(set)
    for p in ir["predicate_basis"]: 
        for kind, name in _refs(p["expression"]): 
            if kind == "REG": 
                predicate_users[name].add(str(p["id"]))
    for rule in ir["update_rules"]: 
        for kind, name in _refs(rule["outcome"]): 
            if kind == "REG" and name != str(rule["target"]): 
                outcome_readers[name].add(str(rule["target"]))
    return predicate_users, outcome_readers



def _dependency_context(
    source: str, 
    target: str, 
    transitions: list[dict[str, Any]], 
    predicates: dict[str, Any], 
    domains: dict[str, tuple[int, ...]], 
    *, 
    max_context_regs: int = 2, 
    max_joint_states: int = 4096, 
) -> tuple[str, ...] | None: 
    context: set[str] = set()
    pair = {source, target}
    for tr in transitions: 
        for rid in (source, target): 
            for kind, name in _refs(tr["outcomes"][rid]): 
                if kind == "REG" and name not in pair: 
                    context.add(name)
        for item in tr["guard"]: 
            expr_refs = {name for kind, name in _refs(predicates[str(item["basis"])]) if kind == "REG"}
            if expr_refs & pair: 
                context |= (expr_refs - pair)
    if any(name not in domains or len(domains[name]) > 8 for name in context): 
        return None
    if len(context) > max_context_regs: 
        return None
    tracked = [source, target] + sorted(context)
    if prod(len(domains[name]) for name in tracked) > max_joint_states: 
        return None
    return tuple(sorted(context))


def _joint_closure(
    tracked: tuple[str, ...], 
    transitions: list[dict[str, Any]], 
    predicates: dict[str, Any], 
    domains: dict[str, tuple[int, ...]], 
    startup: dict[str, int], 
    *, 
    enumeration_limit: int, 
    max_joint_states: int, 
) -> tuple[set[tuple[int, ...]], int, int]: 
    start = tuple(startup[r] for r in tracked)
    reachable: set[tuple[int, ...]] = {start}
    frontier: set[tuple[int, ...]] = {start}
    successor_cache: dict[tuple[int, ...], set[tuple[int, ...]]] = {}
    transition_checks = 0
    iterations = 0
    known = set(tracked)
    while frontier: 
        iterations += 1
        new_states: set[tuple[int, ...]] = set()
        for state in frontier: 
            if state not in successor_cache: 
                current = dict(zip(tracked, state))
                succ: set[tuple[int, ...]] = set()
                for tr in transitions: 
                    if not _guard_allows(tr["guard"], predicates, current, known): 
                        continue
                    transition_checks += 1
                    next_sets = [
                        _outcome_values(
                            tr["outcomes"][rid], current = current, domains = domains, 
                            target = rid, enumeration_limit = enumeration_limit, 
                        )
                        for rid in tracked
                    ]
                    count = prod(len(x) for x in next_sets)
                    if count > enumeration_limit: 
                        next_sets = [domains[rid] for rid in tracked]
                    for values in product(*next_sets): 
                        succ.add(tuple(int(x) for x in values))
                        if len(succ) + len(reachable) > max_joint_states: 
                            break
                    if len(succ) + len(reachable) > max_joint_states: 
                        break
                successor_cache[state] = succ
            new_states |= successor_cache[state]
            if len(new_states) + len(reachable) > max_joint_states: 
                return reachable | new_states, transition_checks, iterations
        new_states -= reachable
        if not new_states: 
            break
        reachable |= new_states
        frontier = new_states
        if len(reachable) > max_joint_states: 
            break
    return reachable, transition_checks, iterations


def analyze_simple_derived_state(
    complete_ir: dict[str, Any], 
    baseline_ir: dict[str, Any], 
    *, 
    enumeration_limit: int = 4096, 
    max_pair_states: int = 4096, 
) -> DerivedStateAnalysis: 
    """Discover simple cross-register invariants from the corrected relation.

    Each pair closure is an over-approximation: guard constraints involving any
    third register, scheduler state, or GPIO are ignored.  Outcome dependencies
    outside the pair are existentially enumerated from proof-backed per-register
    domains, with a full target-domain fallback on complexity.  Therefore a
    functional dependency that survives this closure is safe for every concrete
    execution represented by the source relation.
    """
    transitions = _group_transitions(complete_ir)
    domains = _domain_map(baseline_ir)
    startup = _startup_values(complete_ir)
    predicates = _predicate_map(complete_ir)
    pred_users, out_readers = _use_summary(baseline_ir)
    storage_rows = {
        str(x["register"]): x for x in baseline_ir["storage_optimization"]["register_storage"]
    }
    reg_specs = {str(r["id"]): r for r in complete_ir["architectural_registers"]}

    eligible = [
        rid for rid, spec in reg_specs.items()
        if spec["kind"] not in ("GPIO",)
        and rid in domains
        and int(storage_rows.get(rid, {}).get("storage_bits", spec["width"])) > 0
        and len(domains[rid]) <= 8
    ]
    candidates: list[DerivedStateCandidate] = []
    checked = 0
    for source in eligible: 
        for target in eligible: 
            if source == target: 
                continue
            # Automatic removal is deliberately conservative: the derived value
            # may feed predicates, but not another architectural datapath update.
            if out_readers.get(target): 
                continue
            # Derived-state elimination is intentionally restricted to narrow
            # control-like targets.  Wider datapath recoding is a separate area
            # tradeoff and must not be smuggled into this pass.
            if int(storage_rows[target].get("storage_bits", reg_specs[target]["width"])) > 2: 
                continue
            if len(domains[source]) * len(domains[target]) > max_pair_states: 
                continue
            checked += 1
            context = _dependency_context(
                source, target, transitions, predicates, domains, 
                max_context_regs = 2, max_joint_states = max_pair_states, 
            )
            if context is None: 
                continue
            tracked = (source, target) + context
            reachable_joint, transition_checks, iterations = _joint_closure(
                tracked, transitions, predicates, domains, startup, 
                enumeration_limit = enumeration_limit, max_joint_states = max_pair_states, 
            )
            if len(reachable_joint) > max_pair_states: 
                continue
            reachable = {(state[0], state[1]) for state in reachable_joint}
            mapping: dict[int, int] = {}
            functional = True
            for src_val, dst_val in sorted(reachable): 
                if src_val in mapping and mapping[src_val] != dst_val: 
                    functional = False
                    break
                mapping[src_val] = dst_val
            if not functional: 
                continue
            recognized = _recognize_mapping(source, mapping)
            if recognized is None: 
                continue
            kind, expr = recognized
            bits = int(storage_rows[target].get("storage_bits", reg_specs[target]["width"]))
            if bits <= 0: 
                continue
            candidates.append(DerivedStateCandidate(
                target = target, 
                source = source, 
                reachable_pairs = tuple(sorted(reachable)), 
                mapping = tuple(sorted(mapping.items())), 
                expression = expr, 
                expression_kind = kind, 
                removed_storage_bits = bits, 
                source_width = int(reg_specs[source]["width"]), 
                target_width = int(reg_specs[target]["width"]), 
                transition_checks = transition_checks, 
                iterations = iterations, 
                outcome_readers = tuple(sorted(out_readers.get(target, set()))), 
                predicate_users = tuple(sorted(pred_users.get(target, set()))), 
                proof_context = context, 
            ))

    # Stable order: maximize removed state, then minimize semantic derivation
    # complexity and keep IDs only as deterministic tie breakers.
    complexity = {"IDENTITY": 0, "BIT_SELECT": 0, "NONZERO": 1, "IS_ZERO": 1, 
                  "EQ_CONST": 1, "NE_CONST": 1, "NOT_BIT_SELECT": 1}
    candidates.sort(key = lambda c: (-c.removed_storage_bits, complexity.get(c.expression_kind, 9), c.target, c.source))
    return DerivedStateAnalysis(
        source_transitions = len(transitions), 
        pair_candidates_checked = checked, 
        candidates = candidates, 
        notes = [
            "Pair closures are conservative over-approximations of the corrected feasibility-proven source relation.", 
            "Third-register, scheduler, and GPIO guard constraints are ignored rather than assumed.", 
            "External outcome dependencies are enumerated from proof-backed domains or widened to the full target domain.", 
            "Automatic candidates must have a recognized low-cost derivation and no architectural outcome readers.", 
            "No protocol names, basic-block identity, transition ID special cases, or fixed GPIO bit numbers participate in discovery.", 
        ], 
    )


def apply_derived_state_candidate(
    baseline_ir: dict[str, Any], 
    candidate: DerivedStateCandidate, 
) -> dict[str, Any]: 
    out = deepcopy(baseline_ir)
    plan = out["storage_optimization"]
    rows = {str(x["register"]): x for x in plan["register_storage"]}
    row = rows[candidate.target]
    old_bits = int(row["storage_bits"])
    if old_bits != candidate.removed_storage_bits: 
        raise ValueError("candidate storage-bit count does not match baseline")
    row.update({
        "storage_kind": "DERIVED_EXPR", 
        "storage_bits": 0, 
        "derived_expression": deepcopy(candidate.expression), 
        "derived_from": [candidate.source], 
        "derived_expression_kind": candidate.expression_kind, 
        "proof": "FSE_PAIRWISE_CARTESIAN_INVARIANT_V1", 
        "reachable_pairs_upper_bound": [list(x) for x in candidate.reachable_pairs], 
        "derived_mapping": [list(x) for x in candidate.mapping], 
    })
    plan["natural_storage_bits"] = int(plan["natural_storage_bits"]) - old_bits
    plan.setdefault("policy", {})["derived_state_elimination"] = True
    plan["derived_state_elimination"] = {
        "version": 1, 
        "target": candidate.target, 
        "source": candidate.source, 
        "removed_storage_bits": old_bits, 
        "expression": deepcopy(candidate.expression), 
        "expression_kind": candidate.expression_kind, 
        "reachable_pairs_upper_bound": [list(x) for x in candidate.reachable_pairs], 
        "mapping": [list(x) for x in candidate.mapping], 
        "transition_checks": candidate.transition_checks, 
        "iterations": candidate.iterations, 
        "proof_context": list(candidate.proof_context), 
        "source_relation_preserved": True, 
        "proof_model": "FSE_PAIRWISE_CARTESIAN_INVARIANT_V1", 
    }
    out["version"] = str(out.get("version", "dedicated-event-ir")) + "+derived-state-v1"
    return out


def analysis_as_dict(result: DerivedStateAnalysis) -> dict[str, Any]: 
    return {
        "source_transitions": result.source_transitions, 
        "pair_candidates_checked": result.pair_candidates_checked, 
        "candidates": [asdict(c) for c in result.candidates], 
        "notes": list(result.notes), 
    }
