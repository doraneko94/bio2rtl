from dataclasses import dataclass, field
from dataclasses import replace
import re

from .ir import (
    IROp, 
    IRBlock, 
)


# ============================================================
# SSA value syntax
# ============================================================

REGISTER_SSA_RE = re.compile(
    r"^x(?:[1-9]|[12][0-9]|3[01])_\d+$"
)

STATE_SSA_RE = re.compile(
    r"^stack_[A-Za-z0-9_]+_(?:m|p)\d+_\d+$"
)

VALUE_IN_EXPR_RE = re.compile(
    r"\b(?:"
    r"x(?:[1-9]|[12][0-9]|3[01])_\d+"
    r"|"
    r"stack_[A-Za-z0-9_]+_(?:m|p)\d+_\d+"
    r")\b"
)


# ============================================================
# Operation classes
# ============================================================

PURE_DEF_KINDS = {
    "CONST", 
    "ASSIGN", 
    "ADD", 
    "AND", 
    "OR", 
    "SHL", 
    "SHR", 
    "PHI", 
}


# These operations must never disappear merely because their
# result appears unused.
#
# GPIO_READ is deliberately preserved:
# it represents an external-input sampling point.

PRESERVED_KINDS = {
    "GPIO_READ", 

    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 

    "BRANCH", 
    "JUMP", 
}


OBSERVED_KINDS = {
    "GPIO_READ", 
    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 
    "BRANCH", 
    "JUMP", 
}


# ============================================================
# Result
# ============================================================

@dataclass
class SSAOptimizationResult: 
    blocks: list[IRBlock]

    operations_before: int
    operations_after: int

    copies_removed: int
    trivial_phis_removed: int
    dead_operations_removed: int

    iterations: int

    preserved_before: dict[str, int]
    preserved_after: dict[str, int]

    missing_definitions: list[
        tuple[int, int | None, str]
    ] = field(
        default_factory = list
    )

    duplicate_definitions: list[str] = field(
        default_factory = list
    )


# ============================================================
# Basic helpers
# ============================================================

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


def is_ssa_value(
    value: str, 
) -> bool: 

    return (
        REGISTER_SSA_RE.match(value)
        is not None

        or

        STATE_SSA_RE.match(value)
        is not None
    )


def is_live_in_value(
    value: str, 
) -> bool: 
    """
    SSA version 0 is a permitted live-in value.

    Examples:

        x1_0
        stack_main_sp_m60_0
    """

    if not is_ssa_value(value): 
        return False

    return value.endswith(
        "_0"
    )


def count_operations(
    blocks: list[IRBlock], 
) -> int: 

    return sum(
        len(block.ops)
        for block in blocks
    )


def count_observed_operations(
    blocks: list[IRBlock], 
) -> dict[str, int]: 

    result = {
        kind: 0
        for kind in OBSERVED_KINDS
    }


    for block in blocks: 

        for op in block.ops: 

            if op.kind in result: 

                result[
                    op.kind
                ] += 1


    return result


# ============================================================
# PHI helpers
# ============================================================

def parse_phi_arg(
    arg: str, 
) -> tuple[str, str] | None: 
    """
    Input:

        BB12=x10_17

    Output:

        ("BB12", "x10_17")
    """

    if "=" not in arg: 
        return None

    predecessor, value = arg.split(
        "=", 
        1, 
    )

    return (
        predecessor, 
        value, 
    )


def make_phi_arg(
    predecessor: str, 
    value: str, 
) -> str: 

    return (
        f"{predecessor}="
        f"{value}"
    )


def phi_values(
    op: IROp, 
) -> list[str]: 

    result = []


    for arg in op.args: 

        parsed = parse_phi_arg(
            arg
        )

        if parsed is None: 
            continue

        _, value = parsed

        result.append(
            value
        )


    return result


# ============================================================
# Alias resolution
# ============================================================

def resolve_alias(
    value: str, 
    aliases: dict[str, str], 
) -> str: 

    seen = set()

    current = value


    while current in aliases: 

        if current in seen: 

            # Defensive stop against accidental cycles.
            break

        seen.add(
            current
        )

        current = aliases[
            current
        ]


    return current


def build_aliases(
    blocks: list[IRBlock], 
) -> tuple[
    dict[str, str], 
    set[str], 
    set[str], 
]: 
    """
    Return:

        aliases
        copy-definition destinations
        trivial-PHI destinations
    """

    aliases = {}

    copy_destinations = set()
    trivial_phi_destinations = set()


    changed = True


    while changed: 

        changed = False


        for block in blocks: 

            for op in block.ops: 

                # ------------------------------------------------
                # Ordinary SSA copy:
                #
                # A := B
                # ------------------------------------------------

                if (
                    op.kind == "ASSIGN"
                    and op.dst is not None
                    and len(op.args) == 1
                ): 

                    source = resolve_alias(
                        op.args[0], 
                        aliases, 
                    )


                    if (
                        source != op.dst
                        and (
                            is_ssa_value(source)
                            or source == "x0"
                        )
                    ): 

                        if (
                            aliases.get(
                                op.dst
                            )
                            != source
                        ): 

                            aliases[
                                op.dst
                            ] = source

                            changed = True


                        copy_destinations.add(
                            op.dst
                        )


                # ------------------------------------------------
                # Trivial PHI:
                #
                # A := PHI(B, B, B)
                # ------------------------------------------------

                elif (
                    op.kind == "PHI"
                    and op.dst is not None
                ): 

                    values = [
                        resolve_alias(
                            value, 
                            aliases, 
                        )

                        for value
                        in phi_values(op)
                    ]


                    # Ignore self input when deciding whether
                    # a loop PHI is trivial:
                    #
                    # A = PHI(B, A)
                    #
                    # is equivalent to B if every non-self
                    # incoming value is B.

                    non_self = [
                        value

                        for value in values

                        if value != op.dst
                    ]


                    if not non_self: 
                        continue


                    first = non_self[0]


                    if all(
                        value == first
                        for value in non_self
                    ): 

                        if (
                            aliases.get(
                                op.dst
                            )
                            != first
                        ): 

                            aliases[
                                op.dst
                            ] = first

                            changed = True


                        trivial_phi_destinations.add(
                            op.dst
                        )


    return (
        aliases, 
        copy_destinations, 
        trivial_phi_destinations, 
    )


# ============================================================
# Rewrite aliases everywhere
# ============================================================

def rewrite_expression(
    expression: str, 
    aliases: dict[str, str], 
) -> str: 

    def replacement(
        match, 
    ): 

        value = match.group(0)

        return resolve_alias(
            value, 
            aliases, 
        )


    return VALUE_IN_EXPR_RE.sub(
        replacement, 
        expression, 
    )


def rewrite_aliases(
    blocks: list[IRBlock], 
    aliases: dict[str, str], 
) -> list[IRBlock]: 

    result = []


    for block in blocks: 

        new_ops = []


        for op in block.ops: 

            new_op = clone_op(
                op
            )


            if new_op.kind == "PHI": 

                rewritten = []


                for arg in new_op.args: 

                    parsed = parse_phi_arg(
                        arg
                    )

                    if parsed is None: 

                        rewritten.append(
                            arg
                        )

                        continue


                    predecessor, value = (
                        parsed
                    )


                    rewritten.append(
                        make_phi_arg(
                            predecessor, 
                            resolve_alias(
                                value, 
                                aliases, 
                            ), 
                        )
                    )


                new_op.args = rewritten


            else: 

                new_op.args = [
                    resolve_alias(
                        arg, 
                        aliases, 
                    )

                    if (
                        is_ssa_value(arg)
                        or arg == "x0"
                    )

                    else arg

                    for arg in new_op.args
                ]


            if new_op.kind == "BRANCH": 

                new_op.comment = (
                    rewrite_expression(
                        new_op.comment, 
                        aliases, 
                    )
                )


            new_ops.append(
                new_op
            )


        result.append(
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


    return result


# ============================================================
# Remove alias-producing operations
# ============================================================

def remove_alias_definitions(
    blocks: list[IRBlock], 
    aliases: dict[str, str], 
) -> tuple[
    list[IRBlock], 
    int, 
    int, 
]: 

    result = []

    copies_removed = 0
    trivial_phis_removed = 0


    for block in blocks: 

        new_ops = []


        for op in block.ops: 

            if (
                op.dst is not None
                and op.dst in aliases
            ): 

                if op.kind == "ASSIGN": 

                    copies_removed += 1

                    continue


                if op.kind == "PHI": 

                    trivial_phis_removed += 1

                    continue


            new_ops.append(
                clone_op(op)
            )


        result.append(
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


    return (
        result, 
        copies_removed, 
        trivial_phis_removed, 
    )


# ============================================================
# SSA uses
# ============================================================

def values_used_by_op(
    op: IROp, 
) -> set[str]: 

    result = set()


    if op.kind == "PHI": 

        for value in phi_values(op): 

            if is_ssa_value(value): 

                result.add(
                    value
                )


    else: 

        for arg in op.args: 

            if is_ssa_value(arg): 

                result.add(
                    arg
                )


    if op.kind == "BRANCH": 

        for match in (
            VALUE_IN_EXPR_RE.finditer(
                op.comment
            )
        ): 

            result.add(
                match.group(0)
            )


    return result


def collect_use_counts(
    blocks: list[IRBlock], 
) -> dict[str, int]: 

    result = {}


    for block in blocks: 

        for op in block.ops: 

            for value in (
                values_used_by_op(op)
            ): 

                result[
                    value
                ] = (
                    result.get(
                        value, 
                        0, 
                    )
                    + 1
                )


    return result


# ============================================================
# Dead-code elimination
# ============================================================

def eliminate_dead_pure_ops(
    blocks: list[IRBlock], 
) -> tuple[
    list[IRBlock], 
    int, 
    int, 
]: 

    current = clone_blocks(
        blocks
    )

    removed_total = 0

    iterations = 0


    while True: 

        iterations += 1

        uses = collect_use_counts(
            current
        )


        removed_this_round = 0

        next_blocks = []


        for block in current: 

            new_ops = []


            for op in block.ops: 

                if (
                    op.kind in PURE_DEF_KINDS
                    and op.dst is not None
                    and uses.get(
                        op.dst, 
                        0, 
                    ) == 0
                ): 

                    removed_this_round += 1

                    continue


                new_ops.append(
                    clone_op(op)
                )


            next_blocks.append(
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


        removed_total += (
            removed_this_round
        )

        current = next_blocks


        if removed_this_round == 0: 
            break


    return (
        current, 
        removed_total, 
        iterations, 
    )


# ============================================================
# Verification
# ============================================================

def verify_ssa_definitions(
    blocks: list[IRBlock], 
) -> tuple[
    list[
        tuple[int, int | None, str]
    ], 
    list[str], 
]: 

    definitions = {}

    duplicates = set()


    for block in blocks: 

        for op in block.ops: 

            if (
                op.dst is None
                or not is_ssa_value(
                    op.dst
                )
            ): 
                continue


            if op.dst in definitions: 

                duplicates.add(
                    op.dst
                )

            else: 

                definitions[
                    op.dst
                ] = (
                    block.id, 
                    op.address, 
                )


    missing = []


    for block in blocks: 

        for op in block.ops: 

            for value in (
                values_used_by_op(op)
            ): 

                if (
                    value not in definitions
                    and not is_live_in_value(
                        value
                    )
                ): 

                    missing.append(
                        (
                            block.id, 
                            op.address, 
                            value, 
                        )
                    )


    return (
        sorted(
            set(missing)
        ), 
        sorted(
            duplicates
        ), 
    )


# ============================================================
# Complete optimization
# ============================================================

def optimize_ssa(
    input_blocks: list[IRBlock], 
) -> SSAOptimizationResult: 

    before = count_operations(
        input_blocks
    )


    preserved_before = (
        count_observed_operations(
            input_blocks
        )
    )


    # ========================================================
    # Repeat copy/PHI simplification until stable.
    # ========================================================

    current = clone_blocks(
        input_blocks
    )

    copies_removed = 0
    trivial_phis_removed = 0

    copy_iterations = 0


    while True: 

        copy_iterations += 1


        (
            aliases, 
            _, 
            _, 
        ) = build_aliases(
            current
        )


        if not aliases: 
            break


        rewritten = rewrite_aliases(
            current, 
            aliases, 
        )


        (
            rewritten, 
            copies, 
            phis, 
        ) = remove_alias_definitions(
            rewritten, 
            aliases, 
        )


        copies_removed += copies
        trivial_phis_removed += phis


        if (
            copies == 0
            and phis == 0
        ): 
            current = rewritten
            break


        current = rewritten


    # ========================================================
    # Dead pure SSA values
    # ========================================================

    (
        current, 
        dead_removed, 
        dce_iterations, 
    ) = eliminate_dead_pure_ops(
        current
    )


    after = count_operations(
        current
    )


    preserved_after = (
        count_observed_operations(
            current
        )
    )


    (
        missing, 
        duplicates, 
    ) = verify_ssa_definitions(
        current
    )


    return SSAOptimizationResult(
        blocks = current, 

        operations_before = before, 

        operations_after = after, 

        copies_removed = 
            copies_removed, 

        trivial_phis_removed = 
            trivial_phis_removed, 

        dead_operations_removed = 
            dead_removed, 

        iterations = (
            copy_iterations
            + dce_iterations
        ), 

        preserved_before = 
            preserved_before, 

        preserved_after = 
            preserved_after, 

        missing_definitions = 
            missing, 

        duplicate_definitions = 
            duplicates, 
    )