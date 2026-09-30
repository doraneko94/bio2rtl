from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .event_transition_ir import EventTransitionIR
from .hardware_behavior_ir import HardwareBehaviorIR


@dataclass
class EventDomainRegister: 
    register: str
    width: int
    all_events: list[str]
    reset_events: list[str]
    active_events: list[str]
    event_operation_counts: dict[str, int]
    domain_kind: str
    # RESET_ONLY / SINGLE_EVENT / MULTI_EVENT


@dataclass
class EventDomainAnalysis: 
    registers: list[EventDomainRegister]
    reset_events: list[str]
    single_event_registers: int
    multi_event_registers: int
    reset_only_registers: int
    single_event_bits: int
    multi_event_bits: int
    reset_only_bits: int
    notes: list[str]


def analyze_event_domains(
    ir: EventTransitionIR, 
    behavior: HardwareBehaviorIR | None = None, 
) -> EventDomainAnalysis: 
    # A recovered event is reset-like when it has a shared unconditional
    # zero-write group.  This is intentionally structural and protocol-neutral.
    reset_events = sorted(ir.shared_zero_write_groups)

    ops_by_reg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for transition in ir.transitions: 
        for op in transition.operations: 
            ops_by_reg[op.register][transition.event_id] += 1

    widths = {name: obj.width for name, obj in behavior.objects.items()} if behavior is not None else {}
    rows: list[EventDomainRegister] = []
    for register, counts in sorted(ops_by_reg.items()): 
        all_events = sorted(counts)
        reg_reset_events = sorted(e for e in all_events if e in reset_events)
        active_events = sorted(e for e in all_events if e not in reset_events)
        if not active_events: 
            kind = "RESET_ONLY"
        elif len(active_events) == 1: 
            kind = "SINGLE_EVENT"
        else: 
            kind = "MULTI_EVENT"
        rows.append(
            EventDomainRegister(
                register = register, 
                width = widths.get(register, 1), 
                all_events = all_events, 
                reset_events = reg_reset_events, 
                active_events = active_events, 
                event_operation_counts = {e: counts[e] for e in all_events}, 
                domain_kind = kind, 
            )
        )

    return EventDomainAnalysis(
        registers = rows, 
        reset_events = reset_events, 
        single_event_registers = sum(r.domain_kind == "SINGLE_EVENT" for r in rows), 
        multi_event_registers = sum(r.domain_kind == "MULTI_EVENT" for r in rows), 
        reset_only_registers = sum(r.domain_kind == "RESET_ONLY" for r in rows), 
        single_event_bits = sum(r.width for r in rows if r.domain_kind == "SINGLE_EVENT"), 
        multi_event_bits = sum(r.width for r in rows if r.domain_kind == "MULTI_EVENT"), 
        reset_only_bits = sum(r.width for r in rows if r.domain_kind == "RESET_ONLY"), 
        notes = [
            "Reset-like events are inferred only from shared unconditional CONST(0) writes.", 
            "A SINGLE_EVENT register can in principle map to one event clock plus reset events without multi-clock state.", 
            "A MULTI_EVENT register is a state-recovery/retiming target; directly emitting one FF from multiple event clocks would be unsafe.", 
        ], 
    )


def write_event_domain_report(result: EventDomainAnalysis, path: Path) -> None: 
    lines = [
        "EVENT DOMAIN / HARDWARE STATE DIAGNOSTIC", 
        "=" * 78, 
        f"reset-like canonical events : {', '.join(result.reset_events) or '-'}", 
        f"single-event registers      : {result.single_event_registers}", 
        f"multi-event registers       : {result.multi_event_registers}", 
        f"reset-only registers        : {result.reset_only_registers}", 
        f"single-event state bits     : {result.single_event_bits}", 
        f"multi-event state bits      : {result.multi_event_bits}", 
        f"reset-only state bits       : {result.reset_only_bits}", 
        "", 
    ]
    for row in result.registers: 
        lines += [
            f"{row.register} [{row.width} bit]: {row.domain_kind}", 
            f"  active events : {', '.join(row.active_events) or '-'}", 
            f"  reset events  : {', '.join(row.reset_events) or '-'}", 
            "  operations    : " + ", ".join(f"{e}={row.event_operation_counts[e]}" for e in row.all_events), 
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True)
    )
