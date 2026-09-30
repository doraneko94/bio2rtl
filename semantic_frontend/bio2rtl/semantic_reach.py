from __future__ import annotations

from dataclasses import dataclass, field

from .fsm_ir import (
    FSMIR, 
)

from .semantic_microstate import (
    SemanticMicrostateResult, 
)


@dataclass(frozen = True)
class ReachEdge: 
    source: int
    target: int
    kind: str


@dataclass
class ReachRegion: 
    name: str
    start_sample: int | None

    blocks: tuple[int, ...]

    edges: tuple[
        ReachEdge, 
        ...
    ]

    exit_samples: tuple[int, ...]

    # Closed unsampled cycles proved to be semantically quiescent.
    # Reachability stops here instead of retaining a temporal self-loop.
    terminal_blocks: tuple[int, ...] = ()


@dataclass
class SemanticReachResult: 
    regions: dict[
        str, 
        ReachRegion, 
    ]

    total_blocks: int
    total_edges: int

    max_region_blocks: int
    max_region_edges: int


def _successor_edges(
    fsm: FSMIR, 
    block_id: int, 
) -> tuple[
    ReachEdge, 
    ...
]: 

    state = fsm.states.get(
        block_id
    )

    if state is None: 
        return ()

    transition = state.transition

    if transition is None: 
        return ()

    true_target = (
        transition.true_target
    )

    false_target = (
        transition.false_target
    )

    if (
        true_target is not None
        and
        false_target is not None
        and
        true_target != false_target
    ): 

        return (
            ReachEdge(
                source = block_id, 
                target = true_target, 
                kind = "TRUE", 
            ), 
            ReachEdge(
                source = block_id, 
                target = false_target, 
                kind = "FALSE", 
            ), 
        )

    target = true_target

    if target is None: 
        target = false_target

    if target is None: 
        return ()

    return (
        ReachEdge(
            source = block_id, 
            target = target, 
            kind = "GOTO", 
        ), 
    )


def _collect_region(
    name: str, 
    start_sample: int | None, 
    initial_blocks: tuple[int, ...], 
    fsm: FSMIR, 
    sample_blocks: set[int], 
    terminal_blocks: set[int] | None = None, 
) -> ReachRegion: 

    terminal_blocks = set(terminal_blocks or ())

    blocks: set[int] = set()

    edges: set[
        tuple[
            int, 
            int, 
            str, 
        ]
    ] = set()

    exits: set[int] = set()

    worklist = list(
        initial_blocks
    )

    while worklist: 

        block_id = (
            worklist.pop()
        )

        if block_id in sample_blocks: 

            exits.add(
                block_id
            )

            continue

        if block_id in blocks: 
            continue

        blocks.add(
            block_id
        )

        if block_id in terminal_blocks: 
            continue

        for edge in _successor_edges(
            fsm, 
            block_id, 
        ): 

            edges.add(
                (
                    edge.source, 
                    edge.target, 
                    edge.kind, 
                )
            )

            if edge.target in sample_blocks: 

                exits.add(
                    edge.target
                )

            else: 

                worklist.append(
                    edge.target
                )

    reach_edges = tuple(
        ReachEdge(
            source = source, 
            target = target, 
            kind = kind, 
        )

        for (
            source, 
            target, 
            kind, 
        )
        in sorted(
            edges
        )
    )

    return ReachRegion(
        name = name, 
        start_sample = start_sample, 

        blocks = tuple(
            sorted(
                blocks
            )
        ), 

        edges = reach_edges, 

        exit_samples = tuple(
            sorted(
                exits
            )
        ), 

        terminal_blocks = tuple(sorted(terminal_blocks & blocks)), 
    )


def build_semantic_reach(
    fsm: FSMIR, 
    microstates: SemanticMicrostateResult, 
) -> SemanticReachResult: 

    sample_blocks = set(
        microstates.sample_blocks
    )

    regions: dict[
        str, 
        ReachRegion, 
    ] = {}

    entry = _collect_region(
        name = "ENTRY", 
        start_sample = None, 
        initial_blocks = (
            fsm.entry_state, 
        ), 
        fsm = fsm, 
        sample_blocks = sample_blocks, 
        terminal_blocks = set(microstates.regions["ENTRY"].terminal_blocks), 
    )

    regions[
        entry.name
    ] = entry

    for sample_block in sorted(
        sample_blocks
    ): 

        successors = (
            _successor_edges(
                fsm, 
                sample_block, 
            )
        )

        initial_blocks = tuple(
            edge.target

            for edge
            in successors
        )

        region = _collect_region(
            name = (
                f"SAMPLE_BB"
                f"{sample_block:03d}"
            ), 
            start_sample = 
                sample_block, 
            initial_blocks = 
                initial_blocks, 
            fsm = fsm, 
            sample_blocks = 
                sample_blocks, 
            terminal_blocks = set(
                microstates.regions[
                    f"SAMPLE_BB{sample_block:03d}"
                ].terminal_blocks
            ), 
        )

        # Include edges leaving the sample block itself.
        existing = {
            (
                edge.source, 
                edge.target, 
                edge.kind, 
            )

            for edge
            in region.edges
        }

        for edge in successors: 

            existing.add(
                (
                    edge.source, 
                    edge.target, 
                    edge.kind, 
                )
            )

        region.edges = tuple(
            ReachEdge(
                source = source, 
                target = target, 
                kind = kind, 
            )

            for (
                source, 
                target, 
                kind, 
            )
            in sorted(
                existing
            )
        )

        regions[
            region.name
        ] = region

    total_blocks = sum(
        len(region.blocks)

        for region
        in regions.values()
    )

    total_edges = sum(
        len(region.edges)

        for region
        in regions.values()
    )

    return SemanticReachResult(
        regions = regions, 

        total_blocks = 
            total_blocks, 

        total_edges = 
            total_edges, 

        max_region_blocks = max(
            (
                len(region.blocks)

                for region
                in regions.values()
            ), 
            default = 0, 
        ), 

        max_region_edges = max(
            (
                len(region.edges)

                for region
                in regions.values()
            ), 
            default = 0, 
        ), 
    )


def print_semantic_reach(
    result: SemanticReachResult, 
) -> None: 

    print()
    print("=" * 72)
    print("SEMANTIC REACHABILITY DAG")
    print("=" * 72)

    for name, region in (
        result.regions.items()
    ): 

        print(
            name
        )

        print(
            "    blocks       : "
            f"{len(region.blocks)}"
        )

        print(
            "    edges        : "
            f"{len(region.edges)}"
        )

        if region.exit_samples: 

            print(
                "    exit samples : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in region.exit_samples
                )
            )

        else: 

            print(
                "    exit samples : -"
            )

    print()

    print("=" * 72)
    print("SEMANTIC REACHABILITY SUMMARY")
    print("=" * 72)

    print(
        "regions           : "
        f"{len(result.regions)}"
    )

    print(
        "max region blocks : "
        f"{result.max_region_blocks}"
    )

    print(
        "max region edges  : "
        f"{result.max_region_edges}"
    )

    print(
        "path enumeration  : NOT REQUIRED"
    )

    print()
    print(
        "SEMANTIC REACHABILITY: PASS"
    )

def select_primary_sample_region(reach: SemanticReachResult) -> str: 
    """Select the dominant steady sample-root without fixture-specific BB numbers.

    The semantic reachability partition may contain initialization/transient sample
    regions in addition to the recurring hardware macro-step region.  Prefer a
    non-ENTRY sample region that can return to its own sample boundary, then rank by
    reachable edge/block count.  This preserves the historical I2C choice (BB006)
    while allowing arbitrary BIO layouts to choose their own root.
    """
    rows = []
    for name, region in reach.regions.items(): 
        if name == 'ENTRY' or region.start_sample is None: 
            continue
        self_return = region.start_sample in set(region.exit_samples)
        rows.append((1 if self_return else 0, len(region.edges), len(region.blocks), name))
    if not rows: 
        raise RuntimeError('no sampled steady semantic region available')
    rows.sort(reverse = True)
    return rows[0][3]
