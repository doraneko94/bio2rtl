from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)

from .ssa_opt import (
    parse_phi_arg, 
    values_used_by_op, 
)

from .logical_state import (
    register_state_base, 
    stack_state_base, 
)

from .observable_cone import (
    ObservableConeResult, 
)


# ============================================================
# Structures
# ============================================================

@dataclass
class UpdateLeaf: 
    value: str
    kind: str
    # STACK
    # GPIO
    # CONST
    # ZERO
    # LIVEIN
    # REGISTER_OTHER
    # UNKNOWN

    family: str | None = None


@dataclass
class RegisterUpdateAnalysis: 
    family: str

    state_values: list[str]

    nonself_sources: set[str] = field(
        default_factory = set
    )

    leaves: list[UpdateLeaf] = field(
        default_factory = list
    )

    stack_dependencies: set[str] = field(
        default_factory = set
    )

    register_dependencies: set[str] = field(
        default_factory = set
    )

    has_gpio: bool = False
    has_constant: bool = False
    has_livein: bool = False
    has_unknown: bool = False


@dataclass
class RegisterUpdateResult: 
    analyses: dict[
        str, 
        RegisterUpdateAnalysis
    ]

    carrier_candidates: set[str]

    true_state_candidates: set[str]

    unknown_values: list[
        tuple[str, str]
    ]


# ============================================================
# Definition map
# ============================================================

def build_definition_map(
    blocks: list[IRBlock], 
) -> dict[str, IROp]: 

    result = {}

    for block in blocks: 

        for op in block.ops: 

            if op.dst is not None: 

                result[
                    op.dst
                ] = op

    return result


# ============================================================
# Recursive source tracing
# ============================================================

def trace_nonself_source(
    value: str, 
    root_family: str, 
    definition_map: dict[str, IROp], 
    visited: set[str], 
) -> list[UpdateLeaf]: 

    # --------------------------------------------------------
    # Zero
    # --------------------------------------------------------

    if value == "x0": 

        return [
            UpdateLeaf(
                value = value, 
                kind = "ZERO", 
            )
        ]


    # --------------------------------------------------------
    # Stack state
    # --------------------------------------------------------

    stack_family = (
        stack_state_base(
            value
        )
    )

    if stack_family is not None: 

        return [
            UpdateLeaf(
                value = value, 
                kind = "STACK", 
                family = stack_family, 
            )
        ]


    # --------------------------------------------------------
    # Register SSA value
    # --------------------------------------------------------

    register_family = (
        register_state_base(
            value
        )
    )


    # Same family:
    #
    # Do NOT stop here.
    #
    # Follow its definition so that:
    #
    #   x10_phi = PHI(x10_phi, x8)
    #
    # eventually reduces to x8 rather than being declared
    # persistent merely because of the self edge.

    if (
        register_family == root_family
        and value in visited
    ): 

        # True recursion through exactly the same SSA node.
        # This edge contributes no new update source.

        return []


    # --------------------------------------------------------
    # Live-in
    # --------------------------------------------------------

    if value.endswith(
        "_0"
    ): 

        return [
            UpdateLeaf(
                value = value, 
                kind = "LIVEIN", 
                family = register_family, 
            )
        ]


    # --------------------------------------------------------
    # Other register family
    # --------------------------------------------------------

    if (
        register_family is not None
        and register_family
        != root_family
    ): 

        return [
            UpdateLeaf(
                value = value, 
                kind = "REGISTER_OTHER", 
                family = register_family, 
            )
        ]


    # --------------------------------------------------------
    # Cycle guard
    # --------------------------------------------------------

    if value in visited: 

        return []


    new_visited = set(
        visited
    )

    new_visited.add(
        value
    )


    op = definition_map.get(
        value
    )


    if op is None: 

        return [
            UpdateLeaf(
                value = value, 
                kind = "UNKNOWN", 
            )
        ]


    # --------------------------------------------------------
    # Constants
    # --------------------------------------------------------

    if op.kind == "CONST": 

        return [
            UpdateLeaf(
                value = value, 
                kind = "CONST", 
            )
        ]


    # --------------------------------------------------------
    # GPIO input
    # --------------------------------------------------------

    if op.kind == "GPIO_READ": 

        return [
            UpdateLeaf(
                value = value, 
                kind = "GPIO", 
            )
        ]


    # --------------------------------------------------------
    # PHI
    # --------------------------------------------------------

    dependencies = []


    if op.kind == "PHI": 

        for arg in op.args: 

            parsed = parse_phi_arg(
                arg
            )

            if parsed is None: 
                continue

            _, incoming = parsed


            # Explicit self input:
            #
            #   dst = PHI(..., dst)
            #
            # means HOLD, not a new state source.

            if incoming == value: 
                continue


            dependencies.append(
                incoming
            )


    else: 

        dependencies.extend(
            sorted(
                values_used_by_op(
                    op
                )
            )
        )


    if not dependencies: 

        return []


    result = []


    for dependency in dependencies: 

        result.extend(
            trace_nonself_source(
                dependency, 
                root_family, 
                definition_map, 
                new_visited, 
            )
        )


    return result


# ============================================================
# Main analysis
# ============================================================

def analyze_register_updates(
    blocks: list[IRBlock], 
    observable: 
        ObservableConeResult, 
) -> RegisterUpdateResult: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    analyses = {}

    unknown_values = []


    for family in sorted(
        observable.live_register_families, 
        key = lambda value: 
            int(value[1:]), 
    ): 

        state_values = sorted(
            value

            for value
            in (
                observable
                .live_state_phi_destinations
            )

            if (
                register_state_base(
                    value
                )
                == family
            )
        )


        analysis = (
            RegisterUpdateAnalysis(
                family = family, 
                state_values = 
                    state_values, 
            )
        )


        leaves = []


        for value in state_values: 

            op = definition_map.get(
                value
            )


            if (
                op is None
                or op.kind != "PHI"
            ): 
                continue


            for arg in op.args: 

                parsed = parse_phi_arg(
                    arg
                )

                if parsed is None: 
                    continue


                _, incoming = parsed


                # ------------------------------------------------
                # This is the key rule:
                #
                # self-carried incoming value means HOLD.
                # It is NOT an independent update source.
                # ------------------------------------------------

                # --------------------------------------------
                # Only an exact PHI self-reference is HOLD.
                #
                # Same-family but different SSA values must
                # still be traced.
                #
                # Example:
                #
                #   x7_4 = PHI(BB5=x7_3, BB16=x7_4)
                #
                # x7_4 is HOLD, but x7_3 may be:
                #
                #   x7_3 := 1
                #
                # and therefore is a real update/initial source.
                # --------------------------------------------

                if incoming == value: 
                    continue


                analysis.nonself_sources.add(
                    incoming
                )


                leaves.extend(
                    trace_nonself_source(
                        incoming, 
                        family, 
                        definition_map, 
                        {
                            value
                        }, 
                    )
                )


        # Deduplicate.

        unique = {}


        for leaf in leaves: 

            key = (
                leaf.value, 
                leaf.kind, 
                leaf.family, 
            )

            unique[key] = leaf


        analysis.leaves = list(
            unique.values()
        )


        for leaf in analysis.leaves: 

            if leaf.kind == "STACK": 

                if leaf.family is not None: 

                    analysis.stack_dependencies.add(
                        leaf.family
                    )


            elif leaf.kind == "REGISTER_OTHER": 

                if leaf.family is not None: 

                    analysis.register_dependencies.add(
                        leaf.family
                    )


            elif leaf.kind == "GPIO": 

                analysis.has_gpio = True


            elif leaf.kind in {
                "CONST", 
                "ZERO", 
            }: 

                analysis.has_constant = True


            elif leaf.kind == "LIVEIN": 

                analysis.has_livein = True


            elif leaf.kind == "UNKNOWN": 

                analysis.has_unknown = True

                unknown_values.append(
                    (
                        family, 
                        leaf.value, 
                    )
                )


        analyses[
            family
        ] = analysis


    # ========================================================
    # Classification
    #
    # carrier_candidate:
    #
    # register family has actual incoming values generated
    # elsewhere, but no independent unknown state.
    #
    # true_state_candidate:
    #
    # needs an unexplained live-in or unknown value.
    # ========================================================

    carrier_candidates = set()

    true_state_candidates = set()


    for family, analysis in (
        analyses.items()
    ): 

        if (
            analysis.has_unknown
            or analysis.has_livein
        ): 

            true_state_candidates.add(
                family
            )

        else: 

            carrier_candidates.add(
                family
            )


    return RegisterUpdateResult(
        analyses = analyses, 

        carrier_candidates = 
            carrier_candidates, 

        true_state_candidates = 
            true_state_candidates, 

        unknown_values = 
            unknown_values, 
    )