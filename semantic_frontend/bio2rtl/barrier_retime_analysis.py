from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .definition_site_analysis import DefinitionSiteAnalysis
from .next_state_expr import NextStateExprIR, NextStateWrite
from .sample_barrier_analysis import SampleBarrierAnalysis
from .sample_barrier_fusion_analysis import SampleBarrierFusionAnalysis
from .semantic_reach import SemanticReachResult
from .selective_hoist_analysis import analyze_selective_hoists, SelectiveHoistAnalysis

@dataclass
class RetimedWrite: 
    register: str
    operation_kind: str
    old_edge: tuple[int, int]
    new_edges: list[tuple[int, int]]

@dataclass
class BarrierRetimeAnalysis: 
    retimed_writes: list[RetimedWrite]
    retimed_count: int
    post_retime_hoist: SelectiveHoistAnalysis
    notes: list[str]

def build_retimed_next_state(reach: SemanticReachResult, next_state: NextStateExprIR, behavior: HardwareBehaviorIR, 
    barriers: SampleBarrierAnalysis, fusion: SampleBarrierFusionAnalysis): 
    fusable = {b.sample_block for b in fusion.barriers if b.fusable}
    crossing_keys = set()
    kind_by_key = {}
    for br in barriers.rules: 
        if any(x in fusable for x in br.sample_barriers): 
            # find matching behavior rules by register and definition expression occurrence across region later
            for rule in behavior.objects[br.register].rules: 
                # only rules that actually have a source edge in the barrier region
                if any(any(e.source_block == sb for sb in br.sample_barriers) for e in rule.guard_edges): 
                    k = (rule.register, repr(expr_key(rule.expression)))
                    crossing_keys.add(k)
                    kind_by_key[k] = rule.operation_kind
    incoming = defaultdict(list)
    for rn, r in reach.regions.items(): 
        for e in r.edges: 
            if e.target in fusable and r.start_sample!=e.target: 
                incoming[e.target].append((e.source, e.target))
    old_barrier_edges = set()
    for sb in fusable: 
        r = next((r for r in reach.regions.values() if r.start_sample == sb), None)
        if r: 
            for e in r.edges: 
                if e.source == sb: 
                    old_barrier_edges.add((e.source, e.target))
    new_by = defaultdict(list)
    retimed = []
    for edge, writes in next_state.by_edge.items(): 
        for w in writes: 
            k = (w.family, repr(expr_key(w.expression)))
            if edge in old_barrier_edges and k in crossing_keys: 
                # identify sample barrier from source
                sb = edge[0]
                dests = incoming.get(sb, [])
                for ne in dests: 
                    new_by[ne].append(NextStateWrite(ne[0], ne[1], w.family, w.kind, w.expression))
                retimed.append(RetimedWrite(w.family, kind_by_key[k], edge, list(dests)))
            else: 
                new_by[edge].append(w)
    writes = [w for edge in sorted(new_by) for w in new_by[edge]]
    ir = NextStateExprIR(writes = writes, by_edge = dict(new_by), total_writes = len(writes), 
        const_writes = sum(w.kind == 'CONST' for w in writes), state_copy_writes = sum(w.kind == 'STATE_COPY' for w in writes), 
        expression_writes = sum(w.kind == 'EXPR' for w in writes), operation_kinds = set(next_state.operation_kinds), max_expression_depth = next_state.max_expression_depth)
    return ir, retimed

def analyze_barrier_retime(reach, next_state, behavior, defs, barriers, fusion, registers): 
    retimed_ir, retimed = build_retimed_next_state(reach, next_state, behavior, barriers, fusion)
    hoist = analyze_selective_hoists(reach, retimed_ir, behavior, defs, registers)
    return BarrierRetimeAnalysis(retimed, len(retimed), hoist, [
      'Only writes crossing a barrier already proven fusable are retimed to incoming barrier edges.', 
      'The post-retime selective-hoist analysis is again exhaustive over all semantic-region paths of the retimed state semantics.', 
      'Observational equivalence of the retime relies on the pure-barrier proof: the crossing states are neither read nor externally observed inside the barrier region.', 
    ])

def write_barrier_retime_report(r, path: Path): 
    lines = ['PURE-BARRIER RETIME -> OPERATION HOIST DIAGNOSTIC', '='*78, f'retimed write occurrences: {r.retimed_count}', '']
    for x in r.retimed_writes: 
      lines += [f'{x.register}: {x.operation_kind} {x.old_edge} -> {x.new_edges}']
    h = r.post_retime_hoist
    lines += ['', f'post-retime hoist candidates : {h.tested_candidates}', f'post-retime equivalent        : {h.equivalent_candidates}', 
              f'post-retime removable writes  : {h.removable_edge_writes}', f'post-retime joint equivalence : {"PASS" if h.jointly_equivalent else "FAIL"}', '']
    for c in h.candidates: 
      lines += [f'{c.register}: {c.operation_kind} equiv={c.equivalent} occ={c.edge_occurrences} removable={c.removable_edge_writes} mismatches={c.mismatches}']
    lines += ['', 'Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
