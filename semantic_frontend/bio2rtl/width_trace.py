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
    stack_state_base, 
)


@dataclass
class DefinitionTrace: 
    value: str

    block_id: int | None
    address: int | None

    kind: str

    args: list[str] = field(
        default_factory = list
    )


@dataclass
class FamilyDefinitionTrace: 
    family: str

    phi_values: list[str]

    source_values: set[str]

    definitions: list[
        DefinitionTrace
    ]

    unresolved_values: list[str]


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


def trace_family_definitions(
    blocks: list[IRBlock], 
    family: str, 
) -> FamilyDefinitionTrace: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    # ========================================================
    # Find every PHI belonging to this logical family
    # ========================================================

    phi_values = []


    for block in blocks: 

        for op in block.ops: 

            if (
                op.kind == "PHI"
                and op.dst is not None
                and stack_state_base(
                    op.dst
                )
                == family
            ): 

                phi_values.append(
                    op.dst
                )


    # ========================================================
    # Collect every incoming value that is NOT merely another
    # PHI version of the same family.
    # ========================================================

    source_values = set()


    for phi_value in phi_values: 

        definition = (
            definition_map.get(
                phi_value
            )
        )

        if definition is None: 
            continue


        _, op = definition


        for arg in op.args: 

            parsed = parse_phi_arg(
                arg
            )

            if parsed is None: 
                continue


            _, incoming = parsed


            incoming_family = (
                stack_state_base(
                    incoming
                )
            )


            if incoming_family == family: 
                continue


            source_values.add(
                incoming
            )


    # ========================================================
    # Recursively inspect source definitions
    # ========================================================

    traces = []

    unresolved = []


    visited = set()


    def walk(
        value: str, 
    ): 

        if value in visited: 
            return


        visited.add(
            value
        )


        if value == "x0": 

            traces.append(
                DefinitionTrace(
                    value = value, 
                    block_id = None, 
                    address = None, 
                    kind = "ZERO", 
                    args = [], 
                )
            )

            return


        if value.endswith(
            "_0"
        ): 

            traces.append(
                DefinitionTrace(
                    value = value, 
                    block_id = None, 
                    address = None, 
                    kind = "LIVEIN", 
                    args = [], 
                )
            )

            return


        definition = (
            definition_map.get(
                value
            )
        )


        if definition is None: 

            unresolved.append(
                value
            )

            return


        block_id, op = definition


        traces.append(
            DefinitionTrace(
                value = value, 
                block_id = block_id, 
                address = op.address, 
                kind = op.kind, 
                args = list(
                    op.args
                ), 
            )
        )


        # Stop at external/state boundaries.

        if op.kind in {
            "CONST", 
            "GPIO_READ", 
        }: 
            return


        if op.kind == "PHI": 

            for arg in op.args: 

                parsed = parse_phi_arg(
                    arg
                )

                if parsed is None: 
                    continue


                _, dependency = parsed


                # Ignore same logical-family feedback.
                if (
                    stack_state_base(
                        dependency
                    )
                    == family
                ): 
                    continue


                walk(
                    dependency
                )

            return


        for dependency in (
            values_used_by_op(
                op
            )
        ): 

            walk(
                dependency
            )


    for value in sorted(
        source_values
    ): 

        walk(
            value
        )


    return FamilyDefinitionTrace(
        family = family, 

        phi_values = 
            sorted(
                phi_values
            ), 

        source_values = 
            source_values, 

        definitions = 
            traces, 

        unresolved_values = 
            sorted(
                unresolved
            ), 
    )