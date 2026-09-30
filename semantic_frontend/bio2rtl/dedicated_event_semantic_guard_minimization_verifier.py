from __future__ import annotations

"""Independent finite-domain verifier for semantic guard minimization."""

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from .dedicated_event_guard_minimization import _cube, _hold_key, _outcome_key
from .dedicated_event_semantic_guard_minimization import SemanticCubeDomain, _canonical_cube


@dataclass
class SemanticGuardVerification: 
    checked_source_rows: int
    source_nonhold_rows: int
    minimized_rules: int
    uncovered_nonhold_rows: int
    different_outcome_overlap_rows: int
    hold_rows_spuriously_updated: int
    minimized_cross_outcome_overlap_pairs: int
    semantic_pass: bool
    failure_examples: list[dict[str, Any]]


def verify_semantic_guard_minimization(
    complete_ir: dict[str, Any], minimized_ir: dict[str, Any]
) -> SemanticGuardVerification: 
    domain = SemanticCubeDomain(complete_ir)
    aliases = domain.aliases
    source = list(complete_ir.get("update_rules", []))
    minimized = [r for r in minimized_ir.get("update_rules", []) if r.get("materialize")]
    by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rule in minimized: 
        by_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)

    failures: list[dict[str, Any]] = []
    uncovered = wrong = hold_bad = 0
    for src in source: 
        event, target = str(src["event_class"]), str(src["target"])
        expected = _outcome_key(src)
        sc = _canonical_cube(src, aliases)
        if sc is None: 
            continue
        overlaps = [r for r in by_pair.get((event, target), []) if domain.overlaps(sc, _cube(r))]
        different = [r for r in overlaps if _outcome_key(r) != expected]
        if different: 
            wrong += 1
            if len(failures) < 32: 
                failures.append({"source_rule": src.get("rule_id"), "reason": "feasible different-outcome overlap", "minimized_rules": [r.get("rule_id") for r in different]})
        if expected == _hold_key(target): 
            if overlaps: 
                hold_bad += 1
                if len(failures) < 32: 
                    failures.append({"source_rule": src.get("rule_id"), "reason": "feasible HOLD overlap", "minimized_rules": [r.get("rule_id") for r in overlaps]})
            continue
        covering = [r for r in by_pair.get((event, target), []) if _outcome_key(r) == expected and domain.implies(sc, _cube(r))]
        if not covering: 
            uncovered += 1
            if len(failures) < 32: 
                failures.append({"source_rule": src.get("rule_id"), "reason": "non-HOLD row not semantically covered"})

    cross = 0
    for (event, target), rules in by_pair.items(): 
        for i, a in enumerate(rules): 
            for b in rules[i + 1 :]: 
                if _outcome_key(a) == _outcome_key(b): 
                    continue
                if domain.overlaps(_cube(a), _cube(b)): 
                    cross += 1
                    if len(failures) < 32: 
                        failures.append({"event": event, "target": target, "reason": "feasible minimized cross-outcome overlap", "minimized_rules": [a.get("rule_id"), b.get("rule_id")]})
    ok = uncovered == 0 and wrong == 0 and hold_bad == 0 and cross == 0
    return SemanticGuardVerification(
        checked_source_rows = len(source), 
        source_nonhold_rows = sum(_outcome_key(r) != _hold_key(str(r["target"])) for r in source), 
        minimized_rules = len(minimized), 
        uncovered_nonhold_rows = uncovered, 
        different_outcome_overlap_rows = wrong, 
        hold_rows_spuriously_updated = hold_bad, 
        minimized_cross_outcome_overlap_pairs = cross, 
        semantic_pass = ok, 
        failure_examples = failures, 
    )


def result_to_dict(result: SemanticGuardVerification) -> dict[str, Any]: 
    return asdict(result)
