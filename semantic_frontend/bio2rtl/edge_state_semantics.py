from __future__ import annotations

from dataclasses import dataclass

from .ir import (
    IROp, 
    IRBlock, 
)

from .edge_state import (
    EdgeStateIR, 
    EdgeStateWrite, 
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


@dataclass
class ClassifiedEdgeWrite: 
    source_block: int
    target_block: int

    family: str
    source_value: str

    kind: str
    # HOLD
    # CONST
    # STATE_COPY
    # SSA_COMPUTE
    # LIVEIN
    # UNRESOLVED

    source_family: str | None = None
    constant: int | None = None


@dataclass
class EdgeStateSemantics: 
    writes: list[ClassifiedEdgeWrite]

    hold_writes: list[ClassifiedEdgeWrite]
    effective_writes: list[ClassifiedEdgeWrite]

    const_writes: list[ClassifiedEdgeWrite]
    state_copy_writes: list[ClassifiedEdgeWrite]
    compute_writes: list[ClassifiedEdgeWrite]

    livein_writes: list[ClassifiedEdgeWrite]
    unresolved_writes: list[ClassifiedEdgeWrite]

    by_edge: dict[
        tuple[int, int], 
        list[ClassifiedEdgeWrite], 
    ]


def build_definition_map(
    blocks: list[IRBlock], 
) -> dict[str, tuple[int, IROp]]: 

    result = {}

    for block in blocks: 
        for op in block.ops: 

            if op.dst is not None: 
                result[op.dst] = (
                    block.id, 
                    op, 
                )

    return result


def classify_write(
    write: EdgeStateWrite, 
    definition_map: 
        dict[str, tuple[int, IROp]], 
    final_state: 
        FinalStateResult, 
) -> ClassifiedEdgeWrite: 

    value = write.source_value


    # ========================================================
    # HOLD
    #
    # All SSA versions of the same logical family collapse
    # onto the same physical DFF.
    # ========================================================

    source_family = (
        stack_state_base(
            value
        )
    )


    if source_family == write.family: 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "HOLD", 

            source_family = 
                source_family, 
        )


    # ========================================================
    # Constant
    # ========================================================

    if value == "x0": 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "CONST", 

            constant = 
                0, 
        )


    constant = eval_constant(
        value
    )


    if constant is not None: 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "CONST", 

            constant = 
                constant, 
        )


    # ========================================================
    # Copy from another final DFF
    # ========================================================

    if (
        source_family is not None
        and source_family
        in final_state.states
    ): 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "STATE_COPY", 

            source_family = 
                source_family, 
        )


    # ========================================================
    # CPU/register live-in
    # ========================================================

    if value.endswith(
        "_0"
    ): 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "LIVEIN", 
        )


    # ========================================================
    # Ordinary SSA combinational result
    # ========================================================

    if value in definition_map: 

        return ClassifiedEdgeWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            family = 
                write.family, 

            source_value = 
                value, 

            kind = 
                "SSA_COMPUTE", 
        )


    return ClassifiedEdgeWrite(
        source_block = 
            write.source_block, 

        target_block = 
            write.target_block, 

        family = 
            write.family, 

        source_value = 
            value, 

        kind = 
            "UNRESOLVED", 
    )


def analyze_edge_state_semantics(
    blocks: list[IRBlock], 
    edge_state: EdgeStateIR, 
    final_state: FinalStateResult, 
) -> EdgeStateSemantics: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    writes = []

    by_edge = {}


    for write in edge_state.writes: 

        classified = classify_write(
            write, 
            definition_map, 
            final_state, 
        )


        writes.append(
            classified
        )


        edge = (
            classified.source_block, 
            classified.target_block, 
        )


        by_edge.setdefault(
            edge, 
            [], 
        ).append(
            classified
        )


    hold = [
        write
        for write in writes
        if write.kind == "HOLD"
    ]


    const = [
        write
        for write in writes
        if write.kind == "CONST"
    ]


    copies = [
        write
        for write in writes
        if write.kind == "STATE_COPY"
    ]


    compute = [
        write
        for write in writes
        if write.kind == "SSA_COMPUTE"
    ]


    livein = [
        write
        for write in writes
        if write.kind == "LIVEIN"
    ]


    unresolved = [
        write
        for write in writes
        if write.kind == "UNRESOLVED"
    ]


    effective = [
        write
        for write in writes
        if write.kind != "HOLD"
    ]


    return EdgeStateSemantics(
        writes = writes, 

        hold_writes = hold, 

        effective_writes = effective, 

        const_writes = const, 

        state_copy_writes = copies, 

        compute_writes = compute, 

        livein_writes = livein, 

        unresolved_writes = unresolved, 

        by_edge = by_edge, 
    )