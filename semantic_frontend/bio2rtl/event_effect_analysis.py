from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_guard_analysis import _required_for_block
from .definition_schedule_analysis import _edge_dominates_block
from .edge_event_analysis import EdgeEventAnalysisResult
from .gpio_effect import GPIOEffectResult
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .write_guard_analysis import _event_constituent_blocks
from .control_expr import ControlExpr


@dataclass
class EventGPIOEffect: 
    event_id: str
    block: int
    kind: str
    expression_state_reads: list[str]
    residual_guard_state_reads: list[str]
    residual_guard_literals: list[str]


@dataclass
class EventEffectAnalysis: 
    effects: list[EventGPIOEffect]
    event_counts: dict[str, int]
    event_state_dependencies: dict[str, list[str]]
    notes: list[str]


def _control_state_reads(expr: ControlExpr) -> set[str]: 
    out: set[str] = set()
    if expr.kind == "STATE" and expr.state_family is not None: 
        out.add(expr.state_family)
    for arg in expr.args: 
        out |= _control_state_reads(arg)
    for _, arg in expr.phi_inputs: 
        out |= _control_state_reads(arg)
    return out


def _literal_state_reads(text: str) -> set[str]: 
    return set(re.findall(r"STATE\((stack_[A-Za-z0-9_]+)\)", text))


def analyze_event_gpio_effects(
    reach: SemanticReachResult, 
    semantic_ssa: SemanticSSAResult, 
    edge_events: EdgeEventAnalysisResult, 
    canonical_events: CanonicalEventAnalysisResult, 
    gpio_effects: GPIOEffectResult, 
) -> EventEffectAnalysis: 
    raw_to_canon = {
        raw: ce.event_id
        for ce in canonical_events.canonical_events
        for raw in ce.source_candidates
    }
    raw_by_region = defaultdict(list)
    for event in edge_events.event_candidates: 
        raw_by_region[event.region].append(event)

    regions_by_block = defaultdict(list)
    for region_name, region in reach.regions.items(): 
        for block in region.blocks: 
            regions_by_block[block].append(region_name)

    rows: list[EventGPIOEffect] = []
    deps: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)

    for effect in gpio_effects.effects: 
        candidate_events: set[str] = set()
        residual_literals: list[str] = []
        chosen_region = None
        covered_blocks: set[int] = set()

        for region_name in regions_by_block.get(effect.block_id, []): 
            region = reach.regions[region_name]
            local_events: set[str] = set()
            local_covered: set[int] = set()
            for raw in raw_by_region.get(region_name, []): 
                if _edge_dominates_block(
                    region, 
                    (raw.detector_block, raw.success_target), 
                    effect.block_id, 
                ): 
                    event_id = raw_to_canon.get(raw.event_id)
                    if event_id is not None: 
                        local_events.add(event_id)
                        local_covered |= _event_constituent_blocks(raw)
            if local_events: 
                candidate_events = local_events
                covered_blocks = local_covered
                chosen_region = region
                break

        # Only attach effects proven to occur under exactly one canonical event.
        if len(candidate_events) != 1 or chosen_region is None: 
            continue

        event_id = next(iter(candidate_events))
        required = _required_for_block(chosen_region, effect.block_id, semantic_ssa)
        residual = [x for x in required if x.block not in covered_blocks]
        residual_literals = [f"{x.category}:{x.text}" for x in residual]

        expr_reads = sorted(_control_state_reads(effect.expression))
        guard_reads: set[str] = set()
        for literal in residual_literals: 
            guard_reads |= _literal_state_reads(literal)

        rows.append(
            EventGPIOEffect(
                event_id = event_id, 
                block = effect.block_id, 
                kind = effect.kind, 
                expression_state_reads = expr_reads, 
                residual_guard_state_reads = sorted(guard_reads), 
                residual_guard_literals = residual_literals, 
            )
        )
        counts[event_id] += 1
        deps[event_id] |= set(expr_reads) | guard_reads

    return EventEffectAnalysis(
        effects = rows, 
        event_counts = dict(sorted(counts.items())), 
        event_state_dependencies = {k: sorted(v) for k, v in sorted(deps.items())}, 
        notes = [
            "Only GPIO effects dominated by exactly one recovered canonical event are included.", 
            "Dependencies include both effect data expressions and residual control guards.", 
            "A state absent from an event's dependency set is not directly required to determine that event's external GPIO writes under this analysis.", 
        ], 
    )


def write_event_effect_report(result: EventEffectAnalysis, path: Path) -> None: 
    lines = ["EVENT-SCHEDULED GPIO EFFECT DIAGNOSTIC", "=" * 78]
    for event_id in sorted(set(result.event_counts) | set(result.event_state_dependencies)): 
        lines += [
            f"{event_id}", 
            f"  effects            : {result.event_counts.get(event_id, 0)}", 
            f"  state dependencies : {', '.join(result.event_state_dependencies.get(event_id, [])) or '-'}", 
        ]
        for effect in [e for e in result.effects if e.event_id == event_id]: 
            lines += [
                f"    BB{effect.block:03d} {effect.kind}", 
                f"      expr states : {', '.join(effect.expression_state_reads) or '-'}", 
                f"      guard states: {', '.join(effect.residual_guard_state_reads) or '-'}", 
            ]
        lines.append("")
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
