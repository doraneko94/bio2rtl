from __future__ import annotations

from dataclasses import dataclass, field

from .next_state_expr import (
    Expr, 
    NextStateExprIR, 
    NextStateWrite, 
)


# ============================================================
# Pattern representation
# ============================================================

@dataclass
class HardwarePattern: 
    family: str

    kind: str
    # UP_COUNTER
    # DOWN_COUNTER
    # SHIFT_LEFT_INSERT
    # LOAD_CONST
    # STATE_COPY
    # GENERIC_EXPR

    source_block: int
    target_block: int

    expression: Expr

    constant: int | None = None

    source_family: str | None = None

    shift_amount: int | None = None

    insert_expression: Expr | None = None


@dataclass
class HardwarePatternFamily: 
    family: str

    patterns: list[
        HardwarePattern
    ] = field(
        default_factory = list
    )

    kinds: set[str] = field(
        default_factory = set
    )


@dataclass
class HardwarePatternResult: 
    patterns: list[
        HardwarePattern
    ]

    families: dict[
        str, 
        HardwarePatternFamily
    ]

    counts: dict[str, int]


# ============================================================
# Helpers
# ============================================================

def is_const(
    expr: Expr, 
    value: int | None = None, 
) -> bool: 

    if expr.kind != "CONST": 
        return False

    if value is None: 
        return True

    return expr.value == value


def is_state(
    expr: Expr, 
    family: str | None = None, 
) -> bool: 

    if expr.kind != "STATE": 
        return False

    if family is None: 
        return True

    return expr.state_family == family


def is_op(
    expr: Expr, 
    operation: str, 
) -> bool: 

    return (
        expr.kind == "OP"
        and expr.operation == operation
    )


# ============================================================
# Pattern matching
# ============================================================

def classify_pattern(
    write: NextStateWrite, 
) -> HardwarePattern: 

    family = write.family
    expr = write.expression


    # ========================================================
    # CONST load
    # ========================================================

    if expr.kind == "CONST": 

        return HardwarePattern(
            family = family, 
            kind = "LOAD_CONST", 
            source_block = 
                write.source_block, 
            target_block = 
                write.target_block, 
            expression = expr, 
            constant = expr.value, 
        )


    # ========================================================
    # State copy
    # ========================================================

    if expr.kind == "STATE": 

        return HardwarePattern(
            family = family, 
            kind = "STATE_COPY", 
            source_block = 
                write.source_block, 
            target_block = 
                write.target_block, 
            expression = expr, 
            source_family = 
                expr.state_family, 
        )


    # ========================================================
    # q <= q + 1
    # q <= 1 + q
    # ========================================================

    if (
        is_op(expr, "ADD")
        and len(expr.args) == 2
    ): 

        a, b = expr.args

        if (
            is_state(a, family)
            and is_const(b, 1)
        ) or (
            is_state(b, family)
            and is_const(a, 1)
        ): 

            return HardwarePattern(
                family = family, 
                kind = "UP_COUNTER", 
                source_block = 
                    write.source_block, 
                target_block = 
                    write.target_block, 
                expression = expr, 
            )


        # ====================================================
        # q <= q + (-1)
        # ====================================================

        if (
            is_state(a, family)
            and is_const(b, -1)
        ) or (
            is_state(b, family)
            and is_const(a, -1)
        ): 

            return HardwarePattern(
                family = family, 
                kind = "DOWN_COUNTER", 
                source_block = 
                    write.source_block, 
                target_block = 
                    write.target_block, 
                expression = expr, 
            )


    # ========================================================
    # q <= (q << N) | insert
    #
    # Also accept:
    #
    # q <= insert | (q << N)
    # ========================================================

    if (
        is_op(expr, "OR")
        and len(expr.args) == 2
    ): 

        left, right = expr.args


        shift_expr = None
        insert_expr = None


        if is_op(left, "SHL"): 

            shift_expr = left
            insert_expr = right


        elif is_op(right, "SHL"): 

            shift_expr = right
            insert_expr = left


        if (
            shift_expr is not None
            and len(shift_expr.args) == 2
        ): 

            shift_value, shift_amount = (
                shift_expr.args
            )


            if (
                is_state(
                    shift_value, 
                    family, 
                )
                and shift_amount.kind
                == "CONST"
                and shift_amount.value
                is not None
            ): 

                return HardwarePattern(
                    family = family, 
                    kind = "SHIFT_LEFT_INSERT", 
                    source_block = 
                        write.source_block, 
                    target_block = 
                        write.target_block, 
                    expression = expr, 
                    shift_amount = 
                        shift_amount.value, 
                    insert_expression = 
                        insert_expr, 
                )


    # ========================================================
    # Fallback
    # ========================================================

    return HardwarePattern(
        family = family, 
        kind = "GENERIC_EXPR", 
        source_block = 
            write.source_block, 
        target_block = 
            write.target_block, 
        expression = expr, 
    )


# ============================================================
# Whole IR
# ============================================================

def analyze_hardware_patterns(
    next_state: NextStateExprIR, 
) -> HardwarePatternResult: 

    patterns = []

    families = {}

    counts = {
        "UP_COUNTER": 0, 
        "DOWN_COUNTER": 0, 
        "SHIFT_LEFT_INSERT": 0, 
        "LOAD_CONST": 0, 
        "STATE_COPY": 0, 
        "GENERIC_EXPR": 0, 
    }


    for write in next_state.writes: 

        pattern = classify_pattern(
            write
        )


        patterns.append(
            pattern
        )


        counts[
            pattern.kind
        ] += 1


        family_info = (
            families.setdefault(
                pattern.family, 
                HardwarePatternFamily(
                    family = 
                        pattern.family
                ), 
            )
        )


        family_info.patterns.append(
            pattern
        )

        family_info.kinds.add(
            pattern.kind
        )


    return HardwarePatternResult(
        patterns = patterns, 
        families = families, 
        counts = counts, 
    )