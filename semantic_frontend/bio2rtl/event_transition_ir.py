from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_guard_analysis import DefinitionGuardAnalysis


@dataclass(frozen = True)
class EventTransitionOperation: 
    register: str
    operation_kind: str
    expression_key: str
    definition_block: int
    residual_guards: tuple[str, ...]


@dataclass
class EventTransition: 
    event_id: str
    gpio_bit: int
    edge: str
    qualifiers: tuple[str, ...]
    operations: list[EventTransitionOperation] = field(default_factory = list)
    event_only_operations: int = 0
    residual_guarded_operations: int = 0
    unique_residual_guards: int = 0


@dataclass
class EventTransitionIR: 
    transitions: list[EventTransition]
    scheduled_definition_sites: int
    event_only_definition_sites: int
    residual_guarded_definition_sites: int
    unique_operation_forms: int
    unique_residual_guard_forms: int
    shared_zero_write_groups: dict[str, list[str]]
    notes: list[str]


def build_event_transition_ir(
    canonical_events: CanonicalEventAnalysisResult, 
    definition_guards: DefinitionGuardAnalysis, 
) -> EventTransitionIR: 
    """Build a CFG-edge-free scheduling view at original definition sites.

    The IR intentionally stops before RTL lowering.  A site is attached to a
    recovered physical event only when definition-guard analysis proved that
    event at the original STATE_DEF block.  Remaining control predicates are
    carried as residual hardware guards.
    """
    event_info = {event.event_id: event for event in canonical_events.canonical_events}
    operations_by_event: dict[str, list[EventTransitionOperation]] = defaultdict(list)

    operation_forms: set[tuple[str, str, str]] = set()
    residual_forms: set[tuple[str, ...]] = set()
    scheduled_sites = 0
    event_only_sites = 0
    residual_sites = 0

    # Detect reset-like groups without using protocol names.  Expression keys
    # are retained by DefinitionGuardRule, so CONST(0) is recognized purely
    # from semantic form, not from register/stack names.
    zero_writes_by_event: dict[str, set[str]] = defaultdict(set)

    for rule in definition_guards.rules: 
        for site in rule.sites: 
            # A definition may be under multiple recovered events.  Those are
            # not safe atomic transition sites yet, so leave them out of this
            # first CFG-free scheduling IR.
            if len(site.canonical_events) != 1: 
                continue
            event_id = site.canonical_events[0]
            if event_id not in event_info: 
                continue

            residual = tuple(sorted(site.residual_literals))
            op = EventTransitionOperation(
                register = rule.register, 
                operation_kind = rule.operation_kind, 
                expression_key = rule.expression_key, 
                definition_block = site.block, 
                residual_guards = residual, 
            )
            operations_by_event[event_id].append(op)
            scheduled_sites += 1
            operation_forms.add((rule.register, rule.operation_kind, rule.expression_key))
            residual_forms.add(residual)
            if residual: 
                residual_sites += 1
            else: 
                event_only_sites += 1

            if rule.expression_key == "('CONST', 0)" and not residual: 
                zero_writes_by_event[event_id].add(rule.register)

    transitions: list[EventTransition] = []
    for event in canonical_events.canonical_events: 
        ops = sorted(
            operations_by_event.get(event.event_id, []), 
            key = lambda op: (
                op.definition_block, 
                op.register, 
                op.operation_kind, 
                op.expression_key, 
                op.residual_guards, 
            ), 
        )
        transitions.append(
            EventTransition(
                event_id = event.event_id, 
                gpio_bit = event.gpio_bit, 
                edge = event.edge, 
                qualifiers = tuple(event.qualifiers), 
                operations = ops, 
                event_only_operations = sum(not op.residual_guards for op in ops), 
                residual_guarded_operations = sum(bool(op.residual_guards) for op in ops), 
                unique_residual_guards = len({op.residual_guards for op in ops}), 
            )
        )

    shared_zero_write_groups = {
        event_id: sorted(registers)
        for event_id, registers in sorted(zero_writes_by_event.items())
        if len(registers) >= 2
    }

    return EventTransitionIR(
        transitions = transitions, 
        scheduled_definition_sites = scheduled_sites, 
        event_only_definition_sites = event_only_sites, 
        residual_guarded_definition_sites = residual_sites, 
        unique_operation_forms = len(operation_forms), 
        unique_residual_guard_forms = len(residual_forms), 
        shared_zero_write_groups = shared_zero_write_groups, 
        notes = [
            "Scheduling uses canonical physical events and original STATE_DEF sites, not CFG exit edges.", 
            "Basic-block IDs remain only as source provenance; they are not part of the event schedule identity.", 
            "Residual guards are preserved verbatim until they can be rewritten as hardware-state predicates.", 
            "Shared zero-write groups are detected structurally as candidate event-driven control resets; no I2C names are used.", 
        ], 
    )
