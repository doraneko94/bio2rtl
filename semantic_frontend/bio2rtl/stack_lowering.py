from dataclasses import dataclass, field
from typing import Optional
import re

from .ir import IRBlock
from .functions import FunctionAnalysis


# ============================================================
# SP analysis states
# ============================================================

UNKNOWN = object()
CONFLICT = object()


# ============================================================
# Helpers
# ============================================================

STACK_EXPR_RE = re.compile(
    r"^x2([+-]\d+)?$"
)


def parse_int(
    value: str, 
) -> Optional[int]: 

    value = value.strip()

    try: 
        return int(
            value, 
            0, 
        )
    except ValueError: 
        return None


def parse_sp_operand(
    expr: str, 
) -> Optional[int]: 
    """
    Examples:

        x2       -> 0
        x2+28    -> 28
        x2-4     -> -4
    """

    match = STACK_EXPR_RE.match(
        expr
    )

    if not match: 
        return None

    suffix = match.group(1)

    if suffix is None: 
        return 0

    return int(suffix)


def format_slot(
    function_name: str, 
    offset: int, 
) -> str: 

    if offset >= 0: 
        return (
            f"{function_name}:sp+{offset}"
        )

    return (
        f"{function_name}:sp{offset}"
    )


# ============================================================
# SP structures
# ============================================================

@dataclass
class SpBlockState: 
    block_id: int

    sp_in: object = UNKNOWN
    sp_out: object = UNKNOWN


@dataclass
class CanonicalMemoryAccess: 
    function: str

    block_id: int
    address: int

    kind: str

    raw_slot: str

    canonical_slot: Optional[str]

    sp_delta: object


@dataclass
class SpAnalysis: 
    block_states: dict[
        int, 
        SpBlockState
    ]

    accesses: list[
        CanonicalMemoryAccess
    ]

    unresolved: list[
        CanonicalMemoryAccess
    ]


# ============================================================
# CFG helpers
# ============================================================

def function_local_successors(
    function_block_ids: set[int], 
    blocks_by_id: dict[int, IRBlock], 
) -> dict[int, list[int]]: 

    result = {}

    for block_id in function_block_ids: 

        block = blocks_by_id[
            block_id
        ]

        result[block_id] = [
            successor
            for successor
            in block.successors
            if successor
            in function_block_ids
        ]

    return result


def build_predecessors(
    successors: dict[int, list[int]], 
) -> dict[int, list[int]]: 

    predecessors = {
        block_id: []
        for block_id
        in successors
    }

    for source, targets in (
        successors.items()
    ): 

        for target in targets: 

            predecessors[
                target
            ].append(
                source
            )

    return predecessors


# ============================================================
# SP transfer
# ============================================================

def merge_sp_values(
    values: list[object], 
) -> object: 

    known = [
        value
        for value in values
        if value is not UNKNOWN
    ]

    if not known: 
        return UNKNOWN

    if any(
        value is CONFLICT
        for value in known
    ): 
        return CONFLICT

    first = known[0]

    if all(
        value == first
        for value in known
    ): 
        return first

    return CONFLICT


def transfer_sp(
    block: IRBlock, 
    sp_in: object, 
) -> object: 

    current = sp_in

    for op in block.ops: 

        # ----------------------------------------------------
        # Absolute SP setup.
        #
        # We only care about offsets within the current
        # function frame, therefore redefine it as 0.
        # ----------------------------------------------------

        if (
            op.kind == "CONST"
            and op.dst == "x2"
        ): 
            current = 0
            continue


        # ----------------------------------------------------
        # x2 := x2 + immediate
        # ----------------------------------------------------

        if (
            op.kind == "ADD"
            and op.dst == "x2"
        ): 

            if len(op.args) != 2: 
                current = CONFLICT
                continue

            source = op.args[0]

            amount = parse_int(
                op.args[1]
            )

            if (
                source == "x2"
                and amount is not None
                and isinstance(
                    current, 
                    int, 
                )
            ): 

                current += amount

            elif current is UNKNOWN: 

                current = UNKNOWN

            else: 

                current = CONFLICT

            continue


        # ----------------------------------------------------
        # Unknown write to SP.
        # ----------------------------------------------------

        if op.dst == "x2": 
            current = CONFLICT


    return current


# ============================================================
# SP normalization
# ============================================================

def analyze_sp(
    ir_blocks: list[IRBlock], 
    functions: FunctionAnalysis, 
) -> SpAnalysis: 

    blocks_by_id = {
        block.id: block
        for block in ir_blocks
    }

    block_states = {}

    accesses = []
    unresolved = []


    for _, function in sorted(
        functions.functions.items()
    ): 

        function_blocks = set(
            function.block_ids
        )

        if not function_blocks: 
            continue


        successors = (
            function_local_successors(
                function_blocks, 
                blocks_by_id, 
            )
        )

        predecessors = (
            build_predecessors(
                successors
            )
        )


        entry_block_id = min(
            function.block_ids, 
            key = lambda block_id: 
                blocks_by_id[
                    block_id
                ].start
        )


        states = {
            block_id: 
                SpBlockState(
                    block_id = block_id
                )

            for block_id
            in function_blocks
        }


        states[
            entry_block_id
        ].sp_in = 0


        # ----------------------------------------------------
        # Fixed-point SP propagation
        # ----------------------------------------------------

        changed = True

        while changed: 

            changed = False


            for block_id in sorted(
                function_blocks
            ): 

                block = blocks_by_id[
                    block_id
                ]

                state = states[
                    block_id
                ]


                if (
                    block_id
                    != entry_block_id
                ): 

                    pred_values = [
                        states[
                            pred
                        ].sp_out

                        for pred
                        in predecessors[
                            block_id
                        ]
                    ]


                    if pred_values: 

                        new_in = (
                            merge_sp_values(
                                pred_values
                            )
                        )

                    else: 

                        new_in = UNKNOWN


                    if new_in != state.sp_in: 

                        state.sp_in = new_in

                        changed = True


                new_out = transfer_sp(
                    block, 
                    state.sp_in, 
                )


                if (
                    new_out
                    != state.sp_out
                ): 

                    state.sp_out = (
                        new_out
                    )

                    changed = True


        block_states.update(
            states
        )


        # ----------------------------------------------------
        # Canonicalize LOAD / STORE at exact instruction
        # position.
        # ----------------------------------------------------

        for block_id in sorted(
            function_blocks
        ): 

            block = blocks_by_id[
                block_id
            ]

            current_sp = states[
                block_id
            ].sp_in


            for op in block.ops: 

                # --------------------------------------------
                # Update SP before following instructions.
                # --------------------------------------------

                if (
                    op.kind == "CONST"
                    and op.dst == "x2"
                ): 

                    current_sp = 0

                    continue


                if (
                    op.kind == "ADD"
                    and op.dst == "x2"
                ): 

                    if len(op.args) == 2: 

                        amount = parse_int(
                            op.args[1]
                        )

                        if (
                            op.args[0]
                            == "x2"
                            and amount
                            is not None
                            and isinstance(
                                current_sp, 
                                int, 
                            )
                        ): 

                            current_sp += (
                                amount
                            )

                        elif (
                            current_sp
                            is UNKNOWN
                        ): 

                            current_sp = (
                                UNKNOWN
                            )

                        else: 

                            current_sp = (
                                CONFLICT
                            )

                    else: 

                        current_sp = (
                            CONFLICT
                        )

                    continue


                if (
                    op.dst == "x2"
                    and op.kind
                    not in {
                        "CONST", 
                        "ADD", 
                    }
                ): 

                    current_sp = CONFLICT


                # --------------------------------------------
                # Only memory operations from here.
                # --------------------------------------------

                if op.kind not in {
                    "LOAD", 
                    "STORE", 
                }: 

                    continue


                if not op.args: 
                    continue


                raw_slot = op.args[0]

                offset = parse_sp_operand(
                    raw_slot
                )


                canonical_slot = None


                if (
                    offset is not None
                    and isinstance(
                        current_sp, 
                        int, 
                    )
                ): 

                    absolute_offset = (
                        current_sp
                        + offset
                    )

                    canonical_slot = (
                        format_slot(
                            function.name, 
                            absolute_offset, 
                        )
                    )


                access = (
                    CanonicalMemoryAccess(
                        function = 
                            function.name, 

                        block_id = 
                            block_id, 

                        address = 
                            op.address, 

                        kind = 
                            op.kind, 

                        raw_slot = 
                            raw_slot, 

                        canonical_slot = 
                            canonical_slot, 

                        sp_delta = 
                            current_sp, 
                    )
                )


                accesses.append(
                    access
                )


                if (
                    canonical_slot
                    is None
                ): 

                    unresolved.append(
                        access
                    )


    return SpAnalysis(
        block_states = 
            block_states, 

        accesses = 
            accesses, 

        unresolved = 
            unresolved, 
    )


# ============================================================
# Reaching-definition structures
# ============================================================

@dataclass
class LoadReachingDefinition: 
    function: str

    address: int
    block_id: int

    slot: str

    definitions: set[int] = field(
        default_factory = set
    )


@dataclass
class ReachingDefinitionAnalysis: 
    loads: list[
        LoadReachingDefinition
    ]

    unique: list[
        LoadReachingDefinition
    ]

    ambiguous: list[
        LoadReachingDefinition
    ]

    uninitialized: list[
        LoadReachingDefinition
    ]


# ============================================================
# Reaching definitions
# ============================================================

def analyze_reaching_definitions(
    ir_blocks: list[IRBlock], 
    functions: FunctionAnalysis, 
    sp_analysis: SpAnalysis, 
) -> ReachingDefinitionAnalysis: 

    blocks_by_id = {
        block.id: block
        for block in ir_blocks
    }


    # ========================================================
    # Canonical access lookup by instruction address
    # ========================================================

    access_by_address = {
        access.address: 
            access

        for access
        in sp_analysis.accesses

        if (
            access.canonical_slot
            is not None
        )
    }


    all_load_results = []


    # ========================================================
    # Analyze each function separately
    # ========================================================

    for _, function in sorted(
        functions.functions.items()
    ): 

        function_blocks = set(
            function.block_ids
        )

        if not function_blocks: 
            continue


        successors = (
            function_local_successors(
                function_blocks, 
                blocks_by_id, 
            )
        )

        predecessors = (
            build_predecessors(
                successors
            )
        )


        # ----------------------------------------------------
        # Slots belonging to this function.
        # ----------------------------------------------------

        slots = set()

        for access in (
            sp_analysis.accesses
        ): 

            if (
                access.function
                == function.name
                and access.canonical_slot
                is not None
            ): 

                slots.add(
                    access.canonical_slot
                )


        # ----------------------------------------------------
        # IN and OUT:
        #
        # block_id
        #   -> slot
        #       -> set(store addresses)
        # ----------------------------------------------------

        in_state = {
            block_id: {
                slot: set()
                for slot in slots
            }

            for block_id
            in function_blocks
        }


        out_state = {
            block_id: {
                slot: set()
                for slot in slots
            }

            for block_id
            in function_blocks
        }


        # ----------------------------------------------------
        # Transfer one block.
        #
        # Critical rule:
        #
        # STORE to a slot KILLS previous definitions of that
        # slot on that path.
        # ----------------------------------------------------

        def transfer_block(
            block_id: int, 
            incoming: 
                dict[str, set[int]], 
        ) -> dict[str, set[int]]: 

            current = {
                slot: set(defs)
                for slot, defs
                in incoming.items()
            }


            block = blocks_by_id[
                block_id
            ]


            for op in block.ops: 

                if op.kind != "STORE": 
                    continue


                access = (
                    access_by_address.get(
                        op.address
                    )
                )


                if access is None: 
                    continue


                slot = (
                    access.canonical_slot
                )


                if slot is None: 
                    continue


                # KILL all older definitions of this slot.
                current[slot] = {
                    op.address
                }


            return current


        # ----------------------------------------------------
        # Fixed-point iteration
        # ----------------------------------------------------

        changed = True

        while changed: 

            changed = False


            for block_id in sorted(
                function_blocks
            ): 

                preds = predecessors[
                    block_id
                ]


                # --------------------------------------------
                # Meet operation:
                #
                # union across DIFFERENT predecessor paths.
                # --------------------------------------------

                merged = {
                    slot: set()
                    for slot in slots
                }


                for pred in preds: 

                    for slot in slots: 

                        merged[
                            slot
                        ] |= out_state[
                            pred
                        ][slot]


                if (
                    merged
                    != in_state[
                        block_id
                    ]
                ): 

                    in_state[
                        block_id
                    ] = {
                        slot: set(defs)

                        for slot, defs
                        in merged.items()
                    }

                    changed = True


                new_out = (
                    transfer_block(
                        block_id, 
                        in_state[
                            block_id
                        ], 
                    )
                )


                if (
                    new_out
                    != out_state[
                        block_id
                    ]
                ): 

                    out_state[
                        block_id
                    ] = new_out

                    changed = True


        # ----------------------------------------------------
        # Fixed point is ready.
        #
        # Walk each block instruction-by-instruction so that
        # LOAD observes definitions valid AT THAT EXACT POINT.
        # ----------------------------------------------------

        for block_id in sorted(
            function_blocks
        ): 

            current = {
                slot: set(defs)

                for slot, defs
                in in_state[
                    block_id
                ].items()
            }


            block = blocks_by_id[
                block_id
            ]


            for op in block.ops: 

                # --------------------------------------------
                # LOAD:
                # record currently reaching definitions.
                # --------------------------------------------

                if op.kind == "LOAD": 

                    access = (
                        access_by_address.get(
                            op.address
                        )
                    )

                    if access is None: 
                        continue


                    slot = (
                        access.canonical_slot
                    )

                    if slot is None: 
                        continue


                    result = (
                        LoadReachingDefinition(
                            function = 
                                function.name, 

                            address = 
                                op.address, 

                            block_id = 
                                block_id, 

                            slot = 
                                slot, 

                            definitions = 
                                set(
                                    current[
                                        slot
                                    ]
                                ), 
                        )
                    )


                    all_load_results.append(
                        result
                    )


                # --------------------------------------------
                # STORE:
                #
                # KILL previous definitions, then GEN this one.
                # --------------------------------------------

                elif op.kind == "STORE": 

                    access = (
                        access_by_address.get(
                            op.address
                        )
                    )

                    if access is None: 
                        continue


                    slot = (
                        access.canonical_slot
                    )

                    if slot is None: 
                        continue


                    current[
                        slot
                    ] = {
                        op.address
                    }


    # ========================================================
    # Classification
    # ========================================================

    unique = [
        load

        for load
        in all_load_results

        if (
            len(
                load.definitions
            )
            == 1
        )
    ]


    ambiguous = [
        load

        for load
        in all_load_results

        if (
            len(
                load.definitions
            )
            > 1
        )
    ]


    uninitialized = [
        load

        for load
        in all_load_results

        if (
            len(
                load.definitions
            )
            == 0
        )
    ]


    return (
        ReachingDefinitionAnalysis(
            loads = 
                all_load_results, 

            unique = 
                unique, 

            ambiguous = 
                ambiguous, 

            uninitialized = 
                uninitialized, 
        )
    )