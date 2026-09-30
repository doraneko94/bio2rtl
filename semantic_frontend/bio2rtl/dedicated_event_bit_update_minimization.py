from __future__ import annotations

"""Bit-level transition-relation minimization for Dedicated Event IR.

The register-level semantic guard minimizer must keep distinct full-register
outcomes separate even when they differ only in bits irrelevant to one physical
storage bit.  This pass projects the complete, HOLD-inclusive event-boundary
relation onto each *physically stored semantic bit* and re-minimizes that
SET/CLEAR/HOLD relation over the same proof-backed semantic predicate domain.

It changes neither event semantics nor architectural state meaning.  The result
is metadata consumed by the bit-equation RTL backend.  No protocol names,
source stack identities, GPIO numbers, BB ids, or transition ids participate in
rewrite decisions.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from .dedicated_event_guard_minimization import _cube_key
from .dedicated_event_semantic_guard_minimization import (
    SemanticCubeDomain, 
    _canonical_cube, 
    _candidates, 
    _select_cover, 
)


def _freeze(x: Any) -> Any: 
    if isinstance(x, list): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, tuple): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, dict): 
        return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
    return x


def _physical_storage_bits(row: dict[str, Any], semw: int) -> list[int]: 
    kind = str(row["storage_kind"])
    if kind == "DIRECT": 
        return list(range(semw))
    if kind == "NARROW_ZERO_EXTEND": 
        return list(range(int(row["storage_bits"])))
    if kind == "PACKED_MASK_BITS": 
        return [int(x) for x in row.get("stored_bits", [])]
    if kind in ("CONST", "DERIVED_EXPR"): 
        return []
    raise ValueError(f"unsupported storage kind: {kind}")


def _bit_expr(outcome: Any, bit: int) -> Any: 
    """Exact one-bit integer expression for one semantic outcome bit."""
    if isinstance(outcome, list) and outcome: 
        tag = outcome[0]
        if tag == "CONST": 
            return ["CONST", (int(outcome[1]) >> bit) & 1]
        if tag == "REG": 
            return ["BIT_VALUE", ["REG", str(outcome[1])], int(bit)]
        if tag == "SCHED_REG": 
            return ["BIT_VALUE", ["SCHED_REG", str(outcome[1])], int(bit)]
        if tag == "BIT_VALUE": 
            return deepcopy(outcome) if bit == 0 else ["CONST", 0]
    return ["BIT_VALUE", deepcopy(outcome), int(bit)]


def _hold_bit_expr(target: str, bit: int) -> Any: 
    return ["BIT_VALUE", ["REG", str(target)], int(bit)]


def _bit_outcome_key(rule: dict[str, Any], bit: int) -> Any: 
    target = str(rule["target"])
    return _freeze(_bit_expr(rule["outcome"], bit))


@dataclass
class BitUpdateMinimizationStats: 
    source_relation_rows: int
    physical_event_target_bits: int
    source_nonhold_bit_rows: int
    minimized_bit_rules: int
    source_guard_literals: int
    minimized_guard_literals: int
    predicate_basis_used: int
    shared_condition_keys: int
    semantic_cross_outcome_overlaps: int


def minimize_bit_update_relation(
    complete_ir: dict[str, Any], 
    baseline_ir: dict[str, Any] | None = None, 
    *, 
    max_component_assignments: int = 250_000, 
) -> tuple[dict[str, Any], BitUpdateMinimizationStats]: 
    source_rules = list(complete_ir.get("update_rules", []))
    if not source_rules: 
        raise ValueError("Dedicated Event IR has no update_rules")
    baseline_ir = baseline_ir or complete_ir
    baseline_rules = [r for r in baseline_ir.get("update_rules", []) if r.get("materialize")]
    if not baseline_rules: 
        raise ValueError("bit update minimization requires a materialized baseline relation")
    plan = baseline_ir.get("storage_optimization") or {}
    storage = {str(x["register"]): x for x in plan.get("register_storage", [])}
    widths = {str(x["id"]): int(x["width"]) for x in baseline_ir.get("architectural_registers", [])}
    if not storage: 
        raise ValueError("bit update minimization requires a proof-backed storage plan")

    # Use the already-proven joint semantic domain carried by the accepted
    # baseline. The complete relation supplies only the OFF-set rows.
    domain = SemanticCubeDomain(baseline_ir, max_component_assignments = max_component_assignments)
    aliases = domain.aliases

    # Complete source relation indexed by event/target.  Every physical bit of
    # that target sees the same source cubes but a projected one-bit outcome.
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    source_cubes: dict[str, dict[str, bool]] = {}
    for rule in source_rules: 
        c = _canonical_cube(rule, aliases)
        if c is None: 
            raise ValueError(f"contradictory equivalent predicates: {rule.get('rule_id')}")
        source_cubes[str(rule["rule_id"])] = c
        by_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)
    baseline_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    baseline_cubes: dict[str, dict[str, bool]] = {}
    for rule in baseline_rules: 
        c = _canonical_cube(rule, aliases)
        if c is None: 
            raise ValueError(f"contradictory baseline predicates: {rule.get('rule_id')}")
        baseline_cubes[str(rule["rule_id"])] = c
        baseline_by_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)

    output: list[dict[str, Any]] = []
    physical_pairs = 0
    source_nonhold = 0
    source_literals = 0

    for (event, target), rules in sorted(by_pair.items()): 
        sp = storage.get(target)
        if sp is None: 
            continue
        semw = widths[target]
        base_rules = baseline_by_pair.get((event, target), [])
        for bit in _physical_storage_bits(sp, semw): 
            physical_pairs += 1
            hold_key = _freeze(_hold_bit_expr(target, bit))
            source_by_outcome: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for rule in rules: 
                source_by_outcome[_bit_outcome_key(rule, bit)].append(rule)
            source_nonhold += sum(len(v) for k, v in source_by_outcome.items() if k != hold_key)
            source_literals += sum(len(source_cubes[str(r["rule_id"])]) for k, rows in source_by_outcome.items() if k != hold_key for r in rows)

            # The accepted register-level baseline already covers every source
            # non-HOLD row. Use its much smaller cubes as the ON-set seeds,
            # while the complete source relation remains the exact OFF-set.
            baseline_by_outcome: dict[Any, list[dict[str, Any]]] = defaultdict(list)
            for rule in base_rules: 
                baseline_by_outcome[_bit_outcome_key(rule, bit)].append(rule)

            for outcome_key, good_rules in sorted(baseline_by_outcome.items(), key = lambda kv: repr(kv[0])): 
                if outcome_key == hold_key: 
                    continue
                bad_cubes = [source_cubes[str(r["rule_id"])] for r in rules if _bit_outcome_key(r, bit) != outcome_key]
                good_cubes = [baseline_cubes[str(r["rule_id"])] for r in good_rules]
                good_frequency = Counter(k for cube in good_cubes for k in cube)
                bad_frequency = Counter(k for cube in bad_cubes for k in cube)
                candidate_map: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
                for cube in good_cubes: 
                    for candidate in _candidates(cube, bad_cubes, domain, good_frequency, bad_frequency): 
                        candidate_map[_cube_key(candidate)] = candidate
                implicants = _select_cover(list(candidate_map.values()), good_cubes, domain)
                exemplar = good_rules[0]
                one_bit_outcome = _bit_expr(exemplar["outcome"], bit)
                source_same = source_by_outcome.get(outcome_key, [])
                for cube in implicants: 
                    tids: list[str] = []
                    source_rule_ids: list[str] = []
                    for src in source_same: 
                        if domain.implies(source_cubes[str(src["rule_id"])], cube): 
                            source_rule_ids.append(str(src["rule_id"]))
                            tids.extend(str(x) for x in src.get("source_transition_ids", []))
                    output.append({
                        "rule_id": "", 
                        "event_class": event, 
                        "target": target, 
                        "semantic_bit": int(bit), 
                        "outcome": deepcopy(one_bit_outcome), 
                        "enable": [{"basis": basis, "polarity": polarity} for basis, polarity in _cube_key(cube)], 
                        "source_rule_ids": sorted(set(source_rule_ids)), 
                        "source_transition_ids": sorted(set(tids)), 
                    })

    output.sort(key = lambda r: (str(r["event_class"]), str(r["target"]), int(r["semantic_bit"]), repr(r["outcome"]), repr(r["enable"])))
    for i, rule in enumerate(output): 
        rule["rule_id"] = f"B{i:04d}"

    # A physical bit has no priority semantics. Different one-bit outcomes may
    # overlap only if that overlap is impossible in the proven state domain.
    by_bit: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for rule in output: 
        by_bit[(str(rule["event_class"]), str(rule["target"]), int(rule["semantic_bit"]))].append(rule)
    cross = 0
    examples: list[tuple[str, str, int, str, str]] = []
    for (event, target, bit), rules in by_bit.items(): 
        for i, a in enumerate(rules): 
            for b in rules[i + 1:]: 
                if _freeze(a["outcome"]) == _freeze(b["outcome"]): 
                    continue
                ca = {str(x["basis"]): bool(x["polarity"]) for x in a.get("enable", [])}
                cb = {str(x["basis"]): bool(x["polarity"]) for x in b.get("enable", [])}
                if domain.overlaps(ca, cb): 
                    cross += 1
                    if len(examples) < 8: 
                        examples.append((event, target, bit, str(a["rule_id"]), str(b["rule_id"])))
    if cross: 
        raise ValueError(f"bit relation created feasible cross-outcome overlaps: {examples}")

    used = sorted({str(x["basis"]) for r in output for x in r.get("enable", [])})
    condition_keys = {
        (str(r["event_class"]), tuple((str(x["basis"]), bool(x["polarity"])) for x in r.get("enable", [])))
        for r in output
    }
    stats = BitUpdateMinimizationStats(
        source_relation_rows = len(source_rules), 
        physical_event_target_bits = physical_pairs, 
        source_nonhold_bit_rows = source_nonhold, 
        minimized_bit_rules = len(output), 
        source_guard_literals = source_literals, 
        minimized_guard_literals = sum(len(r.get("enable", [])) for r in output), 
        predicate_basis_used = len(used), 
        shared_condition_keys = len(condition_keys), 
        semantic_cross_outcome_overlaps = cross, 
    )
    meta = {
        "version": 1, 
        "proof_model": "COMPLETE_RELATION_PHYSICAL_BIT_PROJECTION_WITH_SEMANTIC_DOMAIN_OFFSETS", 
        "complete_source_relation": True, 
        "hold_rows_used_as_offset": True, 
        "rules": output, 
        "stats": asdict(stats), 
        "notes": [
            "Each physically stored semantic bit is minimized independently after exact projection of the complete HOLD-inclusive register relation.", 
            "Full-register outcomes that differ only in other bits may therefore share a bit-level implicant.", 
            "Generalized bit guards are rejected when they overlap any different one-bit outcome in the proof-backed semantic predicate domain.", 
            "No protocol name, GPIO number, source stack identity, BB identity, or transition identity is used to decide a rewrite.", 
        ], 
    }
    return meta, stats


def attach_bit_update_relation(ir: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]: 
    out = deepcopy(ir)
    out["bit_update_relation"] = deepcopy(meta)
    out.setdefault("notes", []).append(
        "Physical D-input logic may use a separately proven bit-projected transition relation that re-minimizes SET/CLEAR/HOLD behavior per stored semantic bit."
    )
    return out
