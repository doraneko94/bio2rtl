from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .event_transition_ir import EventTransitionIR


def write_event_transition_report(result: EventTransitionIR, path: Path) -> None: 
    lines = [
        "EVENT TRANSITION IR / CFG-FREE SCHEDULING FRONTIER", 
        "=" * 78, 
        f"canonical events                 : {len(result.transitions)}", 
        f"scheduled definition sites       : {result.scheduled_definition_sites}", 
        f"event-only definition sites      : {result.event_only_definition_sites}", 
        f"residual-guarded definition sites: {result.residual_guarded_definition_sites}", 
        f"unique operation forms           : {result.unique_operation_forms}", 
        f"unique residual guard forms      : {result.unique_residual_guard_forms}", 
        "", 
    ]

    for transition in result.transitions: 
        lines += [
            f"{transition.event_id}: GPIO[{transition.gpio_bit}] {transition.edge}", 
            f"  qualifiers        : {', '.join(transition.qualifiers) or '-'}", 
            f"  operations        : {len(transition.operations)}", 
            f"  event-only        : {transition.event_only_operations}", 
            f"  residual-guarded  : {transition.residual_guarded_operations}", 
            f"  residual forms    : {transition.unique_residual_guards}", 
        ]
        zero_group = result.shared_zero_write_groups.get(transition.event_id)
        if zero_group: 
            lines.append(
                "  shared zero-write : " + ", ".join(zero_group)
            )
        for op in transition.operations: 
            lines += [
                f"    BB{op.definition_block:03d} {op.register}: {op.operation_kind}", 
                f"      guard: {' && '.join(op.residual_guards) or '<event only>'}", 
                f"      expr : {op.expression_key}", 
            ]
        lines.append("")

    lines += ["Notes", "-" * 78]
    lines += [f"- {note}" for note in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True)
    )
