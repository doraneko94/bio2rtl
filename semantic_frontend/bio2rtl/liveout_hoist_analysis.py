from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .definition_site_analysis import DefinitionSiteAnalysis
from .definition_site_equivalence import _paths
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult

@dataclass
class LiveoutCandidate: 
    register: str
    operation_kind: str
    definition_block: int
    edge_occurrences: int
    unsafe_paths: int
    kill_blocks: list[int]
    kill_edges: list[tuple[int, int]]
    guarded_equivalent: bool
    paths_checked: int
    mismatches: int
    removable_edge_writes: int
    mismatch_samples: list[str]

@dataclass
class LiveoutHoistAnalysis: 
    candidates: list[LiveoutCandidate]
    recovered_candidates: int
    removable_edge_writes: int
    notes: list[str]

def _key(f, e): 
    return (f, repr(expr_key(e)))

def analyze_liveout_hoists(reach: SemanticReachResult, edge_ir: NextStateExprIR, behavior: HardwareBehaviorIR, 
    definitions: DefinitionSiteAnalysis, registers: list[str]): 
    defmap = {(f.family, f.expression_key): f for f in definitions.forms}
    state0 = {f: Expr(kind = 'STATE', state_family = f) for f in registers}
    rows = []
    for rule in behavior.rules: 
        ek = repr(expr_key(rule.expression))
        form = defmap.get((rule.register, ek))
        if not form or form.definition_count!=1 or rule.occurrence_count<=1: 
            continue
        db = form.definition_blocks[0]
        unsafe = []
        allpaths = []
        for rn, region in reach.regions.items(): 
            paths, trunc = _paths(region, 200000)
            if trunc: 
                continue
            for nodes, edges in paths: 
                if db not in nodes: 
                    continue
                base = dict(state0)
                for e in edges: 
                    for w in edge_ir.by_edge.get(e, []): 
                        base[w.family] = w.expression
                # A path is unsafe for an unconditional hoist only when the established final
                # value is not this rule's expression and no later baseline write would override
                # the hoisted value in a hybrid implementation. Approximate directly by hybrid sim.
                hyb = dict(state0)
                hoisted = False
                for i, node in enumerate(nodes): 
                    if node == db: 
                        hyb[rule.register] = rule.expression
                        hoisted = True
                    # apply outgoing edge writes, suppress this rule's duplicates
                    matches = [e for e in edges if e[0] == node]
                    if matches: 
                        e = matches[0]
                        for w in edge_ir.by_edge.get(e, []): 
                            if _key(w.family, w.expression) == (rule.register, ek): 
                                continue
                            hyb[w.family] = w.expression
                bad = expr_key(base[rule.register])!=expr_key(hyb[rule.register])
                rec = (rn, nodes, edges, bad)
                allpaths.append(rec)
                if bad: 
                    unsafe.append(rec)
        # Find a conservative kill block set. Prefer terminal internal blocks of unsafe paths
        # when none of those blocks occur on a safe path reaching the same definition.
        unsafe_leaves = {nodes[-1] for _, nodes, _, _ in unsafe}
        safe_nodes = set()
        for _, nodes, _, bad in allpaths: 
            if not bad: 
                safe_nodes.update(nodes)
        kill = sorted(b for b in unsafe_leaves if b not in safe_nodes)
        # Prefer path-discriminating edges.  An external sample transition can
        # be the true live-out kill condition even when its source block is
        # shared with paths that keep the value.
        unsafe_edge_sets = [set(edges) for _, _, edges, _ in unsafe]
        common_unsafe_edges = set.intersection(*unsafe_edge_sets) if unsafe_edge_sets else set()
        safe_edges = set()
        for _, _, edges, bad in allpaths: 
            if not bad: 
                safe_edges.update(edges)
        kill_edges = sorted(common_unsafe_edges-safe_edges)
        # Verify guard: hoist only if no recovered kill block/edge is reached.
        mism = 0
        checked = 0
        samples = []
        for rn, region in reach.regions.items(): 
            paths, trunc = _paths(region, 200000)
            if trunc: 
                mism+=1
                continue
            for nodes, edges in paths: 
                checked+=1
                base = dict(state0)
                hyb = dict(state0)
                for e in edges: 
                    for w in edge_ir.by_edge.get(e, []): 
                        base[w.family] = w.expression
                do_hoist = (db in nodes and not any(k in nodes for k in kill) and not any(e in edges for e in kill_edges))
                for node in nodes: 
                    if node == db and do_hoist: 
                        hyb[rule.register] = rule.expression
                    matches = [e for e in edges if e[0] == node]
                    if matches: 
                        e = matches[0]
                        for w in edge_ir.by_edge.get(e, []): 
                            if _key(w.family, w.expression) == (rule.register, ek): 
                                continue
                            hyb[w.family] = w.expression
                bad = [f for f in registers if expr_key(base[f])!=expr_key(hyb[f])]
                if bad: 
                    mism+=1
                    if len(samples)<10: 
                        samples.append(f"{rn}: {'->'.join(f'BB{x:03d}' for x in nodes)} edges={edges} bad={bad}")
        ok = (mism == 0 and bool(kill or not unsafe))
        rows.append(LiveoutCandidate(rule.register, rule.operation_kind, db, rule.occurrence_count, len(unsafe), kill, kill_edges, ok, checked, mism, 
            rule.occurrence_count-1 if ok else 0, samples))
    return LiveoutHoistAnalysis(rows, sum(r.guarded_equivalent for r in rows), sum(r.removable_edge_writes for r in rows), [
      'A definition is split into operation and live-out retention. Hoisting is suppressed on recovered kill paths where the computed value is not persistent at the next semantic boundary.', 
      'Kill blocks are inferred only from exhaustive path comparison and then re-verified against all persistent registers.', 
      'This is still a CFG-level diagnostic; kill blocks must later be lowered to semantic phase/state conditions before the dedicated backend is finalized.', 
    ])

def write_liveout_hoist_report(r, path: Path): 
    lines = ['OPERATION / LIVE-OUT RECOVERY DIAGNOSTIC', '='*78, f'candidates recovered : {r.recovered_candidates}', f'removable edge writes: {r.removable_edge_writes}', '']
    for x in r.candidates: 
      lines += [f'{x.register}: {x.operation_kind}', f'  definition       : BB{x.definition_block:03d}', f'  edge occurrences : {x.edge_occurrences}', 
                f'  unsafe paths     : {x.unsafe_paths}', f'  kill blocks      : {", ".join(f"BB{b:03d}" for b in x.kill_blocks) or "-"}', 
                f'  kill edges       : {", ".join(f"BB{a:03d}->BB{b:03d}" for a,b in x.kill_edges) or "-"}', 
                f'  guarded equivalence: {"PASS" if x.guarded_equivalent else "FAIL"} ({x.mismatches}/{x.paths_checked})', f'  removable writes: {x.removable_edge_writes}']
      for sm in x.mismatch_samples: 
          lines.append(f'    mismatch: {sm}')
      lines.append('')
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
