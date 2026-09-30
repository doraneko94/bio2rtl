from __future__ import annotations

from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)


# ============================================================
# Structures
# ============================================================

@dataclass
class FSMTransition: 
    source: int

    kind: str
    # GOTO
    # BRANCH
    # TERMINAL

    true_target: int | None = None
    false_target: int | None = None

    condition: str | None = None

    address: int | None = None


@dataclass
class FSMState: 
    state_id: int

    source_block: int

    operations: list[IROp] = field(
        default_factory = list
    )

    transition: FSMTransition | None = None


@dataclass
class FSMIR: 
    entry_state: int

    states: dict[
        int, 
        FSMState
    ]

    reachable_states: set[int]

    terminal_states: set[int]


# ============================================================
# Reachability
# ============================================================

def find_reachable_blocks(
    blocks_by_id: dict[int, IRBlock], 
    entry: int, 
) -> set[int]: 

    reachable = set()

    work = [
        entry
    ]


    while work: 

        block_id = work.pop()


        if block_id in reachable: 
            continue


        reachable.add(
            block_id
        )


        block = blocks_by_id[
            block_id
        ]


        for successor in (
            block.successors
        ): 

            if successor not in reachable: 

                work.append(
                    successor
                )


    return reachable


# ============================================================
# Terminator extraction
# ============================================================

def find_branch(
    block: IRBlock, 
) -> IROp | None: 

    branches = [
        op
        for op in block.ops
        if op.kind == "BRANCH"
    ]


    if not branches: 
        return None


    # A normalized basic block should have at most one
    # terminating conditional branch.
    return branches[-1]


def find_jump(
    block: IRBlock, 
) -> IROp | None: 

    jumps = [
        op
        for op in block.ops
        if op.kind == "JUMP"
    ]


    if not jumps: 
        return None


    return jumps[-1]


# ============================================================
# Transition construction
# ============================================================

def build_transition(
    block: IRBlock, 
) -> FSMTransition: 

    successors = list(
        block.successors
    )


    branch = find_branch(
        block
    )


    # --------------------------------------------------------
    # Conditional branch
    # --------------------------------------------------------

    if branch is not None: 

        if len(successors) != 2: 

            raise RuntimeError(
                f"BB{block.id}: BRANCH requires "
                f"exactly 2 successors, got "
                f"{successors}"
            )


        # Existing CFG convention:
        #
        # successors[0] = branch-taken target
        # successors[1] = fall-through target
        #
        # We make this explicit in FSM IR.

        return FSMTransition(
            source = 
                block.id, 

            kind = 
                "BRANCH", 

            true_target = 
                successors[0], 

            false_target = 
                successors[1], 

            condition = 
                branch.comment, 

            address = 
                branch.address, 
        )


    # --------------------------------------------------------
    # One successor
    # --------------------------------------------------------

    if len(successors) == 1: 

        return FSMTransition(
            source = 
                block.id, 

            kind = 
                "GOTO", 

            true_target = 
                successors[0], 
        )


    # --------------------------------------------------------
    # Terminal
    # --------------------------------------------------------

    if len(successors) == 0: 

        return FSMTransition(
            source = 
                block.id, 

            kind = 
                "TERMINAL", 
        )


    raise RuntimeError(
        f"BB{block.id}: unsupported successor count "
        f"{len(successors)}: {successors}"
    )


# ============================================================
# Remove control-flow pseudo-ops from datapath body
# ============================================================

CONTROL_KINDS = {
    "BRANCH", 
    "JUMP", 
    "RETURN", 
}


def datapath_operations(
    block: IRBlock, 
) -> list[IROp]: 

    return [
        op
        for op in block.ops

        if op.kind
        not in CONTROL_KINDS
    ]


# ============================================================
# Main construction
# ============================================================

def build_fsm_ir(
    blocks: list[IRBlock], 
) -> FSMIR: 

    if not blocks: 

        raise RuntimeError(
            "Cannot build FSM from empty CFG"
        )


    blocks_by_id = {
        block.id: block
        for block in blocks
    }


    entry = min(
        blocks_by_id
    )


    reachable = (
        find_reachable_blocks(
            blocks_by_id, 
            entry, 
        )
    )


    states = {}

    terminal_states = set()


    for block_id in sorted(
        reachable
    ): 

        block = blocks_by_id[
            block_id
        ]


        transition = (
            build_transition(
                block
            )
        )


        if (
            transition.kind
            == "TERMINAL"
        ): 

            terminal_states.add(
                block_id
            )


        states[
            block_id
        ] = FSMState(
            state_id = 
                block_id, 

            source_block = 
                block_id, 

            operations = 
                datapath_operations(
                    block
                ), 

            transition = 
                transition, 
        )


    return FSMIR(
        entry_state = 
            entry, 

        states = 
            states, 

        reachable_states = 
            reachable, 

        terminal_states = 
            terminal_states, 
    )