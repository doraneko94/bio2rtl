from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths, _edge_order
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .structural_netlist import StructuralNetlist


@dataclass
class EventMacroTransitionRow: 
    event_id: str
    region: str
    cpu_paths: int
    unique_full_state_outcomes: int
    unique_control_outcomes: int
    full_state_collapse_pct: float
    control_collapse_pct: float
    source_success_edges: list[list[int]]


@dataclass
class EventMacroTransitionAnalysis: 
    rows: list[EventMacroTransitionRow]
    control_registers: list[str]
    total_event_cpu_paths: int
    total_unique_full_state_outcomes: int
    total_unique_control_outcomes: int
    notes: list[str]


def _state_widths(structural: StructuralNetlist) -> dict[str, int]: 
    out: dict[str, int] = {}
    for family, node_id in structural.state_nodes.items(): 
        node = structural.nodes[node_id]
        out[family] = 32 if node.width is None else int(node.width)
    return out


def _event_success_edges(canonical_events: CanonicalEventAnalysisResult) -> dict[str, set[tuple[int, int]]]: 
    # The canonical analysis retains the detector block(s) and success target(s),
    # but their cartesian product can overstate pairings.  Reconstruct only
    # actual CFG edges later by intersecting with the region edge set.
    out: dict[str, set[tuple[int, int]]] = {}
    for event in canonical_events.canonical_events: 
        out[event.event_id] = {
            (src, dst)
            for src in event.detector_blocks
            for dst in event.success_targets
        }
    return out


def analyze_event_macro_transitions(
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
    structural: StructuralNetlist, 
    canonical_events: CanonicalEventAnalysisResult, 
    control_registers: list[str] | None = None, 
    max_paths_per_region: int = 200_000, 
) -> EventMacroTransitionAnalysis: 
    widths = _state_widths(structural)
    all_registers = sorted(widths)
    if control_registers is None: 
        # Generic default: the small persistent states that remain after the
        # event-domain data registers are treated separately.  This is only a
        # projection for diagnostics; no register name is special-cased by the
        # transformation itself.
        control_registers = sorted(r for r, w in widths.items() if w <= 2)
    else: 
        control_registers = [r for r in control_registers if r in widths]

    event_edges = _event_success_edges(canonical_events)
    rows: list[EventMacroTransitionRow] = []
    total_paths = 0
    total_full = 0
    total_control = 0

    for event in canonical_events.canonical_events: 
        region = reach.regions[event.region]
        actual_edges = {(e.source, e.target) for e in region.edges}
        success_edges = sorted(event_edges[event.event_id] & actual_edges)
        if not success_edges: 
            continue

        paths, truncated = _paths(region, max_paths_per_region)
        if truncated: 
            raise RuntimeError(
                f"{event.event_id}: path enumeration truncated at {max_paths_per_region}"
            )

        selected = [p for p in paths if set(p[1]) & set(success_edges)]
        full_outcomes: set[tuple[tuple[str, object], ...]] = set()
        control_outcomes: set[tuple[tuple[str, object], ...]] = set()
        edge_order = _edge_order(region)

        for _nodes, path_edges in selected: 
            path_set = set(path_edges)
            state = {family: Expr(kind = "STATE", state_family = family) for family in all_registers}
            for edge in edge_order: 
                if (edge.source, edge.target) not in path_set: 
                    continue
                for write in next_state_expr.by_edge.get((edge.source, edge.target), []): 
                    state[write.family] = write.expression

            full_sig = tuple((r, expr_key(state[r])) for r in all_registers)
            control_sig = tuple((r, expr_key(state[r])) for r in control_registers)
            full_outcomes.add(full_sig)
            control_outcomes.add(control_sig)

        npaths = len(selected)
        nfull = len(full_outcomes)
        ncontrol = len(control_outcomes)
        rows.append(
            EventMacroTransitionRow(
                event_id = event.event_id, 
                region = event.region, 
                cpu_paths = npaths, 
                unique_full_state_outcomes = nfull, 
                unique_control_outcomes = ncontrol, 
                full_state_collapse_pct = (0.0 if not npaths else 100.0 * (npaths - nfull) / npaths), 
                control_collapse_pct = (0.0 if not npaths else 100.0 * (npaths - ncontrol) / npaths), 
                source_success_edges = [list(x) for x in success_edges], 
            )
        )
        total_paths += npaths
        total_full += nfull
        total_control += ncontrol

    return EventMacroTransitionAnalysis(
        rows = rows, 
        control_registers = control_registers, 
        total_event_cpu_paths = total_paths, 
        total_unique_full_state_outcomes = total_full, 
        total_unique_control_outcomes = total_control, 
        notes = [
            "Each canonical event selects concrete CFG paths containing one of its recovered success edges.", 
            "For every selected CPU path, established edge-write priority semantics are evaluated symbolically to the semantic-region boundary.", 
            "Paths with identical final persistent-state expression vectors are one macro-transition outcome even if the CPU took different downstream branches.", 
            "The control projection is diagnostic only; it does not change or merge storage.", 
        ], 
    )


def write_event_macro_transition_report(result: EventMacroTransitionAnalysis, path: Path) -> None: 
    lines = [
        "EVENT MACRO-TRANSITION / CPU-PATH COLLAPSE DIAGNOSTIC", 
        "=" * 78, 
        "control projection : " + (", ".join(result.control_registers) or "-"), 
        f"event CPU paths    : {result.total_event_cpu_paths}", 
        f"unique full outcomes: {result.total_unique_full_state_outcomes}", 
        f"unique control outcomes: {result.total_unique_control_outcomes}", 
        "", 
    ]
    for row in result.rows: 
        lines += [
            f"{row.event_id} ({row.region})", 
            f"  CPU paths                  : {row.cpu_paths}", 
            f"  unique full-state outcomes : {row.unique_full_state_outcomes}", 
            f"  unique control outcomes    : {row.unique_control_outcomes}", 
            f"  full-state path collapse   : {row.full_state_collapse_pct:.2f}%", 
            f"  control path collapse      : {row.control_collapse_pct:.2f}%", 
            "  success edges              : " + ", ".join(f"BB{a:03d}->BB{b:03d}" for a, b in row.source_success_edges), 
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
