from dataclasses import dataclass, field
from dataclasses import replace
import re

from .ir import (
    IROp, 
    IRBlock, 
)

from .register_ssa import (
    find_reachable, 
    compute_dominators, 
    compute_idom, 
    compute_dom_tree, 
    compute_dominance_frontier, 
)


STATE_NAME_RE = re.compile(
    r"^stack_[A-Za-z0-9_]+_(?:m|p)\d+$"
)


# ============================================================
# Result
# ============================================================

@dataclass
class StateSSADefinition: 
    name: str
    base_state: str

    block_id: int
    address: int | None

    kind: str


@dataclass
class StateSSAResult: 
    blocks: list[IRBlock]

    phi_count: int

    definitions: dict[
        str, 
        StateSSADefinition
    ]

    versions_per_state: dict[
        str, 
        int
    ]

    remaining_state_writes: int

    unversioned_state_defs: list[
        tuple[int, int | None, str]
    ] = field(
        default_factory = list
    )

    unversioned_state_uses: list[
        tuple[int, int | None, str]
    ] = field(
        default_factory = list
    )


# ============================================================
# Helpers
# ============================================================

def is_state_name(
    value: str, 
) -> bool: 

    return (
        STATE_NAME_RE.match(value)
        is not None
    )


def clone_op(
    op: IROp, 
) -> IROp: 

    return replace(
        op, 
        args = list(op.args), 
    )


def clone_blocks(
    blocks: list[IRBlock], 
) -> list[IRBlock]: 

    return [
        IRBlock(
            id = block.id, 

            start = block.start, 

            ops = [
                clone_op(op)
                for op in block.ops
            ], 

            successors = list(
                block.successors
            ), 

            predecessors = list(
                block.predecessors
            ), 
        )

        for block in blocks
    ]


def state_defined_by_op(
    op: IROp, 
) -> str | None: 

    if (
        op.kind == "STATE_WRITE"
        and op.dst is not None
        and is_state_name(
            op.dst
        )
    ): 
        return op.dst

    return None


def states_read_by_op(
    op: IROp, 
) -> list[str]: 

    result = []

    for arg in op.args: 

        if is_state_name(arg): 

            if arg not in result: 
                result.append(arg)

    return result


# ============================================================
# Definition blocks
# ============================================================

def collect_state_definition_blocks(
    blocks: list[IRBlock], 
) -> dict[str, set[int]]: 

    result = {}

    for block in blocks: 

        for op in block.ops: 

            state = state_defined_by_op(
                op
            )

            if state is None: 
                continue

            result.setdefault(
                state, 
                set(), 
            ).add(
                block.id
            )

    return result


# ============================================================
# PHI placement
# ============================================================

def place_state_phi_nodes(
    blocks: list[IRBlock], 
    dominance_frontier: 
        dict[int, set[int]], 
) -> dict[int, set[str]]: 

    definition_blocks = (
        collect_state_definition_blocks(
            blocks
        )
    )


    phi_by_block = {
        block.id: set()
        for block in blocks
    }


    for state, defs in (
        definition_blocks.items()
    ): 

        work = list(defs)

        processed = set()


        while work: 

            block_id = work.pop()

            for frontier_block in (
                dominance_frontier[
                    block_id
                ]
            ): 

                if (
                    state
                    in phi_by_block[
                        frontier_block
                    ]
                ): 
                    continue


                phi_by_block[
                    frontier_block
                ].add(
                    state
                )


                if (
                    frontier_block
                    not in defs
                    and frontier_block
                    not in processed
                ): 

                    work.append(
                        frontier_block
                    )

                    processed.add(
                        frontier_block
                    )


    return phi_by_block


# ============================================================
# SSA conversion
# ============================================================

def convert_states_to_ssa(
    input_blocks: list[IRBlock], 
) -> StateSSAResult: 

    blocks = clone_blocks(
        input_blocks
    )


    blocks_by_id = {
        block.id: block
        for block in blocks
    }


    if not blocks: 

        raise RuntimeError(
            "No blocks for state SSA conversion"
        )


    entry = min(
        block.id
        for block in blocks
    )


    reachable = find_reachable(
        blocks_by_id, 
        entry, 
    )


    if (
        len(reachable)
        != len(blocks)
    ): 

        unreachable = sorted(
            set(blocks_by_id)
            - reachable
        )

        raise RuntimeError(
            "State SSA found unreachable blocks: "
            + ", ".join(
                f"BB{x}"
                for x in unreachable
            )
        )


    dominators = compute_dominators(
        blocks_by_id, 
        entry, 
        reachable, 
    )


    idom = compute_idom(
        dominators, 
        entry, 
    )


    dom_tree = compute_dom_tree(
        idom
    )


    frontier = compute_dominance_frontier(
        blocks_by_id, 
        idom, 
        reachable, 
    )


    phi_by_block = (
        place_state_phi_nodes(
            blocks, 
            frontier, 
        )
    )


    # ========================================================
    # Collect all state names
    # ========================================================

    all_states = set()


    for block in blocks: 

        for op in block.ops: 

            state = state_defined_by_op(
                op
            )

            if state is not None: 
                all_states.add(state)


            for state in states_read_by_op(
                op
            ): 
                all_states.add(state)


    # ========================================================
    # Version stacks
    #
    # state_0 means live-in value.
    #
    # Correct programs should eventually prove that no
    # actual read depends on an uninitialized state_0.
    # ========================================================

    counters = {
        state: 0
        for state in all_states
    }


    stacks = {
        state: [
            f"{state}_0"
        ]
        for state in all_states
    }


    definitions = {}


    versions_per_state = {
        state: 0
        for state in all_states
    }


    phi_incoming = {}


    for block_id, states in (
        phi_by_block.items()
    ): 

        for state in states: 

            phi_incoming[
                (
                    block_id, 
                    state, 
                )
            ] = {}


    phi_destination = {}


    def current_version(
        state: str, 
    ) -> str: 

        return stacks[
            state
        ][-1]


    def new_version(
        state: str, 
        block_id: int, 
        address: int | None, 
        kind: str, 
    ) -> str: 

        counters[
            state
        ] += 1


        name = (
            f"{state}_"
            f"{counters[state]}"
        )


        stacks[
            state
        ].append(
            name
        )


        versions_per_state[
            state
        ] += 1


        definitions[
            name
        ] = StateSSADefinition(
            name = name, 

            base_state = state, 

            block_id = block_id, 

            address = address, 

            kind = kind, 
        )


        return name


    # ========================================================
    # Dominator-tree rename
    # ========================================================

    def rename_block(
        block_id: int, 
    ): 

        block = blocks_by_id[
            block_id
        ]


        pushed = []


        # ----------------------------------------------------
        # State PHIs at entry
        # ----------------------------------------------------

        for state in sorted(
            phi_by_block[
                block_id
            ]
        ): 

            version = new_version(
                state, 
                block_id, 
                None, 
                "STATE_PHI", 
            )


            pushed.append(
                state
            )


            phi_destination[
                (
                    block_id, 
                    state, 
                )
            ] = version


        # ----------------------------------------------------
        # Ordinary operations
        # ----------------------------------------------------

        renamed_ops = []


        for op in block.ops: 

            new_op = clone_op(
                op
            )


            # -----------------------------------------------
            # Rewrite state USES first
            # -----------------------------------------------

            rewritten_args = []


            for arg in new_op.args: 

                if is_state_name(arg): 

                    rewritten_args.append(
                        current_version(
                            arg
                        )
                    )

                else: 

                    rewritten_args.append(
                        arg
                    )


            new_op.args = (
                rewritten_args
            )


            # -----------------------------------------------
            # STATE_WRITE becomes an ordinary immutable
            # SSA value definition.
            #
            # Before:
            #
            #   STATE_WRITE
            #   stack_X := x10_4
            #
            # After:
            #
            #   ASSIGN
            #   stack_X_3 := x10_4
            # -----------------------------------------------

            state = state_defined_by_op(
                op
            )


            if state is not None: 

                version = new_version(
                    state, 
                    block_id, 
                    op.address, 
                    "STATE_DEF", 
                )


                pushed.append(
                    state
                )


                new_op.kind = "ASSIGN"
                new_op.dst = version


            renamed_ops.append(
                new_op
            )


        block.ops = renamed_ops


        # ----------------------------------------------------
        # Supply state versions to successor PHIs
        # ----------------------------------------------------

        for successor in (
            block.successors
        ): 

            for state in (
                phi_by_block[
                    successor
                ]
            ): 

                phi_incoming[
                    (
                        successor, 
                        state, 
                    )
                ][
                    block_id
                ] = current_version(
                    state
                )


        # ----------------------------------------------------
        # Dominated children
        # ----------------------------------------------------

        for child in (
            dom_tree[
                block_id
            ]
        ): 

            rename_block(
                child
            )


        # ----------------------------------------------------
        # Restore stacks
        # ----------------------------------------------------

        for state in reversed(
            pushed
        ): 

            stacks[
                state
            ].pop()


    rename_block(
        entry
    )


    # ========================================================
    # Materialize state PHIs
    # ========================================================

    phi_count = 0


    for block in blocks: 

        phi_ops = []


        for state in sorted(
            phi_by_block[
                block.id
            ]
        ): 

            destination = (
                phi_destination[
                    (
                        block.id, 
                        state, 
                    )
                ]
            )


            incoming = (
                phi_incoming[
                    (
                        block.id, 
                        state, 
                    )
                ]
            )


            args = [
                (
                    f"BB{pred}="
                    f"{value}"
                )

                for pred, value
                in sorted(
                    incoming.items()
                )
            ]


            phi_ops.append(
                IROp(
                    kind = "PHI", 

                    dst = destination, 

                    args = args, 

                    address = None, 

                    comment = (
                        f"state={state}"
                    ), 
                )
            )


            phi_count += 1


        block.ops = (
            phi_ops
            + block.ops
        )


    # ========================================================
    # Verification
    # ========================================================

    remaining_state_writes = 0

    unversioned_state_defs = []
    unversioned_state_uses = []


    for block in blocks: 

        for op in block.ops: 

            if op.kind == "STATE_WRITE": 

                remaining_state_writes += 1


            if (
                op.dst is not None
                and is_state_name(
                    op.dst
                )
            ): 

                unversioned_state_defs.append(
                    (
                        block.id, 
                        op.address, 
                        op.dst, 
                    )
                )


            for arg in op.args: 

                if is_state_name(
                    arg
                ): 

                    unversioned_state_uses.append(
                        (
                            block.id, 
                            op.address, 
                            arg, 
                        )
                    )


    return StateSSAResult(
        blocks = blocks, 

        phi_count = phi_count, 

        definitions = definitions, 

        versions_per_state = {
            state: count

            for state, count
            in versions_per_state.items()

            if count > 0
        }, 

        remaining_state_writes = 
            remaining_state_writes, 

        unversioned_state_defs = 
            unversioned_state_defs, 

        unversioned_state_uses = 
            unversioned_state_uses, 
    )