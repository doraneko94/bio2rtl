from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .event_phase_composition import EventPhaseCompositionAnalysis
from .event_transition_ir import EventTransitionIR


@dataclass
class EventPhaseProofRow: 
    register: str
    source_event: str
    target_event: str
    width: int
    source_value_after_edge: int
    intervening_event_ids: list[str]
    no_intervening_canonical_event: bool
    source_value_not_externally_observed: bool
    composable_with_symbolic_substitution: bool


@dataclass
class EventPhaseProofResult: 
    rows: list[EventPhaseProofRow]
    proven_registers: int
    proven_bits: int
    remaining_multi_event_bits_after_proven_composition: int | None
    notes: list[str]


def _qualifier_forces_gpio_value(qualifier: str, gpio_bit: int) -> int | None: 
    # Canonical qualifiers currently use a protocol-neutral textual form such
    # as GPIO@BB006[16]=1.  Only accept an exact bit/value equality.
    m = re.search(rf"GPIO@BB\d+\[{gpio_bit}\]=([01])$", qualifier)
    return int(m.group(1)) if m else None


def analyze_event_phase_proofs(
    transitions: EventTransitionIR, 
    candidates: EventPhaseCompositionAnalysis, 
    total_multi_event_bits: int | None = None, 
) -> EventPhaseProofResult: 
    event_by_id = {t.event_id: t for t in transitions.transitions}
    rows: list[EventPhaseProofRow] = []

    for candidate in candidates.candidates: 
        source = event_by_id[candidate.source_event]
        target = event_by_id[candidate.target_event]
        source_value = 0 if source.edge == "FALL" else 1

        # Identify any other recovered event that could be enabled while the
        # source GPIO remains at the level established by source_event.
        intervening: list[str] = []
        for event in transitions.transitions: 
            if event.event_id in {source.event_id, target.event_id}: 
                continue

            if event.gpio_bit == source.gpio_bit: 
                # A second same-direction edge cannot occur without the
                # complementary edge first.  The complementary edge is the
                # target event for this pair.
                if event.edge == source.edge: 
                    continue
                # Any other opposite-edge spelling would be an intervening
                # event and blocks the proof.
                intervening.append(event.event_id)
                continue

            # Events on other GPIOs are impossible in the phase only when one
            # of their qualifiers explicitly requires the source GPIO to the
            # opposite level.  Otherwise conservatively treat as intervening.
            forced = {
                _qualifier_forces_gpio_value(q, source.gpio_bit)
                for q in event.qualifiers
            }
            forced.discard(None)
            if forced and source_value not in forced: 
                continue
            intervening.append(event.event_id)

        no_intervening = not intervening
        not_observed = not candidate.source_external_dependency
        proven = no_intervening and not_observed
        rows.append(
            EventPhaseProofRow(
                register = candidate.register, 
                source_event = candidate.source_event, 
                target_event = candidate.target_event, 
                width = candidate.width, 
                source_value_after_edge = source_value, 
                intervening_event_ids = sorted(intervening), 
                no_intervening_canonical_event = no_intervening, 
                source_value_not_externally_observed = not_observed, 
                composable_with_symbolic_substitution = proven, 
            )
        )

    proven_rows = [r for r in rows if r.composable_with_symbolic_substitution]
    proven_bits = sum(r.width for r in proven_rows)
    remaining = None if total_multi_event_bits is None else max(0, total_multi_event_bits - proven_bits)
    return EventPhaseProofResult(
        rows = rows, 
        proven_registers = len(proven_rows), 
        proven_bits = proven_bits, 
        remaining_multi_event_bits_after_proven_composition = remaining, 
        notes = [
            "The proof is at recovered event semantics: source-phase storage may be removed only if its post-update value is not externally observed before the target event.", 
            "No-intervening-event proof uses only physical edge alternation and canonical event qualifiers.", 
            "The target transition must still substitute the source-phase next-value expression for reads of the eliminated intermediate state; this report proves that such substitution needs no extra event-visible storage.", 
        ], 
    )


def write_event_phase_proof_report(result: EventPhaseProofResult, path: Path) -> None: 
    lines = [
        "EVENT-PHASE COMPOSITION PROOF", 
        "=" * 78, 
        f"proven registers : {result.proven_registers}", 
        f"proven state bits: {result.proven_bits}", 
    ]
    if result.remaining_multi_event_bits_after_proven_composition is not None: 
        lines.append(
            "remaining multi-event bits after composition: "
            f"{result.remaining_multi_event_bits_after_proven_composition}"
        )
    lines.append("")
    for row in result.rows: 
        lines += [
            f"{row.register} [{row.width} bit] {row.source_event}->{row.target_event}", 
            f"  no intervening canonical event : {row.no_intervening_canonical_event}", 
            f"  source result externally used  : {not row.source_value_not_externally_observed}", 
            f"  intervening events             : {', '.join(row.intervening_event_ids) or '-'}", 
            f"  symbolic composition proof     : {'PASS' if row.composable_with_symbolic_substitution else 'FAIL'}", 
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
