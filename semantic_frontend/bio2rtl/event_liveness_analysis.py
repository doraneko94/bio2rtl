from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths, _block_order
from .definition_site_ir import DefinitionSiteIR
from .next_state_expr import Expr
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticExpr, SemanticSSAResult
from .gpio_effect import GPIOEffectResult
from .structural_netlist import StructuralNetlist


def _expr_reads(expr: Expr) -> set[str]: 
    out = set()
    if expr.kind == 'STATE' and expr.state_family: 
        out.add(expr.state_family)
    for a in expr.args: 
        out |= _expr_reads(a)
    return out


def _sem_reads(expr: SemanticExpr) -> set[str]: 
    out = set()
    if expr.kind == 'STATE' and expr.state_family: 
        out.add(expr.state_family)
    for a in expr.args: 
        out |= _sem_reads(a)
    for _, a in expr.phi_inputs: 
        out |= _sem_reads(a)
    return out


def _condition_reads(cond) -> set[str]: 
    return _sem_reads(cond.lhs) | _sem_reads(cond.rhs)


@dataclass
class EventLivenessRow: 
    event_id: str
    cpu_paths: int
    live_in_registers: list[str]
    live_in_bits: int
    definitely_written_registers: list[str]
    maybe_written_registers: list[str]


@dataclass
class EventLivenessAnalysis: 
    rows: list[EventLivenessRow]
    persistent_registers: list[str]
    persistent_bits: int
    non_live_in_registers: list[str]
    non_live_in_bits: int
    notes: list[str]


def analyze_event_boundary_liveness(
    reach: SemanticReachResult, 
    canonical_events: CanonicalEventAnalysisResult, 
    definitions: DefinitionSiteIR, 
    semantic_ssa: SemanticSSAResult, 
    gpio_effects: GPIOEffectResult, 
    structural: StructuralNetlist, 
    max_paths_per_region: int = 200000, 
) -> EventLivenessAnalysis: 
    widths = {f: (32 if structural.nodes[n].width is None else int(structural.nodes[n].width)) for f, n in structural.state_nodes.items()}
    all_regs = set(widths)
    rows = []
    persistent = set()

    for ce in canonical_events.canonical_events: 
        region = reach.regions[ce.region]
        actual = {(e.source, e.target) for e in region.edges}
        success = {(a, b) for a in ce.detector_blocks for b in ce.success_targets if (a, b) in actual}
        paths, trunc = _paths(region, max_paths_per_region)
        if trunc: 
            raise RuntimeError(f'{ce.event_id}: path enumeration truncated')
        selected = [p for p in paths if set(p[1]) & success]
        block_order = _block_order(region)
        event_live = set()
        written_sets = []

        for nodes, edges in selected: 
            node_set = set(nodes)
            written = set()
            live = set()
            for b in block_order: 
                if b not in node_set: 
                    continue
                # Conservative within-block ordering: externally/control-visible
                # reads are considered before state definitions in the same block.
                cond = semantic_ssa.branch_conditions.get(b)
                if cond is not None: 
                    for r in _condition_reads(cond): 
                        if r not in written: 
                            live.add(r)
                for idx, _effect in enumerate(gpio_effects.by_block.get(b, [])): 
                    arg = semantic_ssa.gpio_arguments.get((b, idx))
                    if arg is not None: 
                        for r in _sem_reads(arg): 
                            if r not in written: 
                                live.add(r)
                for w in sorted(definitions.by_block.get(b, []), key = lambda x: x.order): 
                    for r in _expr_reads(w.expression): 
                        if r not in written: 
                            live.add(r)
                    written.add(w.family)
            event_live |= live
            written_sets.append(written)

        definite = set.intersection(*written_sets) if written_sets else set()
        maybe = set.union(*written_sets) if written_sets else set()
        persistent |= event_live
        rows.append(EventLivenessRow(
            event_id = ce.event_id, cpu_paths = len(selected), 
            live_in_registers = sorted(event_live), 
            live_in_bits = sum(widths[r] for r in event_live), 
            definitely_written_registers = sorted(definite), 
            maybe_written_registers = sorted(maybe), 
        ))

    nonlive = all_regs-persistent
    return EventLivenessAnalysis(
        rows = rows, 
        persistent_registers = sorted(persistent), 
        persistent_bits = sum(widths[r] for r in persistent), 
        non_live_in_registers = sorted(nonlive), 
        non_live_in_bits = sum(widths[r] for r in nonlive), 
        notes = [
            'A physical state is event-live-in when some canonical-event CPU path reads its old value before any definition of that state on the same path.', 
            'Reads include branch predicates, GPIO-effect arguments, and RHS operands of original STATE_DEF assignments.', 
            'Within one basic block reads are conservatively ordered before definitions, so this analysis may overestimate persistence but does not underestimate it.', 
            'States never event-live-in are candidates for demotion from FF storage to event-local SSA wires; a separate live-out/use proof is still required before removal.', 
        ], 
    )


def write_event_liveness_report(r: EventLivenessAnalysis, path: Path): 
    lines = ['EVENT-BOUNDARY PERSISTENT-STATE LIVENESS', '='*78, 
           f'event-live-in registers : {len(r.persistent_registers)}', 
           f'event-live-in bits      : {r.persistent_bits}', 
           f'non-live-in registers   : {len(r.non_live_in_registers)}', 
           f'non-live-in bits        : {r.non_live_in_bits}', 
           'persistent registers  : '+(', '.join(r.persistent_registers) or '-'), 
           'demotion candidates   : '+(', '.join(r.non_live_in_registers) or '-'), '']
    for x in r.rows: 
        lines += [f'{x.event_id}: paths={x.cpu_paths} live-in bits={x.live_in_bits}', 
                  '  live-in           : '+(', '.join(x.live_in_registers) or '-'), 
                  '  definitely written: '+(', '.join(x.definitely_written_registers) or '-'), 
                  '  maybe written     : '+(', '.join(x.maybe_written_registers) or '-'), '']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
