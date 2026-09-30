from dataclasses import dataclass, field
from dataclasses import replace

from .ir import (
    IROp, 
    IRBlock, 
)

from .stack_ssa import (
    StackPromotionResult, 
    StackVariable, 
)


# ============================================================
# Result
# ============================================================

@dataclass
class DeadStateEliminationResult: 
    blocks: list[IRBlock]

    kept_variables: dict[
        str, 
        StackVariable
    ]

    removed_variables: dict[
        str, 
        StackVariable
    ]

    state_writes_removed: int

    remaining_state_writes: int

    remaining_state_reads: int

    dangling_state_reads: list[
        tuple[int, str]
    ] = field(
        default_factory = list
    )


# ============================================================
# Helpers
# ============================================================

def state_names_read(
    blocks: list[IRBlock], 
) -> set[str]: 
    """
    Promoted stack reads have the form:

        ASSIGN
            dst  = xN
            args = [stack_...]

    Return every stack-state variable actually read.
    """

    result = set()

    for block in blocks: 

        for op in block.ops: 

            if (
                op.kind == "ASSIGN"
                and len(op.args) >= 1
                and op.args[0].startswith(
                    "stack_"
                )
            ): 
                result.add(
                    op.args[0]
                )

    return result


def count_state_operations(
    blocks: list[IRBlock], 
) -> tuple[int, int]: 

    writes = 0
    reads = 0

    for block in blocks: 

        for op in block.ops: 

            if op.kind == "STATE_WRITE": 
                writes += 1

            if (
                op.kind == "ASSIGN"
                and len(op.args) >= 1
                and op.args[0].startswith(
                    "stack_"
                )
            ): 
                reads += 1

    return writes, reads


# ============================================================
# Dead-state elimination
# ============================================================

def eliminate_dead_states(
    promoted: StackPromotionResult, 
) -> DeadStateEliminationResult: 
    """
    Remove state variables that are never read.

    Safe transformation:

        stack_X := value

    can be deleted if stack_X is never used anywhere.

    We deliberately DO NOT perform:

        xN := stack_X
            ->
        xN := source_of_previous_store

    yet, because RISC-V source registers are not SSA-renamed
    at this stage.
    """

    read_names = state_names_read(
        promoted.blocks
    )


    kept_variables = {}
    removed_variables = {}


    for (
        canonical_slot, 
        variable, 
    ) in promoted.variables.items(): 

        if variable.name in read_names: 

            kept_variables[
                canonical_slot
            ] = variable

        else: 

            removed_variables[
                canonical_slot
            ] = variable


    removed_names = {
        variable.name
        for variable
        in removed_variables.values()
    }


    new_blocks = []

    state_writes_removed = 0


    # --------------------------------------------------------
    # Remove writes to never-read state variables
    # --------------------------------------------------------

    for block in promoted.blocks: 

        new_ops = []

        for op in block.ops: 

            if (
                op.kind == "STATE_WRITE"
                and op.dst in removed_names
            ): 

                state_writes_removed += 1

                continue


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


    remaining_state_writes, (
        remaining_state_reads
    ) = count_state_operations(
        new_blocks
    )


    # --------------------------------------------------------
    # Consistency check:
    #
    # Every state read must refer to a kept variable.
    # --------------------------------------------------------

    kept_names = {
        variable.name
        for variable
        in kept_variables.values()
    }


    dangling_state_reads = []


    for block in new_blocks: 

        for op in block.ops: 

            if not (
                op.kind == "ASSIGN"
                and len(op.args) >= 1
                and op.args[0].startswith(
                    "stack_"
                )
            ): 
                continue


            state_name = op.args[0]


            if state_name not in kept_names: 

                dangling_state_reads.append(
                    (
                        op.address, 
                        state_name, 
                    )
                )


    return DeadStateEliminationResult(
        blocks = new_blocks, 

        kept_variables = 
            kept_variables, 

        removed_variables = 
            removed_variables, 

        state_writes_removed = 
            state_writes_removed, 

        remaining_state_writes = 
            remaining_state_writes, 

        remaining_state_reads = 
            remaining_state_reads, 

        dangling_state_reads = 
            dangling_state_reads, 
    )