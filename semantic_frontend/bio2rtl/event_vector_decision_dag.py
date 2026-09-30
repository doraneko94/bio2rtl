from __future__ import annotations

from dataclasses import asdict, dataclass
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths, _edge_order
from .event_vector_predicate_analysis import _event_success, _phi_pred_for_group, _cond
from .hardware_behavior_ir import expr_key
from .hardware_event_transition_analysis import _apply_path, _path_target
from .hardware_temporal_region import HardwareTemporalRegionIR
from .next_state_expr import Expr, NextStateExprIR
from .definition_site_ir import DefinitionSiteIR
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist


@dataclass
class EventVectorDecisionDagAnalysis: 
    event_vectors: int
    cpu_paths: int
    fused_paths: int
    physical_outcomes: int
    raw_branch_tests: int
    normalized_predicates: int
    reduced_decision_nodes: int
    reduced_leaf_nodes: int
    unresolved_conflicts: int
    rows: list[dict]
    notes: list[str]


def _edge_kind(region): 
    return {(e.source, e.target): e.kind for e in region.edges}


def _success(events, region): 
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


def _vector_paths(reach, events, region_name = "SAMPLE_BB006"): 
    region = reach.regions[region_name]
    success = _success(events, region)
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError("event-vector decision path enumeration truncated")
    groups = {}
    for nodes, edges in paths: 
        es = set(edges)
        sig = tuple(sorted(eid for eid, se in success.items() if es & se))
        if sig: 
            groups.setdefault(sig, []).append((tuple(nodes), tuple(edges)))
    return region, groups


def _fold_value(x): 
    if not isinstance(x, tuple) or not x: 
        return x
    tag = x[0]
    if tag == "OP": 
        op = x[1]
        args = tuple(_fold_value(a) for a in x[2])
        if all(isinstance(a, tuple) and a and a[0] == "CONST" for a in args): 
            vals = [int(a[1]) for a in args]
            try: 
                if op == "ADD": 
                    return ("CONST", vals[0] + vals[1])
                if op == "SUB": 
                    return ("CONST", vals[0] - vals[1])
                if op == "AND": 
                    return ("CONST", vals[0] & vals[1])
                if op == "OR": 
                    return ("CONST", vals[0] | vals[1])
                if op == "XOR": 
                    return ("CONST", vals[0] ^ vals[1])
                if op == "SHL": 
                    return ("CONST", vals[0] << vals[1])
                if op == "SHR": 
                    return ("CONST", vals[0] >> vals[1])
            except Exception: 
                pass
        return ("OP", op, args)
    return tuple(_fold_value(a) if isinstance(a, tuple) else a for a in x)


def _fold_pred(p): 
    p = _fold_value(p)
    if not isinstance(p, tuple) or len(p) != 3: 
        return p
    op, a, b = p
    if isinstance(a, tuple) and a and a[0] == "CONST" and isinstance(b, tuple) and b and b[0] == "CONST": 
        x, y = int(a[1]), int(b[1])
        table = {
            "EQ": x == y, 
            "NE": x != y, 
            "ULT": x < y, 
            "UGE": x >= y, 
            "SLT": x < y, 
            "NOT_SLT": not (x < y), 
        }
        if op in table: 
            return ("BOOL", bool(table[op]))
    if op in ("EQ", "NE") and a == b: 
        return ("BOOL", op == "EQ")
    return p


def _expr_with_env(e, pred, stats, env, phi_state_map = None, phi_family_map = None): 
    from .semantic_ssa import SemanticExpr
    if e.kind == "CONST": 
        return ("CONST", e.value)
    if e.kind == "STATE": 
        return env.get(e.state_family, ("STATE", e.state_family))
    if e.kind == "SEMANTIC_STATE": 
        stats["sem"] += 1; return ("HW_STATE", e.semantic_state_name)
    if e.kind == "GPIO": 
        return ("GPIO_SAMPLE", e.gpio_block, e.gpio_value)
    if e.kind == "LIVEIN": 
        return ("LIVEIN", e.livein_name)
    if e.kind == "OP": 
        return ("OP", e.operation, tuple(_expr_with_env(a, pred, stats, env, phi_state_map, phi_family_map) for a in e.args))
    if e.kind == "PHI": 
        p = pred.get(int(e.phi_block)) if e.phi_block is not None else None
        if p is not None: 
            for q, x in e.phi_inputs: 
                if q == p: 
                    return _expr_with_env(x, pred, stats, env, phi_state_map, phi_family_map)
        # If this PHI is not executed in the current macro-step, a loop-carried
        # semantic-state input denotes the boundary value entering the event.
        # Preserve that state instead of inventing a mux over mutually exclusive
        # predecessor values from other CPU paths.
        sem_names = set()
        def collect_sem(x): 
            if x.kind == "SEMANTIC_STATE" and x.semantic_state_name is not None: 
                sem_names.add(x.semantic_state_name)
            for a in x.args: 
                collect_sem(a)
            for _q, a in x.phi_inputs: 
                collect_sem(a)
        for _q, x in e.phi_inputs: 
            collect_sem(x)
        if len(sem_names) == 1: 
            stats["sem"] += 1
            return ("HW_STATE", next(iter(sem_names)))
        stats["phi"] += 1
        if phi_family_map is not None and e in phi_family_map: 
            family = phi_family_map[e]
            return env.get(family, ("STATE", family))
        if phi_state_map is not None: 
            # A PHI whose predecessor is outside the current event-local trace
            # is loop-carried state, not a combinational mux over predecessor
            # values. Promote it to an anonymous hardware transition state.
            if e not in phi_state_map: 
                phi_state_map[e] = f"transition_state_{len(phi_state_map)}"
            return ("HW_TRANSITION_STATE", phi_state_map[e])
        vals = tuple(sorted({_expr_with_env(x, pred, stats, env, phi_state_map, phi_family_map) for _, x in e.phi_inputs}, key = repr))
        return ("UNRESOLVED_VALUE_MUX", vals)
    return (e.kind,)

def _cond_with_env(c, truth, pred, stats, env, phi_state_map = None, phi_family_map = None): 
    op = c.operation if truth else {"EQ": "NE", "NE": "EQ", "ULT": "UGE", "UGE": "ULT", "SLT": "NOT_SLT"}.get(c.operation, "NOT_"+c.operation)
    return (op, _expr_with_env(c.lhs, pred, stats, env, phi_state_map, phi_family_map), _expr_with_env(c.rhs, pred, stats, env, phi_state_map, phi_family_map))

def _lower_expr_with_env(e: Expr, env): 
    if e.kind == "CONST": 
        return ("CONST", e.value)
    if e.kind == "STATE": 
        return env.get(e.state_family, ("STATE", e.state_family))
    if e.kind == "GPIO": 
        return ("GPIO_VALUE", e.gpio_value)
    if e.kind == "SEMANTIC": 
        return ("HW_VALUE", e.semantic_value)
    if e.kind == "OP": 
        return ("OP", e.operation, tuple(_lower_expr_with_env(a, env) for a in e.args))
    return (e.kind,)

def _branch_trace(region, nodes, edges, ssa, pred_map, def_ir: DefinitionSiteIR, initial_env = None): 
    kinds = _edge_kind(region)
    out = []
    stats = {"phi": 0, "sem": 0}
    env = dict(initial_env or {})
    seen = {}
    edge_by_source = {a: (a, b) for a, b in edges}
    node_set = set(nodes)
    for block in nodes: 
        # Execute CPU-local state definitions before the terminator branch.
        for w in sorted(def_ir.by_block.get(block, []), key = lambda x: x.order): 
            env[w.family] = _fold_value(_lower_expr_with_env(w.expression, env))
        ek = edge_by_source.get(block)
        if ek is None: 
            continue
        a, b = ek
        kind = kinds.get((a, b))
        if kind not in ("TRUE", "FALSE"): 
            continue
        cond = ssa.branch_conditions.get(a)
        if cond is None: 
            continue
        pred = _fold_pred(_cond_with_env(cond, True, pred_map, stats, env))
        truth = kind == "TRUE"
        if pred == ("BOOL", True): 
            if truth: 
                continue
            pred = ("NORMALIZATION_RESIDUAL", f"BB{block:03d}", f"EDGE_{a}_{b}_{kind}", "RESOLVED_TRUE_TAKES_FALSE", repr(cond))
        elif pred == ("BOOL", False): 
            if not truth: 
                continue
            pred = ("NORMALIZATION_RESIDUAL", f"BB{block:03d}", f"EDGE_{a}_{b}_{kind}", "RESOLVED_FALSE_TAKES_TRUE", repr(cond))
        key = repr(pred)
        # Same expression at different program points is safe to merge only after
        # current-state substitution above has made its temporal version explicit.
        if key in seen and seen[key] == truth: 
            continue
        seen[key] = truth
        out.append((key, truth))
    return out, env


def _simplify_items(items, constants): 
    out = []
    for assign, outcome in items: 
        d = {k: v for k, v in assign.items() if k not in constants}
        out.append((d, outcome))
    return out


def _reduce_cubes(items): 
    """Build a conservative reduced multi-terminal decision DAG from path cubes.

    Unknown predicates are replicated to both branches. A node is eliminated when
    both reduced children are identical. This is exact for the enumerated mutually
    exclusive path cover; conflicts are reported rather than guessed through.
    """
    memo = {}
    node_intern = {}
    leaves = {}
    conflicts = 0
    conflict_examples = []

    def leaf(outcome): 
        if outcome not in leaves: 
            leaves[outcome] = ("L", len(leaves))
        return leaves[outcome]

    def canonical(seq): 
        return tuple(sorted(((tuple(sorted(d.items())), o) for d, o in seq), key = repr))

    def rec(seq): 
        nonlocal conflicts, conflict_examples
        outs = {o for _, o in seq}
        if len(outs) == 1: 
            return leaf(next(iter(outs)))
        ck = canonical(seq)
        if ck in memo: 
            return memo[ck]
        # Candidate must explicitly distinguish at least one item on each side.
        counts = {}
        for d, _ in seq: 
            for p, v in d.items(): 
                t, f, u = counts.get(p, (0, 0, 0))
                if v: 
                    t += 1
                else: 
                    f += 1
                counts[p] = (t, f, u)
        cand = [(p, t, f) for p, (t, f, _u) in counts.items() if t and f]
        if not cand: 
            conflicts += 1
            if len(conflict_examples) < 12: 
                common = None
                for d, _o in seq: 
                    pairs = set(d.items())
                    common = pairs if common is None else common & pairs
                conflict_examples.append({
                    "outcomes": len(outs), 
                    "items": len(seq), 
                    "common_assignments": sorted(common or set()), 
                    "remaining_predicates": sorted({p for d, _o in seq for p in d}), 
                })
            # Preserve correctness of the diagnostic: distinct unresolved outcomes
            # remain an explicit conflict leaf, never silently merged.
            val = ("CONFLICT", tuple(sorted(outs, key = repr)))
            r = leaf(val)
            memo[ck] = r
            return r
        # Prefer predicates that split explicit cubes evenly and occur frequently.
        p, _t, _f = max(cand, key = lambda x: (min(x[1], x[2]), x[1] + x[2]))
        ts, fs = [], []
        for d, o in seq: 
            nd = dict(d)
            v = nd.pop(p, None)
            if v is True: 
                ts.append((nd, o))
            elif v is False: 
                fs.append((nd, o))
            else: 
                ts.append((dict(nd), o)); fs.append((dict(nd), o))
        a, b = rec(ts), rec(fs)
        if a == b: 
            r = a
        else: 
            nk = (p, a, b)
            if nk not in node_intern: 
                node_intern[nk] = ("N", len(node_intern), p, a, b)
            r = node_intern[nk]
        memo[ck] = r
        return r

    root = rec(items)
    return root, len(node_intern), len(leaves), conflicts, conflict_examples



def _semantic_next_outcome(ssa, combined_edges): 
    es = set(combined_edges)
    out = []
    stats = {"phi": 0, "sem": 0}
    pred = {b: a for a, b in combined_edges}
    for name, d in sorted(ssa.semantic_state_definitions.items()): 
        matches = [expr for predecessor, expr in d.incoming if (predecessor, d.block_id) in es]
        if len(matches) == 1: 
            val = _fold_value(_expr_with_env(matches[0], pred, stats, {}))
        elif len(matches) == 0: 
            val = ("HW_STATE", name)
        else: 
            val = ("SEMANTIC_NEXT_AMBIG", tuple(sorted((_fold_value(_expr_with_env(x, pred, stats, {})) for x in matches), key = repr)))
        out.append((name, val))
    return tuple(out)

def analyze_event_vector_decision_dag(
    reach: SemanticReachResult, 
    next_state: NextStateExprIR, 
    structural: StructuralNetlist, 
    events: CanonicalEventAnalysisResult, 
    temporal: HardwareTemporalRegionIR, 
    ssa: SemanticSSAResult, 
    def_ir: DefinitionSiteIR, 
    region_name: str = "SAMPLE_BB006", 
) -> EventVectorDecisionDagAnalysis: 
    region, groups = _vector_paths(reach, events, region_name)
    root = region.start_sample
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = "STATE", state_family = r) for r in regs}
    transparent = set()
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)
    cache = {}
    for b in transparent: 
        rn = f"SAMPLE_BB{b:03d}"
        ps, trunc = _paths(reach.regions[rn], 200000)
        if trunc: 
            raise RuntimeError(f"{rn}: decision-DAG path enumeration truncated")
        cache[b] = ps

    rows = []
    total_cpu = total_fused = total_outcomes = total_raw = 0
    all_preds = set()
    total_nodes = total_leaves = total_conflicts = 0

    for sig, ps in sorted(groups.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        traces = []
        raw_tests = 0
        for nodes, edges in ps: 
            # Resolve PHIs with the predecessor taken by this concrete semantic
            # path.  The resulting predicate contains no predecessor/BB identity.
            path_pred = {b: a for a, b in edges}
            tr1, env1 = _branch_trace(region, nodes, edges, ssa, path_pred, def_ir)
            s1 = _apply_path(ident, region, edges, next_state)
            t1 = _path_target(region, nodes, edges)
            if t1 in transparent: 
                r2 = reach.regions[f"SAMPLE_BB{t1:03d}"]
                for n2, e2 in cache[t1]: 
                    # Transparent regions are currently branchless, but include
                    # their hardware predicates if that changes in future.
                    tr2, _env2 = _branch_trace(r2, n2, e2, ssa, {b: a for a, b in e2}, def_ir, env1)
                    st = _apply_path(s1, r2, e2, next_state)
                    out = (tuple((r, expr_key(st[r])) for r in regs), _semantic_next_outcome(ssa, tuple(edges)+tuple(e2)))
                    seq = tr1 + tr2
                    raw_tests += len(seq)
                    traces.append((seq, out))
            else: 
                out = (tuple((r, expr_key(s1[r])) for r in regs), _semantic_next_outcome(ssa, tuple(edges)))
                raw_tests += len(tr1)
                traces.append((tr1, out))

        # Event-vector implied predicates have one truth value in every trace in
        # which they are evaluated. Removing them prevents event detector logic
        # from reappearing inside the transition decision DAG.
        vals = {}
        presence = {}
        for seq, _ in traces: 
            seen_here = set()
            for p, v in seq: 
                vals.setdefault(p, set()).add(v)
                seen_here.add(p)
            for p in seen_here: 
                presence[p] = presence.get(p, 0)+1
        # A predicate is event-implied only when every trace evaluates it and
        # every evaluation has the same truth value.  Merely being constant
        # when reached is not enough: reachability of the test itself can carry
        # control information.
        constants = {p for p, vs in vals.items() if len(vs) == 1 and presence.get(p, 0) == len(traces)}
        items = []
        for seq, out in traces: 
            items.append(({p: v for p, v in seq if p not in constants}, out))
            all_preds.update(p for p, _v in seq if p not in constants)
        root_node, nodes_n, leaves_n, conflicts_n, conflict_examples = _reduce_cubes(items)
        outcomes = len({o for _d, o in items})
        rows.append({
            "events": list(sig), 
            "software_paths": len(ps), 
            "fused_paths": len(traces), 
            "physical_outcomes": outcomes, 
            "raw_branch_tests": raw_tests, 
            "event_implied_predicates": len(constants), 
            "remaining_predicates": len({p for d, _ in items for p in d}), 
            "decision_nodes": nodes_n, 
            "leaf_nodes": leaves_n, 
            "unresolved_conflicts": conflicts_n, 
            "conflict_examples": conflict_examples, 
        })
        total_cpu += len(ps); total_fused += len(traces); total_outcomes += outcomes
        total_raw += raw_tests; total_nodes += nodes_n; total_leaves += leaves_n
        total_conflicts += conflicts_n

    return EventVectorDecisionDagAnalysis(
        event_vectors = len(groups), cpu_paths = total_cpu, fused_paths = total_fused, 
        physical_outcomes = total_outcomes, raw_branch_tests = total_raw, 
        normalized_predicates = len(all_preds), reduced_decision_nodes = total_nodes, 
        reduced_leaf_nodes = total_leaves, unresolved_conflicts = total_conflicts, 
        rows = rows, 
        notes = [
            "Each event vector is reduced independently after event-implied branch predicates are removed.", 
            "Decision nodes are keyed only by normalized hardware predicates; BB/reach/edge identity is absent.", 
            "Identical sub-decisions and identical outcomes are hash-consed; a node whose true/false children are identical is eliminated.", 
            "Any cube overlap that cannot be distinguished by normalized predicates is reported as a conflict rather than guessed.", 
        ], 
    )


def write_event_vector_decision_dag_report(r: EventVectorDecisionDagAnalysis, path: Path) -> None: 
    lines = [
        "EVENT-VECTOR REDUCED HARDWARE DECISION DAG", 
        "=" * 78, 
        f"event vectors             : {r.event_vectors}", 
        f"CPU paths                 : {r.cpu_paths}", 
        f"fused paths               : {r.fused_paths}", 
        f"physical outcomes          : {r.physical_outcomes}", 
        f"raw branch-test occurrences: {r.raw_branch_tests}", 
        f"normalized predicates      : {r.normalized_predicates}", 
        f"reduced decision nodes     : {r.reduced_decision_nodes}", 
        f"reduced leaf nodes         : {r.reduced_leaf_nodes}", 
        f"unresolved conflicts       : {r.unresolved_conflicts}", 
        "", 
        "Vectors", 
        "-" * 78, 
    ]
    for x in r.rows: 
        lines += [
            "+".join(x["events"]), 
            f"  software paths          : {x['software_paths']}", 
            f"  physical outcomes       : {x['physical_outcomes']}", 
            f"  raw branch tests        : {x['raw_branch_tests']}", 
            f"  event-implied predicates: {x['event_implied_predicates']}", 
            f"  remaining predicates    : {x['remaining_predicates']}", 
            f"  reduced decision nodes  : {x['decision_nodes']}", 
            f"  leaf nodes              : {x['leaf_nodes']}", 
            f"  unresolved conflicts    : {x['unresolved_conflicts']}", 
        ]
        for c in x.get("conflict_examples", [])[:3]: 
            lines.append(f"    conflict: outcomes={c['outcomes']} items={c['items']} common={c['common_assignments']} remaining={c['remaining_predicates']}")
        lines += [
            "", 
        ]
    lines += ["Notes", "-" * 78] + [f"- {x}" for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
