from dataclasses import dataclass, field
from dataclasses import replace
import re

from .ir import (
    IROp, 
    IRBlock, 
)

from .stack_lowering import (
    SpAnalysis, 
)


# ============================================================
# Result structures
# ============================================================

@dataclass
class StackVariable: 
    name: str
    canonical_slot: str

    writes: list[int] = field(
        default_factory = list
    )

    reads: list[int] = field(
        default_factory = list
    )


@dataclass
class StackPromotionResult: 
    blocks: list[IRBlock]

    variables: dict[
        str, 
        StackVariable
    ]

    loads_replaced: int
    stores_replaced: int

    remaining_loads: int
    remaining_stores: int

    unresolved_addresses: list[int]


# ============================================================
# Name conversion
# ============================================================

def stack_variable_name(
    canonical_slot: str, 
) -> str: 
    """
    Examples:

        main:sp-68
            ->
        stack_main_sp_m68

        main:sp+12
            ->
        stack_main_sp_p12

        sub_0490:sp-4
            ->
        stack_sub_0490_sp_m4

    The result is a legal-ish RTL/IR identifier.
    """

    name = canonical_slot

    name = name.replace(
        ":", 
        "_", 
    )

    name = name.replace(
        "+", 
        "_p", 
    )

    name = name.replace(
        "-", 
        "_m", 
    )

    name = re.sub(
        r"[^a-zA-Z0-9_]", 
        "_", 
        name, 
    )

    return (
        "stack_"
        + name
    )


# ============================================================
# Main transformation
# ============================================================

def promote_stack_to_state(
    ir_blocks: list[IRBlock], 
    sp_analysis: SpAnalysis, 
) -> StackPromotionResult: 
    """
    Replace CPU stack memory operations with explicit
    hardware-state operations.

    Before:

        STORE MEM[x2+28] := x10
        ...
        x11 := LOAD MEM[x2+28]

    After canonical SP analysis:

        STATE_WRITE stack_main_sp_m68 := x10
        ...
        x11 := stack_main_sp_m68

    No LOAD or STORE operation remains.

    This transformation is semantics-preserving even when
    multiple STOREs can reach one LOAD, because the promoted
    state variable holds whichever value was written along
    the actually executed control-flow path.
    """

    # --------------------------------------------------------
    # Memory-operation address -> canonical stack slot
    # --------------------------------------------------------

    access_by_address = {}

    for access in sp_analysis.accesses: 

        if access.canonical_slot is None: 
            continue

        access_by_address[
            access.address
        ] = access


    variables: dict[
        str, 
        StackVariable
    ] = {}


    def get_variable(
        canonical_slot: str, 
    ) -> StackVariable: 

        if canonical_slot not in variables: 

            variables[
                canonical_slot
            ] = StackVariable(
                name = stack_variable_name(
                    canonical_slot
                ), 
                canonical_slot = 
                    canonical_slot, 
            )

        return variables[
            canonical_slot
        ]


    new_blocks = []

    loads_replaced = 0
    stores_replaced = 0

    unresolved_addresses = []


    # --------------------------------------------------------
    # Rewrite every block
    # --------------------------------------------------------

    for block in ir_blocks: 

        new_ops = []


        for op in block.ops: 

            # =================================================
            # LOAD
            #
            # dst := MEM[slot]
            #
            # becomes
            #
            # dst := stack_state
            # =================================================

            if op.kind == "LOAD": 

                access = (
                    access_by_address.get(
                        op.address
                    )
                )


                if (
                    access is None
                    or access.canonical_slot
                    is None
                ): 

                    unresolved_addresses.append(
                        op.address
                    )

                    # Keep original op so failure is visible.
                    new_ops.append(
                        replace(op)
                    )

                    continue


                variable = get_variable(
                    access.canonical_slot
                )

                variable.reads.append(
                    op.address
                )


                new_ops.append(
                    IROp(
                        kind = "ASSIGN", 

                        dst = op.dst, 

                        args = [
                            variable.name
                        ], 

                        address = 
                            op.address, 

                        comment = (
                            "promoted from "
                            f"{access.canonical_slot}"
                        ), 
                    )
                )


                loads_replaced += 1

                continue


            # =================================================
            # STORE
            #
            # MEM[slot] := source
            #
            # becomes
            #
            # STATE_WRITE stack_state := source
            # =================================================

            if op.kind == "STORE": 

                access = (
                    access_by_address.get(
                        op.address
                    )
                )


                if (
                    access is None
                    or access.canonical_slot
                    is None
                    or len(op.args) < 2
                ): 

                    unresolved_addresses.append(
                        op.address
                    )

                    new_ops.append(
                        replace(op)
                    )

                    continue


                variable = get_variable(
                    access.canonical_slot
                )

                variable.writes.append(
                    op.address
                )


                source = op.args[1]


                new_ops.append(
                    IROp(
                        kind = "STATE_WRITE", 

                        dst = variable.name, 

                        args = [
                            source
                        ], 

                        address = 
                            op.address, 

                        comment = (
                            "promoted from "
                            f"{access.canonical_slot}"
                        ), 
                    )
                )


                stores_replaced += 1

                continue


            # =================================================
            # Everything else remains unchanged
            # =================================================

            new_ops.append(
                replace(op)
            )


        new_blocks.append(
            IRBlock(
                id = block.id, 

                start = block.start, 

                ops = new_ops, 

                successors = list(
                    block.successors
                ), 

                predecessors = list(
                    block.predecessors
                ), 
            )
        )


    # --------------------------------------------------------
    # Verification
    # --------------------------------------------------------

    remaining_loads = 0
    remaining_stores = 0


    for block in new_blocks: 

        for op in block.ops: 

            if op.kind == "LOAD": 
                remaining_loads += 1

            elif op.kind == "STORE": 
                remaining_stores += 1


    return StackPromotionResult(
        blocks = new_blocks, 

        variables = variables, 

        loads_replaced = 
            loads_replaced, 

        stores_replaced = 
            stores_replaced, 

        remaining_loads = 
            remaining_loads, 

        remaining_stores = 
            remaining_stores, 

        unresolved_addresses = 
            sorted(
                set(
                    unresolved_addresses
                )
            ), 
    )