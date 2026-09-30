from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .definition_site_ir import DefinitionSiteIR
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import ReachRegion, SemanticReachResult


@dataclass
class RegionEquivalence: 
    region: str
    paths_checked: int
    leaf_blocks: int
    mismatches: int
    truncated: bool
    first_mismatches: list[str]


@dataclass
class DefinitionSiteEquivalenceResult: 
    equivalent: bool
    regions: list[RegionEquivalence]
    total_paths_checked: int
    total_mismatches: int
    notes: list[str]


def _entry(region: ReachRegion) -> int | None: 
    if region.start_sample is not None: 
        return region.start_sample
    internal = set(region.blocks)
    indeg = {b: 0 for b in internal}
    for e in region.edges: 
        if e.source in internal and e.target in internal: 
            indeg[e.target]+=1
    roots = sorted(b for b, d in indeg.items() if d == 0)
    return roots[0] if len(roots) == 1 else None


def _depths(region: ReachRegion) -> dict[int, int]: 
    root = _entry(region)
    if root is None: 
        return {}
    nodes = set(region.blocks)
    if region.start_sample is not None: 
        nodes.add(region.start_sample)
    d = {root: 0}
    for _ in range(len(nodes)+1): 
        changed = False
        for e in region.edges: 
            if e.source in d and e.target in nodes: 
                nd = d[e.source]+1
                if e.target not in d or nd>d[e.target]: 
                    d[e.target] = nd
                    changed = True
        if not changed: 
            break
    return d


def _edge_order(region: ReachRegion): 
    d = _depths(region)
    return sorted(region.edges, key = lambda e: (d.get(e.source, 10**9), e.source, e.target, e.kind))


def _block_order(region: ReachRegion): 
    d = _depths(region)
    blocks = set(region.blocks)
    if region.start_sample is not None: 
        blocks.add(region.start_sample)
    return sorted(blocks, key = lambda b: (d.get(b, 10**9), b))


def _paths(region: ReachRegion, max_paths: int): 
    root = _entry(region)
    if root is None: 
        return [], False
    internal = set(region.blocks)
    if region.start_sample is not None: 
        internal.add(region.start_sample)
    succ = {b: [] for b in internal}
    for e in region.edges: 
        if e.source in internal: 
            succ.setdefault(e.source, []).append(e.target)
    out = []
    truncated = False
    def dfs(node, path_nodes, path_edges, seen): 
        nonlocal truncated
        if len(out)>=max_paths: 
            truncated = True
            return
        candidates = succ.get(node, [])
        internal_next = [x for x in candidates if x in internal and x not in seen]
        external_next = [x for x in candidates if x not in internal]
        if not internal_next: 
            # Each outgoing external edge is a distinct final control path;
            # a true leaf has one synthetic path with no final edge.
            if external_next: 
                for dst in external_next: 
                    if len(out)>=max_paths: 
                        truncated = True
                        return
                    out.append((list(path_nodes), list(path_edges)+[(node, dst)]))
            else: 
                out.append((list(path_nodes), list(path_edges)))
            return
        for dst in internal_next: 
            dfs(dst, path_nodes+[dst], path_edges+[(node, dst)], seen|{dst})
        # If a node can also exit while having internal successors, preserve
        # those exit alternatives as separate paths.
        for dst in external_next: 
            if len(out)>=max_paths: 
                truncated = True
                return
            out.append((list(path_nodes), list(path_edges)+[(node, dst)]))
    dfs(root, [root], [], {root})
    return out, truncated


def analyze_definition_site_equivalence(
    reach: SemanticReachResult, 
    edge_ir: NextStateExprIR, 
    def_ir: DefinitionSiteIR, 
    register_names: list[str], 
    max_paths_per_region: int = 200000, 
) -> DefinitionSiteEquivalenceResult: 
    regions = []
    total_paths = 0
    total_mismatch = 0
    state_expr = {f: Expr(kind = 'STATE', state_family = f) for f in register_names}
    for rn, region in reach.regions.items(): 
        paths, trunc = _paths(region, max_paths_per_region)
        mismatches = []
        mismatch_count = 0
        eorder = _edge_order(region)
        border = _block_order(region)
        for nodes, edges in paths: 
            edge_set = set(edges)
            node_set = set(nodes)
            a = dict(state_expr)
            b = dict(state_expr)
            # Established edge-exit semantics, in the same ordering used by SV.
            for e in eorder: 
                if (e.source, e.target) not in edge_set: 
                    continue
                for w in edge_ir.by_edge.get((e.source, e.target), []): 
                    a[w.family] = w.expression
            # Definition-site semantics, in topological program order.
            for block in border: 
                if block not in node_set: 
                    continue
                for w in sorted(def_ir.by_block.get(block, []), key = lambda x: x.order): 
                    b[w.family] = w.expression
            bad = []
            for f in register_names: 
                if expr_key(a[f])!=expr_key(b[f]): 
                    bad.append(f)
            if bad: 
                mismatch_count+=1
                if len(mismatches)<12: 
                    mismatches.append(
                        f"path {'->'.join(f'BB{x:03d}' for x in nodes)}: " + ', '.join(bad)
                    )
        total_paths+=len(paths)
        total_mismatch+=mismatch_count
        leaves = len({p[0][-1] for p in paths if p[0]})
        regions.append(RegionEquivalence(rn, len(paths), leaves, mismatch_count, trunc, mismatches))
    return DefinitionSiteEquivalenceResult(
        equivalent = (total_mismatch == 0 and not any(r.truncated for r in regions)), 
        regions = regions, total_paths_checked = total_paths, total_mismatches = total_mismatch, 
        notes = [
            'For each semantic-region control path, the established edge-write priority semantics and definition-site priority semantics are symbolically evaluated.', 
            'All persistent registers are compared by structural expression identity at the end of the same combinational region execution.', 
            'A PASS requires zero mismatches and no path-enumeration truncation.', 
        ], 
    )


def write_definition_site_equivalence_report(result: DefinitionSiteEquivalenceResult, path: Path)->None: 
    lines = ['DEFINITION-SITE BACKEND SYMBOLIC EQUIVALENCE', '='*78, 
           f'equivalent          : {"PASS" if result.equivalent else "FAIL"}', 
           f'total paths checked : {result.total_paths_checked}', 
           f'total mismatches    : {result.total_mismatches}', '']
    for r in result.regions: 
        lines += [f'{r.region}: paths={r.paths_checked} leaves={r.leaf_blocks} mismatches={r.mismatches} truncated={r.truncated}']
        lines += [f'  {x}' for x in r.first_mismatches]
    lines += ['', 'Notes', '-'*78]+[f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
