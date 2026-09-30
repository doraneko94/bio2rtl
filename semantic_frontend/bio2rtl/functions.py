from dataclasses import dataclass, field

from .ir import IRBlock


@dataclass
class FunctionInfo: 
    name: str
    entry: int

    block_ids: list[int] = field(
        default_factory = list
    )

    callsites: list[int] = field(
        default_factory = list
    )

    returns: list[int] = field(
        default_factory = list
    )


@dataclass
class FunctionAnalysis: 
    functions: dict[int, FunctionInfo]
    call_edges: list[tuple[int, int, int]]


def find_call_targets(
    ir_blocks: list[IRBlock], 
) -> set[int]: 

    targets = set()

    for block in ir_blocks: 
        for op in block.ops: 

            if (
                op.kind == "CALL"
                and op.target is not None
            ): 
                targets.add(op.target)

    return targets


def discover_functions(
    ir_blocks: list[IRBlock], 
) -> FunctionAnalysis: 

    if not ir_blocks: 
        raise RuntimeError(
            "No IR blocks"
        )


    block_by_start = {
        block.start: block
        for block in ir_blocks
    }

    block_by_id = {
        block.id: block
        for block in ir_blocks
    }


    main_entry = ir_blocks[0].start

    call_targets = find_call_targets(
        ir_blocks
    )

    function_entries = {
        main_entry, 
        *call_targets, 
    }


    functions = {}

    for entry in sorted(
        function_entries
    ): 

        name = (
            "main"
            if entry == main_entry
            else f"sub_{entry:04x}"
        )

        functions[entry] = FunctionInfo(
            name = name, 
            entry = entry, 
        )


    call_edges = []


    # --------------------------------------------------------
    # Record calls
    #
    # tuple:
    #
    # caller block start,
    # call instruction address,
    # target
    # --------------------------------------------------------

    for block in ir_blocks: 

        for op in block.ops: 

            if (
                op.kind == "CALL"
                and op.target is not None
            ): 

                call_edges.append(
                    (
                        block.start, 
                        op.address, 
                        op.target, 
                    )
                )

                if op.target in functions: 
                    functions[
                        op.target
                    ].callsites.append(
                        op.address
                    )


    # --------------------------------------------------------
    # Recover block membership for each function.
    #
    # Important:
    #
    # Existing CFG has both:
    #
    # CALL -> callee
    # CALL -> continuation
    #
    # For function membership we follow only the
    # continuation edge. The callee belongs to its own
    # function.
    # --------------------------------------------------------

    entry_to_block = {}

    for entry in function_entries: 

        if entry not in block_by_start: 
            raise RuntimeError(
                f"Function entry "
                f"0x{entry:04x} is not a block"
            )

        entry_to_block[entry] = (
            block_by_start[entry].id
        )


    function_entry_block_ids = set(
        entry_to_block.values()
    )


    for entry, function in functions.items(): 

        entry_block_id = (
            entry_to_block[entry]
        )

        visited = set()

        work = [
            entry_block_id
        ]


        while work: 

            block_id = work.pop()

            if block_id in visited: 
                continue

            visited.add(block_id)

            block = block_by_id[
                block_id
            ]


            # Do not accidentally walk into
            # another function entry.

            if (
                block_id
                in function_entry_block_ids
                and block_id
                != entry_block_id
            ): 
                continue


            function.block_ids.append(
                block_id
            )


            has_return = any(
                op.kind == "RETURN"
                for op in block.ops
            )


            if has_return: 

                for op in block.ops: 

                    if op.kind == "RETURN": 
                        function.returns.append(
                            op.address
                        )

                continue


            call_targets_in_block = {
                op.target
                for op in block.ops
                if (
                    op.kind == "CALL"
                    and op.target is not None
                )
            }


            target_block_ids = {
                block_by_start[target].id
                for target
                in call_targets_in_block
                if target in block_by_start
            }


            for successor in block.successors: 

                # CALL target belongs to callee.
                # Follow caller continuation instead.

                if successor in target_block_ids: 
                    continue

                if (
                    successor
                    in function_entry_block_ids
                    and successor
                    != entry_block_id
                ): 
                    continue

                work.append(successor)


        function.block_ids.sort()


    return FunctionAnalysis(
        functions = functions, 
        call_edges = call_edges, 
    )