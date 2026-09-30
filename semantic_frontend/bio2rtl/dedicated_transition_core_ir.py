from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths
from .definition_site_ir import DefinitionSiteIR
from .feasible_transition_core import (
    _contradiction, 
    _event_class, 
    _history_success, 
)
from .event_vector_decision_dag import _branch_trace
from .hardware_behavior_ir import expr_key
from .hardware_event_transition_analysis import _apply_path, _path_target
from .hardware_temporal_region import HardwareTemporalRegionIR
from .next_state_expr import Expr, NextStateExprIR
from .polling_phase_event_analysis import PollingPhaseEventAnalysis
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist


@dataclass(frozen = True)
class TransitionLeaf: 
    leaf_id: str
    state_updates: tuple[tuple[str, object], ...]


@dataclass(frozen = True)
class TransitionNode: 
    node_id: str
    # Ordered predicate-test result required to advance along this edge.
    # This representation preserves conditional evaluation but contains no CFG id.
    edges: tuple[tuple[str, bool, str], ...]


@dataclass(frozen = True)
class EventTransitionRoot: 
    events: tuple[str, ...]
    root_ref: str
    feasible_paths: int


@dataclass
class DedicatedTransitionCoreIR: 
    event_roots: list[EventTransitionRoot]
    nodes: list[TransitionNode]
    leaves: list[TransitionLeaf]
    persistent_states: list[str]
    source_syntactic_paths: int
    feasible_paths: int
    infeasible_paths_pruned: int
    cfg_identity_tokens: int
    normalization_residual_predicates: int
    unresolved_value_mux_predicates: int
    notes: list[str]


class _Trie: 
    __slots__ = ("edges", "outcomes")
    def __init__(self): 
        self.edges = {}
        self.outcomes = set()


def _reduce(root, node_intern, leaf_intern): 
    memo = {}

    def leaf_ref(outcome): 
        if outcome not in leaf_intern: 
            leaf_intern[outcome] = f"L{len(leaf_intern):03d}"
        return leaf_intern[outcome]

    def rec(n): 
        key_mem = id(n)
        if key_mem in memo: 
            return memo[key_mem]
        if not n.edges: 
            if len(n.outcomes) != 1: 
                raise RuntimeError(f"dedicated transition leaf conflict: {len(n.outcomes)} outcomes")
            r = leaf_ref(next(iter(n.outcomes)))
            memo[key_mem] = r
            return r
        kids = []
        for (pred, truth), ch in sorted(n.edges.items(), key = lambda kv: repr(kv[0])): 
            kids.append((pred, bool(truth), rec(ch)))
        key = tuple(kids)
        if key not in node_intern: 
            node_intern[key] = f"N{len(node_intern):03d}"
        r = node_intern[key]
        memo[key_mem] = r
        return r
    return rec(root)


def build_dedicated_transition_core_ir(
    reach: SemanticReachResult, 
    next_state: NextStateExprIR, 
    structural: StructuralNetlist, 
    history_events: CanonicalEventAnalysisResult, 
    polling: PollingPhaseEventAnalysis, 
    temporal: HardwareTemporalRegionIR, 
    ssa: SemanticSSAResult, 
    def_ir: DefinitionSiteIR, 
    region_name: str = "SAMPLE_BB006", 
) -> DedicatedTransitionCoreIR: 
    region = reach.regions[region_name]
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = "STATE", state_family = r) for r in regs}
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError("dedicated transition core path enumeration truncated")

    transparent = set()
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)
    transparent_cache = {}
    for b in transparent: 
        rn = f"SAMPLE_BB{b:03d}"
        ps, t = _paths(reach.regions[rn], 200000)
        if t: 
            raise RuntimeError(f"{rn}: dedicated transition continuation truncated")
        transparent_cache[b] = ps

    history_success = _history_success(history_events, region)
    grouped = {}
    fused_total = 0

    for nodes, edges in paths: 
        seq1, env1 = _branch_trace(region, nodes, edges, ssa, {b: a for a, b in edges}, def_ir)
        state1 = _apply_path(ident, region, edges, next_state)
        target1 = _path_target(region, nodes, edges)
        evclass = _event_class(region, edges, ssa, history_success, polling)
        conts = []
        if target1 in transparent: 
            r2 = reach.regions[f"SAMPLE_BB{target1:03d}"]
            for n2, e2 in transparent_cache[target1]: 
                # Preserve predecessor history from the event-root region when
                # tracing a fused transparent sample region.  Event-local PHIs
                # in the continuation may still refer to merge blocks reached
                # before the transparent GPIO_READ; dropping that predecessor
                # map turns a path-specific SSA value into a spurious mux.
                combined_pred = {b: a for a, b in edges}
                combined_pred.update({b: a for a, b in e2})
                seq2, _ = _branch_trace(r2, n2, e2, ssa, combined_pred, def_ir, env1)
                state2 = _apply_path(state1, r2, e2, next_state)
                conts.append((seq1 + seq2, state2))
        else: 
            conts.append((seq1, state1))
        for seq, state in conts: 
            fused_total += 1
            if _contradiction(seq): 
                continue
            if not evclass: 
                # After feasibility pruning a steady-state dedicated transition must
                # consume a recovered hardware event.  Anything else is a model gap.
                raise RuntimeError("feasible event-free path remains in dedicated transition core")
            out = tuple((r, expr_key(state[r])) for r in regs)
            grouped.setdefault(evclass, []).append((seq, out))

    node_intern = {}
    leaf_intern = {}
    roots = []
    for ev, traces in sorted(grouped.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        vals, presence = {}, {}
        for seq, _ in traces: 
            seen = set()
            for p, v in seq: 
                vals.setdefault(p, set()).add(v)
                seen.add(p)
            for p in seen: 
                presence[p] = presence.get(p, 0) + 1
        constants = {p for p, vs in vals.items() if len(vs) == 1 and presence.get(p, 0) == len(traces)}
        root = _Trie()
        for seq, out in traces: 
            cur = root
            for p, v in [(p, v) for p, v in seq if p not in constants]: 
                item = (p, bool(v))
                if item not in cur.edges: 
                    cur.edges[item] = _Trie()
                cur = cur.edges[item]
            cur.outcomes.add(out)
        root_ref = _reduce(root, node_intern, leaf_intern)
        roots.append(EventTransitionRoot(events = ev, root_ref = root_ref, feasible_paths = len(traces)))

    # Materialize interned DAG in stable ID order.
    id_to_node = sorted(((nid, key) for key, nid in node_intern.items()), key = lambda x: int(x[0][1:]))
    nodes = [TransitionNode(node_id = nid, edges = tuple((p, t, ch) for p, t, ch in key)) for nid, key in id_to_node]
    id_to_leaf = sorted(((lid, out) for out, lid in leaf_intern.items()), key = lambda x: int(x[0][1:]))
    leaves = [TransitionLeaf(leaf_id = lid, state_updates = tuple(out)) for lid, out in id_to_leaf]

    cfg_tokens = 0
    norm_residuals = set()
    unresolved_muxes = set()
    for n in nodes: 
        for p, _t, _ch in n.edges: 
            cfg_tokens += int("BB" in p or "reach_" in p or "edge_" in p)
            if "NORMALIZATION_RESIDUAL" in p: 
                norm_residuals.add(p)
            if "UNRESOLVED_VALUE_MUX" in p: 
                unresolved_muxes.add(p)

    feasible = sum(r.feasible_paths for r in roots)
    return DedicatedTransitionCoreIR(
        event_roots = roots, 
        nodes = nodes, 
        leaves = leaves, 
        persistent_states = regs, 
        source_syntactic_paths = fused_total, 
        feasible_paths = feasible, 
        infeasible_paths_pruned = fused_total-feasible, 
        cfg_identity_tokens = cfg_tokens, 
        normalization_residual_predicates = len(norm_residuals), 
        unresolved_value_mux_predicates = len(unresolved_muxes), 
        notes = [
            "This IR is indexed by recovered hardware-event classes, not CPU basic blocks.", 
            "All transition predicates are normalized hardware expressions evaluated in order.", 
            "Infeasible syntactic CFG paths are removed before IR construction.", 
            "Leaves contain persistent physical-state next expressions; GPIO/semantic observable completion is the next extension before RTL emission.", 
        ], 
    )


def write_dedicated_transition_core_ir(ir: DedicatedTransitionCoreIR, path: Path): 
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text(json.dumps(asdict(ir), indent = 2, sort_keys = True, default = repr) + "\n")
