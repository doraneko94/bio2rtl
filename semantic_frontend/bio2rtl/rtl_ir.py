from __future__ import annotations

from dataclasses import dataclass, field

from .final_state import (
    FinalStateResult, 
)

from .hardware_pattern import (
    HardwarePatternResult, 
)

from .next_state_expr import (
    Expr, 
)


# ============================================================
# RTL state/register
# ============================================================

@dataclass
class RTLRegister: 
    name: str
    width: int

    hardware_kind: str
    # REGISTER
    # UP_COUNTER
    # DOWN_COUNTER
    # SHIFT_REGISTER


@dataclass
class RTLEdgeWrite: 
    source_block: int
    target_block: int

    register: str

    expression: Expr

    pattern_kind: str


@dataclass
class RTLIR: 
    registers: dict[
        str, 
        RTLRegister, 
    ]

    edge_writes: dict[
        tuple[int, int], 
        list[RTLEdgeWrite], 
    ]

    total_register_bits: int

    total_edge_writes: int


# ============================================================
# State width adapter
# ============================================================

def get_state_width(
    state, 
) -> int: 
    """
    FinalStateResult implementation has evolved during the
    pipeline, so keep width extraction isolated here.
    """

    for name in (
        "width", 
        "bits", 
        "bit_width", 
    ): 

        value = getattr(
            state, 
            name, 
            None, 
        )

        if isinstance(value, int): 
            return value


    raise RuntimeError(
        "Cannot determine final state width "
        f"from {state!r}"
    )


# ============================================================
# Family-level hardware classification
# ============================================================

def classify_family_kind(
    family: str, 
    patterns: HardwarePatternResult, 
) -> str: 

    info = patterns.families.get(
        family
    )


    if info is None: 
        return "REGISTER"


    kinds = info.kinds


    if "SHIFT_LEFT_INSERT" in kinds: 
        return "SHIFT_REGISTER"


    if (
        "UP_COUNTER" in kinds
        and "DOWN_COUNTER" not in kinds
    ): 
        return "UP_COUNTER"


    if (
        "DOWN_COUNTER" in kinds
        and "UP_COUNTER" not in kinds
    ): 
        return "DOWN_COUNTER"


    return "REGISTER"


# ============================================================
# Main RTL lowering
# ============================================================

def build_rtl_ir(
    final_state: FinalStateResult, 
    hardware_patterns: 
        HardwarePatternResult, 
) -> RTLIR: 

    registers = {}


    # ========================================================
    # Physical registers
    # ========================================================

    for family, state in (
        final_state.states.items()
    ): 

        width = get_state_width(
            state
        )


        registers[
            family
        ] = RTLRegister(
            name = family, 
            width = width, 
            hardware_kind = 
                classify_family_kind(
                    family, 
                    hardware_patterns, 
                ), 
        )


    # ========================================================
    # Edge next-state assignments
    # ========================================================

    edge_writes = {}


    for pattern in (
        hardware_patterns.patterns
    ): 

        edge = (
            pattern.source_block, 
            pattern.target_block, 
        )


        edge_writes.setdefault(
            edge, 
            [], 
        ).append(
            RTLEdgeWrite(
                source_block = 
                    pattern.source_block, 

                target_block = 
                    pattern.target_block, 

                register = 
                    pattern.family, 

                expression = 
                    pattern.expression, 

                pattern_kind = 
                    pattern.kind, 
            )
        )


    total_bits = sum(
        register.width

        for register
        in registers.values()
    )


    total_writes = sum(
        len(writes)

        for writes
        in edge_writes.values()
    )


    return RTLIR(
        registers = 
            registers, 

        edge_writes = 
            edge_writes, 

        total_register_bits = 
            total_bits, 

        total_edge_writes = 
            total_writes, 
    )