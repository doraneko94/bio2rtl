from __future__ import annotations

from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)

from .edge_state_semantics import (
    EdgeStateSemantics, 
    ClassifiedEdgeWrite, 
)

from .final_state import (
    FinalStateResult, 
)

from .logical_state import (
    stack_state_base, 
)

from .dff_width_infer import (
    eval_constant, 
)

from .ssa_opt import (
    values_used_by_op, 
)


# ============================================================
# Expression tree
# ============================================================

@dataclass(frozen = True)
class Expr: 
    kind: str
    # CONST
    # STATE
    # GPIO
    # SEMANTIC
    # OP

    value: int | None = None

    state_family: str | None = None

    gpio_value: str | None = None

    # Non-stack SSA value whose semantic expression is resolved later.
    semantic_value: str | None = None

    operation: str | None = None

    args: tuple[
        "Expr", 
        ...
    ] = ()


@dataclass
class NextStateWrite: 
    source_block: int
    target_block: int

    family: str

    kind: str
    # CONST
    # STATE_COPY
    # EXPR

    expression: Expr


@dataclass
class NextStateExprIR: 
    writes: list[
        NextStateWrite
    ]

    by_edge: dict[
        tuple[int, int], 
        list[NextStateWrite], 
    ]

    total_writes: int

    const_writes: int
    state_copy_writes: int
    expression_writes: int

    operation_kinds: set[str]

    max_expression_depth: int


# ============================================================
# Helpers
# ============================================================

def build_definition_map(
    blocks: list[IRBlock], 
) -> dict[str, tuple[int, IROp]]: 

    result = {}

    for block in blocks: 

        for op in block.ops: 

            if op.dst is not None: 

                result[
                    op.dst
                ] = (
                    block.id, 
                    op, 
                )

    return result


def expression_depth(
    expression: Expr, 
) -> int: 

    if not expression.args: 
        return 1

    return 1 + max(
        expression_depth(
            arg
        )

        for arg in expression.args
    )


# ============================================================
# SSA -> expression tree
# ============================================================

def lower_value(
    value: str, 
    definition_map: 
        dict[str, tuple[int, IROp]], 
    final_state: 
        FinalStateResult, 
    visiting: set[str], 
) -> Expr: 

    # --------------------------------------------------------
    # zero
    # --------------------------------------------------------

    if value == "x0": 

        return Expr(
            kind = "CONST", 
            value = 0, 
        )


    # --------------------------------------------------------
    # literal
    # --------------------------------------------------------

    constant = eval_constant(
        value
    )


    if constant is not None: 

        return Expr(
            kind = "CONST", 
            value = constant, 
        )


    # --------------------------------------------------------
    # Resolve SSA definition before collapsing a stack value to
    # physical state.
    #
    # A versioned stack SSA value such as stack_*_3 may have a
    # combinational definition in the current semantic interval.
    # Treating every version as STATE here discards that SSA
    # information and unnecessarily feeds the physical register
    # back into downstream logic.
    #
    # Only a stack value that is an actual PHI/live-in boundary
    # is a persistent STATE leaf.
    # --------------------------------------------------------

    family = stack_state_base(
        value
    )


    # --------------------------------------------------------
    # cycle must not occur after previous cone check
    # --------------------------------------------------------

    if value in visiting: 

        raise RuntimeError(
            f"Expression cycle at {value}"
        )


    definition = definition_map.get(
        value
    )


    if definition is None: 

        if (
            family is not None
            and family
            in final_state.states
        ): 

            return Expr(
                kind = "STATE", 
                state_family = family, 
            )

        raise RuntimeError(
            f"Missing expression definition: "
            f"{value}"
        )


    _, op = definition


    # Persistent stack PHIs are the clock-to-clock state boundary.
    #
    # A non-stack PHI is not an error: compiler register allocation can
    # introduce ordinary SSA joins that feed a persistent stack write.
    # Keep such a value symbolic here.  Semantic SSA, built later with
    # this value as an explicit root, lowers it to a predecessor-selected
    # PHI expression (or to a semantic state at a sample boundary).
    if op.kind == "PHI": 

        if (
            family is not None
            and family
            in final_state.states
        ): 

            return Expr(
                kind = "STATE", 
                state_family = family, 
            )

        return Expr(
            kind = "SEMANTIC", 
            semantic_value = value, 
        )


    # Follow simple SSA aliases rather than materialising state.
    if op.kind == "ASSIGN": 

        if len(op.args) != 1: 

            raise RuntimeError(
                f"ASSIGN requires one argument: "
                f"{value}"
            )

        return lower_value(
            op.args[0], 
            definition_map, 
            final_state, 
            visiting, 
        )


    visiting = set(
        visiting
    )

    visiting.add(
        value
    )


    # ========================================================
    # CONST
    # ========================================================

    if op.kind == "CONST": 

        if not op.args: 

            raise RuntimeError(
                f"CONST without argument: "
                f"{value}"
            )


        constant = eval_constant(
            op.args[0]
        )


        if constant is None: 

            raise RuntimeError(
                f"Cannot evaluate CONST: "
                f"{value} <- {op.args[0]}"
            )


        return Expr(
            kind = "CONST", 
            value = constant, 
        )


    # ========================================================
    # GPIO
    # ========================================================

    if op.kind == "GPIO_READ": 

        return Expr(
            kind = "GPIO", 
            gpio_value = value, 
        )


    # ========================================================
    # supported combinational operations
    # ========================================================

    if op.kind not in {
        "ADD", 
        "AND", 
        "OR", 
        "SHL", 
        "SHR", 
    }: 

        raise RuntimeError(
            f"Unsupported expression operation "
            f"{op.kind} at {value}"
        )


    lowered_args = []


    for arg in op.args: 

        constant = eval_constant(
            arg
        )


        if constant is not None: 

            lowered_args.append(
                Expr(
                    kind = "CONST", 
                    value = constant, 
                )
            )

            continue


        lowered_args.append(
            lower_value(
                arg, 
                definition_map, 
                final_state, 
                visiting, 
            )
        )


    return Expr(
        kind = "OP", 

        operation = 
            op.kind, 

        args = 
            tuple(
                lowered_args
            ), 
    )


# ============================================================
# Semantic SSA dependencies
# ============================================================

def semantic_value_roots(
    result: NextStateExprIR, 
) -> set[str]: 

    roots: set[str] = set()

    def walk(expr: Expr) -> None: 
        if expr.kind == "SEMANTIC": 
            if expr.semantic_value is None: 
                raise RuntimeError(
                    "SEMANTIC expression without SSA value"
                )
            roots.add(expr.semantic_value)
            return

        for arg in expr.args: 
            walk(arg)

    for write in result.writes: 
        walk(write.expression)

    return roots


# ============================================================
# Pretty printer
# ============================================================

def format_expr(
    expression: Expr, 
) -> str: 

    if expression.kind == "CONST": 

        return str(
            expression.value
        )


    if expression.kind == "STATE": 

        return (
            expression.state_family
            or "STATE?"
        )


    if expression.kind == "GPIO": 

        return (
            f"GPIO({expression.gpio_value})"
        )


    if expression.kind == "SEMANTIC": 

        return (
            f"SEMANTIC({expression.semantic_value})"
        )


    if expression.kind == "OP": 

        args = ", ".join(
            format_expr(arg)

            for arg
            in expression.args
        )


        return (
            f"{expression.operation}"
            f"({args})"
        )


    raise RuntimeError(
        f"Unknown expression kind "
        f"{expression.kind}"
    )


# ============================================================
# Main lowering
# ============================================================

def build_next_state_expr_ir(
    blocks: list[IRBlock], 
    semantics: 
        EdgeStateSemantics, 
    final_state: 
        FinalStateResult, 
) -> NextStateExprIR: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    writes = []

    by_edge = {}

    operation_kinds = set()


    const_count = 0
    copy_count = 0
    expression_count = 0

    max_depth = 0


    for write in (
        semantics.effective_writes
    ): 

        # ====================================================
        # CONST
        # ====================================================

        if write.kind == "CONST": 

            if write.constant is None: 

                raise RuntimeError(
                    "CONST write without value"
                )


            expression = Expr(
                kind = "CONST", 
                value = write.constant, 
            )


            lowered = NextStateWrite(
                source_block = 
                    write.source_block, 

                target_block = 
                    write.target_block, 

                family = 
                    write.family, 

                kind = 
                    "CONST", 

                expression = 
                    expression, 
            )


            const_count += 1


        # ====================================================
        # state copy
        # ====================================================

        elif write.kind == "STATE_COPY": 

            if write.source_family is None: 

                raise RuntimeError(
                    "STATE_COPY without source"
                )


            expression = Expr(
                kind = "STATE", 
                state_family = 
                    write.source_family, 
            )


            lowered = NextStateWrite(
                source_block = 
                    write.source_block, 

                target_block = 
                    write.target_block, 

                family = 
                    write.family, 

                kind = 
                    "STATE_COPY", 

                expression = 
                    expression, 
            )


            copy_count += 1


        # ====================================================
        # combinational expression
        # ====================================================

        elif write.kind == "SSA_COMPUTE": 

            expression = lower_value(
                write.source_value, 
                definition_map, 
                final_state, 
                set(), 
            )


            lowered = NextStateWrite(
                source_block = 
                    write.source_block, 

                target_block = 
                    write.target_block, 

                family = 
                    write.family, 

                kind = 
                    "EXPR", 

                expression = 
                    expression, 
            )


            expression_count += 1


        else: 

            raise RuntimeError(
                f"Unexpected effective write kind: "
                f"{write.kind}"
            )


        # ----------------------------------------------------
        # statistics
        # ----------------------------------------------------

        def collect_ops(
            expr: Expr, 
        ): 

            if (
                expr.kind == "OP"
                and expr.operation
                is not None
            ): 

                operation_kinds.add(
                    expr.operation
                )


            for arg in expr.args: 

                collect_ops(
                    arg
                )


        collect_ops(
            lowered.expression
        )


        max_depth = max(
            max_depth, 
            expression_depth(
                lowered.expression
            ), 
        )


        writes.append(
            lowered
        )


        edge = (
            lowered.source_block, 
            lowered.target_block, 
        )


        by_edge.setdefault(
            edge, 
            [], 
        ).append(
            lowered
        )


    return NextStateExprIR(
        writes = writes, 

        by_edge = by_edge, 

        total_writes = 
            len(writes), 

        const_writes = 
            const_count, 

        state_copy_writes = 
            copy_count, 

        expression_writes = 
            expression_count, 

        operation_kinds = 
            operation_kinds, 

        max_expression_depth = 
            max_depth, 
    )