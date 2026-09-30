from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_analysis import DefinitionSiteAnalysis
from .hardware_behavior_ir import HardwareBehaviorIR, HardwareUpdateRule, expr_key
from .write_guard_analysis import WriteGuardAnalysisResult


@dataclass(frozen = True)
class ResidualGuard: 
    category: str
    text: str


@dataclass
class DedicatedOperation: 
    register: str
    operation_kind: str
    expression_key: str
    occurrence_count: int
    definition_blocks: list[int]
    canonical_event: str | None
    event_gpio_bit: int | None
    event_edge: str | None
    event_qualifiers: list[str]
    residual_guard_signatures: list[list[str]]
    schedule_kind: str  # EVENT_SCHEDULED / MIXED_EVENT / NO_EVENT
    notes: list[str] = field(default_factory = list)


@dataclass
class DedicatedHardwareIR: 
    operations: list[DedicatedOperation]
    behavioral_rules: int
    event_scheduled_rules: int
    event_scheduled_occurrences: int
    event_scheduled_definition_sites: int
    cfg_occurrences_eliminable_by_schedule: int
    mixed_event_rules: int
    no_event_rules: int


def _definition_lookup(defs: DefinitionSiteAnalysis) -> dict[tuple[str, str], list[int]]: 
    return {(f.family, f.expression_key): list(f.definition_blocks) for f in defs.forms}


def _canonical_raw_map(events: CanonicalEventAnalysisResult) -> dict[str, str]: 
    out: dict[str, str] = {}
    for event in events.canonical_events: 
        for raw in event.source_candidates: 
            out[raw] = event.event_id
    return out


def _event_info(events: CanonicalEventAnalysisResult): 
    return {e.event_id: e for e in events.canonical_events}


def build_dedicated_hardware_ir(
    behavior: HardwareBehaviorIR, 
    definitions: DefinitionSiteAnalysis, 
    canonical_events: CanonicalEventAnalysisResult, 
    write_guards: WriteGuardAnalysisResult, 
) -> DedicatedHardwareIR: 
    """Attach recovered physical events to behavioral hardware operations.

    This deliberately does *not* lower to RTL.  It is a proof-oriented IR:
    an operation is EVENT_SCHEDULED only when every edge occurrence of that
    behavioral rule is attributed to the same canonical hardware event.
    Remaining path predicates are retained as residual guards rather than
    silently discarded.
    """
    def_lookup = _definition_lookup(definitions)
    raw_to_canonical = _canonical_raw_map(canonical_events)
    event_info = _event_info(canonical_events)
    guard_by_edge = {
        (g.source_block, g.target_block): g
        for g in write_guards.write_edges
    }

    operations: list[DedicatedOperation] = []
    for rule in behavior.rules: 
        canonical_per_occurrence: list[set[str]] = []
        residual_sigs: set[tuple[str, ...]] = set()
        for edge in rule.guard_edges: 
            gd = guard_by_edge.get((edge.source_block, edge.target_block))
            if gd is None: 
                canonical_per_occurrence.append(set())
                residual_sigs.add(("<missing-write-guard>",))
                continue
            cevents = {raw_to_canonical[e] for e in gd.events if e in raw_to_canonical}
            canonical_per_occurrence.append(cevents)
            residual_sigs.add(tuple(sorted(f"{x.category}:{x.text}" for x in gd.residual_required_literals)))

        common: set[str]
        if canonical_per_occurrence: 
            common = set(canonical_per_occurrence[0])
            for s in canonical_per_occurrence[1:]: 
                common &= s
        else: 
            common = set()

        union = set().union(*canonical_per_occurrence) if canonical_per_occurrence else set()
        if len(common) == 1: 
            schedule_kind = "EVENT_SCHEDULED"
            canonical_event = next(iter(common))
        elif union: 
            schedule_kind = "MIXED_EVENT"
            canonical_event = None
        else: 
            schedule_kind = "NO_EVENT"
            canonical_event = None

        ei = event_info.get(canonical_event) if canonical_event is not None else None
        ekey = repr(expr_key(rule.expression))
        definition_blocks = def_lookup.get((rule.register, ekey), [])
        notes: list[str] = []
        if schedule_kind == "EVENT_SCHEDULED" and not definition_blocks: 
            notes.append("event schedule recovered, but original definition site was not recovered")
        if schedule_kind == "EVENT_SCHEDULED" and len(residual_sigs) == 1: 
            notes.append("all CFG occurrences share one canonical event and one residual guard signature")
        elif schedule_kind == "EVENT_SCHEDULED": 
            notes.append("all CFG occurrences share one canonical event; residual control guards still differ")

        operations.append(DedicatedOperation(
            register = rule.register, 
            operation_kind = rule.operation_kind, 
            expression_key = ekey, 
            occurrence_count = rule.occurrence_count, 
            definition_blocks = definition_blocks, 
            canonical_event = canonical_event, 
            event_gpio_bit = (ei.gpio_bit if ei else None), 
            event_edge = (ei.edge if ei else None), 
            event_qualifiers = (list(ei.qualifiers) if ei else []), 
            residual_guard_signatures = [list(x) for x in sorted(residual_sigs)], 
            schedule_kind = schedule_kind, 
            notes = notes, 
        ))

    scheduled = [x for x in operations if x.schedule_kind == "EVENT_SCHEDULED"]
    scheduled_with_defs = [x for x in scheduled if x.definition_blocks]
    return DedicatedHardwareIR(
        operations = operations, 
        behavioral_rules = len(operations), 
        event_scheduled_rules = len(scheduled), 
        event_scheduled_occurrences = sum(x.occurrence_count for x in scheduled), 
        event_scheduled_definition_sites = sum(len(x.definition_blocks) for x in scheduled_with_defs), 
        cfg_occurrences_eliminable_by_schedule = sum(
            max(0, x.occurrence_count - max(1, len(x.definition_blocks)))
            for x in scheduled_with_defs
        ), 
        mixed_event_rules = sum(x.schedule_kind == "MIXED_EVENT" for x in operations), 
        no_event_rules = sum(x.schedule_kind == "NO_EVENT" for x in operations), 
    )
