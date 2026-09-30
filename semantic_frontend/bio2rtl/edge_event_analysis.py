from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .final_state import FinalStateResult
from .ir import IRBlock
from .next_state_expr import Expr, NextStateExprIR
from .physical_state_diagnostic import PhysicalStateDiagnosticResult
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticCondition, SemanticExpr, SemanticSSAResult


@dataclass(frozen = True)
class GPIOBitRef: 
    sample_block: int | None
    gpio_value: str | None
    bit: int


@dataclass(frozen = True)
class PredicateAtom: 
    source: str          # GPIO_BIT / STATE_BIT / SEMANTIC_STATE_BIT
    level: int           # level when condition itself is true
    sample_block: int | None = None
    gpio_value: str | None = None
    bit: int | None = None
    family: str | None = None


@dataclass
class SampleLatchInfo: 
    family: str
    width: int
    sample_block: int | None
    gpio_value: str | None
    bit: int
    matching_writes: int
    total_effective_writes: int


@dataclass
class EdgeEventCandidate: 
    event_id: str
    region: str
    sample_block: int | None
    family: str
    gpio_bit: int
    edge: str            # RISE / FALL
    current_level: int
    previous_level: int
    detector_block: int
    success_target: int
    path_blocks: list[int]
    qualifiers: list[str]
    matching_paths: int


@dataclass
class EdgeEventAnalysisResult: 
    sample_latches: list[SampleLatchInfo]
    event_candidates: list[EdgeEventCandidate]
    gpio_predicate_blocks: int
    gpio_level_signatures: list[str]
    candidate_events: int
    rise_events: int
    fall_events: int
    qualified_events: int
    regions_with_events: int
    physical_write_edges: int
    write_edges_in_event_regions: int
    notes: list[str]


def _single_bit(mask: int) -> int | None: 
    if mask <= 0 or mask & (mask - 1): 
        return None
    return mask.bit_length() - 1


def _const(expr: Expr) -> int | None: 
    return expr.value if expr.kind == "CONST" else None


def _gpio_bit_from_expr(expr: Expr) -> tuple[str | None, int] | None: 
    """Recognize common compiler spellings for extracting one GPIO bit."""
    # (gpio & (1<<b)) >> b
    if expr.kind == "OP" and expr.operation == "SHR" and len(expr.args) == 2: 
        lhs, rhs = expr.args
        shift = _const(rhs)
        if shift is not None and lhs.kind == "OP" and lhs.operation == "AND" and len(lhs.args) == 2: 
            a, b = lhs.args
            for gpio, mask_expr in ((a, b), (b, a)): 
                mask = _const(mask_expr)
                if gpio.kind == "GPIO" and mask is not None: 
                    bit = _single_bit(mask)
                    if bit is not None and bit == shift: 
                        return gpio.gpio_value, bit

    # (gpio >> b) & 1
    if expr.kind == "OP" and expr.operation == "AND" and len(expr.args) == 2: 
        a, b = expr.args
        for shifted, one in ((a, b), (b, a)): 
            if _const(one) != 1: 
                continue
            if shifted.kind == "OP" and shifted.operation == "SHR" and len(shifted.args) == 2: 
                gpio, rhs = shifted.args
                shift = _const(rhs)
                if gpio.kind == "GPIO" and shift is not None: 
                    return gpio.gpio_value, shift
        # gpio & (1<<b), kept in masked-word form.  Even without an explicit
        # shift this carries exactly one Boolean input bit (0 versus 2^b), and
        # comparisons against zero are equivalent to a normalized history bit.
        for gpio, mask_expr in ((a, b), (b, a)): 
            mask = _const(mask_expr)
            if gpio.kind == "GPIO" and mask is not None: 
                bit = _single_bit(mask)
                if bit is not None: 
                    return gpio.gpio_value, bit
    return None


def _semantic_const(expr: SemanticExpr) -> int | None: 
    return expr.value if expr.kind == "CONST" else None


def _semantic_gpio_mask(expr: SemanticExpr) -> tuple[int, int] | None: 
    """Return (sample_block, bit) for GPIO & single-bit-mask."""
    if expr.kind != "OP" or expr.operation != "AND" or len(expr.args) != 2: 
        return None
    a, b = expr.args
    for gpio, mask_expr in ((a, b), (b, a)): 
        mask = _semantic_const(mask_expr)
        if gpio.kind == "GPIO" and gpio.gpio_block is not None and mask is not None: 
            bit = _single_bit(mask)
            if bit is not None: 
                return gpio.gpio_block, bit
    return None



def _gpio_history_comparator(cond: SemanticCondition) -> tuple[int | None, int, str] | None: 
    """Recognize a direct comparison between one sampled GPIO bit and history state.

    Many compilers spell an edge detector as ``current == previous`` followed by
    a current-level test, rather than testing current and previous separately.
    Return ``(sample_block, bit, family)`` when either operand is a one-bit GPIO
    extraction and the other is a persistent/semantic state value.
    """
    if cond.operation not in {"EQ", "NE"}: 
        return None
    for gpio_expr, state_expr in ((cond.lhs, cond.rhs), (cond.rhs, cond.lhs)): 
        gpio = _semantic_gpio_bit_from_expr(gpio_expr)
        if gpio is None: 
            continue
        if state_expr.kind == "STATE" and state_expr.state_family is not None: 
            return gpio[0], gpio[1], state_expr.state_family
        if state_expr.kind == "SEMANTIC_STATE" and state_expr.semantic_state_name is not None: 
            return gpio[0], gpio[1], state_expr.semantic_state_name
    return None

def _predicate_atom(cond: SemanticCondition) -> PredicateAtom | None: 
    """Recognize boolean tests of a GPIO bit or a 1-bit persistent state."""
    if cond.operation not in {"EQ", "NE"}: 
        return None

    lhs, rhs = cond.lhs, cond.rhs
    # normalize CONST(0) to rhs
    if _semantic_const(lhs) == 0: 
        lhs, rhs = rhs, lhs
    if _semantic_const(rhs) != 0: 
        return None

    gpio = _semantic_gpio_mask(lhs)
    if gpio is not None: 
        block, bit = gpio
        # (gpio & mask) == 0 => level 0; != 0 => level 1
        level = 0 if cond.operation == "EQ" else 1
        return PredicateAtom(source = "GPIO_BIT", level = level, sample_block = block, bit = bit)

    if lhs.kind == "STATE" and lhs.state_family is not None: 
        level = 0 if cond.operation == "EQ" else 1
        return PredicateAtom(source = "STATE_BIT", level = level, family = lhs.state_family)

    if lhs.kind == "SEMANTIC_STATE" and lhs.semantic_state_name is not None: 
        level = 0 if cond.operation == "EQ" else 1
        return PredicateAtom(source = "SEMANTIC_STATE_BIT", level = level, family = lhs.semantic_state_name)

    return None


def _gpio_definition_blocks(blocks: list[IRBlock]) -> dict[str, int]: 
    result: dict[str, int] = {}
    for block in blocks: 
        for op in block.ops: 
            if op.kind == "GPIO_READ" and op.dst is not None: 
                result[op.dst] = block.id
    return result


def _find_sample_latches(
    blocks: list[IRBlock], 
    final_state: FinalStateResult, 
    next_state: NextStateExprIR, 
    physical: PhysicalStateDiagnosticResult, 
) -> list[SampleLatchInfo]: 
    gpio_blocks = _gpio_definition_blocks(blocks)
    role_by_family = {item.family: item.structural_role for item in physical.families}
    effective_count = {item.family: item.effective_writes for item in physical.families}

    signatures: dict[str, Counter] = defaultdict(Counter)
    for write in next_state.writes: 
        if role_by_family.get(write.family) != "GPIO_SAMPLE_LATCH": 
            continue
        hit = _gpio_bit_from_expr(write.expression)
        if hit is not None: 
            signatures[write.family][hit] += 1

    result: list[SampleLatchInfo] = []
    for family, counts in sorted(signatures.items()): 
        if family not in final_state.states or not counts: 
            continue
        (gpio_value, bit), count = counts.most_common(1)[0]
        result.append(
            SampleLatchInfo(
                family = family, 
                width = final_state.states[family].width, 
                sample_block = gpio_blocks.get(gpio_value) if gpio_value is not None else None, 
                gpio_value = gpio_value, 
                bit = bit, 
                matching_writes = count, 
                total_effective_writes = effective_count.get(family, 0), 
            )
        )
    return result




def _semantic_gpio_bit_from_expr(expr: SemanticExpr) -> tuple[int | None, int] | None: 
    """Recognize a one-bit GPIO extraction in semantic-state incoming values."""
    if expr.kind == "OP" and expr.operation == "SHR" and len(expr.args) == 2: 
        lhs, rhs = expr.args
        shift = _semantic_const(rhs)
        if shift is not None and lhs.kind == "OP" and lhs.operation == "AND" and len(lhs.args) == 2: 
            a, b = lhs.args
            for gpio, mask_expr in ((a, b), (b, a)): 
                mask = _semantic_const(mask_expr)
                if gpio.kind == "GPIO" and mask is not None: 
                    bit = _single_bit(mask)
                    if bit is not None and bit == shift: 
                        return gpio.gpio_block, bit
    if expr.kind == "OP" and expr.operation == "AND" and len(expr.args) == 2: 
        a, b = expr.args
        for shifted, one in ((a, b), (b, a)): 
            if _semantic_const(one) != 1: 
                continue
            if shifted.kind == "OP" and shifted.operation == "SHR" and len(shifted.args) == 2: 
                gpio, rhs = shifted.args
                shift = _semantic_const(rhs)
                if gpio.kind == "GPIO" and shift is not None: 
                    return gpio.gpio_block, shift
        for gpio, mask_expr in ((a, b), (b, a)): 
            mask = _semantic_const(mask_expr)
            if gpio.kind == "GPIO" and mask is not None: 
                bit = _single_bit(mask)
                if bit is not None: 
                    return gpio.gpio_block, bit
    return None


def _find_semantic_sample_latches(semantic_ssa: SemanticSSAResult) -> list[SampleLatchInfo]: 
    result: list[SampleLatchInfo] = []

    def contains_self(expr: SemanticExpr, name: str) -> bool: 
        if expr.kind == "SEMANTIC_STATE" and expr.semantic_state_name == name: 
            return True
        if any(contains_self(a, name) for a in expr.args): 
            return True
        return any(contains_self(a, name) for _p, a in expr.phi_inputs)

    for name, definition in sorted(semantic_ssa.semantic_state_definitions.items()): 
        hits = Counter()
        has_self_hold = False
        for _pred, expr in definition.incoming: 
            has_self_hold = has_self_hold or contains_self(expr, name)
            hit = _semantic_gpio_bit_from_expr(expr)
            if hit is not None: 
                hits[hit] += 1
        if not hits or not has_self_hold: 
            # A true previous-sample latch must be loop-carried: without a
            # self/hold recurrence, the semantic value is a derived control
            # variable that happens to be assigned from GPIO on some paths.
            continue
        (sample_block, bit), count = hits.most_common(1)[0]
        if any(key != (sample_block, bit) for key in hits): 
            continue
        result.append(SampleLatchInfo(
            family = name, width = 1, sample_block = sample_block, gpio_value = None, 
            bit = bit, matching_writes = count, 
            total_effective_writes = len(definition.incoming), 
        ))
    return result


def _successor_map(region) -> dict[int, list[tuple[int, str]]]: 
    result: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for edge in region.edges: 
        result[edge.source].append((edge.target, edge.kind))
    return result


def _qualifier_text(atom: PredicateAtom, truth: bool) -> str: 
    level = atom.level if truth else 1 - atom.level
    if atom.source == "GPIO_BIT": 
        return f"GPIO@BB{atom.sample_block:03d}[{atom.bit}]={level}"
    return f"STATE({atom.family})={level}"


def _path_event(
    path: list[tuple[int, bool, PredicateAtom]], 
    latch: SampleLatchInfo, 
) -> tuple[str, int, int, int, list[str]] | None: 
    current: tuple[int, int] | None = None  # (level, block)
    previous: tuple[int, int] | None = None
    qualifiers: list[str] = []

    for block, truth, atom in path: 
        level = atom.level if truth else 1 - atom.level
        if atom.source == "GPIO_BIT" and atom.bit == latch.bit: 
            # Prefer same sample block if known, but bit identity is still useful
            # across equivalent GPIO_READ spellings.
            current = (level, block)
        elif atom.source in {"STATE_BIT", "SEMANTIC_STATE_BIT"} and atom.family == latch.family: 
            previous = (level, block)
        else: 
            qualifiers.append(_qualifier_text(atom, truth))

    if current is None or previous is None or current[0] == previous[0]: 
        return None
    edge = "RISE" if previous[0] == 0 and current[0] == 1 else "FALL"
    detector_block = previous[1]
    return edge, current[0], previous[0], detector_block, sorted(set(qualifiers))


def analyze_edge_events(
    blocks: list[IRBlock], 
    final_state: FinalStateResult, 
    next_state: NextStateExprIR, 
    semantic_reach: SemanticReachResult, 
    semantic_ssa: SemanticSSAResult, 
    physical: PhysicalStateDiagnosticResult, 
    max_branch_depth: int = 6, 
) -> EdgeEventAnalysisResult: 
    latches = _find_sample_latches(blocks, final_state, next_state, physical)
    latches.extend(_find_semantic_sample_latches(semantic_ssa))
    # Deduplicate by logical storage name.
    latches = list({item.family: item for item in latches}.values())

    atoms = {
        block: atom
        for block, cond in semantic_ssa.branch_conditions.items()
        if (atom := _predicate_atom(cond)) is not None
    }

    # Enumerate only *minimal detector paths*.  A path starts at a branch
    # testing the same current GPIO bit as the sample latch and stops at the
    # first branch testing that latch.  This deliberately excludes unrelated
    # protocol-state branches before/after the detector and prevents CFG loops
    # from multiplying one hardware event into dozens of path variants.
    candidates: dict[tuple, EdgeEventCandidate] = {}
    event_regions: set[str] = set()
    write_edges = {(w.source_block, w.target_block) for w in next_state.writes}
    write_edges_in_regions: set[tuple[int, int]] = set()

    for region_name, region in semantic_reach.regions.items(): 
        succ = _successor_map(region)
        for edge in region.edges: 
            if (edge.source, edge.target) in write_edges: 
                write_edges_in_regions.add((edge.source, edge.target))

        for latch in latches: 
            region_nodes = set(region.blocks)
            if region.start_sample is not None: 
                region_nodes.add(region.start_sample)
            starts = sorted(
                block
                for block, atom in atoms.items()
                if atom.source == "GPIO_BIT"
                and atom.bit == latch.bit
                and block in region_nodes
            )
            for start in starts: 
                start_atom = atoms[start]
                for first_target, first_kind in succ.get(start, []): 
                    if first_kind not in {"TRUE", "FALSE"}: 
                        continue
                    first_truth = first_kind == "TRUE"
                    current_level = start_atom.level if first_truth else 1 - start_atom.level
                    queue = deque([
                        (first_target, [(start, first_truth, start_atom)], [start, first_target], 1)
                    ])
                    seen: set[tuple[int, tuple[tuple[int, bool], ...]]] = set()
                    while queue: 
                        block, decisions, path_blocks, depth = queue.popleft()
                        if depth >= max_branch_depth: 
                            continue
                        atom = atoms.get(block)

                        if atom is None: 
                            for target, kind in succ.get(block, []): 
                                if kind == "GOTO": 
                                    queue.append((target, decisions, path_blocks + [target], depth + 1))
                            continue

                        # A second test of the same current GPIO bit means
                        # this start is not the nearest current-sample test to
                        # the latch comparison.  The later test is considered
                        # independently as its own canonical start.
                        if atom.source == "GPIO_BIT" and atom.bit == latch.bit: 
                            continue

                        for target, kind in succ.get(block, []): 
                            if kind not in {"TRUE", "FALSE"}: 
                                continue
                            truth = kind == "TRUE"
                            level = atom.level if truth else 1 - atom.level
                            new_decisions = decisions + [(block, truth, atom)]

                            # First test of the previous sample terminates the
                            # detector path.  Only opposite previous/current
                            # levels form an edge event.
                            if atom.source in {"STATE_BIT", "SEMANTIC_STATE_BIT"} and atom.family == latch.family: 
                                if level == current_level: 
                                    continue
                                edge_kind = "RISE" if level == 0 and current_level == 1 else "FALL"
                                qualifiers: list[str] = []
                                for qb, qt, qa in decisions[1:]: 
                                    # Re-tests of the same current GPIO bit are
                                    # redundant detector spelling, not qualifiers.
                                    if qa.source == "GPIO_BIT" and qa.bit == latch.bit: 
                                        continue
                                    qualifiers.append(_qualifier_text(qa, qt))
                                qualifiers = sorted(set(qualifiers))
                                key = (
                                    region_name, 
                                    latch.family, 
                                    edge_kind, 
                                    block, 
                                    target, 
                                    tuple(qualifiers), 
                                )
                                candidate_path = path_blocks + [target]
                                if key not in candidates: 
                                    candidates[key] = EdgeEventCandidate(
                                        event_id = "", 
                                        region = region_name, 
                                        sample_block = latch.sample_block, 
                                        family = latch.family, 
                                        gpio_bit = latch.bit, 
                                        edge = edge_kind, 
                                        current_level = current_level, 
                                        previous_level = level, 
                                        detector_block = block, 
                                        success_target = target, 
                                        path_blocks = candidate_path, 
                                        qualifiers = qualifiers, 
                                        matching_paths = 1, 
                                    )
                                else: 
                                    candidates[key].matching_paths += 1
                                    # Keep the shortest representative path.
                                    if len(candidate_path) < len(candidates[key].path_blocks): 
                                        candidates[key].path_blocks = candidate_path
                                event_regions.add(region_name)
                                continue

                            signature = tuple((b, t) for b, t, _ in new_decisions)
                            state_key = (target, signature)
                            if state_key in seen: 
                                continue
                            seen.add(state_key)
                            queue.append((target, new_decisions, path_blocks + [target], depth + 1))

    # Direct current-vs-history comparator spelling:
    #
    #     if current == previous: loop
    #     if current == 0:        FALL path
    #     else:                   RISE path
    #
    # The legacy detector above expects separate current-level and history-level
    # tests.  This form is semantically equivalent but more compact in software.
    # Recover it without depending on protocol names or basic-block numbers.
    latch_by_family = {item.family: item for item in latches}
    for region_name, region in semantic_reach.regions.items(): 
        succ = _successor_map(region)
        region_nodes = set(region.blocks)
        if region.start_sample is not None: 
            region_nodes.add(region.start_sample)
        for cmp_block, cond in sorted(semantic_ssa.branch_conditions.items()): 
            if cmp_block not in region_nodes: 
                continue
            cmp_info = _gpio_history_comparator(cond)
            if cmp_info is None: 
                continue
            sample_block, bit, family = cmp_info
            latch = latch_by_family.get(family)
            if latch is None or latch.bit != bit: 
                continue

            # Follow only the branch on which current != previous.  For EQ that
            # is FALSE; for NE it is TRUE.
            changed_kind = "FALSE" if cond.operation == "EQ" else "TRUE"
            changed_targets = [t for t, k in succ.get(cmp_block, []) if k == changed_kind]
            for changed_target in changed_targets: 
                queue = deque([(changed_target, [cmp_block, changed_target], 0)])
                seen_blocks: set[int] = set()
                while queue: 
                    block, path_blocks, depth = queue.popleft()
                    if block in seen_blocks or depth >= max_branch_depth: 
                        continue
                    seen_blocks.add(block)
                    atom = atoms.get(block)
                    if atom is not None and atom.source == "GPIO_BIT" and atom.bit == bit: 
                        for target, kind in succ.get(block, []): 
                            if kind not in {"TRUE", "FALSE"}: 
                                continue
                            truth = kind == "TRUE"
                            current_level = atom.level if truth else 1 - atom.level
                            previous_level = 1 - current_level
                            edge_kind = "RISE" if current_level == 1 else "FALL"
                            key = (
                                region_name, 
                                family, 
                                edge_kind, 
                                block, 
                                target, 
                                tuple(), 
                            )
                            candidate_path = path_blocks + [target]
                            if key not in candidates: 
                                candidates[key] = EdgeEventCandidate(
                                    event_id = "", 
                                    region = region_name, 
                                    sample_block = sample_block if sample_block is not None else latch.sample_block, 
                                    family = family, 
                                    gpio_bit = bit, 
                                    edge = edge_kind, 
                                    current_level = current_level, 
                                    previous_level = previous_level, 
                                    detector_block = block, 
                                    success_target = target, 
                                    path_blocks = candidate_path, 
                                    qualifiers = [], 
                                    matching_paths = 1, 
                                )
                            else: 
                                candidates[key].matching_paths += 1
                            event_regions.add(region_name)
                        # First same-bit current-level test resolves direction.
                        continue
                    # Between comparator and level split, only unconditional
                    # flow is relevant.  Other conditions would require a
                    # qualifier-aware extension and are deliberately not guessed.
                    if atom is None: 
                        for target, kind in succ.get(block, []): 
                            if kind == "GOTO": 
                                queue.append((target, path_blocks + [target], depth + 1))

    ordered = sorted(
        candidates.values(), 
        key = lambda x: (x.region, x.family, x.edge, x.detector_block, x.success_target, x.qualifiers), 
    )
    for i, item in enumerate(ordered, 1): 
        item.event_id = f"EVT{i:03d}"

    gpio_atoms = [atom for atom in atoms.values() if atom.source == "GPIO_BIT"]
    gpio_level_signatures = sorted(
        {f"GPIO@BB{atom.sample_block:03d}[{atom.bit}]" for atom in gpio_atoms if atom.sample_block is not None}
    )

    return EdgeEventAnalysisResult(
        sample_latches = latches, 
        event_candidates = ordered, 
        gpio_predicate_blocks = len(gpio_atoms), 
        gpio_level_signatures = gpio_level_signatures, 
        candidate_events = len(ordered), 
        rise_events = sum(1 for x in ordered if x.edge == "RISE"), 
        fall_events = sum(1 for x in ordered if x.edge == "FALL"), 
        qualified_events = sum(1 for x in ordered if x.qualifiers), 
        regions_with_events = len(event_regions), 
        physical_write_edges = len(write_edges), 
        write_edges_in_event_regions = len(write_edges_in_regions), 
        notes = [
            "Candidates are inferred from CFG branch paths containing a previous GPIO sample latch and a current GPIO bit at opposite levels.", 
            "Qualifier GPIO/state tests are retained symbolically; no I2C-specific pin names or protocol states are used.", 
            "This pass is diagnostic only and does not alter generated RTL.", 
        ], 
    )


def write_edge_event_report(result: EdgeEventAnalysisResult, path: Path) -> None: 
    lines: list[str] = []
    lines.append("GPIO EDGE EVENT RECOVERY DIAGNOSTIC")
    lines.append("=" * 72)
    lines.append(f"sample latch families       : {len(result.sample_latches)}")
    lines.append(f"simple GPIO predicate blocks: {result.gpio_predicate_blocks}")
    lines.append(f"unique GPIO level signals   : {len(result.gpio_level_signatures)}")
    for signature in result.gpio_level_signatures: 
        lines.append(f"  level primitive           : {signature}")
    lines.append(f"candidate edge events       : {result.candidate_events}")
    lines.append(f"rise / fall                 : {result.rise_events} / {result.fall_events}")
    lines.append(f"qualified events            : {result.qualified_events}")
    lines.append(f"regions containing events   : {result.regions_with_events}")
    lines.append(f"physical next-state edges   : {result.physical_write_edges}")
    lines.append(f"write edges in event regions: {result.write_edges_in_event_regions}")
    lines.append("")
    lines.append("SAMPLE LATCHES")
    lines.append("-" * 72)
    if not result.sample_latches: 
        lines.append("-")
    for latch in result.sample_latches: 
        block = f"BB{latch.sample_block:03d}" if latch.sample_block is not None else "?"
        lines.append(
            f"{latch.family} [{latch.width}b] <- {block}[bit {latch.bit}] "
            f"({latch.matching_writes}/{latch.total_effective_writes} matching writes)"
        )
    lines.append("")
    lines.append("EVENT CANDIDATES")
    lines.append("-" * 72)
    if not result.event_candidates: 
        lines.append("-")
    for item in result.event_candidates: 
        lines.append(
            f"{item.event_id} {item.edge} GPIO[bit {item.gpio_bit}] "
            f"in {item.region}: prev={item.previous_level} -> now={item.current_level}"
        )
        lines.append(
            f"  latch/detector : {item.family} / BB{item.detector_block:03d}"
        )
        lines.append(f"  success target : BB{item.success_target:03d}")
        lines.append(
            "  path           : " + " -> ".join(f"BB{x:03d}" for x in item.path_blocks)
        )
        lines.append(
            "  qualifiers     : " + (", ".join(item.qualifiers) if item.qualifiers else "-")
        )
        lines.append(f"  matching paths : {item.matching_paths}")
    lines.append("")
    lines.append("NOTES")
    lines.append("-" * 72)
    for note in result.notes: 
        lines.append(f"- {note}")

    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines), encoding = "utf-8")
    json_path = path.with_suffix(path.suffix + ".json") if path.suffix else Path(str(path) + ".json")
    json_path.write_text(json.dumps(asdict(result), indent = 2, sort_keys = True), encoding = "utf-8")
