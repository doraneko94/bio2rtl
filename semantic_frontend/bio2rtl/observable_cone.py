from dataclasses import dataclass, field

from .ir import (
    IROp, 
    IRBlock, 
)

from .ssa_opt import (
    is_ssa_value, 
    values_used_by_op, 
)

from .state_extract import (
    StateExtractionResult, 
)

from .logical_state import (
    stack_state_base, 
    register_state_base, 
)


# ============================================================
# Observable operations
# ============================================================

OBSERVABLE_KINDS = {
    # Actual BIO outputs
    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 

    # Control decisions affect externally visible behavior.
    "BRANCH", 
}


# ============================================================
# Structures
# ============================================================

@dataclass
class ObservableRoot: 
    block_id: int
    address: int | None
    kind: str

    values: list[str]


@dataclass
class ObservableConeResult: 
    roots: list[ObservableRoot]

    root_values: set[str]

    live_values: set[str]

    live_definition_values: set[str]

    live_state_phi_destinations: set[str]

    dead_state_phi_destinations: set[str]

    live_stack_families: set[str]

    live_register_families: set[str]

    dead_stack_families: set[str]

    dead_register_families: set[str]

    missing_definitions: list[str] = field(
        default_factory = list
    )


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

            if not is_ssa_value(
                op.dst
            ): 
                continue

            if op.dst in result: 

                raise RuntimeError(
                    "Duplicate SSA definition: "
                    f"{op.dst}"
                )

            result[
                op.dst
            ] = (
                block.id, 
                op, 
            )

    return result


# ============================================================
# Roots
# ============================================================

def find_observable_roots(
    blocks: list[IRBlock], 
) -> list[ObservableRoot]: 

    roots = []


    for block in blocks: 

        for op in block.ops: 

            if (
                op.kind
                not in OBSERVABLE_KINDS
            ): 
                continue


            values = sorted(
                values_used_by_op(
                    op
                )
            )


            roots.append(
                ObservableRoot(
                    block_id = 
                        block.id, 

                    address = 
                        op.address, 

                    kind = 
                        op.kind, 

                    values = 
                        values, 
                )
            )


    return roots


# ============================================================
# Backward dependency walk
# ============================================================

def trace_observable_cone(
    blocks: list[IRBlock], 
    extracted: StateExtractionResult, 
) -> ObservableConeResult: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    roots = find_observable_roots(
        blocks
    )


    root_values = set()


    for root in roots: 

        root_values.update(
            root.values
        )


    live_values = set()
    live_definition_values = set()

    missing_definitions = set()


    work = list(
        root_values
    )


    while work: 

        value = work.pop()


        if value in live_values: 
            continue


        live_values.add(
            value
        )


        # -----------------------------------------------
        # Version 0 is a legal live-in boundary.
        # -----------------------------------------------

        if value.endswith(
            "_0"
        ): 
            continue


        definition = (
            definition_map.get(
                value
            )
        )


        if definition is None: 

            missing_definitions.add(
                value
            )

            continue


        _, op = definition


        live_definition_values.add(
            value
        )


        for dependency in (
            values_used_by_op(
                op
            )
        ): 

            if (
                dependency
                not in live_values
            ): 

                work.append(
                    dependency
                )


    # ========================================================
    # STATE PHIs
    # ========================================================

    all_state_phi_destinations = {
        phi.destination

        for phi
        in extracted.state_phis
    }


    live_state_phi_destinations = (
        all_state_phi_destinations
        & live_values
    )


    dead_state_phi_destinations = (
        all_state_phi_destinations
        - live_state_phi_destinations
    )


    # ========================================================
    # Family classification
    # ========================================================

    all_stack_families = set()
    all_register_families = set()

    live_stack_families = set()
    live_register_families = set()


    for phi in (
        extracted.state_phis
    ): 

        destination = (
            phi.destination
        )


        stack_base = (
            stack_state_base(
                destination
            )
        )


        register_base = (
            register_state_base(
                destination
            )
        )


        if stack_base is not None: 

            all_stack_families.add(
                stack_base
            )

            if (
                destination
                in live_state_phi_destinations
            ): 

                live_stack_families.add(
                    stack_base
                )


        elif register_base is not None: 

            all_register_families.add(
                register_base
            )

            if (
                destination
                in live_state_phi_destinations
            ): 

                live_register_families.add(
                    register_base
                )


    dead_stack_families = (
        all_stack_families
        - live_stack_families
    )


    dead_register_families = (
        all_register_families
        - live_register_families
    )


    return ObservableConeResult(
        roots = roots, 

        root_values = 
            root_values, 

        live_values = 
            live_values, 

        live_definition_values = 
            live_definition_values, 

        live_state_phi_destinations = 
            live_state_phi_destinations, 

        dead_state_phi_destinations = 
            dead_state_phi_destinations, 

        live_stack_families = 
            live_stack_families, 

        live_register_families = 
            live_register_families, 

        dead_stack_families = 
            dead_stack_families, 

        dead_register_families = 
            dead_register_families, 

        missing_definitions = 
            sorted(
                missing_definitions
            ), 
    )