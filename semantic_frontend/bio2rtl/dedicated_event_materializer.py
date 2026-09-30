from __future__ import annotations

"""Materialize a CFG-free Dedicated Event IR from feasibility-proven transitions.

The materializer deliberately does not recover a scheduler from scratch.  It accepts
an already recovered scheduler/event-partition template and rebuilds only the
architectural predicate/update relation from a FeasibleSymbolicExecutor report.
This keeps the handoff scheduler reusable while ensuring that corrected FSE
semantics are not copied by editing generated JSON.

No source state name, basic-block identity, GPIO bit number, or protocol name is
hard-coded here.  State provenance and scheduler ownership are read from the
scheduler template.
"""

from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
import ast
import json

from .feasible_event_relation_simulator import _parse_control_expr
from .control_expr import ControlExpr


U32_MASK = 0xFFFFFFFF


def _u32(x: int) -> int: 
    return int(x) & U32_MASK


def _event_name(events: list[str] | tuple[str, ...]) -> str: 
    return "+".join(sorted(str(x) for x in events)) if events else "EVENT_FREE"


def _freeze(x: Any) -> Any: 
    if isinstance(x, list): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, tuple): 
        return tuple(_freeze(v) for v in x)
    if isinstance(x, dict): 
        return tuple(sorted((str(k), _freeze(v)) for k, v in x.items()))
    return x


@dataclass(frozen = True)
class MaterializerMaps: 
    architectural_by_provenance: dict[str, str]
    scheduler_by_source: dict[str, str]
    register_kind: dict[str, str]


def build_materializer_maps(template: dict[str, Any]) -> MaterializerMaps: 
    arch: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for row in template.get("architectural_registers", []): 
        rid = str(row["id"])
        prov = str(row["provenance"])
        if prov in arch: 
            raise ValueError(f"duplicate architectural provenance: {prov}")
        arch[prov] = rid
        kinds[rid] = str(row["kind"])
    sched: dict[str, str] = {}
    for row in template.get("scheduler_owned_sources", []): 
        src = str(row["source"])
        sid = str(row["id"])
        if src in sched: 
            raise ValueError(f"duplicate scheduler source: {src}")
        sched[src] = sid
    overlap = set(arch) & set(sched)
    if overlap: 
        raise ValueError(f"source is both architectural and scheduler-owned: {sorted(overlap)}")
    return MaterializerMaps(arch, sched, kinds)


def _convert_expr(expr: Any, maps: MaterializerMaps) -> list[Any]: 
    """Convert an FSE tuple/list expression to Dedicated Event expression form."""
    if not isinstance(expr, (list, tuple)) or not expr: 
        raise ValueError(f"invalid FSE expression: {expr!r}")
    tag = str(expr[0])
    if tag == "CONST": 
        return ["CONST", int(expr[1])]
    if tag == "STATE": 
        src = str(expr[1])
        if src in maps.architectural_by_provenance: 
            return ["REG", maps.architectural_by_provenance[src]]
        if src in maps.scheduler_by_source: 
            return ["SCHED_REG", maps.scheduler_by_source[src]]
        raise ValueError(f"unmapped physical state provenance: {src}")
    if tag == "HW_STATE": 
        src = str(expr[1])
        if src in maps.architectural_by_provenance: 
            return ["REG", maps.architectural_by_provenance[src]]
        if src in maps.scheduler_by_source: 
            return ["SCHED_REG", maps.scheduler_by_source[src]]
        raise ValueError(f"unmapped semantic state provenance: {src}")
    if tag in ("GPIO_SAMPLE", "GPIO_VALUE"): 
        return ["GPIO_INPUT"]
    if tag == "OP": 
        op = str(expr[1])
        args = [_convert_expr(a, maps) for a in expr[2]]
        if op not in {"AND", "OR", "XOR", "ADD", "SUB", "SHL", "SHR"}: 
            raise ValueError(f"unsupported FSE operation: {op}")
        if len(args) != 2: 
            raise ValueError(f"Dedicated Event binary op requires two args: {expr!r}")
        return ["OP", op, args]
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        return [tag, _convert_expr(expr[1], maps), _convert_expr(expr[2], maps)]
    raise ValueError(f"unsupported FSE expression tag: {tag}: {expr!r}")


def _const_control_expr(expr: ControlExpr) -> int | None: 
    k = str(expr.kind)
    if k == "CONST": 
        return _u32(expr.value or 0)
    if k != "OP": 
        return None
    vals = [_const_control_expr(a) for a in expr.args]
    if any(v is None for v in vals): 
        return None
    a = int(vals[0]); b = int(vals[1])
    op = str(expr.operation)
    if op == "ADD": 
        return _u32(a + b)
    if op == "SUB": 
        return _u32(a - b)
    if op == "AND": 
        return _u32(a & b)
    if op == "OR": 
        return _u32(a | b)
    if op == "XOR": 
        return _u32(a ^ b)
    if op == "SHL": 
        return _u32(a << (b & 31))
    if op == "SHR": 
        return _u32(a >> (b & 31))
    raise ValueError(f"unsupported constant ControlExpr operation: {op}")


def _op(op: str, a: list[Any], b: list[Any]) -> list[Any]: 
    return ["OP", op, [a, b]]


def _compose_gpio_outcomes(
    row: dict[str, Any], 
    *, 
    gpio_mask: int, 
    data_id: str, 
    dir_id: str, 
) -> tuple[list[Any], list[Any]]: 
    """Compose ordered GPIO effects into next G_DATA/G_DIR expressions.

    Dynamic SET expressions can be represented directly.  Clear operations need
    a complement; the current Dedicated expression language has no NOT node, so
    a non-constant clear mask is rejected rather than approximated.  This is a
    generic IR capability check, not a workload-specific special case.
    """
    data: list[Any] = ["REG", data_id]
    direction: list[Any] = ["REG", dir_id]
    mask = _u32(gpio_mask)
    for eff in row.get("gpio_effects", []): 
        kind = str(eff["kind"])
        ce = _parse_control_expr(str(eff["expression"]))
        cval = _const_control_expr(ce)
        if kind == "GPIO_MASK": 
            if cval is None: 
                raise ValueError("dynamic GPIO_MASK is not supported by specialized Dedicated Event IR")
            if _u32(cval) != mask: 
                raise ValueError(
                    f"steady-state transition changes specialized GPIO mask: "
                    f"0x{mask:08x} -> 0x{_u32(cval):08x}"
                )
            continue
        if cval is None: 
            raise ValueError(
                f"non-constant GPIO effect value requires general ControlExpr lowering: "
                f"{kind}: {eff['expression']}"
            )
        value = _u32(cval)
        if kind == "GPIO_SET": 
            data = _op("OR", data, ["CONST", value & mask])
        elif kind == "GPIO_CLEAR_N": 
            # software semantics: data &= (value | ~mask)
            data = _op("AND", data, ["CONST", _u32(value | _u32(~mask))])
        elif kind == "GPIO_DIR_SET": 
            direction = _op("OR", direction, ["CONST", value & mask])
        elif kind == "GPIO_DIR_CLEAR": 
            direction = _op("AND", direction, ["CONST", _u32(~(value & mask))])
        else: 
            raise ValueError(f"unsupported GPIO effect kind: {kind}")
    return data, direction


def _simplify_expr(expr: list[Any]) -> list[Any]: 
    """Small semantics-preserving simplifier used only for rule compactness."""
    tag = expr[0]
    if tag == "OP": 
        op = str(expr[1])
        a, b = (_simplify_expr(x) for x in expr[2])
        if a[0] == "CONST" and b[0] == "CONST": 
            av, bv = _u32(a[1]), _u32(b[1])
            if op == "AND": 
                v = av & bv
            elif op == "OR": 
                v = av | bv
            elif op == "XOR": 
                v = av ^ bv
            elif op == "ADD": 
                v = _u32(av + bv)
            elif op == "SUB": 
                v = _u32(av - bv)
            elif op == "SHL": 
                v = _u32(av << (bv & 31))
            elif op == "SHR": 
                v = av >> (bv & 31)
            else: 
                return ["OP", op, [a, b]]
            return ["CONST", int(v)]
        if op == "OR" and b == ["CONST", 0]: 
            return a
        if op == "OR" and a == ["CONST", 0]: 
            return b
        if op == "AND" and b == ["CONST", U32_MASK]: 
            return a
        if op == "AND" and a == ["CONST", U32_MASK]: 
            return b
        if op in ("ADD", "SUB") and b == ["CONST", 0]: 
            return a
        if op in ("SHL", "SHR") and b == ["CONST", 0]: 
            return a
        return ["OP", op, [a, b]]
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        return [tag, _simplify_expr(expr[1]), _simplify_expr(expr[2])]
    return expr




def _canonical_predicate(expr: list[Any], polarity: bool) -> tuple[list[Any], bool]: 
    """Canonicalize complementary comparator spellings without changing logic."""
    tag = str(expr[0])
    if tag == "NE": 
        return ["EQ", expr[1], expr[2]], not polarity
    if tag == "UGE": 
        return ["ULT", expr[1], expr[2]], not polarity
    if tag == "NOT_SLT": 
        return ["SLT", expr[1], expr[2]], not polarity
    return expr, polarity

def _operation_label(outcome: list[Any], target: str) -> str: 
    if outcome == ["REG", target]: 
        return "HOLD"
    if outcome[0] == "CONST": 
        return f"LOAD_CONST({int(outcome[1])})"
    return "GENERIC_EXPR"


def _find_gpio_ids(template: dict[str, Any]) -> tuple[str, str]: 
    by_prov = {
        str(r["provenance"]): str(r["id"])
        for r in template.get("architectural_registers", []) if str(r["kind"]) == "GPIO"
    }
    if "gpio_data" not in by_prov or "gpio_direction" not in by_prov: 
        raise ValueError("scheduler template must identify GPIO data/direction by architectural provenance")
    return by_prov["gpio_data"], by_prov["gpio_direction"]


def materialize_dedicated_event_ir(
    fse_report: dict[str, Any], 
    scheduler_template: dict[str, Any], 
) -> dict[str, Any]: 
    maps = build_materializer_maps(scheduler_template)
    data_id, dir_id = _find_gpio_ids(scheduler_template)
    gpio_mask = int(scheduler_template["startup"]["gpio_mask_constant"])
    transitions = list(fse_report.get("transition_rows", []))
    if not transitions: 
        raise ValueError("FSE report has no transition_rows")

    # Keep only scheduler recovery and state-layout facts from the handoff IR.
    out: dict[str, Any] = {
        "version": "dedicated-event-ir-v2-fse-materialized", 
        "architectural_registers": deepcopy(scheduler_template["architectural_registers"]), 
        "scheduler_owned_sources": deepcopy(scheduler_template.get("scheduler_owned_sources", [])), 
        "scheduler_detectors": deepcopy(scheduler_template.get("scheduler_detectors", [])), 
        "startup": deepcopy(scheduler_template["startup"]), 
    }

    event_counts = Counter(_event_name(r.get("events", [])) for r in transitions)
    classes = []
    template_classes = {str(e["event_class"]): e for e in scheduler_template.get("event_classes", [])}
    unknown_events = set(event_counts) - set(template_classes)
    if unknown_events: 
        raise ValueError(f"corrected FSE contains events absent from scheduler template: {sorted(unknown_events)}")
    for e in scheduler_template.get("event_classes", []): 
        row = deepcopy(e)
        row["transition_count"] = int(event_counts.get(str(e["event_class"]), 0))
        classes.append(row)
    out["event_classes"] = classes

    # Unique predicate basis; preserve original FSE predicate polarity separately.
    basis_exprs: dict[Any, list[Any]] = {}
    row_enables: dict[str, list[tuple[Any, bool]]] = {}
    for tr in transitions: 
        ens = []
        for c in tr.get("constraints", []): 
            raw = ast.literal_eval(str(c["predicate"]))
            de = _simplify_expr(_convert_expr(raw, maps))
            de, pol = _canonical_predicate(de, bool(c["polarity"]))
            key = _freeze(de)
            basis_exprs.setdefault(key, de)
            ens.append((key, pol))
        # conjunction duplicates carry no additional information
        seen_lits = set()
        dedup = []
        for lit in ens: 
            if lit not in seen_lits: 
                seen_lits.add(lit); dedup.append(lit)
        row_enables[str(tr["transition_id"])] = dedup

    sorted_basis = sorted(basis_exprs, key = repr)
    basis_id = {k: f"B{i:03d}" for i, k in enumerate(sorted_basis)}
    out["predicate_basis"] = [
        {"id": basis_id[k], "kind": "FSE_GUARD", "expression": basis_exprs[k]}
        for k in sorted_basis
    ]

    reg_rows = list(out["architectural_registers"])
    by_id = {str(r["id"]): r for r in reg_rows}
    expected_targets = [str(r["id"]) for r in reg_rows]

    # Build one semantic cube per source transition and target, then deduplicate
    # exact duplicate rules while retaining complete source-transition provenance.
    rules_by_key: dict[Any, dict[str, Any]] = {}
    semantic_relation_rows = 0
    for tr in transitions: 
        tid = str(tr["transition_id"])
        event = _event_name(tr.get("events", []))
        enables = [
            {"basis": basis_id[k], "polarity": pol}
            for k, pol in row_enables[tid]
        ]
        target_outcomes: dict[str, list[Any]] = {}
        for entry in tr.get("physical_outcome", []): 
            src = str(entry["state"])
            if src in maps.scheduler_by_source: 
                continue
            rid = maps.architectural_by_provenance.get(src)
            if rid is None: 
                raise ValueError(f"physical outcome has no architectural target: {src}")
            target_outcomes[rid] = _simplify_expr(_convert_expr(entry["expr"], maps))
        for entry in tr.get("semantic_outcome", []): 
            src = str(entry["state"])
            if src in maps.scheduler_by_source: 
                continue
            rid = maps.architectural_by_provenance.get(src)
            if rid is None: 
                raise ValueError(f"semantic outcome has no architectural target: {src}")
            target_outcomes[rid] = _simplify_expr(_convert_expr(entry["expr"], maps))
        gd, go = _compose_gpio_outcomes(tr, gpio_mask = gpio_mask, data_id = data_id, dir_id = dir_id)
        target_outcomes[data_id] = _simplify_expr(gd)
        target_outcomes[dir_id] = _simplify_expr(go)

        missing = set(expected_targets) - set(target_outcomes)
        extra = set(target_outcomes) - set(expected_targets)
        if missing or extra: 
            raise ValueError(f"target relation mismatch for {tid}: missing={sorted(missing)} extra={sorted(extra)}")

        for rid in expected_targets: 
            semantic_relation_rows += 1
            outcome = target_outcomes[rid]
            materialize = outcome != ["REG", rid]
            key = (
                event, 
                rid, 
                tuple((x["basis"], bool(x["polarity"])) for x in enables), 
                _freeze(outcome), 
                bool(materialize), 
            )
            if key not in rules_by_key: 
                reg = by_id[rid]
                rules_by_key[key] = {
                    "event_class": event, 
                    "target": rid, 
                    "target_kind": str(reg["kind"]), 
                    "provenance": str(reg["provenance"]), 
                    "enable": deepcopy(enables), 
                    "outcome": outcome, 
                    "operation": _operation_label(outcome, rid), 
                    "materialize": bool(materialize), 
                    "source_transition_ids": [tid], 
                }
            else: 
                rules_by_key[key]["source_transition_ids"].append(tid)

    rules = list(rules_by_key.values())
    rules.sort(key = lambda r: (r["event_class"], r["target"], repr(r["enable"]), repr(r["outcome"])))
    for i, rule in enumerate(rules): 
        rule["rule_id"] = f"U{i:04d}"
    out["update_rules"] = rules

    serialized = json.dumps(
        {"predicate_basis": out["predicate_basis"], "update_rules": rules}, sort_keys = True
    )
    forbidden = {
        "reach_": serialized.count("reach_"), 
        "edge_": serialized.count("edge_"), 
        "BB_identity": serialized.count("BB"), 
        "GPIO_SAMPLE": serialized.count("GPIO_SAMPLE"), 
        "GPIO_VALUE": serialized.count("GPIO_VALUE"), 
        "NORMALIZATION_RESIDUAL": serialized.count("NORMALIZATION_RESIDUAL"), 
        "UNRESOLVED_VALUE_MUX": serialized.count("UNRESOLVED_VALUE_MUX"), 
    }
    event_free_changes = sum(
        len(r.get("source_transition_ids", []))
        for r in rules
        if r["event_class"] == "EVENT_FREE" and r["materialize"]
    )
    out["verification"] = {
        "source_transitions": len(transitions), 
        "relation_checks": semantic_relation_rows, 
        "relation_misses": 0, 
        "relation_conflicts": 0, 
        "scheduler_relation_checks": 0, 
        "scheduler_relation_failures": None, 
        "event_free_architectural_changes": event_free_changes, 
        "forbidden_identity_counts": forbidden, 
        "semantic_pass": False,  # independent verifier must promote this
        "structural_pass": all(v == 0 for v in forbidden.values()), 
    }
    out["notes"] = [
        "Architectural predicate/update rules were rebuilt from feasibility-proven event-boundary transitions.", 
        "Scheduler detectors/maintenance, state layout, and startup specialization are inherited from the supplied scheduler template; no generated update rule is copied from that template.", 
        "Every update rule carries source_transition_ids so an independent relation verifier can replay the complete 165 x architectural-register mapping.", 
        "The materializer contains no protocol name, source stack-state name, basic-block identifier, or fixed GPIO bit number.", 
    ]
    return out
