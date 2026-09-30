from __future__ import annotations

from collections import Counter
from functools import lru_cache
import ast
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths
from .definition_site_ir import DefinitionSiteIR
from .edge_event_analysis import _predicate_atom
from .event_vector_decision_dag import _branch_trace, _reduce_cubes
from .event_vector_sequence_dag import TNode, _intern_reduce
from .hardware_behavior_ir import expr_key
from .hardware_event_transition_analysis import _apply_path, _path_target
from .hardware_temporal_region import HardwareTemporalRegionIR
from .next_state_expr import Expr, NextStateExprIR
from .polling_phase_event_analysis import PollingPhaseEventAnalysis
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist


@dataclass
class FeasibleTransitionCoreAnalysis: 
    syntactic_paths: int
    fused_syntactic_paths: int
    infeasible_paths: int
    feasible_paths: int
    prune_pct: float
    hardware_event_classes: int
    physical_outcomes: int
    normalized_predicates: int
    raw_predicate_occurrences: int
    reduced_decision_nodes: int
    leaf_nodes: int
    sequence_conflicts: int
    cube_decision_nodes: int
    cube_leaf_nodes: int
    cube_conflicts: int
    cfg_identity_tokens: int
    normalization_residual_predicates: int
    unresolved_value_mux_predicates: int
    event_rows: list[dict]
    contradiction_examples: list[dict]
    notes: list[str]


def _history_success(events: CanonicalEventAnalysisResult, region): 
    actual = {(e.source, e.target) for e in region.edges}
    return {
        ev.event_id: {
            (a, b)
            for a in ev.detector_blocks
            for b in ev.success_targets
            if (a, b) in actual
        }
        for ev in events.canonical_events
    }


def _event_class(region, edges, ssa: SemanticSSAResult, history_events, polling): 
    labels = []
    edge_set = set(edges)
    for eid, success_edges in history_events.items(): 
        if edge_set & success_edges: 
            labels.append(eid)

    if len(polling.rows) == 1: 
        phase = polling.rows[0]
        atoms = {b: a for b, c in ssa.branch_conditions.items() if (a := _predicate_atom(c)) is not None}
        kinds = {(e.source, e.target): e.kind for e in region.edges}
        st, gp = [], []
        for a, b in edges: 
            atom = atoms.get(a)
            kind = kinds.get((a, b))
            if atom is None or kind not in ("TRUE", "FALSE"): 
                continue
            truth = kind == "TRUE"
            level = atom.level if truth else 1 - atom.level
            if atom.source == "SEMANTIC_STATE_BIT" and atom.family == phase["state"]: 
                st.append(level)
            if atom.source == "GPIO_BIT" and atom.bit == phase["gpio_bit"]: 
                gp.append(level)
        if st and gp: 
            combo = (st[0], gp[-1])
            if combo == (1, 0): 
                labels.append("PHEVT_FALL")
            elif combo == (0, 1): 
                labels.append("PHEVT_RISE")
    return tuple(sorted(labels))


def _invert_op(op): 
    return {"EQ": "NE", "NE": "EQ", "ULT": "UGE", "UGE": "ULT", "SLT": "NOT_SLT", "NOT_SLT": "SLT"}.get(op, "NOT_" + op)


def _const_value(x): 
    return int(x[1]) if isinstance(x, tuple) and len(x) >= 2 and x[0] == "CONST" else None


@lru_cache(maxsize = 65536)
def _constraint_atom(pred_s, truth): 
    try: 
        p = ast.literal_eval(pred_s)
    except Exception: 
        return None
    if not isinstance(p, tuple) or len(p) != 3: 
        return None
    op, a, b = p
    if not truth: 
        op = _invert_op(op)
    av, bv = _const_value(a), _const_value(b)
    if bv is not None and av is None: 
        return (repr(a), op, bv)
    if av is not None and bv is None: 
        # reverse relation: c < x -> x > c.  We keep only exact/integer-safe forms.
        rev = {"EQ": "EQ", "NE": "NE", "ULT": "GT", "UGE": "LE"}.get(op)
        if rev is not None: 
            return (repr(b), rev, av)
    return None


def _contradiction(seq): 
    # First, exact Boolean contradiction.
    vals = {}
    for pred, truth in seq: 
        vals.setdefault(pred, set()).add(bool(truth))
    bad = sorted(p for p, v in vals.items() if len(v) > 1)
    if bad: 
        return bad

    # Then combine integer constraints on the same normalized hardware expression.
    # Domain is integral; no bit width is required for contradiction proofs below.
    cs = {}
    for pred, truth in seq: 
        atom = _constraint_atom(pred, truth)
        if atom is None: 
            continue
        x, op, c = atom
        st = cs.setdefault(x, {"eq": None, "ne": set(), "lo": None, "hi": None})
        if op == "EQ": 
            if st["eq"] is not None and st["eq"] != c: 
                return [f"{x}: EQ {st['eq']} and EQ {c}"]
            st["eq"] = c
        elif op == "NE": 
            st["ne"].add(c)
        elif op == "ULT": 
            st["hi"] = c if st["hi"] is None else min(st["hi"], c)
        elif op == "UGE": 
            st["lo"] = c if st["lo"] is None else max(st["lo"], c)
        elif op == "GT": 
            lo = c + 1
            st["lo"] = lo if st["lo"] is None else max(st["lo"], lo)
        elif op == "LE": 
            hi = c + 1
            st["hi"] = hi if st["hi"] is None else min(st["hi"], hi)

    for x, st in cs.items(): 
        eq, ne, lo, hi = st["eq"], st["ne"], st["lo"], st["hi"]
        if lo is not None and hi is not None and lo >= hi: 
            return [f"{x}: empty interval [{lo},{hi})"]
        if eq is not None: 
            if eq in ne: 
                return [f"{x}: EQ {eq} and NE {eq}"]
            if lo is not None and eq < lo: 
                return [f"{x}: EQ {eq} below lower bound {lo}"]
            if hi is not None and eq >= hi: 
                return [f"{x}: EQ {eq} above upper bound {hi - 1}"]
    return []


def analyze_feasible_transition_core(
    reach: SemanticReachResult, 
    next_state: NextStateExprIR, 
    structural: StructuralNetlist, 
    history_events: CanonicalEventAnalysisResult, 
    polling: PollingPhaseEventAnalysis, 
    temporal: HardwareTemporalRegionIR, 
    ssa: SemanticSSAResult, 
    def_ir: DefinitionSiteIR, 
    region_name: str = "SAMPLE_BB006", 
) -> FeasibleTransitionCoreAnalysis: 
    region = reach.regions[region_name]
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = "STATE", state_family = r) for r in regs}

    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError("feasible transition path enumeration truncated")

    transparent = set()
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)
    transparent_cache = {}
    for b in transparent: 
        rn = f"SAMPLE_BB{b:03d}"
        ps, t = _paths(reach.regions[rn], 200000)
        if t: 
            raise RuntimeError(f"{rn}: feasible transition path enumeration truncated")
        transparent_cache[b] = ps

    history_success = _history_success(history_events, region)
    feasible = []
    contradictions = []
    fused_total = 0

    for nodes, edges in paths: 
        pred_map = {b: a for a, b in edges}
        seq1, env1 = _branch_trace(region, nodes, edges, ssa, pred_map, def_ir)
        state1 = _apply_path(ident, region, edges, next_state)
        target1 = _path_target(region, nodes, edges)
        evclass = _event_class(region, edges, ssa, history_success, polling)

        continuations = []
        if target1 in transparent: 
            r2 = reach.regions[f"SAMPLE_BB{target1:03d}"]
            for n2, e2 in transparent_cache[target1]: 
                # Preserve predecessor history from the event-root region when
                # tracing a fused transparent sample region. Event-local PHIs
                # in the continuation may refer to merge blocks reached before
                # the transparent sample; dropping that predecessor creates a
                # spurious value mux and fake decision conflicts.
                combined_pred = {b: a for a, b in edges}
                combined_pred.update({b: a for a, b in e2})
                seq2, _ = _branch_trace(r2, n2, e2, ssa, combined_pred, def_ir, env1)
                state2 = _apply_path(state1, r2, e2, next_state)
                continuations.append((seq1 + seq2, state2, evclass))
        else: 
            continuations.append((seq1, state1, evclass))

        for seq, state, ev in continuations: 
            fused_total += 1
            bad = _contradiction(seq)
            if bad: 
                if len(contradictions) < 12: 
                    contradictions.append({"events": list(ev), "predicates": bad[:8]})
                continue
            outcome = tuple((r, expr_key(state[r])) for r in regs)
            feasible.append((ev, seq, outcome))

    # Build one CFG-free ordered hardware decision DAG per hardware-event class.
    grouped = {}
    for ev, seq, out in feasible: 
        grouped.setdefault(ev, []).append((seq, out))

    all_preds = set()
    all_outcomes = set()
    raw = 0
    reduced = 0
    leaves_n = 0
    conflicts = 0
    cube_nodes_total = 0
    cube_leaves_total = 0
    cube_conflicts_total = 0
    event_rows = []
    cfg_tokens = 0
    norm_residuals = set()
    unresolved_muxes = set()

    for ev, traces in sorted(grouped.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        # A predicate can be removed only when every feasible trace in this class
        # evaluates it and always obtains the same truth value.
        vals, presence = {}, {}
        for seq, _ in traces: 
            seen = set()
            for p, v in seq: 
                vals.setdefault(p, set()).add(v)
                seen.add(p)
            for p in seen: 
                presence[p] = presence.get(p, 0) + 1
        constants = {p for p, vs in vals.items() if len(vs) == 1 and presence.get(p, 0) == len(traces)}

        root = TNode()
        node_count = 1
        outcomes = set()
        for seq, out in traces: 
            cur = root
            filtered = [(p, v) for p, v in seq if p not in constants]
            raw += len(filtered)
            outcomes.add(out)
            all_outcomes.add(out)
            for p, v in filtered: 
                all_preds.add(p)
                cfg_tokens += int("BB" in p or "reach_" in p or "edge_" in p)
                if "NORMALIZATION_RESIDUAL" in p: 
                    norm_residuals.add(p)
                if "UNRESOLVED_VALUE_MUX" in p: 
                    unresolved_muxes.add(p)
                item = (p, v)
                if item not in cur.edges: 
                    cur.edges[item] = TNode()
                    node_count += 1
                cur = cur.edges[item]
            cur.outs.add(out)

        memo, intern, leaf_intern, conf = {}, {}, {}, [0]
        _intern_reduce(root, memo, intern, leaf_intern, conf)
        reduced += len(intern)
        leaves_n += len(leaf_intern)
        conflicts += conf[0]

        cube_items = []
        for seq, out in traces: 
            d = {}
            ok = True
            for p, v in seq: 
                if p in constants: 
                    continue
                if p in d and d[p] != bool(v): 
                    ok = False
                    break
                d[p] = bool(v)
            if ok: 
                cube_items.append((d, out))
        _root_cube, c_nodes, c_leaves, c_conf, _examples = _reduce_cubes(cube_items)
        cube_nodes_total += c_nodes
        cube_leaves_total += c_leaves
        cube_conflicts_total += c_conf

        event_rows.append({
            "events": list(ev), 
            "feasible_paths": len(traces), 
            "physical_outcomes": len(outcomes), 
            "reduced_nodes": len(intern), 
            "leaves": len(leaf_intern), 
            "event_implied_predicates": len(constants), 
            "cube_nodes": c_nodes, 
            "cube_leaves": c_leaves, 
            "cube_conflicts": c_conf, 
            "cube_conflict_examples": _examples, 
        })

    infeasible = fused_total - len(feasible)
    return FeasibleTransitionCoreAnalysis(
        syntactic_paths = len(paths), 
        fused_syntactic_paths = fused_total, 
        infeasible_paths = infeasible, 
        feasible_paths = len(feasible), 
        prune_pct = 0.0 if not fused_total else 100.0 * infeasible / fused_total, 
        hardware_event_classes = len(grouped), 
        physical_outcomes = len(all_outcomes), 
        normalized_predicates = len(all_preds), 
        raw_predicate_occurrences = raw, 
        reduced_decision_nodes = reduced, 
        leaf_nodes = leaves_n, 
        sequence_conflicts = conflicts, 
        cube_decision_nodes = cube_nodes_total, 
        cube_leaf_nodes = cube_leaves_total, 
        cube_conflicts = cube_conflicts_total, 
        cfg_identity_tokens = cfg_tokens, 
        normalization_residual_predicates = len(norm_residuals), 
        unresolved_value_mux_predicates = len(unresolved_muxes), 
        event_rows = event_rows, 
        contradiction_examples = contradictions, 
        notes = [
            "Syntactic CFG paths are pruned when the same normalized hardware predicate is required both true and false in one macro-step.", 
            "Decision keys contain normalized hardware expressions only; BB/reach/edge identity is forbidden from the transition core.", 
            "Polling-phase events and trusted history-latch events are unified only as transition-input labels; their recovery proofs remain separate.", 
            "This is a hardware-transition core diagnostic, not yet a SystemVerilog backend.", 
        ], 
    )


def write_feasible_transition_core_report(r: FeasibleTransitionCoreAnalysis, path: Path): 
    lines = [
        "FEASIBLE CFG-FREE HARDWARE TRANSITION CORE", 
        "=" * 78, 
        f"syntactic root paths      : {r.syntactic_paths}", 
        f"fused syntactic paths     : {r.fused_syntactic_paths}", 
        f"infeasible paths pruned   : {r.infeasible_paths}", 
        f"feasible hardware paths   : {r.feasible_paths}", 
        f"path prune                : {r.prune_pct:.2f}%", 
        f"hardware event classes    : {r.hardware_event_classes}", 
        f"physical outcomes         : {r.physical_outcomes}", 
        f"normalized predicates     : {r.normalized_predicates}", 
        f"raw predicate occurrences : {r.raw_predicate_occurrences}", 
        f"reduced decision nodes    : {r.reduced_decision_nodes}", 
        f"leaf nodes                : {r.leaf_nodes}", 
        f"sequence conflicts        : {r.sequence_conflicts}", 
        f"cube decision nodes       : {r.cube_decision_nodes}", 
        f"cube leaf nodes           : {r.cube_leaf_nodes}", 
        f"cube conflicts            : {r.cube_conflicts}", 
        f"CFG identity tokens       : {r.cfg_identity_tokens}", 
        f"normalization residuals   : {r.normalization_residual_predicates}", 
        f"unresolved value muxes    : {r.unresolved_value_mux_predicates}", 
        "", 
        "Event classes", 
        "-" * 78, 
    ]
    for x in r.event_rows: 
        lines += [
            "+".join(x["events"]) or "NO_EXTERNAL_EVENT", 
            f"  feasible paths          : {x['feasible_paths']}", 
            f"  physical outcomes       : {x['physical_outcomes']}", 
            f"  reduced decision nodes  : {x['reduced_nodes']}", 
            f"  leaves                  : {x['leaves']}", 
            f"  event-implied predicates: {x['event_implied_predicates']}", 
            f"  cube nodes/conflicts    : {x['cube_nodes']} / {x['cube_conflicts']}", 
        ]
        for ce in x.get("cube_conflict_examples", [])[:3]: 
            lines += [f"    conflict: outcomes={ce.get('outcomes')} items={ce.get('items')}", f"      common={ce.get('common_assignments')}", f"      remaining={ce.get('remaining_predicates')[:6]}"]
        lines += [
            "", 
        ]
    if r.contradiction_examples: 
        lines += ["Contradiction examples", "-" * 78]
        for x in r.contradiction_examples: 
            lines += [f"events: {'+'.join(x['events']) or 'NONE'}"] + [f"  {p}" for p in x["predicates"]]
        lines += [""]
    lines += ["Notes", "-" * 78] + [f"- {n}" for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
