from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .event_domain_analysis import EventDomainAnalysis
from .event_effect_analysis import EventEffectAnalysis
from .event_transition_ir import EventTransitionIR


@dataclass
class PhaseCompositionCandidate: 
    register: str
    source_event: str
    target_event: str
    gpio_bit: int
    source_edge: str
    target_edge: str
    source_write_count: int
    target_write_count: int
    width: int
    source_external_dependency: bool
    reason: str


@dataclass
class EventPhaseCompositionAnalysis: 
    candidates: list[PhaseCompositionCandidate]
    complementary_event_pairs: list[list[str]]
    candidate_registers: int
    candidate_bits: int
    notes: list[str]


def analyze_event_phase_composition(
    transitions: EventTransitionIR, 
    domains: EventDomainAnalysis, 
    effects: EventEffectAnalysis, 
) -> EventPhaseCompositionAnalysis: 
    event_by_id = {t.event_id: t for t in transitions.transitions}
    effect_deps = {k: set(v) for k, v in effects.event_state_dependencies.items()}

    # Complementary physical edges of the same external GPIO are potential
    # adjacent phases.  Qualified/reset-like events are deliberately excluded.
    pairs: list[tuple[str, str]] = []
    events = list(event_by_id.values())
    for fall in events: 
        if fall.edge != "FALL" or fall.qualifiers: 
            continue
        for rise in events: 
            if rise.edge != "RISE" or rise.qualifiers: 
                continue
            if rise.gpio_bit == fall.gpio_bit: 
                pairs.append((fall.event_id, rise.event_id))

    candidates: list[PhaseCompositionCandidate] = []
    for row in domains.registers: 
        if row.domain_kind != "MULTI_EVENT": 
            continue
        for source_event, target_event in pairs: 
            if source_event not in row.active_events or target_event not in row.active_events: 
                continue
            observable = row.register in effect_deps.get(source_event, set())
            if observable: 
                continue
            candidates.append(
                PhaseCompositionCandidate(
                    register = row.register, 
                    source_event = source_event, 
                    target_event = target_event, 
                    gpio_bit = event_by_id[source_event].gpio_bit, 
                    source_edge = event_by_id[source_event].edge, 
                    target_edge = event_by_id[target_event].edge, 
                    source_write_count = row.event_operation_counts.get(source_event, 0), 
                    target_write_count = row.event_operation_counts.get(target_event, 0), 
                    width = row.width, 
                    source_external_dependency = False, 
                    reason = (
                        "source-phase updates are not required by any externally visible GPIO effect "
                        "scheduled in that phase; compose their symbolic result into the target phase"
                    ), 
                )
            )

    return EventPhaseCompositionAnalysis(
        candidates = candidates, 
        complementary_event_pairs = [list(p) for p in pairs], 
        candidate_registers = len({c.register for c in candidates}), 
        candidate_bits = sum(c.width for c in candidates), 
        notes = [
            "This is a conservative retiming candidate analysis, not yet an equivalence proof.", 
            "A candidate must still pass symbolic transition composition before source-phase storage is removed.", 
            "The criterion is protocol-neutral: complementary edges on one GPIO plus absence from same-phase external-effect dependencies.", 
        ], 
    )


def write_event_phase_composition_report(result: EventPhaseCompositionAnalysis, path: Path) -> None: 
    lines = [
        "EVENT-PHASE COMPOSITION DIAGNOSTIC", 
        "=" * 78, 
        "complementary event pairs : " + (
            ", ".join(f"{a}->{b}" for a, b in result.complementary_event_pairs) or "-"
        ), 
        f"candidate registers        : {result.candidate_registers}", 
        f"candidate state bits       : {result.candidate_bits}", 
        "", 
    ]
    for c in result.candidates: 
        lines += [
            f"{c.register} [{c.width} bit]: {c.source_event} {c.source_edge} -> {c.target_event} {c.target_edge}", 
            f"  source writes : {c.source_write_count}", 
            f"  target writes : {c.target_write_count}", 
            f"  externally observed in source phase: {c.source_external_dependency}", 
            f"  action        : {c.reason}", 
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
