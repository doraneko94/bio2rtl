from __future__ import annotations

from collections import Counter
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
class ControlOutcome: 
    event_id: str
    signature: str
    occurrences: int
    register_kinds: dict[str, str]
    changed_registers: list[str]
    source_state_dependencies: list[str]
    all_simple: bool


@dataclass
class ControlAutomatonAnalysis: 
    control_registers: list[str]
    control_bits: int
    cpu_paths: int
    distinct_outcomes: int
    simple_outcomes: int
    complex_outcomes: int
    distinct_update_masks: int
    source_dependency_registers: list[str]
    theoretical_code_bits_for_outcomes: int
    outcomes: list[ControlOutcome]
    notes: list[str]


def _widths(structural: StructuralNetlist) -> dict[str, int]: 
    out = {}
    for family, nid in structural.state_nodes.items(): 
        node = structural.nodes[nid]
        out[family] = 32 if node.width is None else int(node.width)
    return out


def _success_edges(canonical_events: CanonicalEventAnalysisResult): 
    result = {}
    for e in canonical_events.canonical_events: 
        result[e.event_id] = {(a, b) for a in e.detector_blocks for b in e.success_targets}
    return result


def _classify_expr(expr: Expr, dest: str) -> tuple[str, set[str]]: 
    if expr.kind == 'STATE': 
        dep = {expr.state_family} if expr.state_family else set()
        if expr.state_family == dest: 
            return 'HOLD', dep
        return 'COPY_STATE', dep
    if expr.kind == 'CONST': 
        return f'CONST({expr.value})', set()
    deps: set[str] = set()
    def walk(e: Expr): 
        if e.kind == 'STATE' and e.state_family: 
            deps.add(e.state_family)
        for a in e.args: 
            walk(a)
    walk(expr)
    if expr.kind == 'OP': 
        return f'OP({expr.operation})', deps
    if expr.kind == 'GPIO': 
        return 'GPIO', deps
    return expr.kind, deps


def analyze_control_automaton(
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
    structural: StructuralNetlist, 
    canonical_events: CanonicalEventAnalysisResult, 
    control_registers: list[str], 
    max_paths_per_region: int = 200_000, 
) -> ControlAutomatonAnalysis: 
    widths = _widths(structural)
    regs = [r for r in control_registers if r in widths]
    event_edges = _success_edges(canonical_events)
    outcome_counts: Counter[tuple] = Counter()
    cpu_paths = 0

    for ce in canonical_events.canonical_events: 
        region = reach.regions[ce.region]
        actual = {(e.source, e.target) for e in region.edges}
        success = event_edges[ce.event_id] & actual
        if not success: 
            continue
        paths, trunc = _paths(region, max_paths_per_region)
        if trunc: 
            raise RuntimeError(f'{ce.event_id}: path enumeration truncated')
        order = _edge_order(region)
        for _nodes, edges in paths: 
            es = set(edges)
            if not (es & success): 
                continue
            cpu_paths += 1
            state = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
            for edge in order: 
                ek = (edge.source, edge.target)
                if ek not in es: 
                    continue
                for w in next_state_expr.by_edge.get(ek, []): 
                    if w.family in state: 
                        state[w.family] = w.expression
            sig = tuple((r, expr_key(state[r])) for r in regs)
            outcome_counts[(ce.event_id, sig)] += 1

    outcomes: list[ControlOutcome] = []
    masks = set()
    all_deps: set[str] = set()
    for (event_id, sig), count in sorted(outcome_counts.items(), key = lambda kv: (kv[0][0], repr(kv[0][1]))): 
        # Reconstruct only from keys for signature display; for classification rerun a small decoder
        # by matching sig against paths would be expensive.  expr_key has stable tagged tuples, so classify keys.
        kinds = {}
        changed = []
        deps = set()
        simple = True
        for reg, key in sig: 
            tag = key[0] if isinstance(key, tuple) and key else 'UNKNOWN'
            if tag == 'STATE': 
                src = key[1]
                kinds[reg] = 'HOLD' if src == reg else 'COPY_STATE'
                if src != reg: 
                    changed.append(reg)
                    deps.add(src)
            elif tag == 'CONST': 
                kinds[reg] = f'CONST({key[1]})'
                changed.append(reg)
            else: 
                kinds[reg] = str(tag)
                changed.append(reg)
                simple = False
                # recursively collect STATE tags
                def walk(k): 
                    if isinstance(k, tuple): 
                        if k and k[0] == 'STATE' and len(k) > 1: 
                            deps.add(k[1])
                        for x in k[1:]: 
                            walk(x)
                    elif isinstance(k, list): 
                        for x in k: 
                            walk(x)
                walk(key)
        masks.add(tuple(changed))
        all_deps.update(deps)
        outcomes.append(ControlOutcome(
            event_id = event_id, 
            signature = repr(sig), 
            occurrences = count, 
            register_kinds = kinds, 
            changed_registers = changed, 
            source_state_dependencies = sorted(deps), 
            all_simple = simple, 
        ))

    n = len(outcomes)
    bits = 0
    x = max(1, n)
    while (1 << bits) < x: 
        bits += 1
    return ControlAutomatonAnalysis(
        control_registers = regs, 
        control_bits = sum(widths[r] for r in regs), 
        cpu_paths = cpu_paths, 
        distinct_outcomes = n, 
        simple_outcomes = sum(o.all_simple for o in outcomes), 
        complex_outcomes = sum(not o.all_simple for o in outcomes), 
        distinct_update_masks = len(masks), 
        source_dependency_registers = sorted(all_deps), 
        theoretical_code_bits_for_outcomes = bits, 
        outcomes = outcomes, 
        notes = [
            'This is a transition-template diagnostic, not yet a state-minimization proof.', 
            'A simple outcome updates each control register only by HOLD, constant load, or state copy.', 
            'The theoretical code width is only an information bound for the number of distinct outcome templates; it does not prove that current control storage can be replaced by that many bits.', 
        ], 
    )


def write_control_automaton_report(r: ControlAutomatonAnalysis, path: Path) -> None: 
    lines = [
        'CONTROL-AUTOMATON RECOVERY DIAGNOSTIC', '='*78, 
        'control registers : ' + ', '.join(r.control_registers), 
        f'control bits      : {r.control_bits}', 
        f'CPU paths         : {r.cpu_paths}', 
        f'distinct outcomes : {r.distinct_outcomes}', 
        f'simple outcomes   : {r.simple_outcomes}', 
        f'complex outcomes  : {r.complex_outcomes}', 
        f'update masks      : {r.distinct_update_masks}', 
        f'outcome info bound: {r.theoretical_code_bits_for_outcomes} bit', 
        'source state deps  : ' + (', '.join(r.source_dependency_registers) or '-'), '', 
    ]
    for o in r.outcomes: 
        lines += [
            f'{o.event_id} x{o.occurrences}: {"SIMPLE" if o.all_simple else "COMPLEX"}', 
            '  changed : ' + (', '.join(o.changed_registers) or '-'), 
            '  deps    : ' + (', '.join(o.source_state_dependencies) or '-'), 
            '  ops     : ' + ', '.join(f'{k}={v}' for k, v in o.register_kinds.items()), 
            '', 
        ]
    lines += ['Notes', '-'*78] + [f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
