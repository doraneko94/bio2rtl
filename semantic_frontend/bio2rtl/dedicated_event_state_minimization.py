from __future__ import annotations

from copy import deepcopy
from math import ceil, log2
from typing import Any

from .event_boundary_state_analysis import (
    EventBoundaryStateAnalysisResult, 
    analyze_packed_gpio_state, 
)
from .dedicated_event_reachability import DedicatedEventReachabilityResult


def _info_width(values: list[int]) -> int: 
    if len(values) <= 1: 
        return 0
    return int(ceil(log2(len(values))))


def annotate_dedicated_event_storage(
    ir: dict[str, Any], 
    boundary: EventBoundaryStateAnalysisResult, 
    reach: DedicatedEventReachabilityResult, 
) -> dict[str, Any]: 
    """Attach a conservative physical-storage plan to Dedicated Event IR.

    Soundness rule:
      * architectural/semantic state is minimized only from the exact closure
        of the feasibility-proven source transition relation;
      * GPIO state is minimized from an over-approximate closure in which every
        materialized GPIO update rule may fire nondeterministically;
      * scheduler state is minimized from its event-boundary source-state row;
      * Dedicated scheduler reachability is retained only as an implementation
        diagnostic.  It must never justify deleting semantic storage.

    The abstract Dedicated Event register/update relation is left byte-for-byte
    unchanged apart from this annotation.
    """
    out = deepcopy(ir)
    boundary_rows = {r.state: r for r in boundary.state_rows}
    reach_rows = {r.register: r for r in reach.state_rows}
    gpio_rows = {r.register: r for r in analyze_packed_gpio_state(out)}

    register_storage: list[dict[str, Any]] = []
    under_realized: list[dict[str, Any]] = []

    for reg in out["architectural_registers"]: 
        rid = str(reg["id"])
        semw = int(reg["width"])
        kind = str(reg["kind"])
        row: dict[str, Any] = {
            "register": rid, 
            "kind": kind, 
            "provenance": reg["provenance"], 
            "semantic_width": semw, 
        }

        if kind == "GPIO": 
            gr = gpio_rows[rid]
            values = list(gr.reachable_masked_values)
            row.update({
                "reachable_values": values, 
                "proof": "MATERIALIZED_GPIO_RULE_OVERAPPROX", 
                "mask": int(gr.mask), 
                "constant_zero_bits": list(gr.constant_zero_bits), 
                "constant_one_bits": list(gr.constant_one_bits), 
                "materialized_rules": int(gr.materialized_rules), 
            })
            if gr.storage_bits == 0: 
                const = int(values[0]) if values else int(gr.reset_masked)
                row.update({
                    "storage_kind": "CONST", 
                    "storage_bits": 0, 
                    "constant_value": const, 
                })
            else: 
                row.update({
                    "storage_kind": "PACKED_MASK_BITS", 
                    "storage_bits": int(gr.storage_bits), 
                    "stored_bits": list(gr.variable_bits), 
                    "equal_bit_groups_candidate": [], 
                })

            rr = reach_rows.get(rid)
            if rr is not None and len(values) > 1 and len(rr.reachable_values) == 1: 
                under_realized.append({
                    "register": rid, 
                    "source_relation_values": values, 
                    "dedicated_scheduler_values": list(rr.reachable_values), 
                    "severity": "WARNING", 
                    "reason": "source transition relation permits variable GPIO state but current Dedicated scheduler reachability observes a constant", 
                })
            register_storage.append(row)
            continue

        prov = str(reg["provenance"])
        if prov not in boundary_rows: 
            raise KeyError(f"missing event-boundary proof row for {rid} provenance={prov}")
        br = boundary_rows[prov]
        values = list(br.reachable_values)
        row.update({
            "reachable_values": values, 
            "proof": "FSE_EVENT_BOUNDARY_OVERAPPROX", 
            "recurrence_classes": dict(br.recurrence_classes), 
        })
        if br.constant: 
            row.update({
                "storage_kind": "CONST", 
                "storage_bits": 0, 
                "constant_value": int(values[0]), 
            })
        elif int(br.natural_width) < semw: 
            row.update({
                "storage_kind": "NARROW_ZERO_EXTEND", 
                "storage_bits": int(br.natural_width), 
            })
        else: 
            row.update({
                "storage_kind": "DIRECT", 
                "storage_bits": semw, 
            })

        info = _info_width(values)
        if not br.constant and info < int(br.natural_width): 
            row["sparse_encoding_candidate"] = {
                "storage_bits": info, 
                "values": values, 
                "recurrence_has_counter": "COUNT" in br.recurrence_classes, 
                "automatic": False, 
                "reason": "candidate only; decode/MUX cost must be measured before adoption", 
            }
        if br.derivable_from: 
            row["derivation_candidates"] = list(br.derivable_from)
        register_storage.append(row)

    scheduler_storage: list[dict[str, Any]] = []
    for sched in out.get("scheduler_owned_sources", []): 
        source = str(sched["source"])
        if source not in boundary_rows: 
            raise KeyError(f"missing event-boundary scheduler proof row for {sched['id']} source={source}")
        br = boundary_rows[source]
        vals = list(br.reachable_values)
        bits = int(br.natural_width)
        scheduler_storage.append({
            "scheduler": sched["id"], 
            "source": source, 
            "role": sched["role"], 
            "storage_kind": "CONST" if bits == 0 else "DIRECT", 
            "storage_bits": bits, 
            "reachable_values": vals, 
            "constant_value": int(vals[0]) if bits == 0 else None, 
            "proof": "FSE_EVENT_BOUNDARY_OVERAPPROX", 
        })

    mask = int(out["startup"]["gpio_mask_constant"])
    packed_gpio_preopt = sum(1 for i in range(32) if (mask >> i) & 1) * sum(
        1 for r in out["architectural_registers"] if r["kind"] == "GPIO"
    )
    non_gpio_preopt = sum(
        int(r["width"]) for r in out["architectural_registers"] if r["kind"] != "GPIO"
    )
    scheduler_preopt = sum(max(1, int(boundary_rows[str(s["source"])].original_width)) for s in out.get("scheduler_owned_sources", []))
    active_run_bits = 1
    preopt = non_gpio_preopt + packed_gpio_preopt + scheduler_preopt + active_run_bits
    optimized = sum(x["storage_bits"] for x in register_storage) + sum(
        x["storage_bits"] for x in scheduler_storage
    ) + active_run_bits

    out["storage_optimization"] = {
        "version": 2, 
        "semantic_relation_preserved": True, 
        "safe_storage_proof": True, 
        "source_transitions": int(boundary.source_transitions), 
        "reachable_transition_rows": int(boundary.reachable_transitions), 
        "source_relation_reachable_states": int(boundary.reachable_states), 
        "dedicated_scheduler_reachable_states_advisory": int(reach.reachable_states), 
        "boundary_projection_sha256": boundary.reachable_state_sha256, 
        "dedicated_projection_sha256_advisory": reach.projection_sha256, 
        "projection_hash_match_advisory": boundary.reachable_state_sha256 == reach.projection_sha256, 
        "rule_conflicts_advisory": int(reach.rule_conflicts), 
        "scheduler_realization_warnings": under_realized, 
        "preoptimization_storage_upper_bound_bits": preopt, 
        "natural_storage_bits": optimized, 
        "active_run_storage_bits": active_run_bits, 
        "register_storage": register_storage, 
        "scheduler_storage": scheduler_storage, 
        "unreachable_transition_ids": list(boundary.unreachable_transition_ids), 
        "policy": {
            "drop_constant_state": True, 
            "natural_width_narrowing": True, 
            "gpio_rule_overapproximation": True, 
            "dedicated_reachability_used_for_elimination": False, 
            "sparse_value_encoding": False, 
            "equal_bit_coalescing": False, 
            "derived_state_elimination": False, 
            "joint_fsm_reencoding": False, 
        }, 
        "notes": [
            "The abstract Dedicated Event IR register relation is not rewritten by this pass.", 
            "Architectural/semantic CONST and NARROW storage are justified only by the feasibility-proven event-boundary transition closure.", 
            "GPIO packed storage is justified by a nondeterministic over-approximation over every materialized GPIO update rule.", 
            "Dedicated scheduler reachability is diagnostic only and is never used to eliminate semantic storage.", 
            "A scheduler-realization warning is expected for the handed-off fixture until directed protocol regression passes.", 
            "Sparse encodings, equal-bit sharing, and derived-state elimination remain non-automatic area candidates.", 
        ], 
    }
    return out
