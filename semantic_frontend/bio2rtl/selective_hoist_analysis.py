from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .definition_site_analysis import DefinitionSiteAnalysis
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .definition_site_equivalence import _paths

@dataclass
class HoistCandidate: 
    register: str
    operation_kind: str
    expression_key: str
    definition_block: int
    edge_occurrences: int
    equivalent: bool
    paths_checked: int
    mismatches: int
    removable_edge_writes: int
    mismatch_samples: list[str]

@dataclass
class SelectiveHoistAnalysis: 
    candidates: list[HoistCandidate]
    tested_candidates: int
    equivalent_candidates: int
    removable_edge_writes: int
    jointly_equivalent: bool
    joint_paths_checked: int
    joint_mismatches: int
    notes: list[str]


def _rule_key(family, expr): 
    return (family, repr(expr_key(expr)))

def _simulate_candidate(reach, edge_ir, registers, cands): 
    cand_by_def = {}
    cand_keys = set()
    for c, expr in cands: 
        cand_by_def.setdefault(c.definition_block, []).append((c, expr))
        cand_keys.add((c.register, c.expression_key))
    state0 = {f: Expr(kind = 'STATE', state_family = f) for f in registers}
    paths_checked = mismatches = 0
    samples = []
    for rn, region in reach.regions.items(): 
        paths, trunc = _paths(region, 200000)
        if trunc: 
            return paths_checked, mismatches+1, samples
        for nodes, edges in paths: 
            paths_checked+=1
            base = dict(state0)
            hyb = dict(state0)
            # baseline edge semantics
            for edge in edges: 
                for w in edge_ir.by_edge.get(edge, []): 
                    base[w.family] = w.expression
            # hybrid in actual path order: hoisted definition, then outgoing edge commit.
            for i, node in enumerate(nodes): 
                for c, expr in cand_by_def.get(node, []): 
                    hyb[c.register] = expr
                if i < len(edges): 
                    edge = edges[i]
                    # edge list may include final external edge while nodes end at source; alignment holds for simple path.
                    if edge[0] != node: 
                        # Find the outgoing edge from this node in the path.
                        matches = [e for e in edges if e[0] == node]
                        edge = matches[0] if matches else None
                    if edge is not None: 
                        for w in edge_ir.by_edge.get(edge, []): 
                            if _rule_key(w.family, w.expression) in cand_keys: 
                                continue
                            hyb[w.family] = w.expression
            # external edge from final node may not have been applied if edges longer than nodes-1
            if edges and nodes and edges[-1][0] == nodes[-1]: 
                e = edges[-1]
                # avoid double apply: if len(edges)==len(nodes)-1 it was internal last edge; external has len(edges)==len(nodes)
                if len(edges)>=len(nodes): 
                    for w in edge_ir.by_edge.get(e, []): 
                        if _rule_key(w.family, w.expression) in cand_keys: 
                            continue
                        hyb[w.family] = w.expression
            bad = [f for f in registers if expr_key(base[f])!=expr_key(hyb[f])]
            if bad: 
                mismatches+=1
                if len(samples)<20: 
                    samples.append(f"{rn}: {'->'.join(f'BB{x:03d}' for x in nodes)} :: {','.join(bad)}")
    return paths_checked, mismatches, samples


def analyze_selective_hoists(reach: SemanticReachResult, edge_ir: NextStateExprIR, 
    behavior: HardwareBehaviorIR, definitions: DefinitionSiteAnalysis, registers: list[str]): 
    defmap = {(f.family, f.expression_key): f for f in definitions.forms}
    raw = []
    for rule in behavior.rules: 
        ek = repr(expr_key(rule.expression))
        form = defmap.get((rule.register, ek))
        if form and form.definition_count == 1 and rule.occurrence_count>1: 
            raw.append((rule, form.definition_blocks[0]))
    rows = []
    passing = []
    for rule, b in raw: 
        c = HoistCandidate(rule.register, rule.operation_kind, repr(expr_key(rule.expression)), b, rule.occurrence_count, False, 0, 0, 0, [])
        n, m, samples = _simulate_candidate(reach, edge_ir, registers, [(c, rule.expression)])
        c.paths_checked = n
        c.mismatches = m
        c.mismatch_samples = samples
        c.equivalent = (m == 0)
        c.removable_edge_writes = (rule.occurrence_count-1 if m == 0 else 0)
        rows.append(c)
        if c.equivalent: 
            passing.append((c, rule.expression))
    jn, jm, _js = _simulate_candidate(reach, edge_ir, registers, passing) if passing else (0, 0, [])
    return SelectiveHoistAnalysis(rows, len(rows), sum(c.equivalent for c in rows), sum(c.removable_edge_writes for c in rows), jm == 0, jn, jm, [
        'Only update forms with exactly one original STATE_DEF and multiple edge-exit copies are candidates.', 
        'Each candidate is checked over every enumerated semantic-region CFG path against the established edge-write final-state semantics.', 
        'Passing candidates are then checked jointly; no candidate is enabled merely because it looks like a shift/counter pattern.', 
    ])

def write_selective_hoist_report(r, path: Path): 
    lines = ['SELECTIVE DEFINITION HOIST EQUIVALENCE', '='*78, 
      f'tested candidates       : {r.tested_candidates}', f'equivalent candidates   : {r.equivalent_candidates}', 
      f'removable edge writes   : {r.removable_edge_writes}', f'joint equivalence        : {"PASS" if r.jointly_equivalent else "FAIL"}', 
      f'joint paths checked      : {r.joint_paths_checked}', f'joint mismatches         : {r.joint_mismatches}', '']
    for c in r.candidates: 
      lines += [f'{c.register}: {c.operation_kind}', f'  definition      : BB{c.definition_block:03d}', f'  edge occurrences: {c.edge_occurrences}', 
                f'  equivalence     : {"PASS" if c.equivalent else "FAIL"} ({c.mismatches}/{c.paths_checked} mismatches)', 
                f'  removable writes: {c.removable_edge_writes}']
      for sm in c.mismatch_samples[:8]: 
          lines.append(f'    mismatch: {sm}')
      lines.append('')
    lines += ['Notes', '-'*78]+[f'- {x}' for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
