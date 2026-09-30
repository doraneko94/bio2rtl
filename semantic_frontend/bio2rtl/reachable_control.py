from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from typing import Any

from .next_state_expr import Expr, NextStateExprIR
from .semantic_ssa import SemanticExpr, SemanticCondition, SemanticSSAResult
from .semantic_reach import SemanticReachResult, ReachRegion
from .structural_netlist import StructuralNetlist
from .semantic_systemverilog import infer_sampled_gpio_bits

MASK32 = 0xFFFF_FFFF


@dataclass(frozen = True)
class ConcreteSemanticState: 
    region_name: str
    physical: tuple[tuple[str, int], ...]
    semantic: tuple[tuple[str, int], ...]
    sampled_gpio: tuple[tuple[int, int], ...]

    def physical_dict(self) -> dict[str, int]: 
        return dict(self.physical)

    def semantic_dict(self) -> dict[str, int]: 
        return dict(self.semantic)

    def sampled_dict(self) -> dict[int, int]: 
        return dict(self.sampled_gpio)


@dataclass
class ReachableControlResult: 
    reachable_states: set[ConcreteSemanticState]
    control_families: tuple[str, ...]
    control_vectors: set[tuple[int, ...]]
    control_vectors_by_region: dict[str, set[tuple[int, ...]]]
    input_bits: tuple[int, ...]
    iterations: int
    truncated: bool


def _u32(value: int) -> int: 
    return value & MASK32


def _signed32(value: int) -> int: 
    value &= MASK32
    return value - (1 << 32) if value & (1 << 31) else value


def _eval_semantic_expr(
    expression: SemanticExpr, 
    physical: dict[str, int], 
    semantic: dict[str, int], 
    sampled: dict[int, int], 
    path_edges: set[tuple[int, int]], 
) -> int: 
    kind = expression.kind

    if kind == "CONST": 
        return _u32(0 if expression.value is None else expression.value)

    if kind == "STATE": 
        if expression.state_family is None: 
            raise RuntimeError("STATE without family")
        return _u32(physical[expression.state_family])

    if kind == "SEMANTIC_STATE": 
        if expression.semantic_state_name is None: 
            raise RuntimeError("SEMANTIC_STATE without name")
        return _u32(semantic[expression.semantic_state_name])

    if kind == "GPIO": 
        if expression.gpio_block is None: 
            raise RuntimeError("GPIO without block")
        return _u32(sampled.get(expression.gpio_block, 0))

    if kind == "LIVEIN": 
        raise RuntimeError(
            f"reachable-control audit cannot enumerate LIVEIN {expression.livein_name}"
        )

    if kind == "PHI": 
        if expression.phi_block is None: 
            raise RuntimeError("PHI without block")
        matches = [
            incoming
            for predecessor, incoming in expression.phi_inputs
            if (predecessor, expression.phi_block) in path_edges
        ]
        if not matches: 
            return 0
        if len(matches) != 1: 
            raise RuntimeError(
                f"PHI BB{expression.phi_block:03d} has {len(matches)} active inputs"
            )
        return _eval_semantic_expr(
            matches[0], physical, semantic, sampled, path_edges
        )

    if kind != "OP" or len(expression.args) != 2: 
        raise RuntimeError(f"unsupported semantic expression {kind}")

    a = _eval_semantic_expr(expression.args[0], physical, semantic, sampled, path_edges)
    b = _eval_semantic_expr(expression.args[1], physical, semantic, sampled, path_edges)

    if expression.operation == "ADD": 
        return _u32(a + b)
    if expression.operation == "AND": 
        return _u32(a & b)
    if expression.operation == "OR": 
        return _u32(a | b)
    if expression.operation == "SHL": 
        return _u32(a << (b & 0x1F))
    if expression.operation == "SHR": 
        return _u32(a >> (b & 0x1F))

    raise RuntimeError(f"unsupported semantic operation {expression.operation}")


def _eval_condition(
    condition: SemanticCondition, 
    physical: dict[str, int], 
    semantic: dict[str, int], 
    sampled: dict[int, int], 
    path_edges: set[tuple[int, int]], 
) -> bool: 
    lhs = _eval_semantic_expr(condition.lhs, physical, semantic, sampled, path_edges)
    rhs = _eval_semantic_expr(condition.rhs, physical, semantic, sampled, path_edges)

    if condition.operation == "EQ": 
        return lhs == rhs
    if condition.operation == "NE": 
        return lhs != rhs
    if condition.operation == "ULT": 
        return lhs < rhs
    if condition.operation == "UGE": 
        return lhs >= rhs
    if condition.operation == "SLT": 
        return _signed32(lhs) < _signed32(rhs)

    raise RuntimeError(f"unsupported condition {condition.operation}")


def _eval_structural_expr(
    expression: Expr, 
    physical: dict[str, int], 
    semantic: dict[str, int], 
    sampled: dict[int, int], 
    path_edges: set[tuple[int, int]], 
    semantic_ssa: SemanticSSAResult, 
) -> int: 
    if expression.kind == "CONST": 
        return _u32(0 if expression.value is None else expression.value)

    if expression.kind == "STATE": 
        if expression.state_family is None: 
            raise RuntimeError("structural STATE without family")
        return _u32(physical[expression.state_family])

    if expression.kind == "GPIO": 
        if expression.gpio_value is None: 
            raise RuntimeError("structural GPIO without SSA name")
        semantic_expr = semantic_ssa.expressions.get(expression.gpio_value)
        if semantic_expr is None: 
            raise RuntimeError(
                f"cannot resolve structural GPIO {expression.gpio_value}"
            )
        return _eval_semantic_expr(
            semantic_expr, physical, semantic, sampled, path_edges
        )

    if expression.kind != "OP" or len(expression.args) != 2: 
        raise RuntimeError(f"unsupported structural expression {expression.kind}")

    a = _eval_structural_expr(
        expression.args[0], physical, semantic, sampled, path_edges, semantic_ssa
    )
    b = _eval_structural_expr(
        expression.args[1], physical, semantic, sampled, path_edges, semantic_ssa
    )

    if expression.operation == "ADD": 
        return _u32(a + b)
    if expression.operation == "AND": 
        return _u32(a & b)
    if expression.operation == "OR": 
        return _u32(a | b)
    if expression.operation == "SHL": 
        return _u32(a << (b & 0x1F))
    if expression.operation == "SHR": 
        return _u32(a >> (b & 0x1F))

    raise RuntimeError(f"unsupported structural operation {expression.operation}")


def _region_entry(region: ReachRegion) -> int: 
    if region.start_sample is not None: 
        return region.start_sample

    internal = set(region.blocks)
    incoming = {
        edge.target
        for edge in region.edges
        if edge.target in internal and edge.target not in region.exit_samples
    }
    roots = sorted(internal - incoming)
    if len(roots) != 1: 
        raise RuntimeError(f"{region.name}: cannot identify unique entry root: {roots}")
    return roots[0]


def _successors(region: ReachRegion, block_id: int) -> list[Any]: 
    return [edge for edge in region.edges if edge.source == block_id]


def _execute_region(
    state: ConcreteSemanticState, 
    reach: SemanticReachResult, 
    next_state_expr: NextStateExprIR, 
    semantic_ssa: SemanticSSAResult, 
    widths: dict[str, int], 
) -> tuple[str | None, dict[str, int], dict[str, int], dict[int, int]]: 
    region = reach.regions[state.region_name]
    physical = state.physical_dict()
    semantic = state.semantic_dict()
    sampled = state.sampled_dict()

    physical_next = dict(physical)
    semantic_next = dict(semantic)

    semantic_defs_by_edge: dict[tuple[int, int], list[tuple[str, SemanticExpr]]] = {}
    for name, definition in semantic_ssa.semantic_state_definitions.items(): 
        for predecessor, incoming in definition.incoming: 
            semantic_defs_by_edge.setdefault(
                (predecessor, definition.block_id), []
            ).append((name, incoming))

    block = _region_entry(region)
    path_edges: set[tuple[int, int]] = set()
    seen: set[int] = set()

    while True: 
        if block in region.exit_samples: 
            target_name = f"SAMPLE_BB{block:03d}"
            return target_name, physical_next, semantic_next, sampled

        if block in seen: 
            raise RuntimeError(f"{region.name}: concrete execution cycle at BB{block:03d}")
        seen.add(block)

        outgoing = _successors(region, block)
        if not outgoing: 
            return None, physical_next, semantic_next, sampled

        if len(outgoing) == 1: 
            chosen = outgoing[0]
        elif len(outgoing) == 2: 
            condition = semantic_ssa.branch_conditions.get(block)
            if condition is None: 
                raise RuntimeError(f"missing branch condition BB{block:03d}")
            truth = _eval_condition(
                condition, physical, semantic, sampled, path_edges
            )
            wanted = "TRUE" if truth else "FALSE"
            matches = [edge for edge in outgoing if edge.kind == wanted]
            if len(matches) != 1: 
                raise RuntimeError(
                    f"{region.name}: BB{block:03d} cannot choose {wanted} edge"
                )
            chosen = matches[0]
        else: 
            raise RuntimeError(
                f"{region.name}: BB{block:03d} has {len(outgoing)} successors"
            )

        edge_key = (chosen.source, chosen.target)
        path_edges.add(edge_key)

        for write in next_state_expr.by_edge.get(edge_key, []): 
            value = _eval_structural_expr(
                write.expression, 
                physical, 
                semantic, 
                sampled, 
                path_edges, 
                semantic_ssa, 
            )
            width = widths[write.family]
            mask = MASK32 if width >= 32 else (1 << width) - 1
            physical_next[write.family] = value & mask

        for name, incoming in semantic_defs_by_edge.get(edge_key, []): 
            semantic_next[name] = _eval_semantic_expr(
                incoming, physical, semantic, sampled, path_edges
            )

        block = chosen.target


def _candidate_control_families(widths: dict[str, int]) -> tuple[str, ...]: 
    # Keep this generic and conservative: small physical states are the candidates
    # for subsequent joint FSM recovery. Counters/data registers can be excluded in
    # the next pass using operation/use analysis.
    return tuple(sorted(family for family, width in widths.items() if width <= 2))


def analyze_reachable_control_states(
    structural: StructuralNetlist, 
    next_state_expr: NextStateExprIR, 
    reach: SemanticReachResult, 
    semantic_ssa: SemanticSSAResult, 
    gpio_effects: Any, 
    max_states: int = 200_000, 
) -> ReachableControlResult: 
    widths: dict[str, int] = {}
    for family, node_id in structural.state_nodes.items(): 
        node = structural.nodes[node_id]
        widths[family] = 32 if node.width is None else int(node.width)

    sampled_bits = infer_sampled_gpio_bits(
        structural, next_state_expr, semantic_ssa, gpio_effects
    )
    input_bits = tuple(sorted(set().union(*sampled_bits.values()))) if sampled_bits else ()
    if len(input_bits) > 8: 
        raise RuntimeError(
            f"reachable-control audit requires enumerating {len(input_bits)} GPIO bits; "
            "limit is 8"
        )

    physical0 = tuple(sorted((family, 0) for family in widths))
    semantic0 = tuple(sorted((name, 0) for name in semantic_ssa.semantic_states))
    sampled0 = tuple(sorted((block, 0) for block in semantic_ssa.gpio_sample_blocks))

    initial = ConcreteSemanticState(
        region_name = "ENTRY", 
        physical = physical0, 
        semantic = semantic0, 
        sampled_gpio = sampled0, 
    )

    reachable: set[ConcreteSemanticState] = {initial}
    frontier: set[ConcreteSemanticState] = {initial}
    iterations = 0
    truncated = False

    gpio_values = []
    for bits in product((0, 1), repeat = len(input_bits)): 
        value = 0
        for bit_index, bit_value in zip(input_bits, bits): 
            if bit_value: 
                value |= 1 << bit_index
        gpio_values.append(value)
    if not gpio_values: 
        gpio_values = [0]

    while frontier: 
        iterations += 1
        new_frontier: set[ConcreteSemanticState] = set()

        for state in frontier: 
            target_region, physical_next, semantic_next, sampled = _execute_region(
                state, reach, next_state_expr, semantic_ssa, widths
            )
            if target_region is None: 
                continue

            target_block = int(target_region.rsplit("BB", 1)[1])
            for gpio_value in gpio_values: 
                next_sampled = dict(sampled)
                next_sampled[target_block] = gpio_value
                new_state = ConcreteSemanticState(
                    region_name = target_region, 
                    physical = tuple(sorted(physical_next.items())), 
                    semantic = tuple(sorted(semantic_next.items())), 
                    sampled_gpio = tuple(sorted(next_sampled.items())), 
                )
                if new_state not in reachable: 
                    reachable.add(new_state)
                    new_frontier.add(new_state)
                    if len(reachable) >= max_states: 
                        truncated = True
                        frontier = set()
                        new_frontier = set()
                        break
            if truncated: 
                break
        if truncated: 
            break
        frontier = new_frontier

    control_families = _candidate_control_families(widths)
    control_vectors: set[tuple[int, ...]] = set()
    by_region: dict[str, set[tuple[int, ...]]] = {}

    for state in reachable: 
        physical = state.physical_dict()
        semantic = state.semantic_dict()
        vector = tuple(
            [physical[family] for family in control_families]
            + [semantic[name] for name in sorted(semantic)]
        )
        control_vectors.add(vector)
        by_region.setdefault(state.region_name, set()).add(vector)

    return ReachableControlResult(
        reachable_states = reachable, 
        control_families = control_families, 
        control_vectors = control_vectors, 
        control_vectors_by_region = by_region, 
        input_bits = input_bits, 
        iterations = iterations, 
        truncated = truncated, 
    )


def print_reachable_control_states(result: ReachableControlResult) -> None: 
    print()
    print("=" * 72)
    print("EXACT REACHABLE CONTROL STATE AUDIT")
    print("=" * 72)
    print(f"enumerated GPIO bits   : {', '.join(map(str, result.input_bits)) or '-'}")
    print(f"reachable full states  : {len(result.reachable_states)}")
    print(f"control families       : {len(result.control_families)}")
    print(f"control vectors        : {len(result.control_vectors)}")
    bits = max(1, (len(result.control_vectors) - 1).bit_length())
    print(f"minimum code bits      : {bits}")
    print(f"fixed-point iterations : {result.iterations}")
    print(f"truncated              : {result.truncated}")
    print("control family names   : " + ", ".join(result.control_families))
    for region_name in sorted(result.control_vectors_by_region): 
        count = len(result.control_vectors_by_region[region_name])
        region_bits = max(1, (count - 1).bit_length())
        print(f"{region_name:20s}: {count:5d} vectors, bits>={region_bits}")
    print("EXACT REACHABLE CONTROL STATE AUDIT: PASS")
