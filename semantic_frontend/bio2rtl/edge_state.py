from __future__ import annotations

from dataclasses import dataclass, field

from .ir import IRBlock

from .final_state import FinalStateResult

from .logical_state import stack_state_base


@dataclass
class EdgeStateWrite: 
    source_block: int
    target_block: int

    family: str

    phi_destination: str
    source_value: str


@dataclass
class EdgeStateIR: 
    writes: list[EdgeStateWrite]

    by_edge: dict[
        tuple[int, int], 
        list[EdgeStateWrite], 
    ]

    written_families: set[str]

    edges_with_writes: int


def family_of_value(
    value: str | None, 
    final_state: FinalStateResult, 
) -> str | None: 

    if value is None: 
        return None

    family = stack_state_base(value)

    if (
        family is not None
        and family in final_state.states
    ): 
        return family

    return None


def parse_phi_argument(
    arg: object, 
) -> tuple[int, str] | None: 
    """
    Parse:

        BB5=stack_main_sp_m16_3

    into:

        (5, "stack_main_sp_m16_3")
    """

    if not isinstance(arg, str): 
        return None

    if "=" not in arg: 
        return None

    pred_text, value = arg.split(
        "=", 
        1, 
    )

    pred_text = pred_text.strip()
    value = value.strip()

    if not pred_text.startswith("BB"): 
        return None

    try: 
        predecessor = int(
            pred_text[2:]
        )
    except ValueError: 
        return None

    return predecessor, value


def build_edge_state_ir(
    blocks: list[IRBlock], 
    final_state: FinalStateResult, 
) -> EdgeStateIR: 

    writes: list[EdgeStateWrite] = []

    by_edge: dict[
        tuple[int, int], 
        list[EdgeStateWrite], 
    ] = {}

    written_families: set[str] = set()

    block_ids = {
        block.id
        for block in blocks
    }

    for block in blocks: 

        target_block = block.id

        for op in block.ops: 

            if op.kind != "PHI": 
                continue

            family = family_of_value(
                op.dst, 
                final_state, 
            )

            # Only PHIs belonging to final hardware DFFs
            # matter here.
            if family is None: 
                continue

            if op.dst is None: 
                raise RuntimeError(
                    f"BB{target_block}: "
                    "PHI without destination"
                )

            for arg in op.args: 

                parsed = parse_phi_argument(
                    arg
                )

                if parsed is None: 
                    raise RuntimeError(
                        f"BB{target_block}: "
                        f"cannot parse PHI argument "
                        f"{arg!r}"
                    )

                predecessor, source_value = (
                    parsed
                )

                if predecessor not in block_ids: 
                    raise RuntimeError(
                        f"BB{target_block}: "
                        f"PHI predecessor "
                        f"BB{predecessor} "
                        "does not exist"
                    )

                edge = (
                    predecessor, 
                    target_block, 
                )

                write = EdgeStateWrite(
                    source_block = 
                        predecessor, 

                    target_block = 
                        target_block, 

                    family = 
                        family, 

                    phi_destination = 
                        op.dst, 

                    source_value = 
                        source_value, 
                )

                writes.append(
                    write
                )

                by_edge.setdefault(
                    edge, 
                    [], 
                ).append(
                    write
                )

                written_families.add(
                    family
                )

    return EdgeStateIR(
        writes = writes, 

        by_edge = by_edge, 

        written_families = 
            written_families, 

        edges_with_writes = 
            len(by_edge), 
    )