from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .edge_event_analysis import EdgeEventAnalysisResult, PredicateAtom, _predicate_atom
from .next_state_expr import NextStateExprIR
from .semantic_reach import ReachRegion, SemanticReachResult
from .semantic_ssa import SemanticCondition, SemanticExpr, SemanticSSAResult


@dataclass
class RequiredLiteral: 
    block: int
    truth: bool
    category: str
    text: str


@dataclass
class WriteGuardDiagnostic: 
    region: str
    source_block: int
    target_block: int
    write_count: int
    families: list[str]
    events: list[str]
    required_literals: list[RequiredLiteral]
    gpio_level_literals: int
    physical_state_literals: int
    semantic_state_literals: int
    other_literals: int
    event_covered_literals: int
    residual_literals: int
    residual_required_literals: list[RequiredLiteral]


@dataclass
class WriteGuardAnalysisResult: 
    write_edges: list[WriteGuardDiagnostic]
    total_write_edge_contexts: int
    unique_physical_write_edges: int
    event_guarded_contexts: int
    event_guarded_unique_edges: int
    rise_guarded_edges: int
    fall_guarded_edges: int
    gpio_level_guarded_edges: int
    physical_state_guarded_edges: int
    semantic_state_guarded_edges: int
    other_guarded_edges: int
    zero_residual_after_event_edges: int
    total_required_literals: int
    total_event_covered_literals: int
    total_residual_literals: int
    unique_residual_literal_signatures: int
    event_usage: dict[str, int]
    category_counts: dict[str, int]
    top_residual_literals: list[tuple[str, str, int]]
    notes: list[str]


def _successors(region: ReachRegion) -> dict[int, list[tuple[int, str]]]: 
    out: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for edge in region.edges: 
        out[edge.source].append((edge.target, edge.kind))
    return out


def _roots(region: ReachRegion) -> list[int]: 
    # For sampled regions, control enters through the sample block.  ENTRY
    # regions have no start sample, so use internal zero-indegree roots.
    if region.start_sample is not None: 
        return [region.start_sample]
    internal = set(region.blocks)
    indegree = {b: 0 for b in internal}
    for edge in region.edges: 
        if edge.source in internal and edge.target in internal: 
            indegree[edge.target] += 1
    return sorted(b for b, degree in indegree.items() if degree == 0)


def _edge_reachable_without(
    region: ReachRegion, 
    target_edge: tuple[int, int], 
    removed_edge: tuple[int, int] | None, 
) -> bool: 
    """Whether target_edge can execute when removed_edge is unavailable."""
    succ = _successors(region)
    target_src, target_dst = target_edge
    queue = deque(_roots(region))
    seen: set[int] = set()
    while queue: 
        node = queue.popleft()
        if node in seen: 
            continue
        seen.add(node)
        for dst, _kind in succ.get(node, []): 
            edge = (node, dst)
            if removed_edge is not None and edge == removed_edge: 
                continue
            if edge == target_edge: 
                return True
            if dst not in seen: 
                queue.append(dst)
    return False


def _edge_dominates(
    region: ReachRegion, 
    candidate: tuple[int, int], 
    target: tuple[int, int], 
) -> bool: 
    if candidate == target: 
        return True
    # If target itself is unreachable in the original graph, do not claim
    # dominance.  Regions should normally make this impossible.
    if not _edge_reachable_without(region, target, None): 
        return False
    return not _edge_reachable_without(region, target, candidate)


def _expr_deps(expr: SemanticExpr, out: dict[str, set]) -> None: 
    if expr.kind == "STATE" and expr.state_family is not None: 
        out["physical"].add(expr.state_family)
    elif expr.kind == "SEMANTIC_STATE" and expr.semantic_state_name is not None: 
        out["semantic"].add(expr.semantic_state_name)
    elif expr.kind == "GPIO" and expr.gpio_block is not None: 
        out["gpio"].add(expr.gpio_block)
    elif expr.kind == "LIVEIN" and expr.livein_name is not None: 
        out["livein"].add(expr.livein_name)
    for arg in expr.args: 
        _expr_deps(arg, out)
    for _pred, incoming in expr.phi_inputs: 
        _expr_deps(incoming, out)


def _condition_category(cond: SemanticCondition) -> str: 
    # First use the simple GPIO/state atom recognizer shared with event
    # recovery.  It identifies level predicates precisely.
    atom = _predicate_atom(cond)
    if atom is not None: 
        if atom.source == "GPIO_BIT": 
            return "GPIO_LEVEL"
        if atom.source == "STATE_BIT": 
            return "PHYSICAL_STATE"

    deps = {"physical": set(), "semantic": set(), "gpio": set(), "livein": set()}
    _expr_deps(cond.lhs, deps)
    _expr_deps(cond.rhs, deps)
    used = [name for name, values in deps.items() if values]
    if used == ["physical"]: 
        return "PHYSICAL_STATE"
    if used == ["semantic"]: 
        return "SEMANTIC_STATE"
    if used == ["gpio"]: 
        return "GPIO_LEVEL"
    return "OTHER"


def _expr_text(expr: SemanticExpr) -> str: 
    if expr.kind == "CONST": 
        return str(0 if expr.value is None else expr.value)
    if expr.kind == "STATE": 
        return f"STATE({expr.state_family})"
    if expr.kind == "SEMANTIC_STATE": 
        return f"SEM({expr.semantic_state_name})"
    if expr.kind == "GPIO": 
        return f"GPIO@BB{expr.gpio_block:03d}" if expr.gpio_block is not None else "GPIO(?)"
    if expr.kind == "LIVEIN": 
        return f"LIVEIN({expr.livein_name})"
    if expr.kind == "OP": 
        return f"{expr.operation}({','.join(_expr_text(a) for a in expr.args)})"
    if expr.kind == "PHI": 
        return f"PHI@{expr.phi_block}"
    return expr.kind


def _literal_text(block: int, truth: bool, cond: SemanticCondition) -> str: 
    atom = _predicate_atom(cond)
    if atom is not None: 
        level = atom.level if truth else 1 - atom.level
        if atom.source == "GPIO_BIT": 
            return f"GPIO@BB{atom.sample_block:03d}[{atom.bit}]={level}"
        return f"STATE({atom.family})={level}"
    op = cond.operation if truth else f"NOT_{cond.operation}"
    return f"BB{block:03d}:{op}({_expr_text(cond.lhs)},{_expr_text(cond.rhs)})"


def _required_branch_literals(
    region: ReachRegion, 
    write_edge: tuple[int, int], 
    semantic_ssa: SemanticSSAResult, 
) -> list[RequiredLiteral]: 
    succ = _successors(region)
    result: list[RequiredLiteral] = []
    for block, cond in sorted(semantic_ssa.branch_conditions.items()): 
        outgoing = [(dst, kind) for dst, kind in succ.get(block, []) if kind in {"TRUE", "FALSE"}]
        if len(outgoing) < 2: 
            continue
        dominated: list[tuple[int, str]] = []
        for dst, kind in outgoing: 
            if _edge_dominates(region, (block, dst), write_edge): 
                dominated.append((dst, kind))
        if len(dominated) != 1: 
            continue
        _dst, kind = dominated[0]
        truth = kind == "TRUE"
        category = _condition_category(cond)
        result.append(
            RequiredLiteral(
                block = block, 
                truth = truth, 
                category = category, 
                text = _literal_text(block, truth, cond), 
            )
        )
    return result


def _event_constituent_blocks(event) -> set[int]: 
    # path_blocks contains the current-sample branch, intermediate qualifiers,
    # detector branch and success target.  Predicate blocks are all but the
    # terminal target; this is sufficient for measuring guard compression.
    if not event.path_blocks: 
        return set()
    return set(event.path_blocks[:-1])


def analyze_write_guards(
    reach: SemanticReachResult, 
    next_state: NextStateExprIR, 
    semantic_ssa: SemanticSSAResult, 
    events: EdgeEventAnalysisResult, 
) -> WriteGuardAnalysisResult: 
    event_by_region: dict[str, list] = defaultdict(list)
    for event in events.event_candidates: 
        event_by_region[event.region].append(event)

    diagnostics: list[WriteGuardDiagnostic] = []
    event_usage = Counter()
    category_counts = Counter()
    residual_signatures: set[tuple[str, ...]] = set()
    residual_literal_counts = Counter()

    for region_name, region in sorted(reach.regions.items()): 
        write_edges = sorted(
            edge
            for edge in next_state.by_edge
            if any(e.source == edge[0] and e.target == edge[1] for e in region.edges)
        )
        for edge in write_edges: 
            writes = next_state.by_edge.get(edge, [])
            required = _required_branch_literals(region, edge, semantic_ssa)

            dominated_events = []
            covered_blocks: set[int] = set()
            for event in event_by_region.get(region_name, []): 
                event_edge = (event.detector_block, event.success_target)
                if _edge_dominates(region, event_edge, edge): 
                    dominated_events.append(event)
                    event_usage[event.event_id] += 1
                    covered_blocks.update(_event_constituent_blocks(event))

            residual = [lit for lit in required if lit.block not in covered_blocks]
            for lit in residual: 
                category_counts[lit.category] += 1
                residual_literal_counts[(lit.category, lit.text)] += 1
            signature = tuple(sorted(f"{lit.category}:{lit.text}" for lit in residual))
            residual_signatures.add(signature)

            diagnostics.append(
                WriteGuardDiagnostic(
                    region = region_name, 
                    source_block = edge[0], 
                    target_block = edge[1], 
                    write_count = len(writes), 
                    families = sorted({w.family for w in writes}), 
                    events = [event.event_id for event in dominated_events], 
                    required_literals = required, 
                    gpio_level_literals = sum(l.category == "GPIO_LEVEL" for l in residual), 
                    physical_state_literals = sum(l.category == "PHYSICAL_STATE" for l in residual), 
                    semantic_state_literals = sum(l.category == "SEMANTIC_STATE" for l in residual), 
                    other_literals = sum(l.category == "OTHER" for l in residual), 
                    event_covered_literals = len(required) - len(residual), 
                    residual_literals = len(residual), 
                    residual_required_literals = residual, 
                )
            )

    event_kind = {event.event_id: event.edge for event in events.event_candidates}
    total_required = sum(len(x.required_literals) for x in diagnostics)
    covered = sum(x.event_covered_literals for x in diagnostics)
    residual = sum(x.residual_literals for x in diagnostics)

    unique_edges = {(x.source_block, x.target_block) for x in diagnostics}
    event_unique_edges = {
        (x.source_block, x.target_block) for x in diagnostics if x.events
    }
    top_residual = [
        (category, text, count)
        for (category, text), count in residual_literal_counts.most_common(20)
    ]

    return WriteGuardAnalysisResult(
        write_edges = diagnostics, 
        total_write_edge_contexts = len(diagnostics), 
        unique_physical_write_edges = len(unique_edges), 
        event_guarded_contexts = sum(bool(x.events) for x in diagnostics), 
        event_guarded_unique_edges = len(event_unique_edges), 
        rise_guarded_edges = sum(any(event_kind.get(e) == "RISE" for e in x.events) for x in diagnostics), 
        fall_guarded_edges = sum(any(event_kind.get(e) == "FALL" for e in x.events) for x in diagnostics), 
        gpio_level_guarded_edges = sum(x.gpio_level_literals > 0 for x in diagnostics), 
        physical_state_guarded_edges = sum(x.physical_state_literals > 0 for x in diagnostics), 
        semantic_state_guarded_edges = sum(x.semantic_state_literals > 0 for x in diagnostics), 
        other_guarded_edges = sum(x.other_literals > 0 for x in diagnostics), 
        zero_residual_after_event_edges = sum(bool(x.events) and x.residual_literals == 0 for x in diagnostics), 
        total_required_literals = total_required, 
        total_event_covered_literals = covered, 
        total_residual_literals = residual, 
        unique_residual_literal_signatures = len(residual_signatures), 
        event_usage = dict(sorted(event_usage.items())), 
        category_counts = dict(sorted(category_counts.items())), 
        top_residual_literals = top_residual, 
        notes = [
            "A required branch literal is reported only when one branch edge CFG-dominates the physical write edge; this is conservative.", 
            "An event guards a write only when the recovered event success edge CFG-dominates that write edge.", 
            "Event constituent predicates are removed only for compression accounting; generated RTL is not modified.", 
            "Stack offsets and I2C pin names are not used by the classification logic.", 
        ], 
    )


def write_write_guard_report(result: WriteGuardAnalysisResult, path: Path) -> None: 
    lines: list[str] = []
    lines.append("PHYSICAL WRITE GUARD / EVENT ATTRIBUTION DIAGNOSTIC")
    lines.append("=" * 78)
    lines.append(f"physical write edge contexts    : {result.total_write_edge_contexts}")
    lines.append(f"unique physical write edges     : {result.unique_physical_write_edges}")
    lines.append(f"event-guarded contexts          : {result.event_guarded_contexts}")
    lines.append(f"event-guarded unique edges      : {result.event_guarded_unique_edges}")
    lines.append(f"  rise-guarded                   : {result.rise_guarded_edges}")
    lines.append(f"  fall-guarded                   : {result.fall_guarded_edges}")
    lines.append(f"residual GPIO-level guarded      : {result.gpio_level_guarded_edges}")
    lines.append(f"residual physical-state guarded  : {result.physical_state_guarded_edges}")
    lines.append(f"residual semantic-state guarded  : {result.semantic_state_guarded_edges}")
    lines.append(f"residual other guarded           : {result.other_guarded_edges}")
    lines.append(f"event-only after compression     : {result.zero_residual_after_event_edges}")
    lines.append(f"required branch literals total   : {result.total_required_literals}")
    lines.append(f"covered by recovered events      : {result.total_event_covered_literals}")
    lines.append(f"residual branch literals         : {result.total_residual_literals}")
    coverage = (100.0 * result.total_event_covered_literals / result.total_required_literals) if result.total_required_literals else 0.0
    lines.append(f"event literal coverage            : {coverage:.2f}%")
    lines.append(f"unique residual guard signatures : {result.unique_residual_literal_signatures}")
    lines.append("event usage:")
    if result.event_usage: 
        for event_id, count in result.event_usage.items(): 
            lines.append(f"  {event_id:12s}: {count}")
    else: 
        lines.append("  -")
    lines.append("residual literal categories:")
    if result.category_counts: 
        for category, count in result.category_counts.items(): 
            lines.append(f"  {category:20s}: {count}")
    else: 
        lines.append("  -")
    lines.append("")
    lines.append("TOP RESIDUAL LITERALS")
    lines.append("-" * 78)
    if result.top_residual_literals: 
        for category, text, count in result.top_residual_literals: 
            lines.append(f"{count:3d}x {category:16s} {text}")
    else: 
        lines.append("-")
    lines.append("")
    lines.append("WRITE EDGE CONTEXTS")
    lines.append("-" * 78)
    for item in result.write_edges: 
        event_text = ",".join(item.events) if item.events else "-"
        family_text = ", ".join(item.families)
        lines.append(
            f"BB{item.source_block:03d}->BB{item.target_block:03d} "
            f"writes={item.write_count:2d} events={event_text} "
            f"required={len(item.required_literals):2d} covered={item.event_covered_literals:2d} "
            f"residual={item.residual_literals:2d}"
        )
        lines.append(f"  families: {family_text}")
        if item.required_literals: 
            residual_keys = {(lit.block, lit.truth, lit.category, lit.text) for lit in item.residual_required_literals}
            for lit in item.required_literals: 
                key = (lit.block, lit.truth, lit.category, lit.text)
                mark = "RES" if key in residual_keys else "EVT"
                lines.append(f"  {mark} {lit.category:16s} BB{lit.block:03d} {'T' if lit.truth else 'F'}  {lit.text}")
        else: 
            lines.append("  required literals: -")
    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 78)
    for note in result.notes: 
        lines.append(f"- {note}")

    path = Path(path)
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n", encoding = "utf-8")
    Path(str(path) + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), 
        encoding = "utf-8", 
    )
