from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths, _edge_order, _block_order
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticExpr, SemanticSSAResult
from .gpio_effect import GPIOEffectResult
from .structural_netlist import StructuralNetlist


def semantic_expr_key(expr: SemanticExpr, path_edges: set[tuple[int, int]]): 
    if expr.kind == 'CONST': 
        return ('CONST', expr.value)
    if expr.kind == 'STATE': 
        return ('STATE', expr.state_family)
    if expr.kind == 'SEMANTIC_STATE': 
        return ('SEMANTIC_STATE', expr.semantic_state_name)
    if expr.kind == 'GPIO': 
        return ('GPIO', expr.gpio_block, expr.gpio_value)
    if expr.kind == 'LIVEIN': 
        return ('LIVEIN', expr.livein_name)
    if expr.kind == 'OP': 
        return ('OP', expr.operation, tuple(semantic_expr_key(a, path_edges) for a in expr.args))
    if expr.kind == 'PHI': 
        matches = [incoming for pred, incoming in expr.phi_inputs if (pred, expr.phi_block) in path_edges]
        if len(matches) == 1: 
            return semantic_expr_key(matches[0], path_edges)
        if not matches: 
            return ('CONST', 0)
        return ('PHI_AMBIG', expr.phi_block, tuple(semantic_expr_key(x, path_edges) for x in matches))
    return ('UNKNOWN', expr.kind)


@dataclass
class FullMacroRow: 
    event_id: str
    cpu_paths: int
    unique_complete_outcomes: int
    collapse_pct: float
    unique_target_samples: int
    unique_gpio_effect_sequences: int


@dataclass
class FullMacroAnalysis: 
    rows: list[FullMacroRow]
    total_event_cpu_paths: int
    total_unique_complete_outcomes: int
    notes: list[str]


def _widths(structural): 
    return {f: (32 if structural.nodes[n].width is None else int(structural.nodes[n].width)) for f, n in structural.state_nodes.items()}


def analyze_full_event_macro_transitions(
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
    structural: StructuralNetlist, 
    semantic_ssa: SemanticSSAResult, 
    gpio_effects: GPIOEffectResult, 
    canonical_events: CanonicalEventAnalysisResult, 
    max_paths_per_region: int = 200000, 
) -> FullMacroAnalysis: 
    regs = sorted(_widths(structural))
    sems = sorted(semantic_ssa.semantic_states)
    sem_defs_by_edge = {}
    for name, d in semantic_ssa.semantic_state_definitions.items(): 
        for pred, incoming in d.incoming: 
            sem_defs_by_edge.setdefault((pred, d.block_id), []).append((name, incoming))

    rows = []
    totalp = 0
    totalo = 0
    for ce in canonical_events.canonical_events: 
        region = reach.regions[ce.region]
        actual = {(e.source, e.target) for e in region.edges}
        success = {(a, b) for a in ce.detector_blocks for b in ce.success_targets if (a, b) in actual}
        paths, trunc = _paths(region, max_paths_per_region)
        if trunc: 
            raise RuntimeError(f'{ce.event_id}: path enumeration truncated')
        selected = [p for p in paths if set(p[1]) & success]
        order = _edge_order(region)
        border = _block_order(region)
        outcomes = set()
        targets = set()
        gpio_sequences = set()
        for nodes, edges in selected: 
            es = set(edges)
            ns = set(nodes)
            phys = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
            sem = {s: ('SEMANTIC_STATE', s) for s in sems}
            target = None
            for e in order: 
                ek = (e.source, e.target)
                if ek not in es: 
                    continue
                for w in next_state_expr.by_edge.get(ek, []): 
                    phys[w.family] = w.expression
                for name, incoming in sem_defs_by_edge.get(ek, []): 
                    sem[name] = semantic_expr_key(incoming, es)
                if e.target in region.exit_samples: 
                    target = e.target
            gpio_seq = []
            for b in border: 
                if b not in ns: 
                    continue
                for idx, effect in enumerate(gpio_effects.by_block.get(b, [])): 
                    arg = semantic_ssa.gpio_arguments.get((b, idx))
                    gpio_seq.append((b, effect.kind, semantic_expr_key(arg, es) if arg is not None else ('MISSING',)))
            full = (
                target, 
                tuple((r, expr_key(phys[r])) for r in regs), 
                tuple((s, sem[s]) for s in sems), 
                tuple(gpio_seq), 
            )
            outcomes.add(repr(full))
            targets.add(target)
            gpio_sequences.add(repr(tuple(gpio_seq)))
        n = len(selected)
        o = len(outcomes)
        rows.append(FullMacroRow(ce.event_id, n, o, 0.0 if not n else 100*(n-o)/n, len(targets), len(gpio_sequences)))
        totalp+=n
        totalo+=o
    return FullMacroAnalysis(rows, totalp, totalo, [
        'Complete outcomes include physical persistent state, semantic persistent state, next semantic sample, and ordered GPIO effects.', 
        'PHI expressions are resolved against each concrete CFG path before outcome comparison.', 
        'Two CPU paths are merged only when every persistent/output-visible macro-step result is structurally identical.', 
    ])


def write_full_event_macro_report(r: FullMacroAnalysis, path: Path): 
    lines = ['FULL EVENT MACRO-TRANSITION EQUIVALENCE DIAGNOSTIC', '='*78, 
           f'event CPU paths           : {r.total_event_cpu_paths}', 
           f'unique complete outcomes  : {r.total_unique_complete_outcomes}', '']
    for x in r.rows: 
        lines += [f'{x.event_id}', f'  CPU paths                  : {x.cpu_paths}', 
                  f'  complete macro outcomes    : {x.unique_complete_outcomes}', 
                  f'  exact path collapse        : {x.collapse_pct:.2f}%', 
                  f'  next-sample alternatives   : {x.unique_target_samples}', 
                  f'  GPIO-effect sequences      : {x.unique_gpio_effect_sequences}', '']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
