from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)

from .ssa_opt import (
    values_used_by_op, 
    VALUE_IN_EXPR_RE, 
)

from .logical_state import (
    stack_state_base, 
)

from .dff_width_infer import (
    eval_constant, 
)


# ============================================================
# Structures
# ============================================================

@dataclass
class FamilyUpdateSite: 
    family: str

    block_id: int
    address: int | None

    destination: str
    kind: str

    args: list[str]


@dataclass
class FamilyLoopBound: 
    family: str

    update_sites: list[
        FamilyUpdateSite
    ] = field(
        default_factory = list
    )

    branch_sites: list[
        FamilyUpdateSite
    ] = field(
        default_factory = list
    )

    initial_constants: set[int] = field(
        default_factory = set
    )

    increment_constants: set[int] = field(
        default_factory = set
    )

    decrement_constants: set[int] = field(
        default_factory = set
    )

    shift_left_amounts: set[int] = field(
        default_factory = set
    )

    shift_right_amounts: set[int] = field(
        default_factory = set
    )


@dataclass
class LoopBoundAnalysis: 
    families: dict[
        str, 
        FamilyLoopBound
    ]


def branch_values(
    op: IROp, 
) -> set[str]: 
    """
    BRANCH condition operands are stored in op.comment.

    Example:

        stack_main_sp_m16_7 != 0
        x10_12 != 0

    Extract SSA values from the expression.
    """

    if op.kind != "BRANCH": 
        return set()

    return {
        match.group(0)

        for match in (
            VALUE_IN_EXPR_RE.finditer(
                op.comment
            )
        )
    }


# ============================================================
# Definition map
# ============================================================

def build_definition_map(
    blocks: list[IRBlock], 
) -> dict[str, tuple[int, IROp]]: 

    result = {}

    for block in blocks: 

        for op in block.ops: 

            if op.dst is None: 
                continue

            result[
                op.dst
            ] = (
                block.id, 
                op, 
            )

    return result


# ============================================================
# Recursive dependency check
# ============================================================

def depends_on_family(
    value: str, 
    family: str, 
    definition_map: 
        dict[str, tuple[int, IROp]], 
    visited: set[str], 
) -> bool: 

    if (
        stack_state_base(
            value
        )
        == family
    ): 
        return True


    if value in visited: 
        return False


    visited = set(
        visited
    )

    visited.add(
        value
    )


    definition = (
        definition_map.get(
            value
        )
    )


    if definition is None: 
        return False


    _, op = definition


    dependencies = set(
        values_used_by_op(
            op
        )
    )


    if op.kind == "BRANCH": 

        dependencies.update(
            branch_values(
                op
            )
        )


    for dependency in dependencies: 

        if depends_on_family(
            dependency, 
            family, 
            definition_map, 
            visited, 
        ): 

            return True


    return False


# ============================================================
# Trace state-definition sources
# ============================================================

def analyze_family_loop_bounds(
    blocks: list[IRBlock], 
    families: set[str], 
) -> LoopBoundAnalysis: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    result = {
        family: 
            FamilyLoopBound(
                family = family
            )

        for family in families
    }


    # ========================================================
    # Inspect definitions whose destination belongs to family
    # ========================================================

    for block in blocks: 

        for op in block.ops: 

            if op.dst is None: 
                continue


            family = (
                stack_state_base(
                    op.dst
                )
            )


            if family not in result: 
                continue


            info = result[
                family
            ]


            # ------------------------------------------------
            # PHI itself is not the arithmetic update.
            # ------------------------------------------------

            if op.kind == "PHI": 
                continue


            site = FamilyUpdateSite(
                family = family, 

                block_id = 
                    block.id, 

                address = 
                    op.address, 

                destination = 
                    op.dst, 

                kind = 
                    op.kind, 

                args = list(
                    op.args
                ), 
            )


            info.update_sites.append(
                site
            )


    # ========================================================
    # Search whole datapath for operations that feed family
    # updates.
    # ========================================================

    for family, info in (
        result.items()
    ): 

        for block in blocks: 

            for op in block.ops: 

                # --------------------------------------------
                # Branches do not have a destination.  They
                # must therefore be inspected BEFORE the
                # generic ``op.dst is None`` filter below.
                #
                # The previous implementation skipped every
                # BRANCH here, which meant branch_sites was
                # always empty even when dff-width inference
                # had already observed a ZERO guard.
                # --------------------------------------------

                if op.kind == "BRANCH": 

                    dependencies = branch_values(
                        op
                    )

                    if any(
                        depends_on_family(
                            value, 
                            family, 
                            definition_map, 
                            set(), 
                        )

                        for value
                        in dependencies
                    ): 

                        info.branch_sites.append(
                            FamilyUpdateSite(
                                family = family, 
                                block_id = block.id, 
                                address = op.address, 
                                destination = "-", 
                                kind = op.kind, 
                                args = [op.comment], 
                            )
                        )

                    # A branch is control only; it cannot be
                    # one of the arithmetic/shift definitions
                    # handled below.
                    continue


                if op.dst is None: 
                    continue


                # --------------------------------------------
                # Does this intermediate value depend on
                # current family value?
                # --------------------------------------------

                family_dependent = any(
                    depends_on_family(
                        arg, 
                        family, 
                        definition_map, 
                        set(), 
                    )

                    for arg in op.args
                )


                # --------------------------------------------
                # ADD state +/- constant
                # --------------------------------------------

                if (
                    op.kind == "ADD"
                    and family_dependent
                ): 

                    constants = [
                        eval_constant(
                            arg
                        )

                        for arg in op.args
                    ]


                    constants = [
                        value
                        for value in constants
                        if value is not None
                    ]


                    for constant in constants: 

                        if constant > 0: 

                            info.increment_constants.add(
                                constant
                            )

                        elif constant < 0: 

                            info.decrement_constants.add(
                                -constant
                            )


                # --------------------------------------------
                # Shift
                # --------------------------------------------

                if (
                    op.kind == "SHL"
                    and family_dependent
                    and len(op.args) >= 2
                ): 

                    amount = eval_constant(
                        op.args[1]
                    )


                    if (
                        amount is not None
                        and amount >= 0
                    ): 

                        info.shift_left_amounts.add(
                            amount
                        )


                if (
                    op.kind == "SHR"
                    and family_dependent
                    and len(op.args) >= 2
                ): 

                    amount = eval_constant(
                        op.args[1]
                    )


                    if (
                        amount is not None
                        and amount >= 0
                    ): 

                        info.shift_right_amounts.add(
                            amount
                        )


        # ====================================================
        # Find direct constants entering family PHIs
        # ====================================================

        for block in blocks: 

            for op in block.ops: 

                if (
                    op.kind != "PHI"
                    or op.dst is None
                    or stack_state_base(
                        op.dst
                    )
                    != family
                ): 
                    continue


                for arg in op.args: 

                    if "=" not in arg: 
                        continue


                    _, value = arg.split(
                        "=", 
                        1, 
                    )


                    constant = eval_constant(
                        value
                    )


                    if constant is not None: 

                        info.initial_constants.add(
                            constant
                        )


                    definition = (
                        definition_map.get(
                            value
                        )
                    )


                    if definition is None: 
                        continue


                    _, source_op = definition


                    if (
                        source_op.kind
                        == "CONST"
                        and source_op.args
                    ): 

                        constant = eval_constant(
                            source_op.args[0]
                        )


                        if constant is not None: 

                            info.initial_constants.add(
                                constant
                            )


    return LoopBoundAnalysis(
        families = result
    )