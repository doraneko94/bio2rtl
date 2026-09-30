from __future__ import annotations

"""Independent checks for FSE -> Dedicated Event relation materialization."""

from dataclasses import dataclass, asdict
from itertools import product
from typing import Any
import ast

from .dedicated_event_materializer import (
    _compose_gpio_outcomes, 
    _convert_expr, 
    _event_name, 
    _freeze, 
    _find_gpio_ids, 
    _simplify_expr, 
    _canonical_predicate, 
    build_materializer_maps, 
)
from .dedicated_event_reachability import (
    _detector_value, 
    _eval_expr, 
    _event_class, 
    _gpio_input_dependencies, 
    _scheduler_next, 
)


@dataclass
class DedicatedEventRelationVerification: 
    source_transitions: int
    architectural_registers: int
    architectural_relation_checks: int
    architectural_relation_misses: int
    architectural_relation_conflicts: int
    scheduler_states: int
    scheduler_relation_checks: int
    scheduler_relation_failures: int
    scheduler_unsatisfied_rows: int
    forbidden_identity_counts: dict[str, int]
    semantic_pass: bool
    structural_pass: bool
    failure_examples: list[dict[str, Any]]


def _expected_relation_for_transition(tr: dict[str, Any], ir: dict[str, Any]) -> dict[str, list[Any]]: 
    maps = build_materializer_maps(ir)
    data_id, dir_id = _find_gpio_ids(ir)
    out: dict[str, list[Any]] = {}
    for key in ("physical_outcome", "semantic_outcome"): 
        for item in tr.get(key, []): 
            src = str(item["state"])
            if src in maps.scheduler_by_source: 
                continue
            rid = maps.architectural_by_provenance.get(src)
            if rid is None: 
                raise ValueError(f"unmapped source outcome {src}")
            out[rid] = _simplify_expr(_convert_expr(item["expr"], maps))
    gd, dr = _compose_gpio_outcomes(
        tr, 
        gpio_mask = int(ir["startup"]["gpio_mask_constant"]), 
        data_id = data_id, 
        dir_id = dir_id, 
    )
    out[data_id] = _simplify_expr(gd)
    out[dir_id] = _simplify_expr(dr)
    return out


def _expected_enable(tr: dict[str, Any], ir: dict[str, Any]) -> list[tuple[Any, bool]]: 
    maps = build_materializer_maps(ir)
    lits = []
    for c in tr.get("constraints", []): 
        raw = ast.literal_eval(str(c["predicate"]))
        de = _simplify_expr(_convert_expr(raw, maps))
        de, pol = _canonical_predicate(de, bool(c["polarity"]))
        lits.append((_freeze(de), pol))
    seen = set()
    out = []
    for x in lits: 
        if x not in seen: 
            seen.add(x)
            out.append(x)
    return out


def _expr_has_reg(expr: Any) -> bool: 
    if isinstance(expr, (list, tuple)): 
        if expr and expr[0] == "REG": 
            return True
        return any(_expr_has_reg(x) for x in expr)
    if isinstance(expr, dict): 
        return any(_expr_has_reg(v) for v in expr.values())
    return False


def _scheduler_outcome(tr: dict[str, Any], source: str) -> Any: 
    for key in ("physical_outcome", "semantic_outcome"): 
        for item in tr.get(key, []): 
            if str(item["state"]) == source: 
                return item["expr"]
    raise ValueError(f"transition {tr['transition_id']} lacks scheduler source outcome {source}")


def _scheduler_relation_check(tr: dict[str, Any], ir: dict[str, Any], sched_row: dict[str, Any]) -> tuple[bool, bool, dict[str, Any]|None]: 
    """Check scheduler maintenance over every hardware-only valuation compatible with a transition.

    Architectural guard literals are intentionally ignored: they can only
    restrict applicability further and cannot alter scheduler maintenance.
    A transition that completes in a proved quiescent terminal has no subsequent
    scheduler observation, so post-terminal scheduler maintenance is semantically N/A.
    """
    if bool(tr.get('terminal_completion')): 
        return True, False, None
    maps = build_materializer_maps(ir)
    sched_rows = {str(x['id']): x for x in ir.get('scheduler_owned_sources', [])}
    sched_ids = list(sched_rows)
    detectors = list(ir.get('scheduler_detectors', []))
    event_expected = _event_name(tr.get('events', []))
    src_expr = _simplify_expr(_convert_expr(_scheduler_outcome(tr, str(sched_row['source'])), maps))

    hw_constraints = []
    gpio_bits = set()
    for c in tr.get('constraints', []): 
        raw = ast.literal_eval(str(c['predicate']))
        de = _simplify_expr(_convert_expr(raw, maps))
        if not _expr_has_reg(de): 
            hw_constraints.append((de, bool(c['polarity'])))
            gpio_bits |= _gpio_input_dependencies(de)
    gpio_bits |= _gpio_input_dependencies(src_expr)
    for d in detectors: 
        gpio_bits.add(int(d['input_bit']))
        for q in d.get('qualifiers', []): 
            if q.get('source') == 'GPIO_INPUT': 
                gpio_bits.add(int(q['bit']))
    bits = sorted(gpio_bits)
    if len(bits)>12: 
        raise RuntimeError(f"scheduler verification GPIO dependency too large: {bits}")

    matched = 0
    for sval_bits in product((0, 1), repeat = len(sched_ids)): 
        sched = dict(zip(sched_ids, sval_bits))
        for gp in range(1<<len(bits)): 
            gpio = 0
            for i, b in enumerate(bits): 
                if (gp>>i)&1: 
                    gpio |= 1<<b
            detvals = {d['event_id']: bool(_detector_value(d, sched_rows, sched, gpio)) for d in detectors}
            try: 
                ev = _event_class(ir, detvals)
            except ValueError: 
                continue
            if ev!=event_expected: 
                continue
            if any(bool(_eval_expr(e, {}, sched, gpio))!=pol for e, pol in hw_constraints): 
                continue
            matched += 1
            raw_actual = int(_eval_expr(src_expr, {}, sched, gpio))
            if isinstance(src_expr, list) and src_expr and src_expr[0] == 'SCHED_REG': 
                actual = raw_actual & 1
            else: 
                one = int(sched_row.get('source_one_value', 1))
                actual = 1 if raw_actual == one else 0
            expected = int(_scheduler_next(sched_row, int(sched[str(sched_row['id'])]), gpio, detvals, detectors)) & 1
            if actual!=expected: 
                return False, False, {
                    'transition_id': str(tr['transition_id']), 
                    'scheduler': str(sched_row['id']), 
                    'event': event_expected, 
                    'sched': sched, 
                    'gpio': gpio, 
                    'source_outcome': src_expr, 
                    'actual': actual, 'expected': expected, 
                }
    if matched == 0: 
        return False, True, {
            'transition_id': str(tr['transition_id']), 
            'scheduler': str(sched_row['id']), 
            'event': event_expected, 
            'reason': 'no hardware-only valuation satisfies event and scheduler/GPIO guards', 
        }
    return True, False, None


def verify_dedicated_event_relation(fse: dict[str, Any], ir: dict[str, Any]) -> DedicatedEventRelationVerification: 
    transitions = list(fse.get('transition_rows', []))
    reg_ids = [str(r['id']) for r in ir.get('architectural_registers', [])]
    pred = {str(p['id']): _freeze(p['expression']) for p in ir.get('predicate_basis', [])}
    by_tid_target: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rule in ir.get('update_rules', []): 
        for tid in rule.get('source_transition_ids', []): 
            by_tid_target.setdefault((str(tid), str(rule['target'])), []).append(rule)

    checks = misses = conflicts = 0
    failures = []
    for tr in transitions: 
        tid = str(tr['transition_id'])
        event = _event_name(tr.get('events', []))
        expected = _expected_relation_for_transition(tr, ir)
        expected_enable = _expected_enable(tr, ir)
        for rid in reg_ids: 
            checks+=1
            rows = by_tid_target.get((tid, rid), [])
            if len(rows)!=1: 
                if not rows: 
                    misses+=1
                else: 
                    conflicts+=1
                if len(failures)<20: 
                    failures.append({'transition_id': tid, 'target': rid, 'reason': f'rule_count={len(rows)}'})
                continue
            rule = rows[0]
            actual_enable = [(pred[str(x['basis'])], bool(x['polarity'])) for x in rule.get('enable', [])]
            ok = (str(rule['event_class']) == event and _freeze(rule['outcome']) == _freeze(expected[rid]) and actual_enable == expected_enable)
            if not ok: 
                misses+=1
                if len(failures)<20: 
                    failures.append({'transition_id': tid, 'target': rid, 'reason': 'event/enable/outcome mismatch'})

    sched_checks = sched_fail = sched_unsat = 0
    for tr in transitions: 
        for srow in ir.get('scheduler_owned_sources', []): 
            sched_checks+=1
            ok, unsat, ex = _scheduler_relation_check(tr, ir, srow)
            if not ok: 
                sched_fail+=1
                sched_unsat += int(unsat)
                if ex is not None and len(failures)<20: 
                    failures.append(ex)

    import json
    ser = json.dumps({'predicate_basis': ir.get('predicate_basis', []), 'update_rules': ir.get('update_rules', [])}, sort_keys = True)
    forbidden = {
      'reach_': ser.count('reach_'), 'edge_': ser.count('edge_'), 'BB_identity': ser.count('BB'), 
      'GPIO_SAMPLE': ser.count('GPIO_SAMPLE'), 'GPIO_VALUE': ser.count('GPIO_VALUE'), 
      'NORMALIZATION_RESIDUAL': ser.count('NORMALIZATION_RESIDUAL'), 'UNRESOLVED_VALUE_MUX': ser.count('UNRESOLVED_VALUE_MUX')}
    structural = all(v == 0 for v in forbidden.values())
    semantic = (misses == 0 and conflicts == 0 and sched_fail == 0)
    return DedicatedEventRelationVerification(
        source_transitions = len(transitions), architectural_registers = len(reg_ids), 
        architectural_relation_checks = checks, architectural_relation_misses = misses, 
        architectural_relation_conflicts = conflicts, scheduler_states = len(ir.get('scheduler_owned_sources', [])), 
        scheduler_relation_checks = sched_checks, scheduler_relation_failures = sched_fail, 
        scheduler_unsatisfied_rows = sched_unsat, forbidden_identity_counts = forbidden, 
        semantic_pass = semantic, structural_pass = structural, failure_examples = failures)


def result_to_dict(r: DedicatedEventRelationVerification) -> dict[str, Any]: 
    return asdict(r)
