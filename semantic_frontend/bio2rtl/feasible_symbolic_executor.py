from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import json

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_ir import DefinitionSiteIR
from .event_vector_decision_dag import _cond_with_env, _fold_pred, _lower_expr_with_env
from .event_vector_sequence_dag import TNode, _intern_reduce
from .event_vector_decision_dag import _semantic_next_outcome
from .gpio_effect import GPIOEffectResult
from .feasible_transition_core import _contradiction, _event_class, _history_success
from .hardware_behavior_ir import expr_key
from .hardware_temporal_region import HardwareTemporalRegionIR
from .polling_phase_event_analysis import PollingPhaseEventAnalysis
from .semantic_reach import SemanticReachResult, ReachRegion, select_primary_sample_region
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist
from .logical_state import stack_state_base


@dataclass
class FeasibleSymbolicExecutorResult: 
    completed_transitions: int
    pruned_constant_edges: int
    pruned_constraint_edges: int
    explored_symbolic_states: int
    event_classes: int
    physical_outcomes: int
    complete_outcomes: int
    ordered_decision_nodes: int
    ordered_leaf_nodes: int
    sequence_conflicts: int
    cfg_identity_tokens: int
    normalization_residuals: int
    unresolved_value_muxes: int
    unresolved_mux_examples: list[str]
    unresolved_mux_blocks: dict[str, int]
    transition_phi_states: int
    transition_phi_state_rows: list[dict]
    transition_rows: list[dict]
    rows: list[dict]
    notes: list[str]


def _region_edges(region: ReachRegion): 
    out = {}
    for e in region.edges: 
        out.setdefault(e.source, []).append(e)
    return out


def _execute_writes(block: int, env: dict, def_ir: DefinitionSiteIR): 
    """Record definition-site next values without re-substituting prior writes.

    ``DefinitionSiteIR`` expressions are already SSA-expanded by ``lower_value``:
    a use of a state version defined earlier in the same event has already been
    replaced by that version's defining expression.  The remaining ``STATE``
    leaves therefore denote event-boundary persistent state.  Substituting the
    accumulating ``env`` into those leaves applies the same local definition a
    second time (for example, a shift-register update becomes two shifts).

    ``env`` is still accumulated so the final physical outcome contains the
    latest write to each family, but every RHS is evaluated relative to the
    event-boundary state represented by the SSA-expanded expression itself.
    """
    env = dict(env)
    for w in sorted(def_ir.by_block.get(block, []), key = lambda x: x.order): 
        env[w.family] = _fold_pred(_lower_expr_with_env(w.expression, {}))
    return env


def _state_outcome(regs, env): 
    out = []
    for r in regs: 
        v = env.get(r, ("STATE", r))
        out.append((r, v))
    return tuple(out)


def analyze_feasible_symbolic_executor(
    reach: SemanticReachResult, 
    structural: StructuralNetlist, 
    history_events: CanonicalEventAnalysisResult, 
    polling: PollingPhaseEventAnalysis, 
    temporal: HardwareTemporalRegionIR, 
    ssa: SemanticSSAResult, 
    def_ir: DefinitionSiteIR, 
    gpio_effects: GPIOEffectResult | None = None, 
    root_region: str | None = None, 
    max_states: int = 200000, 
) -> FeasibleSymbolicExecutorResult: 
    """Symbolically execute one hardware-event macro-step without enumerating
    the Cartesian product of CFG branch choices first.

    State definitions are executed at their definition sites. A branch whose
    transition-local predicate folds to a constant follows only the feasible
    successor. A symbolic branch forks only after constraint consistency is
    checked. Transparent sample barriers are fused into the same macro-step.
    """
    if root_region is None: 
        sampled = [name for name, region in reach.regions.items() if region.start_sample is not None]
        root_region = select_primary_sample_region(reach) if sampled else "ENTRY"
    regs = sorted(structural.state_nodes)
    root = reach.regions[root_region]
    root_sample = root.start_sample

    # Startup-only quiescent programs have no sample root.  Their ENTRY reach
    # graph is nevertheless an acyclic macro-step ending in a proved terminal.
    # Recover its unique source rather than inventing a polling/sample event.
    if root_sample is None: 
        targets = {e.target for e in root.edges}
        sources = sorted(set(root.blocks) - targets)
        if len(sources) != 1: 
            raise RuntimeError(
                f"startup-only semantic region requires one reach source, got {sources}"
            )
        root_start = sources[0]
    else: 
        root_start = root_sample

    transparent = set()
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)

    edge_maps = {name: _region_edges(r) for name, r in reach.regions.items()}
    history_success = _history_success(history_events, root)

    # tuple(region_name, block, predecessor-map, env, constraints, traversed-edges)
    work = [(root_region, root_start, {}, {}, [], [])]
    completed = []
    unresolved_blocks = {}
    transition_phi_states = {}
    # Recover physical-family provenance lost when a versioned stack SSA PHI
    # was converted to a generic SemanticExpr(PHI). Such PHIs are not new
    # hardware state; they are transition-local versions of the same register.
    phi_family_map = {}
    for value_name, expr in ssa.expressions.items(): 
        family = stack_state_base(value_name)
        if family in structural.state_nodes and getattr(expr, "kind", None) == "PHI": 
            phi_family_map.setdefault(expr, family)
    seen_count = 0
    pruned_const = 0
    pruned_constraints = 0

    while work: 
        if seen_count >= max_states: 
            raise RuntimeError("feasible symbolic executor state limit reached")
        seen_count += 1
        region_name, block, pred_map, env, constraints, traversed = work.pop()
        region = reach.regions[region_name]
        env2 = _execute_writes(block, env, def_ir)
        outgoing = edge_maps[region_name].get(block, [])

        if not outgoing: 
            # A proof-classified quiescent terminal completes the current
            # macro-step.  This preserves all state/GPIO side effects on the
            # path into the terminal without turning the CPU spin-loop into a
            # hardware clock.
            if block in set(region.terminal_blocks): 
                ev = _event_class(root, traversed, ssa, history_success, polling)
                completed.append(
                    (ev, list(constraints), _state_outcome(regs, env2), tuple(traversed), True)
                )
            continue

        cond = ssa.branch_conditions.get(block)
        branch_pred = None
        if cond is not None and len(outgoing) >= 2: 
            stats = {"phi": 0, "sem": 0}
            # Transition semantic SSA already follows event-local state SSA
            # definitions.  STATE leaves here are boundary live-ins, not the
            # accumulating next-state environment.  Re-substituting env2 would
            # apply an event-local definition twice.
            branch_pred = _fold_pred(_cond_with_env(cond, True, pred_map, stats, {}, transition_phi_states, phi_family_map))
            if "UNRESOLVED_VALUE_MUX" in repr(branch_pred): 
                unresolved_blocks[f"BB{block:03d}"] = unresolved_blocks.get(f"BB{block:03d}", 0) + 1

        for e in outgoing: 
            new_constraints = list(constraints)
            if e.kind in ("TRUE", "FALSE") and branch_pred is not None: 
                want = e.kind == "TRUE"
                if branch_pred == ("BOOL", True): 
                    if not want: 
                        pruned_const += 1
                        continue
                elif branch_pred == ("BOOL", False): 
                    if want: 
                        pruned_const += 1
                        continue
                else: 
                    key = repr(branch_pred)
                    new_constraints.append((key, want))
                    if _contradiction(new_constraints): 
                        pruned_constraints += 1
                        continue

            new_pred = dict(pred_map)
            new_pred[e.target] = e.source
            new_edges = traversed + [(e.source, e.target)]

            if root_sample is not None and e.target == root_sample: 
                # Any traversed edge that returns to the sample root completes
                # one hardware macro-step, including a direct sample-root
                # self-loop.  Excluding ``block == root_sample`` makes ordinary
                # polling/history loops accumulate the same constraint forever.
                ev = _event_class(root, new_edges, ssa, history_success, polling)
                completed.append((ev, new_constraints, _state_outcome(regs, env2), tuple(new_edges), False))
                continue

            if e.target in transparent: 
                nr = f"SAMPLE_BB{e.target:03d}"
                work.append((nr, e.target, new_pred, env2, new_constraints, new_edges))
                continue

            # Remain in the current semantic region. Sample exits other than the
            # fused transparent set are macro-step boundaries and are not silently
            # traversed.
            if e.target in region.exit_samples and (root_sample is None or e.target != root_sample): 
                ev = _event_class(root, new_edges, ssa, history_success, polling)
                completed.append((ev, new_constraints, _state_outcome(regs, env2), tuple(new_edges), False))
                continue

            work.append((region_name, e.target, new_pred, env2, new_constraints, new_edges))

    grouped = {}
    cfg = 0
    residual = set()
    muxes = set()
    def effect_trace(edges): 
        if gpio_effects is None: 
            return ()
        blocks = []
        for a, b in edges: 
            if not blocks or blocks[-1] != a: 
                blocks.append(a)
            if not blocks or blocks[-1] != b: 
                blocks.append(b)
        trace = []
        for block in blocks: 
            for eff in gpio_effects.by_block.get(block, []): 
                trace.append((eff.kind, repr(eff.expression)))
        return tuple(trace)

    for ev, cs, out, edges, terminal_completion in completed: 
        sem = _semantic_next_outcome(ssa, edges)
        complete = (out, sem, effect_trace(edges))
        grouped.setdefault(ev, []).append((cs, out, complete, edges))
        for p, _ in cs: 
            cfg += int("BB" in p or "reach_" in p or "edge_" in p)
            if "NORMALIZATION_RESIDUAL" in p: 
                residual.add(p)
            if "UNRESOLVED_VALUE_MUX" in p: 
                muxes.add(p)

    transition_rows = []
    terminal_blocks_all = {b for region in reach.regions.values() for b in getattr(region, "terminal_blocks", [])}
    for tid, (ev, cs, out, edges, terminal_completion) in enumerate(completed): 
        sem = _semantic_next_outcome(ssa, edges)
        is_terminal = bool(terminal_completion)
        transition_rows.append({
            "transition_id": f"T{tid:03d}", 
            "terminal_completion": is_terminal, 
            "events": list(ev), 
            "constraints": [
                {"predicate": pred, "polarity": bool(polarity)}
                for pred, polarity in cs
            ], 
            "physical_outcome": [
                {"state": family, "expr": expr}
                for family, expr in out
            ], 
            "semantic_outcome": [
                {"state": name, "expr": expr}
                for name, expr in sem
            ], 
            "gpio_effects": [
                {"kind": kind, "expression": expression}
                for kind, expression in effect_trace(edges)
            ], 
        })

    rows = []
    all_out = set()
    all_complete = set()
    total_nodes = total_leaves = total_conflicts = 0
    for ev, items in sorted(grouped.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        outs = {x[1] for x in items}
        complete_outs = {x[2] for x in items}
        all_out.update(outs)
        all_complete.update(complete_outs)
        # Build an ordered hardware decision DAG directly from feasible traces.
        vals, presence = {}, {}
        for cs, _out, _complete, _edges in items: 
            seen = set()
            for p, v in cs: 
                vals.setdefault(p, set()).add(v)
                seen.add(p)
            for p in seen: 
                presence[p] = presence.get(p, 0) + 1
        constants = {p for p, vs in vals.items() if len(vs) == 1 and presence.get(p, 0) == len(items)}
        root = TNode()
        for cs, _out, complete, _edges in items: 
            out = complete
            cur = root
            for item in [(p, v) for p, v in cs if p not in constants]: 
                if item not in cur.edges: 
                    cur.edges[item] = TNode()
                cur = cur.edges[item]
            cur.outs.add(out)
        memo = {}
        intern = {}
        leaves = {}
        conf = [0]
        _intern_reduce(root, memo, intern, leaves, conf)
        total_nodes += len(intern)
        total_leaves += len(leaves)
        total_conflicts += conf[0]
        identity = tuple((r, ("STATE", r)) for r in regs)
        rows.append({
            "events": list(ev), 
            "transitions": len(items), 
            "physical_outcomes": len(outs), 
            "complete_outcomes": len(complete_outs), 
            "max_predicates": max((len(x[0]) for x in items), default = 0), 
            "decision_nodes": len(intern), 
            "leaves": len(leaves), 
            "conflicts": conf[0], 
            "event_implied_predicates": len(constants), 
            "all_hold": all(out == identity for _cs, out, _complete, _edges in items), 
        })

    return FeasibleSymbolicExecutorResult(
        completed_transitions = len(completed), 
        pruned_constant_edges = pruned_const, 
        pruned_constraint_edges = pruned_constraints, 
        explored_symbolic_states = seen_count, 
        event_classes = len(grouped), 
        physical_outcomes = len(all_out), 
        complete_outcomes = len(all_complete), 
        ordered_decision_nodes = total_nodes, 
        ordered_leaf_nodes = total_leaves, 
        sequence_conflicts = total_conflicts, 
        cfg_identity_tokens = cfg, 
        normalization_residuals = len(residual), 
        unresolved_value_muxes = len(muxes), 
        unresolved_mux_examples = sorted(muxes)[:20], 
        unresolved_mux_blocks = dict(sorted(unresolved_blocks.items(), key = lambda kv: (-kv[1], kv[0]))), 
        transition_phi_states = len(transition_phi_states), 
        transition_phi_state_rows = [
            {"state": name, "phi_block": expr.phi_block, "incoming": len(expr.phi_inputs), "expr": repr(expr)}
            for expr, name in sorted(transition_phi_states.items(), key = lambda kv: kv[1])
        ], 
        transition_rows = transition_rows, 
        rows = rows, 
        notes = [
            "CFG branch combinations are not enumerated ahead of time.", 
            "Transition-local state definitions are symbolically executed before each branch.", 
            "Constant-infeasible branches are discarded at the branch site.", 
            "Transparent GPIO sample barriers are fused into the same macro-step.", 
        ], 
    )


def write_feasible_symbolic_executor_report(result: FeasibleSymbolicExecutorResult, path: Path): 
    lines = [
        "FEASIBILITY-AWARE SYMBOLIC HARDWARE EXECUTOR", 
        "=" * 78, 
        f"completed transitions       : {result.completed_transitions}", 
        f"explored symbolic states    : {result.explored_symbolic_states}", 
        f"constant edges pruned       : {result.pruned_constant_edges}", 
        f"constraint edges pruned     : {result.pruned_constraint_edges}", 
        f"hardware event classes      : {result.event_classes}", 
        f"physical outcomes           : {result.physical_outcomes}", 
        f"complete observable outcomes: {result.complete_outcomes}", 
        f"ordered decision nodes      : {result.ordered_decision_nodes}", 
        f"ordered leaf nodes          : {result.ordered_leaf_nodes}", 
        f"sequence conflicts          : {result.sequence_conflicts}", 
        f"CFG identity tokens         : {result.cfg_identity_tokens}", 
        f"normalization residuals     : {result.normalization_residuals}", 
        f"unresolved value muxes      : {result.unresolved_value_muxes}", 
        f"promoted transition states  : {result.transition_phi_states}", 
        "", "Unresolved mux examples", "-" * 78, 
    ]
    lines += [f"- {x}" for x in result.unresolved_mux_examples] or ["- none"]
    lines += ["", "Unresolved mux branch blocks", "-" * 78]
    lines += [f"- {k}: {v}" for k, v in result.unresolved_mux_blocks.items()] or ["- none"]
    lines += ["", "Promoted transition states", "-" * 78]
    lines += [f"- {x['state']}: phi_block={x['phi_block']} incoming={x['incoming']} {x['expr']}" for x in result.transition_phi_state_rows] or ["- none"]
    lines += [
        "", "Event classes", "-" * 78, 
    ]
    for row in result.rows: 
        lines.append(f"{'+'.join(row['events']) or 'EVENT_FREE'}: transitions={row['transitions']} physical={row['physical_outcomes']} complete={row['complete_outcomes']} nodes={row['decision_nodes']} conflicts={row['conflicts']} hold={row['all_hold']} max_predicates={row['max_predicates']}")
    lines += ["", "Notes", "-" * 78] + [f"- {x}" for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
