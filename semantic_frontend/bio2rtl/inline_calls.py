from dataclasses import dataclass, field
from dataclasses import replace

from .ir import IRBlock
from .functions import FunctionAnalysis


@dataclass
class InlineCallRecord: 
    call_address: int
    caller_block_id: int

    callee_name: str
    callee_entry: int

    continuation_block_id: int
    cloned_entry_block_id: int

    cloned_block_ids: list[int] = field(
        default_factory = list
    )


@dataclass
class InlineResult: 
    blocks: list[IRBlock]

    calls_inlined: int

    records: list[InlineCallRecord]

    remaining_calls: int
    remaining_returns: int

    original_block_count: int
    final_block_count: int


def clone_op(op): 
    return replace(
        op, 
        args = list(op.args), 
    )


def clone_block(
    block: IRBlock, 
    new_id: int, 
) -> IRBlock: 

    return IRBlock(
        id = new_id, 

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


def recompute_predecessors(
    blocks: list[IRBlock], 
): 
    block_by_id = {
        block.id: block
        for block in blocks
    }

    for block in blocks: 
        block.predecessors = []

    for block in blocks: 

        for successor in (
            block.successors
        ): 

            if successor not in block_by_id: 
                raise RuntimeError(
                    f"BB{block.id} has missing "
                    f"successor BB{successor}"
                )

            block_by_id[
                successor
            ].predecessors.append(
                block.id
            )


def find_call_op(
    block: IRBlock, 
): 
    calls = [
        op
        for op in block.ops
        if op.kind == "CALL"
    ]

    if len(calls) > 1: 
        raise RuntimeError(
            f"BB{block.id} contains "
            f"multiple CALL operations"
        )

    if not calls: 
        return None

    return calls[0]


def count_kind(
    blocks: list[IRBlock], 
    kind: str, 
) -> int: 

    return sum(
        1
        for block in blocks
        for op in block.ops
        if op.kind == kind
    )


def inline_helper_functions(
    blocks: list[IRBlock], 
    function_analysis: 
        FunctionAnalysis, 
) -> InlineResult: 
    """
    Inline all non-main functions into main.

    Current i2c-gpio-hw program contains:

        main
        sub_0490

    and two CALL sites to sub_0490.

    Each CALL gets its own cloned copy of the
    callee CFG.

    The original out-of-line callee is then removed.

    After this transformation there must be:

        CALL   = 0
        RETURN = 0

    so later register SSA can treat the complete
    program as one CFG.
    """

    original_block_count = len(
        blocks
    )

    original_by_id = {
        block.id: block
        for block in blocks
    }


    # ========================================================
    # Identify main
    # ========================================================

    main_functions = [
        function
        for function
        in function_analysis.functions.values()
        if function.name == "main"
    ]

    if len(main_functions) != 1: 
        raise RuntimeError(
            "Expected exactly one main function"
        )

    main_function = main_functions[0]

    main_block_ids = set(
        main_function.block_ids
    )


    # ========================================================
    # Identify helper functions
    # ========================================================

    helper_functions = {
        entry: function

        for entry, function
        in function_analysis.functions.items()

        if function.name != "main"
    }


    # Map function entry address -> entry BB id

    helper_entry_block_id = {}

    for entry, function in (
        helper_functions.items()
    ): 

        candidates = [
            block_id

            for block_id
            in function.block_ids

            if (
                original_by_id[
                    block_id
                ].start
                == entry
            )
        ]

        if len(candidates) != 1: 
            raise RuntimeError(
                f"Could not identify entry block "
                f"for {function.name}"
            )

        helper_entry_block_id[
            entry
        ] = candidates[0]


    # ========================================================
    # Begin with MAIN blocks only.
    #
    # Original helper blocks are discarded after cloning.
    # ========================================================

    output_blocks = [
        clone_block(
            original_by_id[
                block_id
            ], 
            block_id, 
        )

        for block_id
        in sorted(
            main_block_ids
        )
    ]


    output_by_id = {
        block.id: block
        for block in output_blocks
    }


    next_block_id = (
        max(
            block.id
            for block in blocks
        )
        + 1
    )


    records = []


    # ========================================================
    # Locate CALL sites in main.
    # ========================================================

    call_sites = []

    for block_id in sorted(
        main_block_ids
    ): 

        block = original_by_id[
            block_id
        ]

        call = find_call_op(
            block
        )

        if call is None: 
            continue

        if call.target is None: 
            raise RuntimeError(
                f"CALL at 0x{call.address:04x} "
                f"has no direct target"
            )

        call_sites.append(
            (
                block_id, 
                call, 
            )
        )


    # ========================================================
    # Inline each CALL independently.
    # ========================================================

    for (
        caller_block_id, 
        call_op, 
    ) in call_sites: 

        target = call_op.target


        if target not in helper_functions: 
            raise RuntimeError(
                f"CALL at "
                f"0x{call_op.address:04x} "
                f"targets unknown function "
                f"0x{target:04x}"
            )


        callee = helper_functions[
            target
        ]

        callee_block_ids = set(
            callee.block_ids
        )

        callee_entry_id = (
            helper_entry_block_id[
                target
            ]
        )


        caller = output_by_id[
            caller_block_id
        ]


        # ----------------------------------------------------
        # Existing CFG for CALL contains:
        #
        #     caller -> callee entry
        #     caller -> continuation
        #
        # Determine the continuation edge.
        # ----------------------------------------------------

        continuation_candidates = [
            successor

            for successor
            in caller.successors

            if successor
            != callee_entry_id
        ]


        if (
            len(
                continuation_candidates
            )
            != 1
        ): 
            raise RuntimeError(
                f"CALL BB{caller_block_id} "
                f"does not have exactly one "
                f"continuation successor: "
                f"{caller.successors}"
            )


        continuation_block_id = (
            continuation_candidates[
                0
            ]
        )


        # ----------------------------------------------------
        # Clone complete callee CFG.
        # ----------------------------------------------------

        id_map = {}


        for old_id in sorted(
            callee_block_ids
        ): 

            id_map[
                old_id
            ] = next_block_id

            next_block_id += 1


        cloned_blocks = []


        for old_id in sorted(
            callee_block_ids
        ): 

            old_block = original_by_id[
                old_id
            ]

            new_id = id_map[
                old_id
            ]


            new_ops = []


            has_return = False


            for op in old_block.ops: 

                if op.kind == "RETURN": 

                    has_return = True

                    # RETURN disappears when inlined.
                    continue


                if op.kind == "CALL": 

                    raise RuntimeError(
                        "Nested CALL inside helper "
                        "is not supported yet"
                    )


                new_ops.append(
                    clone_op(op)
                )


            if has_return: 

                # Inlined RETURN continues at the
                # caller's post-call block.

                new_successors = [
                    continuation_block_id
                ]

            else: 

                new_successors = []


                for successor in (
                    old_block.successors
                ): 

                    if (
                        successor
                        not in callee_block_ids
                    ): 
                        raise RuntimeError(
                            f"Helper "
                            f"{callee.name} "
                            f"BB{old_id} exits "
                            f"without RETURN"
                        )

                    new_successors.append(
                        id_map[
                            successor
                        ]
                    )


            cloned = IRBlock(
                id = new_id, 

                start = old_block.start, 

                ops = new_ops, 

                successors = 
                    new_successors, 

                predecessors = [], 
            )


            cloned_blocks.append(
                cloned
            )


        cloned_entry_id = (
            id_map[
                callee_entry_id
            ]
        )


        # ----------------------------------------------------
        # CALL itself disappears.
        #
        # Caller now enters cloned helper directly.
        # ----------------------------------------------------

        caller.ops = [
            clone_op(op)

            for op in caller.ops

            if op.kind != "CALL"
        ]


        caller.successors = [
            cloned_entry_id
        ]


        # ----------------------------------------------------
        # Add cloned blocks to program.
        # ----------------------------------------------------

        for cloned in cloned_blocks: 

            output_blocks.append(
                cloned
            )

            output_by_id[
                cloned.id
            ] = cloned


        records.append(
            InlineCallRecord(
                call_address = 
                    call_op.address, 

                caller_block_id = 
                    caller_block_id, 

                callee_name = 
                    callee.name, 

                callee_entry = 
                    target, 

                continuation_block_id = 
                    continuation_block_id, 

                cloned_entry_block_id = 
                    cloned_entry_id, 

                cloned_block_ids = [
                    block.id
                    for block
                    in cloned_blocks
                ], 
            )
        )


    # ========================================================
    # Recompute CFG predecessors.
    # ========================================================

    recompute_predecessors(
        output_blocks
    )


    # Keep deterministic order by BB id.

    output_blocks.sort(
        key = lambda block: 
            block.id
    )


    remaining_calls = (
        count_kind(
            output_blocks, 
            "CALL", 
        )
    )

    remaining_returns = (
        count_kind(
            output_blocks, 
            "RETURN", 
        )
    )


    return InlineResult(
        blocks = 
            output_blocks, 

        calls_inlined = 
            len(records), 

        records = 
            records, 

        remaining_calls = 
            remaining_calls, 

        remaining_returns = 
            remaining_returns, 

        original_block_count = 
            original_block_count, 

        final_block_count = 
            len(
                output_blocks
            ), 
    )