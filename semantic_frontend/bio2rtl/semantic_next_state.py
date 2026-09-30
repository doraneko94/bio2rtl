from __future__ import annotations

from dataclasses import dataclass

from .next_state_expr import NextStateExprIR, NextStateWrite
from .semantic_reach import ReachRegion, SemanticReachResult


@dataclass(frozen = True)
class GuardExpr: 
    kind: str
    # ACTIVE, PRED, NOT, AND, OR, CONST
    region_name: str | None = None
    block_id: int | None = None
    value: bool | None = None
    args: tuple["GuardExpr", ...] = ()


@dataclass(frozen = True)
class FoldedStateWrite: 
    region_name: str
    source_block: int
    target_block: int
    guard: GuardExpr
    write: NextStateWrite


@dataclass
class SemanticNextStateResult: 
    writes: tuple[FoldedStateWrite, ...]
    by_region: dict[str, tuple[FoldedStateWrite, ...]]
    edge_guards_by_region: dict[str, dict[tuple[int, int], GuardExpr]]
    block_guards_by_region: dict[str, dict[int, GuardExpr]]
    original_state_write_edges: int
    folded_state_writes: int


def _const(value: bool) -> GuardExpr: 
    return GuardExpr(kind = "CONST", value = value)


def _active(region_name: str) -> GuardExpr: 
    return GuardExpr(kind = "ACTIVE", region_name = region_name)


def _pred(block_id: int) -> GuardExpr: 
    return GuardExpr(kind = "PRED", block_id = block_id)


def _not(expr: GuardExpr) -> GuardExpr: 
    if expr.kind == "CONST": 
        return _const(not bool(expr.value))
    if expr.kind == "NOT": 
        return expr.args[0]
    return GuardExpr(kind = "NOT", args = (expr,))


def _and(*items: GuardExpr) -> GuardExpr: 
    flat: list[GuardExpr] = []
    for item in items: 
        if item.kind == "CONST": 
            if not item.value: 
                return _const(False)
            continue
        if item.kind == "AND": 
            flat.extend(item.args)
        else: 
            flat.append(item)
    if not flat: 
        return _const(True)
    unique = tuple(dict.fromkeys(flat))
    if len(unique) == 1: 
        return unique[0]
    return GuardExpr(kind = "AND", args = unique)


def _or(*items: GuardExpr) -> GuardExpr: 
    flat: list[GuardExpr] = []
    for item in items: 
        if item.kind == "CONST": 
            if item.value: 
                return _const(True)
            continue
        if item.kind == "OR": 
            flat.extend(item.args)
        else: 
            flat.append(item)
    if not flat: 
        return _const(False)
    unique = tuple(dict.fromkeys(flat))
    if len(unique) == 1: 
        return unique[0]
    return GuardExpr(kind = "OR", args = unique)


def _region_guards(
    region: ReachRegion, 
) -> tuple[
    dict[tuple[int, int], GuardExpr], 
    dict[int, GuardExpr], 
]: 
    """Build symbolic path guards without materializing reach_/edge_ nets.

    Semantic regions are required to be acyclic.  We therefore propagate a
    symbolic reach predicate in topological order.  The result is a Boolean
    DAG over only region-active and branch predicates.
    """
    internal = set(region.blocks)
    incoming: dict[int, list[tuple[int, int, str]]] = {b: [] for b in internal}
    outgoing: dict[int, list[tuple[int, int, str]]] = {}
    indegree: dict[int, int] = {b: 0 for b in internal}

    for edge in region.edges: 
        key = (edge.source, edge.target, edge.kind)
        outgoing.setdefault(edge.source, []).append(key)
        if edge.target in internal: 
            incoming.setdefault(edge.target, []).append(key)
            if edge.source in internal: 
                indegree[edge.target] += 1

    roots: list[int] = []
    if region.start_sample is None: 
        roots = [b for b in internal if indegree.get(b, 0) == 0]
    else: 
        # Edges leaving the sample block start from region-active directly.
        roots = [region.start_sample]

    reach_guard: dict[int, GuardExpr] = {}
    if region.start_sample is not None: 
        reach_guard[region.start_sample] = _active(region.name)
    else: 
        for root in roots: 
            reach_guard[root] = _active(region.name)

    # Kahn-like propagation.  Exit sample targets are not internal nodes.
    pending = set(internal)

    # ENTRY has no real sample block.  Its zero-indegree CFG roots are
    # seeded directly with the region-active guard, so they are already
    # resolved and must not remain in the Kahn work set.  For ordinary
    # sample regions the start sample is outside ``region.blocks`` in the
    # current ReachRegion representation, but discard it defensively.
    if region.start_sample is not None: 
        pending.discard(region.start_sample)
    else: 
        for root in roots: 
            pending.discard(root)

    edge_guards: dict[tuple[int, int], GuardExpr] = {}
    progressed = True
    while pending and progressed: 
        progressed = False
        for block in sorted(tuple(pending)): 
            ins = incoming.get(block, [])
            if not ins: 
                continue
            if any((src not in reach_guard) for src, _dst, _kind in ins): 
                continue
            candidates: list[GuardExpr] = []
            for src, dst, kind in ins: 
                base = reach_guard[src]
                if kind == "TRUE": 
                    guard = _and(base, _pred(src))
                elif kind == "FALSE": 
                    guard = _and(base, _not(_pred(src)))
                else: 
                    guard = base
                edge_guards[(src, dst)] = guard
                candidates.append(guard)
            reach_guard[block] = _or(*candidates)
            pending.remove(block)
            progressed = True

    if pending: 
        raise RuntimeError(
            f"{region.name}: folded next-state guard construction "
            f"could not resolve blocks {sorted(pending)}"
        )

    # Emit guards for all remaining outgoing edges, including exits.
    for edge in region.edges: 
        key = (edge.source, edge.target)
        if key in edge_guards: 
            continue
        base = reach_guard.get(edge.source)
        if base is None: 
            raise RuntimeError(
                f"{region.name}: no symbolic reach guard for BB{edge.source:03d}"
            )
        if edge.kind == "TRUE": 
            guard = _and(base, _pred(edge.source))
        elif edge.kind == "FALSE": 
            guard = _and(base, _not(_pred(edge.source)))
        else: 
            guard = base
        edge_guards[key] = guard

    return edge_guards, reach_guard


def build_semantic_next_state(
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
) -> SemanticNextStateResult: 
    folded: list[FoldedStateWrite] = []
    by_region: dict[str, tuple[FoldedStateWrite, ...]] = {}
    original_edges: set[tuple[int, int]] = set()
    edge_guards_by_region: dict[str, dict[tuple[int, int], GuardExpr]] = {}
    block_guards_by_region: dict[str, dict[int, GuardExpr]] = {}

    for region_name, region in reach.regions.items(): 
        guards, block_guards = _region_guards(region)
        edge_guards_by_region[region_name] = guards
        block_guards_by_region[region_name] = block_guards
        region_writes: list[FoldedStateWrite] = []
        for edge in region.edges: 
            writes = next_state_expr.by_edge.get((edge.source, edge.target), [])
            if not writes: 
                continue
            original_edges.add((edge.source, edge.target))
            guard = guards[(edge.source, edge.target)]
            for write in writes: 
                item = FoldedStateWrite(
                    region_name = region_name, 
                    source_block = edge.source, 
                    target_block = edge.target, 
                    guard = guard, 
                    write = write, 
                )
                folded.append(item)
                region_writes.append(item)
        by_region[region_name] = tuple(region_writes)

    return SemanticNextStateResult(
        writes = tuple(folded), 
        by_region = by_region, 
        edge_guards_by_region = edge_guards_by_region, 
        block_guards_by_region = block_guards_by_region, 
        original_state_write_edges = len(original_edges), 
        folded_state_writes = len(folded), 
    )


def print_semantic_next_state(result: SemanticNextStateResult) -> None: 
    print()
    print("=" * 72)
    print("SEMANTIC NEXT-STATE FOLDING")
    print("=" * 72)
    print(f"state-write edges       : {result.original_state_write_edges}")
    print(f"folded state writes     : {result.folded_state_writes}")
    print(f"regions                 : {len(result.by_region)}")
    print("SEMANTIC NEXT-STATE: PASS")
