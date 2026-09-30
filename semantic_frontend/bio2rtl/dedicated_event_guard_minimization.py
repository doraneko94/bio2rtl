from __future__ import annotations

"""Generic guard-cube minimization for Dedicated Event IR.

The source Dedicated Event IR contains a complete feasibility-proven relation:
for every source transition and every architectural target there is exactly one
rule, including HOLD rules.  The emitted RTL does not need to preserve the
transition identity.  It only needs to preserve, for each event/target, the
mapping from feasible guard cubes to next-state outcomes.

This pass therefore generalizes non-HOLD guard cubes while treating every
source cube with a different outcome (including HOLD) as an OFF-set.  A literal
may be removed only when the generalized cube remains Boolean-disjoint from
all OFF-set cubes.  States outside the feasibility-proven source relation are
don't-cares, but generalized cubes for different outcomes are additionally
required to be mutually disjoint so the emitted hardware has no priority-
ordering ambiguity.

No source state names, protocol names, GPIO numbers, basic-block identities, or
transition IDs participate in the minimization decision.
"""

from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, asdict
from typing import Any


def _freeze(x: Any) -> Any: 
    if isinstance(x, list): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, tuple): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, dict): 
        return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
    return x


def _cube(rule: dict[str, Any]) -> dict[str, bool]: 
    out: dict[str, bool] = {}
    for lit in rule.get("enable", []): 
        key = str(lit["basis"])
        value = bool(lit["polarity"])
        old = out.setdefault(key, value)
        if old != value: 
            raise ValueError(f"contradictory enable cube in {rule.get('rule_id')}: {key}")
    return out


def _cube_key(cube: dict[str, bool]) -> tuple[tuple[str, bool], ...]: 
    return tuple(sorted(cube.items()))


def _cube_overlaps(a: dict[str, bool], b: dict[str, bool]) -> bool: 
    """True when two Boolean cubes have at least one common valuation."""
    return all(k not in b or b[k] == v for k, v in a.items())


def _cube_subsumes(general: dict[str, bool], specific: dict[str, bool]) -> bool: 
    """True when every valuation of *specific* also satisfies *general*."""
    return all(k in specific and specific[k] == v for k, v in general.items())


def _generalize_cube_with_order(
    source: dict[str, bool], 
    bad_cubes: list[dict[str, bool]], 
    order_fn, 
) -> dict[str, bool]: 
    """Drop literals in a deterministic order until a local safe minimum."""
    if any(_cube_overlaps(source, bad) for bad in bad_cubes): 
        return dict(source)
    current = dict(source)
    while current: 
        changed = False
        for key in list(order_fn(current)): 
            trial = dict(current)
            del trial[key]
            if any(_cube_overlaps(trial, bad) for bad in bad_cubes): 
                continue
            current = trial
            changed = True
            break
        if not changed: 
            break
    return current


def _generalization_candidates(
    source: dict[str, bool], 
    bad_cubes: list[dict[str, bool]], 
    good_frequency: dict[str, int], 
    bad_frequency: dict[str, int], 
) -> list[dict[str, bool]]: 
    """Generate several safe local minima, independent of source ordering.

    Cube minimization is not unique.  Trying multiple generic literal orders and
    later solving a cover across same-outcome source cubes substantially reduces
    duplicated implicants without introducing protocol knowledge.
    """
    insertion = list(source)
    orders = [
        lambda c: [k for k in insertion if k in c], 
        lambda c: sorted(c), 
        lambda c: sorted(c, reverse = True), 
        lambda c: sorted(c, key = lambda k: (bad_frequency.get(k, 0), k)), 
        lambda c: sorted(c, key = lambda k: (-good_frequency.get(k, 0), k)), 
    ]
    unique: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
    for order_fn in orders: 
        cube = _generalize_cube_with_order(source, bad_cubes, order_fn)
        unique[_cube_key(cube)] = cube
    return list(unique.values())


def _select_cover(
    candidates: list[dict[str, bool]], 
    source_cubes: list[dict[str, bool]], 
) -> list[dict[str, bool]]: 
    """Greedy set cover with a deterministic literal-count tie-break."""
    # Discard implicants subsumed by a strictly broader candidate.
    primes = _remove_subsumed(candidates)
    cover_sets = [
        {i for i, src in enumerate(source_cubes) if _cube_subsumes(cube, src)}
        for cube in primes
    ]
    uncovered = set(range(len(source_cubes)))
    chosen: list[int] = []
    while uncovered: 
        best = None
        for i, covered in enumerate(cover_sets): 
            newly = len(covered & uncovered)
            if newly == 0: 
                continue
            score = (newly, -len(primes[i]), tuple(reversed(_cube_key(primes[i]))))
            if best is None or score > best[0]: 
                best = (score, i)
        if best is None: 
            raise ValueError('guard candidate set does not cover all same-outcome source cubes')
        idx = best[1]
        chosen.append(idx)
        uncovered -= cover_sets[idx]

    # Remove any term made redundant by the final cover.
    changed = True
    while changed: 
        changed = False
        for pos in range(len(chosen) - 1, -1, -1): 
            trial = chosen[:pos] + chosen[pos + 1:]
            if all(any(i in cover_sets[j] for j in trial) for i in range(len(source_cubes))): 
                chosen = trial
                changed = True
                break
    return [primes[i] for i in chosen]

def _remove_subsumed(cubes: list[dict[str, bool]]) -> list[dict[str, bool]]: 
    result: list[dict[str, bool]] = []
    for cube in sorted(cubes, key = lambda c: (len(c), _cube_key(c))): 
        if any(_cube_subsumes(existing, cube) for existing in result): 
            continue
        result = [x for x in result if not _cube_subsumes(cube, x)]
        result.append(cube)
    return result


def _outcome_key(rule: dict[str, Any]) -> Any: 
    return _freeze(rule["outcome"])


def _hold_key(target: str) -> Any: 
    return _freeze(["REG", target])


@dataclass
class GuardMinimizationStats: 
    source_rules: int
    source_materialized_rules: int
    source_enable_literals: int
    minimized_rules: int
    minimized_enable_literals: int
    source_event_target_pairs: int
    minimized_event_target_pairs: int
    predicate_basis_total: int
    predicate_basis_used: int
    max_source_cube_literals: int
    max_minimized_cube_literals: int
    cross_outcome_overlap_pairs: int


def minimize_dedicated_event_guards(ir: dict[str, Any]) -> tuple[dict[str, Any], GuardMinimizationStats]: 
    source_rules = list(ir.get("update_rules", []))
    if not source_rules: 
        raise ValueError("Dedicated Event IR has no update_rules")

    # A source semantic relation must retain HOLD rows.  Without them there is
    # no sound OFF-set against which non-HOLD cubes can be generalized.
    by_event_target: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rule in source_rules: 
        by_event_target[(str(rule["event_class"]), str(rule["target"]))].append(rule)

    output_rules: list[dict[str, Any]] = []
    coverage: dict[str, list[str]] = {}

    for (event, target), rules in sorted(by_event_target.items()): 
        by_outcome: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for rule in rules: 
            by_outcome[_outcome_key(rule)].append(rule)

        for outcome_key, good_rules in sorted(by_outcome.items(), key = lambda kv: repr(kv[0])): 
            if outcome_key == _hold_key(target): 
                continue
            bad_cubes = [
                _cube(rule) for rule in rules if _outcome_key(rule) != outcome_key
            ]
            good_cubes = [_cube(rule) for rule in good_rules]
            good_frequency: dict[str, int] = defaultdict(int)
            bad_frequency: dict[str, int] = defaultdict(int)
            for cube in good_cubes: 
                for basis in cube: 
                    good_frequency[basis] += 1
            for cube in bad_cubes: 
                for basis in cube: 
                    bad_frequency[basis] += 1
            candidate_map: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
            for cube in good_cubes: 
                for candidate in _generalization_candidates(
                    cube, bad_cubes, good_frequency, bad_frequency
                ): 
                    candidate_map[_cube_key(candidate)] = candidate
            implicants = _select_cover(list(candidate_map.values()), good_cubes)

            exemplar = good_rules[0]
            for cube in implicants: 
                # Record exactly which same-outcome source transitions are
                # guaranteed covered by this implicant.  This is provenance
                # only; transition identity is not emitted as hardware logic.
                tids: list[str] = []
                for src in good_rules: 
                    if _cube_subsumes(cube, _cube(src)): 
                        tids.extend(str(x) for x in src.get("source_transition_ids", []))
                row = deepcopy(exemplar)
                row["enable"] = [
                    {"basis": basis, "polarity": polarity}
                    for basis, polarity in _cube_key(cube)
                ]
                row["materialize"] = True
                row["source_transition_ids"] = sorted(set(tids))
                output_rules.append(row)

    output_rules.sort(
        key = lambda r: (
            str(r["event_class"]), str(r["target"]), repr(r["outcome"]), repr(r["enable"])
        )
    )
    for i, rule in enumerate(output_rules): 
        rule["rule_id"] = f"M{i:04d}"
        coverage[rule["rule_id"]] = list(rule.get("source_transition_ids", []))

    # Global deterministic-hardware check: generalized cubes belonging to
    # different outcomes for the same event/target must not overlap even in the
    # don't-care region.
    overlaps: list[tuple[str, str, str, str]] = []
    compressed_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for rule in output_rules: 
        compressed_by_pair[(str(rule["event_class"]), str(rule["target"]))].append(rule)
    for (event, target), rules in compressed_by_pair.items(): 
        for i, a in enumerate(rules): 
            for b in rules[i + 1:]: 
                if _outcome_key(a) == _outcome_key(b): 
                    continue
                if _cube_overlaps(_cube(a), _cube(b)): 
                    overlaps.append((event, target, str(a["rule_id"]), str(b["rule_id"])))
    if overlaps: 
        raise ValueError(f"guard minimization created cross-outcome overlaps: {overlaps[:8]}")

    out = deepcopy(ir)
    out["version"] = str(ir.get("version", "dedicated-event-ir")) + "+guardmin-v1"
    out["update_rules"] = output_rules

    used_basis = sorted({
        str(x["basis"]) for rule in output_rules for x in rule.get("enable", [])
    })
    stats = GuardMinimizationStats(
        source_rules = len(source_rules), 
        source_materialized_rules = sum(bool(r.get("materialize")) for r in source_rules), 
        source_enable_literals = sum(len(r.get("enable", [])) for r in source_rules if r.get("materialize")), 
        minimized_rules = len(output_rules), 
        minimized_enable_literals = sum(len(r.get("enable", [])) for r in output_rules), 
        source_event_target_pairs = len({(str(r["event_class"]), str(r["target"])) for r in source_rules if r.get("materialize")}), 
        minimized_event_target_pairs = len(compressed_by_pair), 
        predicate_basis_total = len(ir.get("predicate_basis", [])), 
        predicate_basis_used = len(used_basis), 
        max_source_cube_literals = max((len(r.get("enable", [])) for r in source_rules if r.get("materialize")), default = 0), 
        max_minimized_cube_literals = max((len(r.get("enable", [])) for r in output_rules), default = 0), 
        cross_outcome_overlap_pairs = len(overlaps), 
    )
    out["guard_minimization"] = {
        "version": 1, 
        "proof_model": "BOOLEAN_CUBE_ON_FEASIBILITY_PROVEN_SOURCE_RELATION", 
        "source_relation_preserved": True, 
        "hold_rows_used_as_offset": True, 
        "outside_source_relation": "DONT_CARE", 
        "cross_outcome_global_disjointness_required": True, 
        "stats": asdict(stats), 
        "rule_source_transition_coverage": coverage, 
        "notes": [
            "Guard literals are removed only when the generalized cube cannot overlap any source transition with a different next-state outcome.", 
            "HOLD source rows are retained as the OFF-set during proof even though they are not emitted as update rules.", 
            "Different minimized outcomes are globally Boolean-disjoint, so RTL behavior does not depend on rule priority in the don't-care region.", 
            "The optimization uses only event class, predicate-basis literals, target identity, and next-state expression equality.", 
            "No protocol name, source stack-state name, basic-block number, GPIO bit number, or transition identity is used to decide a rewrite.", 
        ], 
    }
    out.setdefault("notes", []).append(
        "Feasibility-proven update guards were minimized as Boolean cubes against different-outcome source rows; transition identity is retained only as provenance."
    )
    return out, stats
