from dataclasses import dataclass, field
from dataclasses import replace
import re

from .ir import (
    IROp, 
    IRBlock, 
)


# ============================================================
# Register syntax
# ============================================================

BASE_REGISTER_RE = re.compile(
    r"^x(?:[1-9]|[12][0-9]|3[01])$"
)

REGISTER_IN_EXPR_RE = re.compile(
    r"\bx(?:[1-9]|[12][0-9]|3[01])\b"
)


REGISTER_DEF_KINDS = {
    "CONST", 
    "ASSIGN", 
    "ADD", 
    "AND", 
    "OR", 
    "SHL", 
    "SHR", 
    "GPIO_READ", 
}


# ============================================================
# Result structures
# ============================================================

@dataclass
class SSADefinition: 
    name: str
    base_register: str

    block_id: int
    address: int | None

    kind: str


@dataclass
class SSAResult: 
    blocks: list[IRBlock]

    entry_block_id: int

    phi_count: int

    definitions: dict[
        str, 
        SSADefinition
    ]

    versions_per_register: dict[
        str, 
        int
    ]

    unversioned_defs: list[
        tuple[int, int | None, str]
    ] = field(
        default_factory = list
    )

    unversioned_uses: list[
        tuple[int, int | None, str]
    ] = field(
        default_factory = list
    )


# ============================================================
# Helpers
# ============================================================

def is_base_register(
    value: str, 
) -> bool: 

    return (
        BASE_REGISTER_RE.match(value)
        is not None
    )


def defined_register(
    op: IROp, 
) -> str | None: 

    if (
        op.kind in REGISTER_DEF_KINDS
        and op.dst is not None
        and is_base_register(
            op.dst
        )
    ): 
        return op.dst

    return None


def registers_used_by_op(
    op: IROp, 
) -> list[str]: 

    result = []


    for arg in op.args: 

        if is_base_register(arg): 

            if arg not in result: 
                result.append(arg)


    if op.kind == "BRANCH": 

        for match in (
            REGISTER_IN_EXPR_RE.finditer(
                op.comment
            )
        ): 

            reg = match.group(0)

            if reg not in result: 
                result.append(reg)


    return result


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


# ============================================================
# Reachability
# ============================================================

def find_reachable(
    blocks_by_id: dict[int, IRBlock], 
    entry: int, 
) -> set[int]: 

    visited = set()
    work = [entry]


    while work: 

        block_id = work.pop()

        if block_id in visited: 
            continue

        visited.add(block_id)

        for successor in (
            blocks_by_id[
                block_id
            ].successors
        ): 

            work.append(successor)


    return visited


# ============================================================
# Dominators
# ============================================================

def compute_dominators(
    blocks_by_id: dict[int, IRBlock], 
    entry: int, 
    reachable: set[int], 
) -> dict[int, set[int]]: 

    dominators = {}


    for block_id in reachable: 

        if block_id == entry: 

            dominators[
                block_id
            ] = {
                block_id
            }

        else: 

            dominators[
                block_id
            ] = set(
                reachable
            )


    changed = True


    while changed: 

        changed = False


        for block_id in sorted(
            reachable
        ): 

            if block_id == entry: 
                continue


            predecessors = [
                pred

                for pred in (
                    blocks_by_id[
                        block_id
                    ].predecessors
                )

                if pred in reachable
            ]


            if not predecessors: 

                new_dom = {
                    block_id
                }

            else: 

                intersection = set(
                    dominators[
                        predecessors[0]
                    ]
                )


                for pred in (
                    predecessors[1:]
                ): 

                    intersection &= (
                        dominators[
                            pred
                        ]
                    )


                new_dom = (
                    intersection
                    | {
                        block_id
                    }
                )


            if (
                new_dom
                != dominators[
                    block_id
                ]
            ): 

                dominators[
                    block_id
                ] = new_dom

                changed = True


    return dominators


def compute_idom(
    dominators: 
        dict[int, set[int]], 
    entry: int, 
) -> dict[int, int | None]: 

    idom = {
        entry: None
    }


    for block_id in sorted(
        dominators
    ): 

        if block_id == entry: 
            continue


        strict = (
            dominators[
                block_id
            ]
            - {
                block_id
            }
        )


        if not strict: 

            idom[
                block_id
            ] = None

            continue


        # Immediate dominator is the deepest
        # strict dominator.

        candidate = max(
            strict, 
            key = lambda item: 
                len(
                    dominators[
                        item
                    ]
                ), 
        )


        idom[
            block_id
        ] = candidate


    return idom


def compute_dom_tree(
    idom: dict[int, int | None], 
) -> dict[int, list[int]]: 

    tree = {
        block_id: []

        for block_id
        in idom
    }


    for block_id, parent in (
        idom.items()
    ): 

        if parent is None: 
            continue

        tree[
            parent
        ].append(
            block_id
        )


    for children in (
        tree.values()
    ): 

        children.sort()


    return tree


def compute_dominance_frontier(
    blocks_by_id: dict[int, IRBlock], 
    idom: dict[int, int | None], 
    reachable: set[int], 
) -> dict[int, set[int]]: 

    frontier = {
        block_id: set()

        for block_id
        in reachable
    }


    for block_id in sorted(
        reachable
    ): 

        predecessors = [
            pred

            for pred in (
                blocks_by_id[
                    block_id
                ].predecessors
            )

            if pred in reachable
        ]


        if len(predecessors) < 2: 
            continue


        for pred in predecessors: 

            runner = pred


            while (
                runner is not None
                and runner
                != idom[
                    block_id
                ]
            ): 

                frontier[
                    runner
                ].add(
                    block_id
                )

                runner = idom[
                    runner
                ]


    return frontier


# ============================================================
# PHI placement
# ============================================================

def collect_definition_blocks(
    blocks: list[IRBlock], 
) -> dict[str, set[int]]: 

    result = {}


    for block in blocks: 

        for op in block.ops: 

            reg = defined_register(
                op
            )

            if reg is None: 
                continue


            result.setdefault(
                reg, 
                set(), 
            ).add(
                block.id
            )


    return result


def place_phi_nodes(
    blocks: list[IRBlock], 
    dominance_frontier: 
        dict[int, set[int]], 
) -> dict[int, set[str]]: 

    definition_blocks = (
        collect_definition_blocks(
            blocks
        )
    )


    phi_by_block: dict[
        int, 
        set[str]
    ] = {
        block.id: set()
        for block in blocks
    }


    for reg, defs in (
        definition_blocks.items()
    ): 

        work = list(
            defs
        )

        processed = set()


        while work: 

            block_id = work.pop()


            for frontier_block in (
                dominance_frontier[
                    block_id
                ]
            ): 

                if (
                    reg
                    in phi_by_block[
                        frontier_block
                    ]
                ): 
                    continue


                phi_by_block[
                    frontier_block
                ].add(
                    reg
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
# SSA renaming
# ============================================================

def convert_to_ssa(
    input_blocks: list[IRBlock], 
) -> SSAResult: 

    blocks = clone_blocks(
        input_blocks
    )


    blocks_by_id = {
        block.id: block
        for block in blocks
    }


    if not blocks: 

        raise RuntimeError(
            "No blocks for SSA conversion"
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
            set(
                blocks_by_id
            )
            - reachable
        )

        raise RuntimeError(
            "SSA conversion found "
            "unreachable blocks: "
            + ", ".join(
                f"BB{x}"
                for x in unreachable
            )
        )


    dominators = (
        compute_dominators(
            blocks_by_id, 
            entry, 
            reachable, 
        )
    )


    idom = compute_idom(
        dominators, 
        entry, 
    )


    dom_tree = compute_dom_tree(
        idom
    )


    frontier = (
        compute_dominance_frontier(
            blocks_by_id, 
            idom, 
            reachable, 
        )
    )


    phi_by_block = (
        place_phi_nodes(
            blocks, 
            frontier, 
        )
    )


    # --------------------------------------------------------
    # Version stacks.
    #
    # xN_0 means function/program live-in value.
    # --------------------------------------------------------

    all_registers = {
        f"x{i}"
        for i in range(
            1, 
            32, 
        )
    }


    counters = {
        reg: 0
        for reg in all_registers
    }


    stacks = {
        reg: [
            f"{reg}_0"
        ]
        for reg in all_registers
    }


    definitions = {}


    versions_per_register = {
        reg: 0
        for reg in all_registers
    }


    # PHI incoming:
    #
    # (block, base_reg)
    #     -> predecessor BB
    #         -> SSA value

    phi_incoming = {}

    # ========================================================
    # Pre-create PHI incoming dictionaries.
    #
    # Important for loops:
    #
    # A predecessor may be renamed before the successor block
    # containing the PHI itself is visited in dominator-tree
    # order.
    #
    # Therefore the incoming table must exist before SSA
    # renaming starts.
    # ========================================================

    for block_id, registers in (
        phi_by_block.items()
    ): 

        for reg in registers: 

            phi_incoming[
                (
                    block_id, 
                    reg, 
                )
            ] = {}


    # PHI destination names.

    phi_destination = {}


    def new_version(
        reg: str, 
        block_id: int, 
        address: int | None, 
        kind: str, 
    ) -> str: 

        counters[
            reg
        ] += 1


        version = (
            f"{reg}_"
            f"{counters[reg]}"
        )


        stacks[
            reg
        ].append(
            version
        )


        versions_per_register[
            reg
        ] += 1


        definitions[
            version
        ] = SSADefinition(
            name = version, 

            base_register = reg, 

            block_id = block_id, 

            address = address, 

            kind = kind, 
        )


        return version


    def current_version(
        reg: str, 
    ) -> str: 

        return stacks[
            reg
        ][-1]


    def rewrite_arg(
        value: str, 
    ) -> str: 

        if is_base_register(
            value
        ): 

            return current_version(
                value
            )

        return value


    def rewrite_branch(
        expression: str, 
    ) -> str: 

        def replacement(
            match, 
        ): 

            reg = match.group(0)

            return current_version(
                reg
            )


        return (
            REGISTER_IN_EXPR_RE.sub(
                replacement, 
                expression, 
            )
        )


    # --------------------------------------------------------
    # Dominator-tree DFS rename
    # --------------------------------------------------------

    def rename_block(
        block_id: int, 
    ): 

        block = blocks_by_id[
            block_id
        ]


        pushed = []


        # ====================================================
        # PHI definitions occur at block entry.
        # ====================================================

        for reg in sorted(
            phi_by_block[
                block_id
            ], 
            key = lambda value: 
                int(
                    value[1:]
                ), 
        ): 

            version = new_version(
                reg, 
                block_id, 
                None, 
                "PHI", 
            )


            pushed.append(
                reg
            )


            phi_destination[
                (
                    block_id, 
                    reg, 
                )
            ] = version


        # ====================================================
        # Ordinary operations
        # ====================================================

        renamed_ops = []


        for op in block.ops: 

            new_op = clone_op(
                op
            )


            # -----------------------------------------------
            # Rewrite register USES first.
            # -----------------------------------------------

            new_op.args = [
                rewrite_arg(arg)
                for arg
                in new_op.args
            ]


            if new_op.kind == "BRANCH": 

                new_op.comment = (
                    rewrite_branch(
                        new_op.comment
                    )
                )


            # -----------------------------------------------
            # Then create new version for DEF.
            # -----------------------------------------------

            reg = defined_register(
                op
            )


            if reg is not None: 

                version = new_version(
                    reg, 
                    block_id, 
                    op.address, 
                    op.kind, 
                )


                pushed.append(
                    reg
                )


                new_op.dst = version


            renamed_ops.append(
                new_op
            )


        block.ops = renamed_ops


        # ====================================================
        # Supply this block's current versions to PHIs in
        # successor blocks.
        # ====================================================

        for successor in (
            block.successors
        ): 

            for reg in (
                phi_by_block[
                    successor
                ]
            ): 

                phi_incoming[
                    (
                        successor, 
                        reg, 
                    )
                ][
                    block_id
                ] = current_version(
                    reg
                )


        # ====================================================
        # Rename dominated children.
        # ====================================================

        for child in (
            dom_tree[
                block_id
            ]
        ): 

            rename_block(
                child
            )


        # ====================================================
        # Restore value stacks when leaving dom-tree subtree.
        # ====================================================

        for reg in reversed(
            pushed
        ): 

            stacks[
                reg
            ].pop()


    rename_block(
        entry
    )


    # ========================================================
    # Materialize PHI instructions.
    # ========================================================

    phi_count = 0


    for block in blocks: 

        phi_ops = []


        for reg in sorted(
            phi_by_block[
                block.id
            ], 
            key = lambda value: 
                int(
                    value[1:]
                ), 
        ): 

            destination = (
                phi_destination[
                    (
                        block.id, 
                        reg, 
                    )
                ]
            )


            incoming = (
                phi_incoming[
                    (
                        block.id, 
                        reg, 
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
                        f"base={reg}"
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

    unversioned_defs = []
    unversioned_uses = []


    for block in blocks: 

        for op in block.ops: 

            # PHI dst is already versioned.

            if (
                op.dst is not None
                and is_base_register(
                    op.dst
                )
            ): 

                unversioned_defs.append(
                    (
                        block.id, 
                        op.address, 
                        op.dst, 
                    )
                )


            for arg in op.args: 

                if is_base_register(
                    arg
                ): 

                    unversioned_uses.append(
                        (
                            block.id, 
                            op.address, 
                            arg, 
                        )
                    )


            if op.kind == "BRANCH": 

                for match in (
                    REGISTER_IN_EXPR_RE.finditer(
                        op.comment
                    )
                ): 

                    unversioned_uses.append(
                        (
                            block.id, 
                            op.address, 
                            match.group(0), 
                        )
                    )


    # Remove registers that got no explicit definition
    # from summary output.

    versions_per_register = {
        reg: count

        for reg, count
        in versions_per_register.items()

        if count > 0
    }


    return SSAResult(
        blocks = blocks, 

        entry_block_id = entry, 

        phi_count = phi_count, 

        definitions = definitions, 

        versions_per_register = 
            versions_per_register, 

        unversioned_defs = 
            unversioned_defs, 

        unversioned_uses = 
            unversioned_uses, 
    )