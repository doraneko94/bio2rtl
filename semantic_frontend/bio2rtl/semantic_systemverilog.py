from __future__ import annotations

from pathlib import Path
from typing import Any
import math
import re

from .next_state_expr import (
    Expr, 
    NextStateExprIR, 
)
from .definition_site_ir import DefinitionSiteIR
from .proven_hoist_plan import ProvenHoistPlan
from .hardware_behavior_ir import expr_key

from .structural_netlist import (
    StructuralNetlist, 
)

from .semantic_reach import (
    ReachEdge, 
    ReachRegion, 
    SemanticReachResult, 
)

from .semantic_next_state import (
    GuardExpr, 
    SemanticNextStateResult, 
)

from .semantic_ssa import (
    SemanticExpr, 
    SemanticCondition, 
    SemanticSSAResult, 
)


def sv_name(
    text: str, 
) -> str: 

    result = re.sub(
        r"[^A-Za-z0-9_]", 
        "_", 
        text, 
    )

    if not result: 
        return "unnamed"

    if result[0].isdigit(): 
        result = "_" + result

    return result


def state_reg_name(
    family: str, 
) -> str: 

    return (
        "r_"
        + sv_name(
            family
        )
    )


def state_next_name(
    family: str, 
) -> str: 

    return (
        state_reg_name(
            family
        )
        + "_next"
    )


def semantic_reg_name(
    name: str, 
) -> str: 

    return (
        "sem_"
        + sv_name(
            name
        )
    )


def semantic_next_name(
    name: str, 
) -> str: 

    return (
        semantic_reg_name(
            name
        )
        + "_next"
    )


def sampled_gpio_name(
    block_id: int, 
) -> str: 

    return (
        f"sampled_gpio_bb"
        f"{block_id:03d}"
    )


def region_token(
    region_name: str, 
) -> str: 

    return sv_name(
        region_name
    ).lower()


def reach_name(
    region_name: str, 
    block_id: int, 
) -> str: 

    return (
        f"reach_"
        f"{region_token(region_name)}_"
        f"bb{block_id:03d}"
    )


def edge_name(
    region_name: str, 
    source: int, 
    target: int, 
) -> str: 

    return (
        f"edge_"
        f"{region_token(region_name)}_"
        f"bb{source:03d}_"
        f"bb{target:03d}"
    )


def const32(
    value: int | None, 
) -> str: 

    number = (
        0
        if value is None
        else value
    )

    number &= 0xFFFF_FFFF

    return (
        f"32'h"
        f"{number:08X}"
    )


def structural_widths(
    structural: StructuralNetlist, 
) -> dict[str, int]: 

    result: dict[
        str, 
        int, 
    ] = {}

    for (
        family, 
        node_id, 
    ) in structural.state_nodes.items(): 

        node = (
            structural.nodes[
                node_id
            ]
        )

        width = (
            32
            if node.width is None
            else int(
                node.width
            )
        )

        if width <= 0: 

            raise RuntimeError(
                f"{family}: invalid width "
                f"{width}"
            )

        result[
            family
        ] = width

    return result


def edge_tuple_set(
    region: ReachRegion, 
) -> set[
    tuple[int, int]
]: 

    return {
        (
            edge.source, 
            edge.target, 
        )

        for edge
        in region.edges
    }


def region_entry_block(
    region: ReachRegion, 
) -> int | None: 

    if (
        region.start_sample
        is not None
    ): 

        return (
            region.start_sample
        )

    internal = set(
        region.blocks
    )

    incoming: set[int] = set()

    for edge in region.edges: 

        if (
            edge.target
            in region.exit_samples
        ): 
            continue

        if edge.target in internal: 

            incoming.add(
                edge.target
            )

    roots = sorted(
        internal
        - incoming
    )

    if len(roots) != 1: 

        raise RuntimeError(
            f"{region.name}: expected "
            "one entry root, found "
            f"{roots}"
        )

    return roots[0]


def region_depths(
    region: ReachRegion, 
) -> dict[int, int]: 

    root = region_entry_block(
        region
    )

    if root is None: 
        return {}

    internal = set(
        region.blocks
    )

    if (
        region.start_sample
        is not None
    ): 

        internal.add(
            region.start_sample
        )

    depth: dict[
        int, 
        int, 
    ] = {
        root: 0
    }

    changed = True
    iterations = 0

    max_iterations = (
        len(internal)
        + 1
    )

    while changed: 

        changed = False

        iterations += 1

        if (
            iterations
            > max_iterations
        ): 

            raise RuntimeError(
                f"{region.name}: "
                "internal reachability graph "
                "is cyclic"
            )

        for edge in region.edges: 

            # Every edge entering an exit sample belongs to
            # the next semantic sample.
            if (
                edge.target
                in region.exit_samples
            ): 

                continue

            if (
                edge.source
                not in depth
            ): 
                continue

            if (
                edge.target
                not in internal
            ): 
                continue

            candidate = (
                depth[
                    edge.source
                ]
                + 1
            )

            old = depth.get(
                edge.target
            )

            if (
                old is None
                or
                candidate > old
            ): 

                depth[
                    edge.target
                ] = candidate

                changed = True

    missing = (
        internal
        - set(
            depth
        )
    )

    if missing: 

        raise RuntimeError(
            f"{region.name}: cannot assign "
            f"depth to {sorted(missing)}"
        )

    return depth


def region_edge_order(
    region: ReachRegion, 
) -> list[ReachEdge]: 

    depth = region_depths(
        region
    )

    return sorted(
        region.edges, 

        key = lambda edge: (
            depth.get(
                edge.source, 
                10**9, 
            ), 
            edge.source, 
            edge.target, 
            edge.kind, 
        ), 
    )


def region_block_order(
    region: ReachRegion, 
) -> list[int]: 

    depth = region_depths(
        region
    )

    blocks = set(
        region.blocks
    )

    if (
        region.start_sample
        is not None
    ): 

        blocks.add(
            region.start_sample
        )

    return sorted(
        blocks, 

        key = lambda block_id: (
            depth.get(
                block_id, 
                10**9, 
            ), 
            block_id, 
        ), 
    )


def sample_state_map(
    reach: SemanticReachResult, 
) -> tuple[
    dict[str, int], 
    dict[int, int], 
]: 

    region_codes: dict[
        str, 
        int, 
    ] = {
        "ENTRY": 0
    }

    sample_codes: dict[
        int, 
        int, 
    ] = {}

    code = 1

    samples = sorted(
        {
            region.start_sample

            for region
            in reach.regions.values()

            if (
                region.start_sample
                is not None
            )
        }
    )

    for sample in samples: 

        if sample is None: 
            continue

        name = (
            f"SAMPLE_BB"
            f"{sample:03d}"
        )

        region_codes[
            name
        ] = code

        sample_codes[
            sample
        ] = code

        code += 1

    return (
        region_codes, 
        sample_codes, 
    )


def emit_semantic_expr(
    expression: SemanticExpr, 
    region: ReachRegion, 
    widths: dict[str, int], 
    liveins: set[str], 
) -> str: 

    if expression.kind == "CONST": 

        return const32(
            expression.value
        )

    if expression.kind == "STATE": 

        family = (
            expression.state_family
        )

        if (
            family is None
            or
            family not in widths
        ): 

            raise RuntimeError(
                "invalid STATE expression"
            )

        width = widths[
            family
        ]

        register = (
            state_reg_name(
                family
            )
        )

        if width >= 32: 
            return register

        return (
            "{{"
            f"{32 - width}"
            "{1'b0}}, "
            f"{register}"
            "}"
        )

    if (
        expression.kind
        == "SEMANTIC_STATE"
    ): 

        name = (
            expression
            .semantic_state_name
        )

        if name is None: 

            raise RuntimeError(
                "SEMANTIC_STATE "
                "without name"
            )

        return semantic_reg_name(
            name
        )

    if expression.kind == "GPIO": 

        block_id = (
            expression.gpio_block
        )

        if block_id is None: 

            raise RuntimeError(
                "GPIO expression "
                "without source block"
            )

        return sampled_gpio_name(
            block_id
        )

    if expression.kind == "LIVEIN": 

        name = (
            expression.livein_name
        )

        if (
            name is None
            or
            name not in liveins
        ): 

            raise RuntimeError(
                "invalid LIVEIN"
            )

        return (
            "livein_"
            + sv_name(
                name
            )
        )

    if expression.kind == "OP": 

        args = [
            emit_semantic_expr(
                arg, 
                region, 
                widths, 
                liveins, 
            )

            for arg
            in expression.args
        ]

        if len(args) != 2: 

            raise RuntimeError(
                "semantic OP requires "
                "2 operands"
            )

        operation = (
            expression.operation
        )

        operators = {
            "ADD": "+", 
            "AND": "&", 
            "OR": "|", 
            "SHL": "<<", 
            "SHR": ">>", 
        }

        if (
            operation
            not in operators
        ): 

            raise RuntimeError(
                "unsupported semantic "
                f"operation {operation}"
            )

        return (
            f"({args[0]} "
            f"{operators[operation]} "
            f"{args[1]})"
        )

    if expression.kind == "PHI": 

        phi_block = (
            expression.phi_block
        )

        if phi_block is None: 

            raise RuntimeError(
                "PHI without block"
            )

        region_edges = (
            edge_tuple_set(
                region
            )
        )

        candidates: list[
            tuple[str, str]
        ] = []

        for (
            predecessor, 
            incoming, 
        ) in expression.phi_inputs: 

            if (
                predecessor, 
                phi_block, 
            ) not in region_edges: 

                continue

            candidates.append(
                (
                    edge_name(
                        region.name, 
                        predecessor, 
                        phi_block, 
                    ), 
                    emit_semantic_expr(
                        incoming, 
                        region, 
                        widths, 
                        liveins, 
                    ), 
                )
            )

        if not candidates: 
            return "32'd0"

        result = "32'd0"

        for (
            selector, 
            value, 
        ) in reversed(
            candidates
        ): 

            result = (
                f"({selector} ? "
                f"{value} : "
                f"{result})"
            )

        return result

    raise RuntimeError(
        "unsupported semantic kind "
        f"{expression.kind}"
    )


def emit_condition(
    condition: SemanticCondition, 
    region: ReachRegion, 
    widths: dict[str, int], 
    liveins: set[str], 
) -> str: 

    lhs = emit_semantic_expr(
        condition.lhs, 
        region, 
        widths, 
        liveins, 
    )

    rhs = emit_semantic_expr(
        condition.rhs, 
        region, 
        widths, 
        liveins, 
    )

    if condition.operation == "EQ": 
        return f"({lhs} == {rhs})"

    if condition.operation == "NE": 
        return f"({lhs} != {rhs})"

    if condition.operation == "ULT": 
        return (
            f"($unsigned({lhs}) "
            f"< $unsigned({rhs}))"
        )

    if condition.operation == "UGE": 
        return (
            f"($unsigned({lhs}) "
            f">= $unsigned({rhs}))"
        )

    if condition.operation == "SLT": 
        return (
            f"($signed({lhs}) "
            f"< $signed({rhs}))"
        )

    raise RuntimeError(
        "unsupported condition "
        f"{condition.operation}"
    )


def emit_structural_expr(
    expression: Expr, 
    widths: dict[str, int], 
    semantic_ssa: SemanticSSAResult, 
    region: ReachRegion, 
) -> str: 

    if expression.kind == "CONST": 

        return const32(
            expression.value
        )

    if expression.kind == "STATE": 

        family = (
            expression.state_family
        )

        if (
            family is None
            or
            family not in widths
        ): 

            raise RuntimeError(
                "invalid structural STATE"
            )

        width = widths[
            family
        ]

        register = (
            state_reg_name(
                family
            )
        )

        if width >= 32: 
            return register

        return (
            "{{"
            f"{32 - width}"
            "{1'b0}}, "
            f"{register}"
            "}"
        )

    if expression.kind == "GPIO": 

        gpio_value = (
            expression.gpio_value
        )

        if gpio_value is None: 

            raise RuntimeError(
                "structural GPIO "
                "without SSA name"
            )

        semantic = (
            semantic_ssa
            .expressions
            .get(
                gpio_value
            )
        )

        if (
            semantic is None
            or
            semantic.kind != "GPIO"
            or
            semantic.gpio_block is None
        ): 

            raise RuntimeError(
                "cannot resolve structural "
                f"GPIO sample {gpio_value}"
            )

        return sampled_gpio_name(
            semantic.gpio_block
        )

    if expression.kind == "SEMANTIC": 

        value = expression.semantic_value
        if value is None: 
            raise RuntimeError(
                "structural SEMANTIC without SSA value"
            )

        semantic = semantic_ssa.expressions.get(value)
        if semantic is None: 
            raise RuntimeError(
                f"cannot resolve structural semantic SSA {value}"
            )

        return emit_semantic_expr(
            semantic, 
            region, 
            widths, 
            semantic_ssa.liveins, 
        )

    if expression.kind == "OP": 

        args = [
            emit_structural_expr(
                arg, 
                widths, 
                semantic_ssa, 
                region, 
            )

            for arg
            in expression.args
        ]

        if len(args) != 2: 

            raise RuntimeError(
                "structural OP requires "
                "2 operands"
            )

        operation = (
            expression.operation
        )

        operators = {
            "ADD": "+", 
            "AND": "&", 
            "OR": "|", 
            "SHL": "<<", 
            "SHR": ">>", 
        }

        if (
            operation
            not in operators
        ): 

            raise RuntimeError(
                "unsupported structural "
                f"operation {operation}"
            )

        return (
            f"({args[0]} "
            f"{operators[operation]} "
            f"{args[1]})"
        )

    raise RuntimeError(
        "unsupported structural kind "
        f"{expression.kind}"
    )


def source_reach_expr(
    region: ReachRegion, 
    source: int, 
    active_name: str, 
) -> str: 

    if (
        region.start_sample
        is not None
        and
        source
        == region.start_sample
    ): 

        return active_name

    entry = region_entry_block(
        region
    )

    if (
        region.start_sample
        is None
        and
        source == entry
    ): 

        return active_name

    return reach_name(
        region.name, 
        source, 
    )


def incoming_edge_names(
    region: ReachRegion, 
    block_id: int, 
) -> list[str]: 

    return [
        edge_name(
            region.name, 
            edge.source, 
            edge.target, 
        )

        for edge
        in region.edges

        if (
            edge.target
            == block_id
        )
    ]



def sampled_gpio_bit_reg_name(
    block_id: int, 
    bit_index: int, 
) -> str: 

    return (
        f"{sampled_gpio_name(block_id)}"
        f"_b{bit_index:02d}"
    )


def _const_value_from_semantic(
    expression: SemanticExpr, 
) -> int | None: 

    if expression.kind != "CONST": 
        return None

    if expression.value is None: 
        return 0

    return int(expression.value) & 0xFFFF_FFFF


def _const_value_from_structural(
    expression: Expr, 
) -> int | None: 

    if expression.kind != "CONST": 
        return None

    if expression.value is None: 
        return 0

    return int(expression.value) & 0xFFFF_FFFF


def _normalize_demanded_bits(
    demanded_bits: set[int], 
) -> set[int]: 

    return {
        bit
        for bit in demanded_bits
        if 0 <= bit < 32
    }


def _all_gpio_blocks_in_semantic(
    expression: SemanticExpr, 
) -> set[int]: 

    result: set[int] = set()

    if expression.kind == "GPIO": 

        if expression.gpio_block is not None: 
            result.add(
                expression.gpio_block
            )

        return result

    for arg in expression.args: 
        result.update(
            _all_gpio_blocks_in_semantic(
                arg
            )
        )

    for (
        _predecessor, 
        incoming, 
    ) in expression.phi_inputs: 

        result.update(
            _all_gpio_blocks_in_semantic(
                incoming
            )
        )

    return result


def _all_gpio_blocks_in_structural(
    expression: Expr, 
    semantic_ssa: SemanticSSAResult, 
) -> set[int]: 

    result: set[int] = set()

    if expression.kind == "GPIO": 

        gpio_value = (
            expression.gpio_value
        )

        if gpio_value is None: 
            return result

        semantic = (
            semantic_ssa
            .expressions
            .get(
                gpio_value
            )
        )

        if semantic is None: 
            return result

        result.update(
            _all_gpio_blocks_in_semantic(
                semantic
            )
        )

        return result

    if expression.kind == "SEMANTIC": 
        value = expression.semantic_value
        if value is None: 
            return result
        semantic = semantic_ssa.expressions.get(value)
        if semantic is None: 
            return result
        result.update(
            _all_gpio_blocks_in_semantic(semantic)
        )
        return result

    for arg in expression.args: 
        result.update(
            _all_gpio_blocks_in_structural(
                arg, 
                semantic_ssa, 
            )
        )

    return result


def _record_gpio_demand(
    usage: dict[int, set[int]], 
    block_id: int, 
    demanded_bits: set[int], 
) -> None: 

    normalized = (
        _normalize_demanded_bits(
            demanded_bits
        )
    )

    if not normalized: 
        return

    usage.setdefault(
        block_id, 
        set(), 
    ).update(
        normalized
    )


def _collect_semantic_gpio_usage(
    expression: SemanticExpr, 
    demanded_bits: set[int], 
    usage: dict[int, set[int]], 
) -> None: 

    demanded = (
        _normalize_demanded_bits(
            demanded_bits
        )
    )

    if not demanded: 
        return

    if expression.kind == "GPIO": 

        block_id = (
            expression.gpio_block
        )

        if block_id is None: 
            raise RuntimeError(
                "GPIO expression "
                "without source block"
            )

        _record_gpio_demand(
            usage, 
            block_id, 
            demanded, 
        )

        return

    if expression.kind in {
        "CONST", 
        "STATE", 
        "SEMANTIC_STATE", 
        "LIVEIN", 
    }: 
        return

    if expression.kind == "PHI": 

        for (
            _predecessor, 
            incoming, 
        ) in expression.phi_inputs: 

            _collect_semantic_gpio_usage(
                incoming, 
                demanded, 
                usage, 
            )

        return

    if expression.kind != "OP": 

        for block_id in (
            _all_gpio_blocks_in_semantic(
                expression
            )
        ): 

            _record_gpio_demand(
                usage, 
                block_id, 
                set(range(32)), 
            )

        return

    if len(expression.args) != 2: 

        for block_id in (
            _all_gpio_blocks_in_semantic(
                expression
            )
        ): 

            _record_gpio_demand(
                usage, 
                block_id, 
                set(range(32)), 
            )

        return

    left = expression.args[0]
    right = expression.args[1]
    operation = expression.operation

    if operation == "AND": 

        left_const = (
            _const_value_from_semantic(
                left
            )
        )

        right_const = (
            _const_value_from_semantic(
                right
            )
        )

        left_demand = set(demanded)
        right_demand = set(demanded)

        if right_const is not None: 

            left_demand = {
                bit
                for bit in demanded
                if (
                    right_const
                    & (1 << bit)
                )
            }

        if left_const is not None: 

            right_demand = {
                bit
                for bit in demanded
                if (
                    left_const
                    & (1 << bit)
                )
            }

        _collect_semantic_gpio_usage(
            left, 
            left_demand, 
            usage, 
        )

        _collect_semantic_gpio_usage(
            right, 
            right_demand, 
            usage, 
        )

        return

    if operation == "OR": 

        left_const = (
            _const_value_from_semantic(
                left
            )
        )

        right_const = (
            _const_value_from_semantic(
                right
            )
        )

        left_demand = set(demanded)
        right_demand = set(demanded)

        if right_const is not None: 

            left_demand = {
                bit
                for bit in demanded
                if not (
                    right_const
                    & (1 << bit)
                )
            }

        if left_const is not None: 

            right_demand = {
                bit
                for bit in demanded
                if not (
                    left_const
                    & (1 << bit)
                )
            }

        _collect_semantic_gpio_usage(
            left, 
            left_demand, 
            usage, 
        )

        _collect_semantic_gpio_usage(
            right, 
            right_demand, 
            usage, 
        )

        return

    if operation in {
        "SHL", 
        "SHR", 
    }: 

        shift = (
            _const_value_from_semantic(
                right
            )
        )

        if shift is None: 

            for block_id in (
                _all_gpio_blocks_in_semantic(
                    expression
                )
            ): 

                _record_gpio_demand(
                    usage, 
                    block_id, 
                    set(range(32)), 
                )

            return

        shift &= 0x1F

        if operation == "SHL": 

            source_bits = {
                bit - shift
                for bit in demanded
                if bit >= shift
            }

        else: 

            source_bits = {
                bit + shift
                for bit in demanded
                if bit + shift < 32
            }

        _collect_semantic_gpio_usage(
            left, 
            source_bits, 
            usage, 
        )

        return

    if operation == "ADD": 

        max_bit = max(
            demanded
        )

        source_bits = set(
            range(
                max_bit + 1
            )
        )

        _collect_semantic_gpio_usage(
            left, 
            source_bits, 
            usage, 
        )

        _collect_semantic_gpio_usage(
            right, 
            source_bits, 
            usage, 
        )

        return

    for block_id in (
        _all_gpio_blocks_in_semantic(
            expression
        )
    ): 

        _record_gpio_demand(
            usage, 
            block_id, 
            set(range(32)), 
        )


def _collect_structural_gpio_usage(
    expression: Expr, 
    demanded_bits: set[int], 
    usage: dict[int, set[int]], 
    semantic_ssa: SemanticSSAResult, 
) -> None: 

    demanded = (
        _normalize_demanded_bits(
            demanded_bits
        )
    )

    if not demanded: 
        return

    if expression.kind == "GPIO": 

        gpio_value = (
            expression.gpio_value
        )

        if gpio_value is None: 

            raise RuntimeError(
                "structural GPIO "
                "without SSA name"
            )

        semantic = (
            semantic_ssa
            .expressions
            .get(
                gpio_value
            )
        )

        if semantic is None: 

            raise RuntimeError(
                "cannot resolve structural "
                f"GPIO sample {gpio_value}"
            )

        _collect_semantic_gpio_usage(
            semantic, 
            demanded, 
            usage, 
        )

        return

    if expression.kind == "SEMANTIC": 
        value = expression.semantic_value
        if value is None: 
            raise RuntimeError(
                "structural SEMANTIC without SSA value"
            )
        semantic = semantic_ssa.expressions.get(value)
        if semantic is None: 
            raise RuntimeError(
                f"cannot resolve structural semantic SSA {value}"
            )
        _collect_semantic_gpio_usage(
            semantic, 
            demanded, 
            usage, 
        )
        return

    if expression.kind in {
        "CONST", 
        "STATE", 
    }: 
        return

    if expression.kind != "OP": 

        for block_id in (
            _all_gpio_blocks_in_structural(
                expression, 
                semantic_ssa, 
            )
        ): 

            _record_gpio_demand(
                usage, 
                block_id, 
                set(range(32)), 
            )

        return

    if len(expression.args) != 2: 

        for block_id in (
            _all_gpio_blocks_in_structural(
                expression, 
                semantic_ssa, 
            )
        ): 

            _record_gpio_demand(
                usage, 
                block_id, 
                set(range(32)), 
            )

        return

    left = expression.args[0]
    right = expression.args[1]
    operation = expression.operation

    if operation == "AND": 

        left_const = (
            _const_value_from_structural(
                left
            )
        )

        right_const = (
            _const_value_from_structural(
                right
            )
        )

        left_demand = set(demanded)
        right_demand = set(demanded)

        if right_const is not None: 

            left_demand = {
                bit
                for bit in demanded
                if (
                    right_const
                    & (1 << bit)
                )
            }

        if left_const is not None: 

            right_demand = {
                bit
                for bit in demanded
                if (
                    left_const
                    & (1 << bit)
                )
            }

        _collect_structural_gpio_usage(
            left, 
            left_demand, 
            usage, 
            semantic_ssa, 
        )

        _collect_structural_gpio_usage(
            right, 
            right_demand, 
            usage, 
            semantic_ssa, 
        )

        return

    if operation == "OR": 

        left_const = (
            _const_value_from_structural(
                left
            )
        )

        right_const = (
            _const_value_from_structural(
                right
            )
        )

        left_demand = set(demanded)
        right_demand = set(demanded)

        if right_const is not None: 

            left_demand = {
                bit
                for bit in demanded
                if not (
                    right_const
                    & (1 << bit)
                )
            }

        if left_const is not None: 

            right_demand = {
                bit
                for bit in demanded
                if not (
                    left_const
                    & (1 << bit)
                )
            }

        _collect_structural_gpio_usage(
            left, 
            left_demand, 
            usage, 
            semantic_ssa, 
        )

        _collect_structural_gpio_usage(
            right, 
            right_demand, 
            usage, 
            semantic_ssa, 
        )

        return

    if operation in {
        "SHL", 
        "SHR", 
    }: 

        shift = (
            _const_value_from_structural(
                right
            )
        )

        if shift is None: 

            for block_id in (
                _all_gpio_blocks_in_structural(
                    expression, 
                    semantic_ssa, 
                )
            ): 

                _record_gpio_demand(
                    usage, 
                    block_id, 
                    set(range(32)), 
                )

            return

        shift &= 0x1F

        if operation == "SHL": 

            source_bits = {
                bit - shift
                for bit in demanded
                if bit >= shift
            }

        else: 

            source_bits = {
                bit + shift
                for bit in demanded
                if bit + shift < 32
            }

        _collect_structural_gpio_usage(
            left, 
            source_bits, 
            usage, 
            semantic_ssa, 
        )

        return

    if operation == "ADD": 

        max_bit = max(
            demanded
        )

        source_bits = set(
            range(
                max_bit + 1
            )
        )

        _collect_structural_gpio_usage(
            left, 
            source_bits, 
            usage, 
            semantic_ssa, 
        )

        _collect_structural_gpio_usage(
            right, 
            source_bits, 
            usage, 
            semantic_ssa, 
        )

        return

    for block_id in (
        _all_gpio_blocks_in_structural(
            expression, 
            semantic_ssa, 
        )
    ): 

        _record_gpio_demand(
            usage, 
            block_id, 
            set(range(32)), 
        )


def infer_sampled_gpio_bits(
    structural: StructuralNetlist, 
    next_state_expr: NextStateExprIR, 
    semantic_ssa: SemanticSSAResult, 
    gpio_effects: Any, 
) -> dict[int, set[int]]: 

    usage: dict[
        int, 
        set[int], 
    ] = {
        block_id: set()
        for block_id in (
            semantic_ssa
            .gpio_sample_blocks
        )
    }

    all_bits = set(
        range(32)
    )

    for condition in (
        semantic_ssa
        .branch_conditions
        .values()
    ): 

        _collect_semantic_gpio_usage(
            condition.lhs, 
            all_bits, 
            usage, 
        )

        _collect_semantic_gpio_usage(
            condition.rhs, 
            all_bits, 
            usage, 
        )

    for expression in (
        semantic_ssa
        .gpio_arguments
        .values()
    ): 

        _collect_semantic_gpio_usage(
            expression, 
            all_bits, 
            usage, 
        )

    for definition in (
        semantic_ssa
        .semantic_state_definitions
        .values()
    ): 

        for (
            _predecessor, 
            incoming, 
        ) in definition.incoming: 

            _collect_semantic_gpio_usage(
                incoming, 
                all_bits, 
                usage, 
            )

    widths = structural_widths(
        structural
    )

    all_writes = [
        write
        for writes in (
            next_state_expr
            .by_edge
            .values()
        )
        for write in writes
    ]

    for write in all_writes: 

        width = widths.get(
            write.family, 
            32, 
        )

        demanded = set(
            range(
                min(
                    32, 
                    max(
                        1, 
                        width, 
                    ), 
                )
            )
        )

        _collect_structural_gpio_usage(
            write.expression, 
            demanded, 
            usage, 
            semantic_ssa, 
        )

    for (
        block_id, 
        effects, 
    ) in gpio_effects.by_block.items(): 

        for effect_index, _effect in enumerate(
            effects
        ): 

            expression = (
                semantic_ssa
                .gpio_arguments
                .get(
                    (
                        block_id, 
                        effect_index, 
                    )
                )
            )

            if expression is None: 
                continue

            _collect_semantic_gpio_usage(
                expression, 
                all_bits, 
                usage, 
            )

    for block_id in list(
        usage
    ): 

        if not usage[block_id]: 

            # Conservative fallback. A GPIO sample point that is
            # present in the semantic IR but whose exact bit demand
            # cannot be proven must retain all bits.
            usage[
                block_id
            ] = set(
                range(32)
            )

    return usage


def reconstructed_sampled_gpio_expr(
    block_id: int, 
    used_bits: set[int], 
) -> str: 

    terms = [
        (
            "({{31{1'b0}}, "
            f"{sampled_gpio_bit_reg_name(block_id, bit)}"
            "} << "
            f"32'd{bit})"
        )
        for bit in sorted(
            used_bits
        )
    ]

    if not terms: 
        return "32'd0"

    return (
        "32'd0 | "
        + " | ".join(
            terms
        )
    )



def semantic_storage_reg_name(
    name: str, 
) -> str: 

    return (
        semantic_reg_name(name)
        + "_storage"
    )


def _semantic_expr_value_width(
    expression: SemanticExpr, 
    structural_widths_map: dict[str, int], 
    semantic_widths: dict[str, int], 
) -> int: 
    """Conservatively prove the number of low bits needed to store a value.

    The semantic IR models RV32 values, so anything whose range cannot be
    proven from constants, already-narrow structural state, PHIs, or another
    semantic state remains 32 bits.  This is deliberately conservative: it
    only narrows persistent semantic state when truncation is provably lossless.
    """

    if expression.kind == "CONST": 

        value = expression.value

        if value is None: 
            return 1

        value &= 0xFFFF_FFFF

        return max(
            1, 
            value.bit_length(), 
        )

    if expression.kind == "STATE": 

        family = expression.state_family

        if family is None: 
            return 32

        return min(
            32, 
            max(
                1, 
                structural_widths_map.get(
                    family, 
                    32, 
                ), 
            ), 
        )

    if expression.kind == "SEMANTIC_STATE": 

        name = expression.semantic_state_name

        if name is None: 
            return 32

        return semantic_widths.get(
            name, 
            32, 
        )

    if expression.kind == "PHI": 

        if not expression.phi_inputs: 
            return 1

        return min(
            32, 
            max(
                _semantic_expr_value_width(
                    incoming, 
                    structural_widths_map, 
                    semantic_widths, 
                )
                for _predecessor, incoming
                in expression.phi_inputs
            ), 
        )

    # GPIO and LIVEIN are full RV32 values.  OP is also kept at 32 bits
    # unless a later dedicated range analysis proves otherwise.  This avoids
    # changing RV32 arithmetic/shift semantics merely to save registers.
    return 32


def infer_semantic_state_widths(
    structural: StructuralNetlist, 
    semantic_ssa: SemanticSSAResult, 
) -> dict[str, int]: 
    """Infer a safe persistent storage width for each semantic state."""

    structural_widths_map = structural_widths(
        structural
    )

    result: dict[str, int] = {
        name: 1
        for name in semantic_ssa.semantic_states
    }

    # Semantic states may refer to themselves or to one another through PHIs.
    # A monotone fixed point from width=1 discovers the smallest width proven
    # by all incoming values.  Unknown operations immediately force 32 bits.
    max_iterations = (
        len(result)
        + 2
    )

    for _iteration in range(max_iterations): 

        changed = False

        for name in sorted(result): 

            definition = (
                semantic_ssa
                .semantic_state_definitions
                .get(name)
            )

            if definition is None: 
                required = 32
            else: 
                incoming_widths = [
                    _semantic_expr_value_width(
                        incoming, 
                        structural_widths_map, 
                        result, 
                    )
                    for _predecessor, incoming
                    in definition.incoming
                ]

                required = (
                    max(incoming_widths)
                    if incoming_widths
                    else 1
                )

            required = min(
                32, 
                max(
                    1, 
                    required, 
                ), 
            )

            if required > result[name]: 
                result[name] = required
                changed = True

        if not changed: 
            break
    else: 
        raise RuntimeError(
            "semantic-state width inference "
            "did not converge"
        )

    return result

def collect_folded_guards(
    semantic_next_state: SemanticNextStateResult, 
) -> dict[str, tuple[GuardExpr, ...]]: 
    """Collect a shared Boolean DAG for folded physical-state guards.

    The first folded prototype recursively inlined every path predicate at
    every state write.  That preserves semantics but duplicates common path
    prefixes exponentially in emitted SV.  Collect each structurally equal
    GuardExpr once per semantic region and emit it as a shared one-bit net.
    """

    by_region: dict[str, tuple[GuardExpr, ...]] = {}

    for region_name, writes in semantic_next_state.by_region.items(): 
        seen: set[GuardExpr] = set()
        ordered: list[GuardExpr] = []

        def visit(expr: GuardExpr) -> None: 
            for child in expr.args: 
                visit(child)
            if expr.kind in {"CONST", "ACTIVE"}: 
                return
            if expr not in seen: 
                seen.add(expr)
                ordered.append(expr)

        for folded in writes: 
            visit(folded.guard)

        for guard in (
            semantic_next_state
            .edge_guards_by_region
            .get(region_name, {})
            .values()
        ): 
            visit(guard)

        for guard in (
            semantic_next_state
            .block_guards_by_region
            .get(region_name, {})
            .values()
        ): 
            visit(guard)

        by_region[region_name] = tuple(ordered)

    return by_region


def folded_guard_net_name(
    region_name: str, 
    index: int, 
) -> str: 
    return (
        "fold_guard_"
        + region_token(region_name)
        + "_"
        + str(index)
    )


def folded_guard_signal(
    guard: GuardExpr, 
    region_name: str, 
    names_by_region: dict[str, dict[GuardExpr, str]], 
    region: ReachRegion, 
    widths: dict[str, int], 
    liveins: set[str], 
    semantic_ssa: SemanticSSAResult, 
) -> str: 
    signal = names_by_region.get(region_name, {}).get(guard)
    if signal is not None: 
        return signal
    return emit_folded_guard(
        guard, 
        region, 
        widths, 
        liveins, 
        semantic_ssa, 
    )


def emit_folded_guard_shared_expr(
    guard: GuardExpr, 
    region: ReachRegion, 
    widths: dict[str, int], 
    liveins: set[str], 
    semantic_ssa: SemanticSSAResult, 
    names: dict[GuardExpr, str], 
) -> str: 
    if guard.kind == "CONST": 
        return "1'b1" if guard.value else "1'b0"

    if guard.kind == "ACTIVE": 
        if guard.region_name != region.name: 
            raise RuntimeError("folded guard region mismatch")
        return "active_" + region_token(region.name)

    if guard.kind == "PRED": 
        if guard.block_id is None: 
            raise RuntimeError("folded predicate without block")
        condition = semantic_ssa.branch_conditions.get(guard.block_id)
        if condition is None: 
            raise RuntimeError(
                f"missing folded condition BB{guard.block_id:03d}"
            )
        return emit_condition(condition, region, widths, liveins)

    if guard.kind == "NOT": 
        child = guard.args[0]
        child_text = names.get(child)
        if child_text is None: 
            child_text = emit_folded_guard_shared_expr(
                child, region, widths, liveins, semantic_ssa, names
            )
        return f"!({child_text})"

    if guard.kind in {"AND", "OR"}: 
        operator = " & " if guard.kind == "AND" else " | "
        parts: list[str] = []
        for child in guard.args: 
            child_text = names.get(child)
            if child_text is None: 
                child_text = emit_folded_guard_shared_expr(
                    child, region, widths, liveins, semantic_ssa, names
                )
            parts.append(child_text)
        return "(" + operator.join(parts) + ")"

    raise RuntimeError(f"unsupported folded guard kind {guard.kind}")


def emit_folded_guard(
    guard: GuardExpr, 
    region: ReachRegion, 
    widths: dict[str, int], 
    liveins: set[str], 
    semantic_ssa: SemanticSSAResult, 
) -> str: 

    if guard.kind == "CONST": 
        return "1'b1" if guard.value else "1'b0"

    if guard.kind == "ACTIVE": 
        if guard.region_name != region.name: 
            raise RuntimeError("folded guard region mismatch")
        return "active_" + region_token(region.name)

    if guard.kind == "PRED": 
        if guard.block_id is None: 
            raise RuntimeError("folded predicate without block")
        condition = semantic_ssa.branch_conditions.get(guard.block_id)
        if condition is None: 
            raise RuntimeError(
                f"missing folded condition BB{guard.block_id:03d}"
            )
        return emit_condition(condition, region, widths, liveins)

    if guard.kind == "NOT": 
        return f"!({emit_folded_guard(guard.args[0], region, widths, liveins, semantic_ssa)})"

    if guard.kind in {"AND", "OR"}: 
        operator = " & " if guard.kind == "AND" else " | "
        return "(" + operator.join(
            emit_folded_guard(item, region, widths, liveins, semantic_ssa)
            for item in guard.args
        ) + ")"

    raise RuntimeError(f"unsupported folded guard kind {guard.kind}")


def emit_semantic_systemverilog(
    structural: StructuralNetlist, 
    next_state_expr: NextStateExprIR, 
    reach: SemanticReachResult, 
    semantic_ssa: SemanticSSAResult, 
    gpio_effects: Any, 
    output_path: Path, 
    semantic_next_state: SemanticNextStateResult | None = None, 
    definition_site_ir: DefinitionSiteIR | None = None, 
    proven_hoist_plan: ProvenHoistPlan | None = None, 
) -> None: 

    widths = structural_widths(
        structural
    )

    semantic_widths = (
        infer_semantic_state_widths(
            structural, 
            semantic_ssa, 
        )
    )

    (
        region_codes, 
        sample_codes, 
    ) = sample_state_map(
        reach
    )

    state_width = max(
        1, 
        math.ceil(
            math.log2(
                max(
                    2, 
                    len(
                        region_codes
                    ), 
                )
            )
        ), 
    )

    liveins = set(
        semantic_ssa.liveins
    )

    sample_blocks = sorted(
        semantic_ssa
        .gpio_sample_blocks
    )

    sampled_gpio_bits = (
        infer_sampled_gpio_bits(
            structural, 
            next_state_expr, 
            semantic_ssa, 
            gpio_effects, 
        )
    )

    lines: list[str] = []

    lines.append(
        "`timescale 1ns/1ps"
    )

    lines.append("")

    lines.append(
        "module bio2rtl_generated ("
    )

    lines.append(
        "    input logic clk,"
    )

    lines.append(
        "    input logic reset,"
    )

    lines.append(
        "    input logic [31:0] gpio_in,"
    )

    for livein in sorted(
        liveins
    ): 

        lines.append(
            "    input logic [31:0] "
            f"livein_{sv_name(livein)},"
        )

    lines.append(
        "    output logic [31:0] gpio_out,"
    )

    lines.append(
        "    output logic [31:0] gpio_oe"
    )

    lines.append(
        ");"
    )

    lines.append("")

    lines.append(
        f"logic [{state_width - 1}:0] "
        "sample_state;"
    )

    lines.append(
        f"logic [{state_width - 1}:0] "
        "sample_state_next;"
    )

    lines.append("")

    for (
        region_name, 
        code, 
    ) in sorted(
        region_codes.items(), 
        key = lambda item: 
            item[1], 
    ): 

        lines.append(
            "localparam logic "
            f"[{state_width - 1}:0] "
            f"SS_{sv_name(region_name).upper()} "
            f"= {state_width}'d{code};"
        )

    lines.append("")

    lines.append(
        "logic [6:0] fsm_state;"
    )

    lines.append("")

    for block_id in sample_blocks: 

        used_bits = sorted(
            sampled_gpio_bits[
                block_id
            ]
        )

        for bit_index in used_bits: 

            lines.append(
                "logic "
                f"{sampled_gpio_bit_reg_name(block_id, bit_index)};"
            )

        lines.append(
            "logic [31:0] "
            f"{sampled_gpio_name(block_id)};"
        )

        lines.append(
            f"assign {sampled_gpio_name(block_id)} = "
            f"{reconstructed_sampled_gpio_expr(block_id, set(used_bits))};"
        )

    lines.append("")

    for family in sorted(
        widths
    ): 

        width = widths[
            family
        ]

        lines.append(
            f"logic [{width - 1}:0] "
            f"{state_reg_name(family)};"
        )

        lines.append(
            f"logic [{width - 1}:0] "
            f"{state_next_name(family)};"
        )

    lines.append("")

    for name in sorted(
        semantic_ssa.semantic_states
    ): 

        semantic_width = (
            semantic_widths[name]
        )

        if semantic_width < 32: 

            lines.append(
                f"logic [{semantic_width - 1}:0] "
                f"{semantic_storage_reg_name(name)};"
            )

            lines.append(
                "logic [31:0] "
                f"{semantic_reg_name(name)};"
            )

            lines.append(
                f"assign {semantic_reg_name(name)} = "
                "{{"
                f"{32 - semantic_width}"
                "{1'b0}}, "
                f"{semantic_storage_reg_name(name)}"
                "};"
            )

        else: 

            lines.append(
                "logic [31:0] "
                f"{semantic_reg_name(name)};"
            )

        # Keep next-state expressions at RV32 width.  Narrowing only the
        # persistent storage preserves all existing semantic expression
        # emission and truncates only after the value range has been proven.
        lines.append(
            "logic [31:0] "
            f"{semantic_next_name(name)};"
        )

    lines.append("")

    lines.append(
        "logic [31:0] gpio_data;"
    )

    lines.append(
        "logic [31:0] gpio_data_next;"
    )

    lines.append(
        "logic [31:0] gpio_direction;"
    )

    lines.append(
        "logic [31:0] gpio_direction_next;"
    )

    lines.append(
        "logic [31:0] gpio_mask;"
    )

    lines.append(
        "logic [31:0] gpio_mask_next;"
    )

    lines.append("")

    lines.append(
        "assign gpio_out = gpio_data;"
    )

    lines.append(
        "assign gpio_oe = gpio_direction;"
    )

    lines.append("")

    for (
        region_name, 
        code, 
    ) in sorted(
        region_codes.items(), 
        key = lambda item: 
            item[1], 
    ): 

        active = (
            "active_"
            + region_token(
                region_name
            )
        )

        lines.append(
            f"logic {active};"
        )

        lines.append(
            f"assign {active} = "
            "(sample_state == "
            f"SS_{sv_name(region_name).upper()});"
        )

    lines.append("")

    folded_guard_names_by_region: dict[
        str, 
        dict[GuardExpr, str], 
    ] = {}

    if semantic_next_state is not None: 
        folded_guards = collect_folded_guards(
            semantic_next_state
        )

        for region_name, guards in folded_guards.items(): 
            region = reach.regions[region_name]
            names = {
                guard: folded_guard_net_name(region_name, index)
                for index, guard in enumerate(guards)
            }
            folded_guard_names_by_region[region_name] = names

            for guard in guards: 
                lines.append(
                    "logic " + names[guard] + ";"
                )

            for guard in guards: 
                expression = emit_folded_guard_shared_expr(
                    guard, 
                    region, 
                    widths, 
                    liveins, 
                    semantic_ssa, 
                    names, 
                )
                lines.append(
                    "assign "
                    + names[guard]
                    + " = "
                    + expression
                    + ";"
                )

        lines.append("")

        # Compatibility aliases for semantic PHI expressions and legacy
        # verification testbenches.  The actual reachability computation is
        # performed only once by the shared fold_guard_* DAG above.  These
        # edge_*/reach_* nets are aliases and synthesize away; they do not
        # recreate the old edge/reach network.
        for region_name, region in reach.regions.items(): 
            names = folded_guard_names_by_region.get(region_name, {})

            for block_id in sorted(set(region.blocks)): 
                alias = reach_name(region_name, block_id)
                lines.append(f"logic {alias};")

                block_guard = (
                    semantic_next_state
                    .block_guards_by_region
                    .get(region_name, {})
                    .get(block_id)
                )

                if block_guard is not None: 
                    target = folded_guard_signal(
                        block_guard, 
                        region_name, 
                        folded_guard_names_by_region, 
                        region, 
                        widths, 
                        liveins, 
                        semantic_ssa, 
                    )
                else: 
                    entry = region_entry_block(region)
                    if region.start_sample is None and block_id == entry: 
                        target = "active_" + region_token(region_name)
                    elif region.start_sample is not None and block_id == region.start_sample: 
                        target = "active_" + region_token(region_name)
                    else: 
                        raise RuntimeError(
                            f"missing folded compatibility block guard "
                            f"{region_name} BB{block_id:03d}"
                        )

                lines.append(f"assign {alias} = {target};")

            for edge in region.edges: 
                alias = edge_name(region_name, edge.source, edge.target)
                lines.append(f"logic {alias};")
                edge_guard = (
                    semantic_next_state
                    .edge_guards_by_region[region_name]
                    [(edge.source, edge.target)]
                )
                target = folded_guard_signal(
                    edge_guard, 
                    region_name, 
                    folded_guard_names_by_region, 
                    region, 
                    widths, 
                    liveins, 
                    semantic_ssa, 
                )
                lines.append(f"assign {alias} = {target};")

        lines.append("")

    if semantic_next_state is None: 
        for (
            region_name, 
            region, 
        ) in reach.regions.items(): 

            for block_id in sorted(
                set(
                    region.blocks
                )
            ): 

                lines.append(
                    "logic "
                    f"{reach_name(region_name, block_id)};"
                )

            for edge in region.edges: 

                lines.append(
                    "logic "
                    f"{edge_name(region_name, edge.source, edge.target)};"
                )

        lines.append("")

        for (
            region_name, 
            region, 
        ) in reach.regions.items(): 

            active = (
                "active_"
                + region_token(
                    region_name
                )
            )

            entry = region_entry_block(
                region
            )

            if (
                region.start_sample
                is None
            ): 

                if entry is None: 

                    raise RuntimeError(
                        "ENTRY has no root"
                    )

                lines.append(
                    f"assign "
                    f"{reach_name(region_name, entry)} "
                    f"= {active};"
                )

            for block_id in sorted(
                set(
                    region.blocks
                )
            ): 

                if (
                    region.start_sample
                    is None
                    and
                    block_id == entry
                ): 
                    continue

                incoming = (
                    incoming_edge_names(
                        region, 
                        block_id, 
                    )
                )

                if not incoming: 

                    raise RuntimeError(
                        f"{region_name}: "
                        f"BB{block_id:03d} "
                        "has no incoming edge"
                    )

                lines.append(
                    f"assign "
                    f"{reach_name(region_name, block_id)} "
                    f"= "
                    + " | ".join(
                        incoming
                    )
                    + ";"
                )

            for edge in region.edges: 

                source_reach = (
                    source_reach_expr(
                        region, 
                        edge.source, 
                        active, 
                    )
                )

                if edge.kind == "GOTO": 

                    predicate = "1'b1"

                else: 

                    condition = (
                        semantic_ssa
                        .branch_conditions
                        .get(
                            edge.source
                        )
                    )

                    if condition is None: 

                        raise RuntimeError(
                            f"missing condition "
                            f"BB{edge.source:03d}"
                        )

                    condition_sv = (
                        emit_condition(
                            condition, 
                            region, 
                            widths, 
                            liveins, 
                        )
                    )

                    if edge.kind == "TRUE": 

                        predicate = (
                            condition_sv
                        )

                    elif edge.kind == "FALSE": 

                        predicate = (
                            f"!({condition_sv})"
                        )

                    else: 

                        raise RuntimeError(
                            "unsupported edge kind "
                            f"{edge.kind}"
                        )

                lines.append(
                    f"assign "
                    f"{edge_name(region_name, edge.source, edge.target)} "
                    f"= {source_reach} "
                    f"& ({predicate});"
                )

            lines.append("")

    lines.append(
        "always_comb begin"
    )

    lines.append(
        "    sample_state_next = sample_state;"
    )

    for family in sorted(
        widths
    ): 

        lines.append(
            f"    {state_next_name(family)} "
            f"= {state_reg_name(family)};"
        )

    for name in sorted(
        semantic_ssa.semantic_states
    ): 

        lines.append(
            f"    {semantic_next_name(name)} "
            f"= {semantic_reg_name(name)};"
        )

    lines.append(
        "    gpio_data_next = gpio_data;"
    )

    lines.append(
        "    gpio_direction_next = gpio_direction;"
    )

    lines.append(
        "    gpio_mask_next = gpio_mask;"
    )

    lines.append("")

    for (
        region_name, 
        region, 
    ) in reach.regions.items(): 

        for edge in region.edges: 

            if (
                edge.target
                not in sample_codes
            ): 
                continue

            target_name = (
                f"SAMPLE_BB"
                f"{edge.target:03d}"
            )

            if semantic_next_state is None: 
                sample_select = edge_name(
                    region_name, 
                    edge.source, 
                    edge.target, 
                )
            else: 
                sample_guard = (
                    semantic_next_state
                    .edge_guards_by_region[region_name]
                    [(edge.source, edge.target)]
                )
                sample_select = folded_guard_signal(
                    sample_guard, 
                    region_name, 
                    folded_guard_names_by_region, 
                    region, 
                    widths, 
                    liveins, 
                    semantic_ssa, 
                )

            lines.append(
                f"    if ({sample_select}) begin"
            )

            lines.append(
                "        sample_state_next = "
                f"SS_{sv_name(target_name).upper()};"
            )

            lines.append(
                "    end"
            )

    lines.append("")

    if proven_hoist_plan is not None: 

        hoist_by_block = {}
        hoist_keys = set()
        for h in proven_hoist_plan.rules: 
            hoist_by_block.setdefault(h.definition_block, []).append(h)
            hoist_keys.add((h.register, h.expression_key))

        # Emit in program/topological order: a proven hoisted operation at
        # its original definition block, followed by remaining edge commits
        # from that block.  This ordering is the one exhaustively verified by
        # the selective-hoist analysis.
        for region_name, region in reach.regions.items(): 
            edges_by_source = {}
            for edge in region_edge_order(region): 
                edges_by_source.setdefault(edge.source, []).append(edge)
            for block_id in region_block_order(region): 
                hs = hoist_by_block.get(block_id, [])
                if hs: 
                    lines.append(f"    if ({reach_name(region_name, block_id)}) begin")
                    for h in hs: 
                        expression_sv = emit_structural_expr(h.expression, widths, semantic_ssa, region)
                        lines.append(f"        {state_next_name(h.register)} = {expression_sv};")
                    lines.append("    end")
                for edge in edges_by_source.get(block_id, []): 
                    writes = proven_hoist_plan.retimed_next_state.by_edge.get((edge.source, edge.target), [])
                    filtered = [w for w in writes if (w.family, repr(expr_key(w.expression))) not in hoist_keys]
                    if not filtered: 
                        continue
                    signal = edge_name(region_name, edge.source, edge.target)
                    lines.append(f"    if ({signal}) begin")
                    for write in filtered: 
                        expression_sv = emit_structural_expr(write.expression, widths, semantic_ssa, region)
                        lines.append(f"        {state_next_name(write.family)} = {expression_sv};")
                    lines.append("    end")

    elif definition_site_ir is not None: 

        # Recover persistent-state updates at their original SSA definition
        # sites.  Reach signals already describe whether a basic block
        # executes in the current semantic region.  Emitting definitions in
        # topological block order preserves sequential overwrite semantics
        # while avoiding duplication across later CFG exit edges.
        for region_name, region in reach.regions.items(): 
            for block_id in region_block_order(region): 
                writes = definition_site_ir.by_block.get(block_id, [])
                if not writes: 
                    continue
                signal = reach_name(region_name, block_id)
                lines.append(f"    if ({signal}) begin")
                for write in sorted(writes, key = lambda w: w.order): 
                    expression_sv = emit_structural_expr(
                        write.expression, widths, semantic_ssa, region, 
                    )
                    lines.append(
                        f"        {state_next_name(write.family)} = {expression_sv};"
                    )
                lines.append("    end")

    elif semantic_next_state is None: 

        for (
            region_name, 
            region, 
        ) in reach.regions.items(): 

            for edge in region_edge_order(
                region
            ): 

                writes = (
                    next_state_expr
                    .by_edge
                    .get(
                        (
                            edge.source, 
                            edge.target, 
                        ), 
                        [], 
                    )
                )

                if not writes: 
                    continue

                signal = edge_name(
                    region_name, 
                    edge.source, 
                    edge.target, 
                )

                lines.append(
                    f"    if ({signal}) begin"
                )

                for write in writes: 

                    expression_sv = (
                        emit_structural_expr(
                            write.expression, 
                            widths, 
                            semantic_ssa, 
                            region, 
                        )
                    )

                    lines.append(
                        f"        "
                        f"{state_next_name(write.family)} "
                        f"= {expression_sv};"
                    )

                lines.append(
                    "    end"
                )

    else: 

        for region_name, region in reach.regions.items(): 

            for folded in semantic_next_state.by_region.get(region_name, ()): 

                guard_sv = (
                    folded_guard_names_by_region
                    .get(region_name, {})
                    .get(folded.guard)
                )

                if guard_sv is None: 
                    guard_sv = emit_folded_guard(
                        folded.guard, 
                        region, 
                        widths, 
                        liveins, 
                        semantic_ssa, 
                    )

                expression_sv = emit_structural_expr(
                    folded.write.expression, 
                    widths, 
                    semantic_ssa, 
                    region, 
                )

                lines.append(
                    f"    if ({guard_sv}) begin"
                )
                lines.append(
                    f"        {state_next_name(folded.write.family)} "
                    f"= {expression_sv};"
                )
                lines.append(
                    "    end"
                )

    lines.append("")

    for (
        name, 
        definition, 
    ) in sorted(
        semantic_ssa
        .semantic_state_definitions
        .items()
    ): 

        for (
            predecessor, 
            incoming_expression, 
        ) in definition.incoming: 

            for (
                region_name, 
                region, 
            ) in reach.regions.items(): 

                if (
                    predecessor, 
                    definition.block_id, 
                ) not in edge_tuple_set(
                    region
                ): 
                    continue

                if semantic_next_state is None: 
                    select = edge_name(
                        region_name, 
                        predecessor, 
                        definition.block_id, 
                    )
                else: 
                    semantic_guard = (
                        semantic_next_state
                        .edge_guards_by_region[region_name]
                        [(predecessor, definition.block_id)]
                    )
                    select = folded_guard_signal(
                        semantic_guard, 
                        region_name, 
                        folded_guard_names_by_region, 
                        region, 
                        widths, 
                        liveins, 
                        semantic_ssa, 
                    )

                value = (
                    emit_semantic_expr(
                        incoming_expression, 
                        region, 
                        widths, 
                        liveins, 
                    )
                )

                lines.append(
                    f"    if ({select}) begin"
                )

                lines.append(
                    f"        "
                    f"{semantic_next_name(name)} "
                    f"= {value};"
                )

                lines.append(
                    "    end"
                )

    lines.append("")

    for (
        region_name, 
        region, 
    ) in reach.regions.items(): 

        for block_id in region_block_order(
            region
        ): 

            effects = (
                gpio_effects
                .by_block
                .get(
                    block_id, 
                    [], 
                )
            )

            if not effects: 
                continue

            if semantic_next_state is not None: 
                block_guard = (
                    semantic_next_state
                    .block_guards_by_region
                    .get(region_name, {})
                    .get(block_id)
                )
                if block_guard is None: 
                    if (
                        region.start_sample is not None
                        and block_id == region.start_sample
                    ): 
                        block_reach = (
                            "active_"
                            + region_token(region_name)
                        )
                    else: 
                        raise RuntimeError(
                            f"missing folded block guard "
                            f"{region_name} BB{block_id:03d}"
                        )
                else: 
                    block_reach = folded_guard_signal(
                        block_guard, 
                        region_name, 
                        folded_guard_names_by_region, 
                        region, 
                        widths, 
                        liveins, 
                        semantic_ssa, 
                    )
            elif (
                region.start_sample
                is not None
                and
                block_id
                == region.start_sample
            ): 

                block_reach = (
                    "active_"
                    + region_token(
                        region_name
                    )
                )

            else: 

                block_reach = (
                    reach_name(
                        region_name, 
                        block_id, 
                    )
                )

            lines.append(
                f"    if ({block_reach}) begin"
            )

            for (
                effect_index, 
                effect, 
            ) in enumerate(
                effects
            ): 

                expression = (
                    semantic_ssa
                    .gpio_arguments
                    .get(
                        (
                            block_id, 
                            effect_index, 
                        )
                    )
                )

                if expression is None: 

                    raise RuntimeError(
                        "missing GPIO semantic "
                        f"argument BB{block_id:03d}"
                    )

                value = emit_semantic_expr(
                    expression, 
                    region, 
                    widths, 
                    liveins, 
                )

                if effect.kind == "GPIO_MASK": 

                    lines.append(
                        "        "
                        "gpio_mask_next = "
                        f"{value};"
                    )

                elif effect.kind == "GPIO_SET": 

                    lines.append(
                        "        "
                        "gpio_data_next = "
                        "gpio_data_next | "
                        f"({value} & "
                        "gpio_mask_next);"
                    )

                elif effect.kind == "GPIO_CLEAR_N": 

                    lines.append(
                        "        "
                        "gpio_data_next = "
                        "gpio_data_next & "
                        f"({value} | "
                        "~gpio_mask_next);"
                    )

                elif effect.kind == "GPIO_DIR_SET": 

                    lines.append(
                        "        "
                        "gpio_direction_next = "
                        "gpio_direction_next | "
                        f"({value} & "
                        "gpio_mask_next);"
                    )

                elif effect.kind == "GPIO_DIR_CLEAR": 

                    lines.append(
                        "        "
                        "gpio_direction_next = "
                        "gpio_direction_next & "
                        f"~({value} & "
                        "gpio_mask_next);"
                    )

                else: 

                    raise RuntimeError(
                        "unsupported GPIO "
                        f"effect {effect.kind}"
                    )

            lines.append(
                "    end"
            )

    lines.append(
        "end"
    )

    lines.append("")

    lines.append(
        "always_comb begin"
    )

    lines.append(
        "    fsm_state = 7'd0;"
    )

    lines.append(
        "    case (sample_state)"
    )

    for sample_block in sorted(
        sample_codes
    ): 

        name = (
            f"SAMPLE_BB"
            f"{sample_block:03d}"
        )

        lines.append(
            f"        "
            f"SS_{sv_name(name).upper()}: "
            f"fsm_state = "
            f"7'd{sample_block};"
        )

    lines.append(
        "        default: "
        "fsm_state = 7'd0;"
    )

    lines.append(
        "    endcase"
    )

    lines.append(
        "end"
    )

    lines.append("")

    lines.append(
        "always_ff @(posedge clk) begin"
    )

    lines.append(
        "    if (reset) begin"
    )

    lines.append(
        "        sample_state <= "
        "SS_ENTRY;"
    )

    for block_id in sample_blocks: 

        for bit_index in sorted(
            sampled_gpio_bits[
                block_id
            ]
        ): 

            lines.append(
                f"        "
                f"{sampled_gpio_bit_reg_name(block_id, bit_index)} "
                "<= 1'b0;"
            )

    for family in sorted(
        widths
    ): 

        lines.append(
            f"        "
            f"{state_reg_name(family)} "
            f"<= {widths[family]}'d0;"
        )

    for name in sorted(
        semantic_ssa.semantic_states
    ): 

        semantic_width = (
            semantic_widths[name]
        )

        target = (
            semantic_storage_reg_name(name)
            if semantic_width < 32
            else semantic_reg_name(name)
        )

        lines.append(
            f"        {target} "
            f"<= {semantic_width}'d0;"
        )

    lines.append(
        "        gpio_data <= 32'd0;"
    )

    lines.append(
        "        gpio_direction <= 32'd0;"
    )

    lines.append(
        "        gpio_mask <= "
        "32'hFFFF_FFFF;"
    )

    lines.append(
        "    end else begin"
    )

    # Capture the GPIO value for the semantic sample that
    # becomes active at this clock edge.
    for block_id in sample_blocks: 

        name = (
            f"SAMPLE_BB"
            f"{block_id:03d}"
        )

        lines.append(
            f"        if "
            f"(sample_state_next == "
            f"SS_{sv_name(name).upper()}) "
            "begin"
        )

        for bit_index in sorted(
            sampled_gpio_bits[
                block_id
            ]
        ): 

            lines.append(
                f"            "
                f"{sampled_gpio_bit_reg_name(block_id, bit_index)} "
                f"<= gpio_in[{bit_index}];"
            )

        lines.append(
            "        end"
        )

    lines.append(
        "        sample_state <= "
        "sample_state_next;"
    )

    for family in sorted(
        widths
    ): 

        lines.append(
            f"        "
            f"{state_reg_name(family)} "
            f"<= {state_next_name(family)};"
        )

    for name in sorted(
        semantic_ssa.semantic_states
    ): 

        semantic_width = (
            semantic_widths[name]
        )

        if semantic_width < 32: 
            target = semantic_storage_reg_name(
                name
            )
            value = (
                f"{semantic_next_name(name)}"
                f"[{semantic_width - 1}:0]"
            )
        else: 
            target = semantic_reg_name(name)
            value = semantic_next_name(name)

        lines.append(
            f"        {target} <= {value};"
        )

    lines.append(
        "        gpio_data <= "
        "gpio_data_next;"
    )

    lines.append(
        "        gpio_direction <= "
        "gpio_direction_next;"
    )

    lines.append(
        "        gpio_mask <= "
        "gpio_mask_next;"
    )

    lines.append(
        "    end"
    )

    lines.append(
        "end"
    )

    lines.append("")

    lines.append(
        "endmodule"
    )

    output_path.parent.mkdir(
        parents = True, 
        exist_ok = True, 
    )

    output_path.write_text(
        "\n".join(
            lines
        )
        + "\n", 
        encoding = "utf-8", 
    )