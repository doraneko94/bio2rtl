from __future__ import annotations

"""Cross-event semantic minimization for Dedicated Event updates.

The accepted Dedicated Event relation partitions hardware observations into
named event classes.  Register-level guard minimization historically runs
inside each class, so identical state updates in different detector patterns
cannot share logic.  This pass extends each source cube with the underlying
scheduler-detector truth values and minimizes across event-class boundaries.

Only detector combinations that can actually arise from the generic detector
and scheduler definitions are admitted.  Detector truth is conservatively
Cartesian-producted with the already proof-backed semantic predicate domain;
therefore ignoring cross-correlations can only reduce optimization, not make it
unsound.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import asdict, dataclass
from itertools import product
from typing import Any

from .dedicated_event_guard_minimization import _cube_key, _hold_key, _outcome_key
from .dedicated_event_reachability import _detector_value
from .dedicated_event_semantic_guard_minimization import (
    SemanticCubeDomain, 
    _canonical_cube, 
    _candidates, 
    _select_cover, 
    _scheduler_domain, 
)


def _event_detector_cube(ir: dict[str, Any]) -> dict[str, dict[str, bool]]: 
    out: dict[str, dict[str, bool]] = {}
    all_det = {str(d["event_id"]) for d in ir.get("scheduler_detectors", [])}
    for event in ir.get("event_classes", []): 
        req = {str(x) for x in event.get("required_detectors", [])}
        forb = {str(x) for x in event.get("forbidden_detectors", [])}
        if req & forb: 
            raise ValueError(f"event has detector both required and forbidden: {event}")
        if req | forb != all_det: 
            raise ValueError(
                "cross-event minimization requires event classes to fully specify detector truth: "
                f"event={event.get('event_class')} missing={sorted(all_det-(req|forb))}"
            )
        out[str(event["event_class"])] = {**{x: True for x in req}, **{x: False for x in forb}}
    return out


def feasible_detector_patterns(ir: dict[str, Any], *, max_assignments: int = 65536) -> tuple[tuple[str, ...], tuple[tuple[bool, ...], ...], int]: 
    dets = list(ir.get("scheduler_detectors", []))
    dnames = tuple(str(d["event_id"]) for d in dets)
    sched_rows = {str(r["id"]): r for r in ir.get("scheduler_owned_sources", [])}
    sched_ids = tuple(sorted(sched_rows))
    sched_domains: list[tuple[int, ...]] = []
    for sid in sched_ids: 
        vals = _scheduler_domain(ir, sid)
        if vals is None: 
            raise ValueError(f"cannot prove finite scheduler domain for {sid}")
        sched_domains.append(tuple(vals))
    gpio_bits = tuple(sorted(
        {int(d["input_bit"]) for d in dets}
        | {int(q["bit"]) for d in dets for q in d.get("qualifiers", [])}
    ))
    assignments = 1
    for vals in sched_domains: 
        assignments *= len(vals)
    assignments *= 1 << len(gpio_bits)
    if assignments > max_assignments: 
        raise ValueError(f"detector truth enumeration too large: {assignments} > {max_assignments}")
    patterns: set[tuple[bool, ...]] = set()
    for svals in product(*sched_domains): 
        sched = dict(zip(sched_ids, svals))
        for gvals in product((0, 1), repeat = len(gpio_bits)): 
            gpio = sum((int(v) & 1) << bit for bit, v in zip(gpio_bits, gvals))
            patterns.add(tuple(bool(_detector_value(d, sched_rows, sched, gpio)) for d in dets))
    return dnames, tuple(sorted(patterns)), assignments


class EventSemanticCubeDomain: 
    def __init__(self, ir: dict[str, Any]): 
        self.semantic = SemanticCubeDomain(ir)
        self.detector_names, self.detector_patterns, self.detector_assignments = feasible_detector_patterns(ir)
        self._didx = {name: i for i, name in enumerate(self.detector_names)}
        self._cache: dict[tuple[tuple[str, bool], ...], bool] = {}

    def satisfiable(self, cube: dict[str, bool]) -> bool: 
        key = tuple(sorted(cube.items()))
        if key in self._cache: 
            return self._cache[key]
        pc = {k[2:]: v for k, v in cube.items() if k.startswith("P:")}
        dc = {k[2:]: v for k, v in cube.items() if k.startswith("D:")}
        if not self.semantic.satisfiable(pc): 
            self._cache[key] = False
            return False
        ok = any(all(pattern[self._didx[name]] == value for name, value in dc.items()) for pattern in self.detector_patterns)
        self._cache[key] = ok
        return ok

    def overlaps(self, a: dict[str, bool], b: dict[str, bool]) -> bool: 
        merged = dict(a)
        for key, value in b.items(): 
            if key in merged and merged[key] != value: 
                return False
            merged[key] = value
        return self.satisfiable(merged)

    def implies(self, specific: dict[str, bool], general: dict[str, bool]) -> bool: 
        if not self.satisfiable(specific): 
            return True
        for key, value in general.items(): 
            if key in specific: 
                if specific[key] != value: 
                    return False
                continue
            witness = dict(specific)
            witness[key] = not value
            if self.satisfiable(witness): 
                return False
        return True


@dataclass
class EventSemanticMinimizationStats: 
    complete_source_rows: int
    baseline_rules: int
    minimized_rules: int
    baseline_extended_literals: int
    minimized_literals: int
    predicate_literals: int
    detector_literals: int
    unique_conditions: int
    detector_assignments_enumerated: int
    feasible_detector_patterns: int
    semantic_cross_outcome_overlaps: int


def _extended_cube(rule: dict[str, Any], aliases: dict[str, str], event_map: dict[str, dict[str, bool]]) -> dict[str, bool]: 
    pc = _canonical_cube(rule, aliases)
    if pc is None: 
        raise ValueError(f"contradictory predicate cube: {rule.get('rule_id')}")
    out = {f"P:{k}": bool(v) for k, v in pc.items()}
    for det, value in event_map[str(rule["event_class"])].items(): 
        out[f"D:{det}"] = bool(value)
    return out


def minimize_event_semantic_updates(complete_ir: dict[str, Any], baseline_ir: dict[str, Any]) -> tuple[dict[str, Any], EventSemanticMinimizationStats]: 
    source = list(complete_ir.get("update_rules", []))
    baseline = [r for r in baseline_ir.get("update_rules", []) if r.get("materialize")]
    if not source or not baseline: 
        raise ValueError("cross-event minimization requires complete source and materialized baseline relations")
    domain = EventSemanticCubeDomain(complete_ir)
    aliases = domain.semantic.aliases
    event_map = _event_detector_cube(complete_ir)

    source_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    baseline_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_cubes: dict[str, dict[str, bool]] = {}
    baseline_cubes: dict[str, dict[str, bool]] = {}
    for rule in source: 
        source_by_target[str(rule["target"])].append(rule)
        source_cubes[str(rule["rule_id"])] = _extended_cube(rule, aliases, event_map)
    for rule in baseline: 
        baseline_by_target[str(rule["target"])].append(rule)
        baseline_cubes[str(rule["rule_id"])] = _extended_cube(rule, aliases, event_map)

    output: list[dict[str, Any]] = []
    for target, good_target_rules in sorted(baseline_by_target.items()): 
        source_target = source_by_target[target]
        emitted_other_outcome_cubes: list[tuple[Any, dict[str, bool]]] = []
        by_outcome: dict[Any, list[dict[str, Any]]] = defaultdict(list)
        for rule in good_target_rules: 
            by_outcome[_outcome_key(rule)].append(rule)
        for outcome_key, good_rules in sorted(by_outcome.items(), key = lambda kv: repr(kv[0])): 
            if outcome_key == _hold_key(target): 
                continue
            bad_cubes = [source_cubes[str(r["rule_id"])] for r in source_target if _outcome_key(r) != outcome_key]
            # Generalized cubes are required to have deterministic hardware
            # behavior even in proof-domain states that are outside the source
            # transition relation.  Treat already selected different-outcome
            # implicants as an additional OFF-set.
            bad_cubes += [c for ok, c in emitted_other_outcome_cubes if ok != outcome_key]
            good_cubes = [baseline_cubes[str(r["rule_id"])] for r in good_rules]
            gf = Counter(k for cube in good_cubes for k in cube)
            bf = Counter(k for cube in bad_cubes for k in cube)
            candidate_map: dict[tuple[tuple[str, bool], ...], dict[str, bool]] = {}
            for cube in good_cubes: 
                for candidate in _candidates(cube, bad_cubes, domain, gf, bf): 
                    candidate_map[_cube_key(candidate)] = candidate
            implicants = _select_cover(list(candidate_map.values()), good_cubes, domain)
            exemplar = good_rules[0]
            for cube in implicants: 
                tids: list[str] = []
                for src in source_target: 
                    if _outcome_key(src) == outcome_key and domain.implies(source_cubes[str(src["rule_id"])], cube): 
                        tids.extend(str(x) for x in src.get("source_transition_ids", []))
                pred_enable = []
                det_enable = []
                for key, value in _cube_key(cube): 
                    if key.startswith("P:"): 
                        pred_enable.append({"basis": key[2:], "polarity": value})
                    elif key.startswith("D:"): 
                        det_enable.append({"detector": key[2:], "polarity": value})
                    else: 
                        raise ValueError(key)
                output.append({
                    "rule_id": "", 
                    "target": target, 
                    "outcome": deepcopy(exemplar["outcome"]), 
                    "enable": pred_enable, 
                    "detector_enable": det_enable, 
                    "source_transition_ids": sorted(set(tids)), 
                })
                emitted_other_outcome_cubes.append((outcome_key, dict(cube)))
    output.sort(key = lambda r: (str(r["target"]), repr(r["outcome"]), repr(r["detector_enable"]), repr(r["enable"])))
    for i, rule in enumerate(output): 
        rule["rule_id"] = f"X{i:04d}"

    # No priority semantics: cross-outcome generalized conditions for one target
    # must remain disjoint in the exact detector x proof-state product domain.
    # If cross-event generalization for a target creates an overlap only in the
    # proof-domain don't-care region, conservatively fall that entire target
    # back to the already proven baseline event-partition cubes.  This keeps the
    # optimization local and auditable rather than relying on priority order.
    def rcube(r: dict[str, Any]) -> dict[str, bool]: 
        c = {f"P:{x['basis']}": bool(x["polarity"]) for x in r.get("enable", [])}
        c.update({f"D:{x['detector']}": bool(x["polarity"]) for x in r.get("detector_enable", [])})
        return c

    def conflicting_targets(rows: list[dict[str, Any]]) -> set[str]: 
        by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows: 
            by_target[str(r["target"])].append(r)
        bad: set[str] = set()
        for target, rules in by_target.items(): 
            for i, a in enumerate(rules): 
                for b in rules[i+1:]: 
                    if _outcome_key(a) == _outcome_key(b): 
                        continue
                    if domain.overlaps(rcube(a), rcube(b)): 
                        bad.add(target)
                        break
                if target in bad: 
                    break
        return bad

    fallback_targets = conflicting_targets(output)
    if fallback_targets: 
        kept = [r for r in output if str(r["target"]) not in fallback_targets]
        for target in sorted(fallback_targets): 
            for src in baseline_by_target[target]: 
                pred = _canonical_cube(src, aliases)
                assert pred is not None
                kept.append({
                    "rule_id": "", 
                    "target": target, 
                    "outcome": deepcopy(src["outcome"]), 
                    "enable": [{"basis": k, "polarity": v} for k, v in _cube_key(pred)], 
                    "detector_enable": [{"detector": k, "polarity": v} for k, v in sorted(event_map[str(src["event_class"])].items())], 
                    "source_transition_ids": sorted(set(str(x) for x in src.get("source_transition_ids", []))), 
                })
        output = kept

    output.sort(key = lambda r: (str(r["target"]), repr(r["outcome"]), repr(r["detector_enable"]), repr(r["enable"])))
    for i, rule in enumerate(output): 
        rule["rule_id"] = f"X{i:04d}"
    residual = conflicting_targets(output)
    if residual: 
        raise ValueError(f"baseline fallback failed to remove cross-outcome overlaps: {sorted(residual)}")
    cross = 0

    base_lits = sum(len(baseline_cubes[str(r["rule_id"])]) for r in baseline)
    pred_lits = sum(len(r.get("enable", [])) for r in output)
    det_lits = sum(len(r.get("detector_enable", [])) for r in output)
    unique_conditions = len({
        (tuple((x["basis"], bool(x["polarity"])) for x in r.get("enable", [])), 
         tuple((x["detector"], bool(x["polarity"])) for x in r.get("detector_enable", [])))
        for r in output
    })
    stats = EventSemanticMinimizationStats(
        complete_source_rows = len(source), 
        baseline_rules = len(baseline), 
        minimized_rules = len(output), 
        baseline_extended_literals = base_lits, 
        minimized_literals = pred_lits+det_lits, 
        predicate_literals = pred_lits, 
        detector_literals = det_lits, 
        unique_conditions = unique_conditions, 
        detector_assignments_enumerated = domain.detector_assignments, 
        feasible_detector_patterns = len(domain.detector_patterns), 
        semantic_cross_outcome_overlaps = cross, 
    )
    return {
        "version": 1, 
        "proof_model": "DETECTOR_TRUTH_ENUMERATION_X_PROOF_BACKED_SEMANTIC_PREDICATE_DOMAIN", 
        "rules": output, 
        "detector_names": list(domain.detector_names), 
        "feasible_detector_patterns": [list(x) for x in domain.detector_patterns], 
        "stats": {**asdict(stats), "fallback_targets": sorted(fallback_targets)}, 
        "notes": [
            "Event-class names are not synthesized by this relation; rules use generalized detector truth cubes directly.", 
            "Exact detector truth patterns are enumerated from scheduler-state domains and GPIO bits referenced by generic detector definitions.", 
            "Detector truth and semantic predicate domains are conservatively Cartesian-producted; ignored cross-correlation can only reduce optimization.", 
            "Complete HOLD rows remain the OFF-set, so combined-event semantics are preserved whenever they differ from singleton events.", 
            "No protocol, state provenance, GPIO identity, BB identity, or transition identity is used to decide a rewrite.", 
        ], 
    }, stats


def attach_event_semantic_relation(ir: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]: 
    out = deepcopy(ir)
    out["event_semantic_update_relation"] = deepcopy(meta)
    out.setdefault("notes", []).append(
        "A separately proven cross-event update relation may realize physical D-input logic directly from detector+predicate cubes without materializing named event-class decoders."
    )
    return out
