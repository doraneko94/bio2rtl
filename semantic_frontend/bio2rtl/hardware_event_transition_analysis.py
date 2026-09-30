from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths, _edge_order
from .hardware_behavior_ir import expr_key
from .hardware_temporal_region import HardwareTemporalRegionIR
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import ReachRegion, SemanticReachResult
from .structural_netlist import StructuralNetlist


@dataclass(frozen = True)
class HardwareEventTransitionRow: 
    event_id: str
    software_event_paths: int
    fused_execution_paths: int
    unique_physical_outcomes: int
    path_collapse_pct: float
    transparent_samples_crossed: tuple[int, ...]
    final_sample_counts: tuple[tuple[int | None, int], ...]
    reaches_event_wait_state: bool


@dataclass
class HardwareEventTransitionAnalysis: 
    rows: list[HardwareEventTransitionRow]
    total_software_event_paths: int
    total_fused_execution_paths: int
    total_unique_physical_outcomes: int
    all_transitions_reach_event_wait_state: bool
    notes: list[str]


def _state_widths(structural: StructuralNetlist) -> dict[str, int]: 
    out: dict[str, int] = {}
    for family, node_id in structural.state_nodes.items(): 
        node = structural.nodes[node_id]
        out[family] = 32 if node.width is None else int(node.width)
    return out


def _subst(expr: Expr, state: dict[str, Expr]) -> Expr: 
    if expr.kind == "STATE" and expr.state_family in state: 
        return state[expr.state_family]
    if expr.kind == "OP": 
        return Expr(
            kind = "OP", 
            operation = expr.operation, 
            args = tuple(_subst(a, state) for a in expr.args), 
        )
    return expr


def _apply_path(
    state_in: dict[str, Expr], 
    region: ReachRegion, 
    path_edges: tuple[tuple[int, int], ...] | list[tuple[int, int]], 
    next_state_expr: NextStateExprIR, 
) -> dict[str, Expr]: 
    state = dict(state_in)
    selected = set(path_edges)
    for edge in _edge_order(region): 
        ek = (edge.source, edge.target)
        if ek not in selected: 
            continue
        # Edge writes are simultaneous relative to the pre-edge state.
        before = dict(state)
        for write in next_state_expr.by_edge.get(ek, []): 
            state[write.family] = _subst(write.expression, before)
    return state


def _path_target(
    region: ReachRegion, 
    path_nodes: tuple[int, ...] | list[int], 
    path_edges: tuple[tuple[int, int], ...] | list[tuple[int, int]], 
) -> int | None: 
    exits = set(region.exit_samples)
    for _src, dst in reversed(path_edges): 
        if dst in exits: 
            return dst
    # _paths() intentionally refuses to revisit the region root.  A polling
    # path that returns to the starting GPIO_READ therefore ends at the block
    # immediately before the root without recording the back-edge.  Recover
    # that semantic exit here instead of misclassifying it as a terminal leaf.
    if path_nodes and region.start_sample is not None: 
        last = path_nodes[-1]
        if any(e.source == last and e.target == region.start_sample for e in region.edges): 
            return region.start_sample
    return None


def _success_edges(canonical_events: CanonicalEventAnalysisResult, event_id: str, region: ReachRegion) -> set[tuple[int, int]]: 
    event = next(e for e in canonical_events.canonical_events if e.event_id == event_id)
    actual = {(e.source, e.target) for e in region.edges}
    return {
        (src, dst)
        for src in event.detector_blocks
        for dst in event.success_targets
        if (src, dst) in actual
    }


def analyze_hardware_event_transitions(
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
    structural: StructuralNetlist, 
    canonical_events: CanonicalEventAnalysisResult, 
    temporal: HardwareTemporalRegionIR, 
    max_paths_per_region: int = 200_000, 
) -> HardwareEventTransitionAnalysis: 
    regs = sorted(_state_widths(structural))
    identity = {r: Expr(kind = "STATE", state_family = r) for r in regs}
    temporal_by_event = {r.event_id: r for r in temporal.event_regions}

    path_cache: dict[str, list[tuple[tuple[int, ...], tuple[tuple[int, int], ...]]]] = {}
    for rn, region in reach.regions.items(): 
        paths, truncated = _paths(region, max_paths_per_region)
        if truncated: 
            raise RuntimeError(f"{rn}: path enumeration truncated at {max_paths_per_region}")
        path_cache[rn] = [(tuple(nodes), tuple(edges)) for nodes, edges in paths]

    rows: list[HardwareEventTransitionRow] = []
    total_sw = total_fused = total_out = 0
    all_wait = True

    for event in canonical_events.canonical_events: 
        region = reach.regions[event.region]
        success = _success_edges(canonical_events, event.event_id, region)
        selected = [p for p in path_cache[event.region] if set(p[1]) & success]
        temporal_row = temporal_by_event[event.event_id]
        root_sample = region.start_sample

        fused_results: list[tuple[dict[str, Expr], int | None]] = []
        for nodes, edges in selected: 
            state1 = _apply_path(identity, region, edges, next_state_expr)
            target1 = _path_target(region, nodes, edges)
            if target1 is None or target1 == root_sample: 
                fused_results.append((state1, target1))
                continue

            if target1 in temporal_row.fused_transparent_samples: 
                r2name = f"SAMPLE_BB{target1:03d}"
                r2 = reach.regions[r2name]
                for nodes2, edges2 in path_cache[r2name]: 
                    state2 = _apply_path(state1, r2, edges2, next_state_expr)
                    target2 = _path_target(r2, nodes2, edges2)
                    fused_results.append((state2, target2))
            else: 
                fused_results.append((state1, target1))

        outcomes = {
            tuple((r, expr_key(state[r])) for r in regs)
            for state, _target in fused_results
        }
        target_counts: dict[int | None, int] = {}
        for _state, target in fused_results: 
            target_counts[target] = target_counts.get(target, 0) + 1
        reaches_wait = all(target == root_sample for _state, target in fused_results)
        nsw = len(selected)
        nfused = len(fused_results)
        nout = len(outcomes)
        rows.append(
            HardwareEventTransitionRow(
                event_id = event.event_id, 
                software_event_paths = nsw, 
                fused_execution_paths = nfused, 
                unique_physical_outcomes = nout, 
                path_collapse_pct = (0.0 if not nfused else 100.0 * (nfused - nout) / nfused), 
                transparent_samples_crossed = temporal_row.fused_transparent_samples, 
                final_sample_counts = tuple(sorted(target_counts.items(), key = lambda x: (-1 if x[0] is None else x[0]))), 
                reaches_event_wait_state = reaches_wait, 
            )
        )
        total_sw += nsw
        total_fused += nfused
        total_out += nout
        all_wait = all_wait and reaches_wait

    return HardwareEventTransitionAnalysis(
        rows = rows, 
        total_software_event_paths = total_sw, 
        total_fused_execution_paths = total_fused, 
        total_unique_physical_outcomes = total_out, 
        all_transitions_reach_event_wait_state = all_wait, 
        notes = [
            "Each transition starts at a recovered canonical physical event, not at GPIO_READ.", 
            "Proven transparent input-sample barriers are symbolically fused before the transition outcome is measured.", 
            "Persistent-state expressions are composed across the fused barrier by substituting the incoming event result into the following region.", 
            "The resulting transition identity contains no reach/edge signal names; CPU path identity is used only during proof enumeration.", 
            "A full CFG-free RTL backend still requires residual path predicates to be rewritten as hardware-state/input predicates before outcome selection can be emitted.", 
        ], 
    )


def write_hardware_event_transition_report(result: HardwareEventTransitionAnalysis, path: Path) -> None: 
    lines = [
        "HARDWARE-EVENT MACRO TRANSITION / FUSED SOFTWARE-TIME DIAGNOSTIC", 
        "=" * 78, 
        f"software event paths          : {result.total_software_event_paths}", 
        f"fused execution paths         : {result.total_fused_execution_paths}", 
        f"unique physical outcomes      : {result.total_unique_physical_outcomes}", 
        f"all return to event wait state: {result.all_transitions_reach_event_wait_state}", 
        "", 
    ]
    for row in result.rows: 
        lines += [
            row.event_id, 
            f"  software event paths       : {row.software_event_paths}", 
            f"  fused execution paths      : {row.fused_execution_paths}", 
            f"  unique physical outcomes   : {row.unique_physical_outcomes}", 
            f"  path collapse              : {row.path_collapse_pct:.2f}%", 
            "  transparent samples fused  : " + (", ".join(f"BB{x:03d}" for x in row.transparent_samples_crossed) or "-"), 
            "  final samples               : " + ", ".join(("NONE" if sample is None else f"BB{sample:03d}") + f"={count}" for sample, count in row.final_sample_counts), 
            f"  reaches event wait state   : {row.reaches_event_wait_state}", 
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
