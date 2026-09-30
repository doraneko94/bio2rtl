from __future__ import annotations

from dataclasses import dataclass, field

from .rtl_ir import RTLIR
from .hardware_pattern import HardwarePatternResult
from .next_state_expr import Expr


# ============================================================
# Structural hardware primitives
# ============================================================

@dataclass
class StructuralNode: 
    id: str

    kind: str
    # REGISTER
    # COUNTER_UP
    # COUNTER_DOWN
    # SHIFT_REGISTER
    # CONST
    # ADD
    # AND
    # OR
    # SHL
    # SHR
    # GPIO_INPUT
    # SEMANTIC_INPUT

    width: int | None = None

    family: str | None = None

    value: int | None = None

    inputs: list[str] = field(
        default_factory = list
    )


@dataclass
class StructuralWrite: 
    source_block: int
    target_block: int

    state_node: str
    expression_node: str


@dataclass
class StructuralNetlist: 
    nodes: dict[
        str, 
        StructuralNode, 
    ]

    state_nodes: dict[
        str, 
        str, 
    ]

    writes: list[
        StructuralWrite, 
    ]

    primitive_counts: dict[
        str, 
        int, 
    ]


# ============================================================
# Expression interning
# ============================================================

class ExpressionBuilder: 

    def __init__(
        self, 
        nodes: dict[str, StructuralNode], 
        state_nodes: dict[str, str], 
    ): 

        self.nodes = nodes
        self.state_nodes = state_nodes

        self.expr_cache = {}

        self.next_id = 0


    def new_id(
        self, 
        prefix: str, 
    ) -> str: 

        result = (
            f"{prefix}_{self.next_id}"
        )

        self.next_id += 1

        return result


    def build(
        self, 
        expr: Expr, 
    ) -> str: 

        # ====================================================
        # Constant
        # ====================================================

        if expr.kind == "CONST": 

            key = (
                "CONST", 
                expr.value, 
            )

            cached = self.expr_cache.get(
                key
            )

            if cached is not None: 
                return cached


            node_id = self.new_id(
                "const"
            )

            self.nodes[node_id] = (
                StructuralNode(
                    id = node_id, 
                    kind = "CONST", 
                    value = expr.value, 
                )
            )

            self.expr_cache[key] = (
                node_id
            )

            return node_id


        # ====================================================
        # Persistent state reference
        # ====================================================

        if expr.kind == "STATE": 

            family = (
                expr.state_family
            )

            if family is None: 

                raise RuntimeError(
                    "STATE without family"
                )


            node_id = (
                self.state_nodes.get(
                    family
                )
            )


            if node_id is None: 

                raise RuntimeError(
                    f"Unknown state family "
                    f"{family}"
                )


            return node_id


        # ====================================================
        # GPIO input
        # ====================================================

        if expr.kind == "GPIO": 

            key = (
                "GPIO", 
                expr.gpio_value, 
            )

            cached = self.expr_cache.get(
                key
            )

            if cached is not None: 
                return cached


            node_id = self.new_id(
                "gpio"
            )


            self.nodes[node_id] = (
                StructuralNode(
                    id = node_id, 
                    kind = "GPIO_INPUT", 
                    family = 
                        expr.gpio_value, 
                )
            )


            self.expr_cache[key] = (
                node_id
            )

            return node_id


        # ====================================================
        # Semantic SSA input
        # ====================================================

        if expr.kind == "SEMANTIC": 

            if expr.semantic_value is None: 
                raise RuntimeError(
                    "SEMANTIC without SSA value"
                )

            key = (
                "SEMANTIC", 
                expr.semantic_value, 
            )

            cached = self.expr_cache.get(key)
            if cached is not None: 
                return cached

            node_id = self.new_id("semantic")
            self.nodes[node_id] = StructuralNode(
                id = node_id, 
                kind = "SEMANTIC_INPUT", 
                family = expr.semantic_value, 
            )
            self.expr_cache[key] = node_id
            return node_id


        # ====================================================
        # Combinational operation
        # ====================================================

        if expr.kind == "OP": 

            if expr.operation is None: 

                raise RuntimeError(
                    "OP without operation"
                )


            input_nodes = tuple(
                self.build(arg)

                for arg in expr.args
            )


            key = (
                expr.operation, 
                input_nodes, 
            )


            cached = (
                self.expr_cache.get(
                    key
                )
            )


            if cached is not None: 
                return cached


            node_id = self.new_id(
                expr.operation.lower()
            )


            self.nodes[node_id] = (
                StructuralNode(
                    id = node_id, 
                    kind = expr.operation, 
                    inputs = list(
                        input_nodes
                    ), 
                )
            )


            self.expr_cache[key] = (
                node_id
            )


            return node_id


        raise RuntimeError(
            f"Unsupported Expr kind "
            f"{expr.kind}"
        )


# ============================================================
# State primitive inference
# ============================================================

def infer_state_primitive(
    family: str, 
    width: int, 
    hardware_patterns: 
        HardwarePatternResult, 
) -> str: 

    info = (
        hardware_patterns.families.get(
            family
        )
    )


    if info is None: 

        return "REGISTER"


    kinds = info.kinds


    # --------------------------------------------------------
    # Recurrence structure determines primitive kind.
    # No application/domain knowledge is used.
    # --------------------------------------------------------

    if (
        "SHIFT_LEFT_INSERT"
        in kinds
    ): 

        return "SHIFT_REGISTER"


    if (
        "UP_COUNTER" in kinds
        and
        "DOWN_COUNTER" not in kinds
    ): 

        return "COUNTER_UP"


    if (
        "DOWN_COUNTER" in kinds
        and
        "UP_COUNTER" not in kinds
    ): 

        return "COUNTER_DOWN"


    return "REGISTER"


# ============================================================
# Build structural netlist
# ============================================================

def build_structural_netlist(
    rtl_ir: RTLIR, 
    hardware_patterns: 
        HardwarePatternResult, 
) -> StructuralNetlist: 

    nodes = {}

    state_nodes = {}

    writes = []


    # ========================================================
    # Persistent storage primitives
    # ========================================================

    for (
        family, 
        register, 
    ) in sorted(
        rtl_ir.registers.items()
    ): 

        kind = infer_state_primitive(
            family, 
            register.width, 
            hardware_patterns, 
        )


        node_id = (
            f"state_{len(state_nodes)}"
        )


        nodes[node_id] = (
            StructuralNode(
                id = node_id, 
                kind = kind, 
                width = 
                    register.width, 
                family = 
                    family, 
            )
        )


        state_nodes[
            family
        ] = node_id


    # ========================================================
    # Combination logic
    # ========================================================

    builder = ExpressionBuilder(
        nodes, 
        state_nodes, 
    )


    for (
        edge, 
        edge_writes, 
    ) in sorted(
        rtl_ir.edge_writes.items()
    ): 

        source_block, target_block = (
            edge
        )


        for write in edge_writes: 

            expression_node = (
                builder.build(
                    write.expression
                )
            )


            writes.append(
                StructuralWrite(
                    source_block = 
                        source_block, 

                    target_block = 
                        target_block, 

                    state_node = 
                        state_nodes[
                            write.register
                        ], 

                    expression_node = 
                        expression_node, 
                )
            )


    # ========================================================
    # Generic primitive statistics
    # ========================================================

    primitive_counts = {}


    for node in nodes.values(): 

        primitive_counts[
            node.kind
        ] = (
            primitive_counts.get(
                node.kind, 
                0, 
            )
            + 1
        )


    return StructuralNetlist(
        nodes = nodes, 

        state_nodes = 
            state_nodes, 

        writes = writes, 

        primitive_counts = 
            primitive_counts, 
    )