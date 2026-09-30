from __future__ import annotations

from dataclasses import dataclass, field

from .ir import (
    IRBlock, 
)


# ============================================================
# Semantic boundary kinds
# ============================================================


INPUT_BOUNDARY_KINDS = {
    "GPIO_READ", 
}


OUTPUT_BOUNDARY_KINDS = {
    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 
}


# ============================================================
# Result structures
# ============================================================


@dataclass(frozen = True)
class SemanticBoundary: 
    block_id: int

    reads_external_input: bool
    writes_external_output: bool

    input_kinds: tuple[str, ...]
    output_kinds: tuple[str, ...]


@dataclass
class SemanticBoundaryResult: 
    boundaries: dict[
        int, 
        SemanticBoundary, 
    ]

    input_blocks: set[int] = field(
        default_factory = set
    )

    output_blocks: set[int] = field(
        default_factory = set
    )


# ============================================================
# Analysis
# ============================================================


def analyze_semantic_boundaries(
    blocks: list[IRBlock], 
) -> SemanticBoundaryResult: 

    boundaries: dict[
        int, 
        SemanticBoundary, 
    ] = {}

    input_blocks: set[int] = set()
    output_blocks: set[int] = set()


    for block in blocks: 

        input_kinds: set[str] = set()
        output_kinds: set[str] = set()


        for op in block.ops: 

            if op.kind in INPUT_BOUNDARY_KINDS: 

                input_kinds.add(
                    op.kind
                )


            if op.kind in OUTPUT_BOUNDARY_KINDS: 

                output_kinds.add(
                    op.kind
                )


        reads_external_input = bool(
            input_kinds
        )

        writes_external_output = bool(
            output_kinds
        )


        if (
            not reads_external_input
            and
            not writes_external_output
        ): 
            continue


        boundary = SemanticBoundary(
            block_id = 
                block.id, 

            reads_external_input = 
                reads_external_input, 

            writes_external_output = 
                writes_external_output, 

            input_kinds = 
                tuple(
                    sorted(
                        input_kinds
                    )
                ), 

            output_kinds = 
                tuple(
                    sorted(
                        output_kinds
                    )
                ), 
        )


        boundaries[
            block.id
        ] = boundary


        if reads_external_input: 

            input_blocks.add(
                block.id
            )


        if writes_external_output: 

            output_blocks.add(
                block.id
            )


    return SemanticBoundaryResult(
        boundaries = 
            boundaries, 

        input_blocks = 
            input_blocks, 

        output_blocks = 
            output_blocks, 
    )


# ============================================================
# Report
# ============================================================


def print_semantic_boundaries(
    result: SemanticBoundaryResult, 
) -> None: 

    print()
    print(
        "=" * 72
    )

    print(
        "SEMANTIC TIME BOUNDARIES"
    )

    print(
        "=" * 72
    )


    for block_id, boundary in sorted(
        result.boundaries.items()
    ): 

        kinds: list[str] = []


        if boundary.input_kinds: 

            kinds.append(
                "INPUT="
                + ",".join(
                    boundary.input_kinds
                )
            )


        if boundary.output_kinds: 

            kinds.append(
                "OUTPUT="
                + ",".join(
                    boundary.output_kinds
                )
            )


        print(
            f"BB{block_id:03d} "
            + " ".join(
                kinds
            )
        )


    print()
    print(
        "=" * 72
    )

    print(
        "SEMANTIC TIME BOUNDARY SUMMARY"
    )

    print(
        "=" * 72
    )


    print(
        "boundary blocks : "
        f"{len(result.boundaries)}"
    )

    print(
        "GPIO read blocks: "
        f"{len(result.input_blocks)}"
    )

    print(
        "GPIO write blocks: "
        f"{len(result.output_blocks)}"
    )


    print()

    if result.input_blocks: 

        print(
            "SEMANTIC TIME BOUNDARY: PASS"
        )

    else: 

        print(
            "SEMANTIC TIME BOUNDARY: FAIL"
        )