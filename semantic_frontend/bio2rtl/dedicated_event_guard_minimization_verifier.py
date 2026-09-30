from __future__ import annotations

"""Independent semantic verifier for Dedicated Event guard minimization."""

from dataclasses import dataclass, asdict
from collections import defaultdict
from typing import Any

from .dedicated_event_guard_minimization import (
    _cube, _cube_overlaps, _cube_subsumes, _outcome_key, _hold_key, 
)


@dataclass
class GuardMinimizationVerification: 
    source_relation_rows: int
    source_materialized_rows: int
    minimized_rules: int
    checked_source_rows: int
    uncovered_nonhold_rows: int
    different_outcome_overlap_rows: int
    hold_rows_spuriously_updated: int
    minimized_cross_outcome_overlap_pairs: int
    semantic_pass: bool
    failure_examples: list[dict[str, Any]]


def verify_guard_minimization(source_ir: dict[str, Any], minimized_ir: dict[str, Any]) -> GuardMinimizationVerification: 
    source_rules = list(source_ir.get("update_rules", []))
    min_rules = [r for r in minimized_ir.get("update_rules", []) if r.get("materialize")]

    min_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in min_rules: 
        min_by_pair[(str(r["event_class"]), str(r["target"]))].append(r)

    failures: list[dict[str, Any]] = []
    uncovered = wrong_overlap = hold_spurious = 0

    for src in source_rules: 
        event, target = str(src["event_class"]), str(src["target"])
        expected = _outcome_key(src)
        src_cube = _cube(src)
        candidates = min_by_pair.get((event, target), [])
        overlaps = [r for r in candidates if _cube_overlaps(_cube(r), src_cube)]
        different = [r for r in overlaps if _outcome_key(r) != expected]
        if different: 
            wrong_overlap += 1
            if len(failures) < 32: 
                failures.append({
                    "source_rule": str(src.get("rule_id")), "event": event, "target": target, 
                    "reason": "different-outcome minimized rule overlaps source cube", 
                    "minimized_rules": [str(r.get("rule_id")) for r in different], 
                })
        if expected == _hold_key(target): 
            if overlaps: 
                hold_spurious += 1
                if len(failures) < 32: 
                    failures.append({
                        "source_rule": str(src.get("rule_id")), "event": event, "target": target, 
                        "reason": "HOLD source cube is updated by minimized rule", 
                        "minimized_rules": [str(r.get("rule_id")) for r in overlaps], 
                    })
            continue
        covering = [
            r for r in candidates
            if _outcome_key(r) == expected and _cube_subsumes(_cube(r), src_cube)
        ]
        if not covering: 
            uncovered += 1
            if len(failures) < 32: 
                failures.append({
                    "source_rule": str(src.get("rule_id")), "event": event, "target": target, 
                    "reason": "non-HOLD source cube has no same-outcome minimized implicant that subsumes it", 
                })

    cross = 0
    for (event, target), rules in min_by_pair.items(): 
        for i, a in enumerate(rules): 
            for b in rules[i + 1:]: 
                if _outcome_key(a) == _outcome_key(b): 
                    continue
                if _cube_overlaps(_cube(a), _cube(b)): 
                    cross += 1
                    if len(failures) < 32: 
                        failures.append({
                            "event": event, "target": target, 
                            "reason": "two different minimized outcomes overlap globally", 
                            "minimized_rules": [str(a.get("rule_id")), str(b.get("rule_id"))], 
                        })

    ok = uncovered == 0 and wrong_overlap == 0 and hold_spurious == 0 and cross == 0
    return GuardMinimizationVerification(
        source_relation_rows = len(source_rules), 
        source_materialized_rows = sum(bool(r.get("materialize")) for r in source_rules), 
        minimized_rules = len(min_rules), 
        checked_source_rows = len(source_rules), 
        uncovered_nonhold_rows = uncovered, 
        different_outcome_overlap_rows = wrong_overlap, 
        hold_rows_spuriously_updated = hold_spurious, 
        minimized_cross_outcome_overlap_pairs = cross, 
        semantic_pass = ok, 
        failure_examples = failures, 
    )


def result_to_dict(r: GuardMinimizationVerification) -> dict[str, Any]: 
    return asdict(r)
