from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .fsm_ir import (
    FSMIR, 
)

from .semantic_microstate import (
    SemanticMicrostateResult, 
)

from .next_state_expr import (
    NextStateExprIR, 
)


# ============================================================
# Structures
# ============================================================


@dataclass(frozen = True)
class SemanticPathEdge: 
    source: int
    target: int

    # GOTO
    # TRUE
    # FALSE
    kind: str


@dataclass
class SemanticTransitionPath: 
    region_name: str

    start_sample: int | None
    target_sample: int | None

    blocks: tuple[int, ...]

    edges: tuple[
        SemanticPathEdge, 
        ...
    ]

    # Existing lowered next-state writes.
    #
    # These are NextStateWrite instances from next_state_expr.py.
    # Keep them intact here. The following lowering stage will
    # combine them under the accumulated path predicate.
    state_writes: tuple[
        Any, 
        ...
    ]

    # Existing GPIO-effect objects.
    #
    # gpio_effect.py already provides by_block. We deliberately
    # avoid guessing a result-class name here.
    gpio_effects: tuple[
        Any, 
        ...
    ]


@dataclass
class SemanticTransitionRegion: 
    name: str

    start_sample: int | None

    paths: list[
        SemanticTransitionPath
    ] = field(
        default_factory = list
    )


@dataclass
class SemanticTransitionResult: 
    regions: dict[
        str, 
        SemanticTransitionRegion, 
    ]

    total_paths: int

    max_path_blocks: int
    max_path_edges: int

    paths_with_state_write: int
    paths_with_gpio_effect: int

    target_samples: set[int]

    duplicate_cfg_paths: int


# ============================================================
# FSM helpers
# ============================================================


def _successor_edges(
    fsm: FSMIR, 
    state_id: int, 
) -> tuple[
    SemanticPathEdge, 
    ...
]: 

    state = fsm.states.get(
        state_id
    )

    if state is None: 
        return ()

    transition = state.transition

    if transition is None: 
        return ()

    result: list[
        SemanticPathEdge
    ] = []

    true_target = (
        transition.true_target
    )

    false_target = (
        transition.false_target
    )

    # --------------------------------------------------------
    # Two-way branch
    # --------------------------------------------------------

    if (
        true_target is not None
        and
        false_target is not None
        and
        true_target != false_target
    ): 

        result.append(
            SemanticPathEdge(
                source = state_id, 
                target = true_target, 
                kind = "TRUE", 
            )
        )

        result.append(
            SemanticPathEdge(
                source = state_id, 
                target = false_target, 
                kind = "FALSE", 
            )
        )

        return tuple(
            result
        )

    # --------------------------------------------------------
    # One-way transition
    # --------------------------------------------------------

    target = true_target

    if target is None: 
        target = false_target

    if target is None: 
        return ()

    result.append(
        SemanticPathEdge(
            source = state_id, 
            target = target, 
            kind = "GOTO", 
        )
    )

    return tuple(
        result
    )


# ============================================================
# Lowered effects along one CFG edge
# ============================================================


def _edge_state_writes(
    next_state_expr: NextStateExprIR, 
    source: int, 
    target: int, 
) -> tuple[Any, ...]: 

    writes = (
        next_state_expr
        .by_edge
        .get(
            (
                source, 
                target, 
            ), 
            [], 
        )
    )

    return tuple(
        writes
    )


def _block_gpio_effects(
    gpio_effects: Any, 
    block_id: int, 
) -> tuple[Any, ...]: 

    effects = (
        gpio_effects
        .by_block
        .get(
            block_id, 
            [], 
        )
    )

    return tuple(
        effects
    )


# ============================================================
# Path enumeration
# ============================================================


def _enumerate_region_paths(
    region_name: str, 
    start_sample: int | None, 
    initial_state: int, 
    fsm: FSMIR, 
    sample_blocks: set[int], 
    next_state_expr: NextStateExprIR, 
    gpio_effects: Any, 
    max_paths: int, 
) -> list[
    SemanticTransitionPath
]: 

    result: list[
        SemanticTransitionPath
    ] = []

    # Work item:
    #
    # current CFG block
    # visited blocks in this path
    # accumulated CFG edges
    # accumulated persistent-state writes
    # accumulated GPIO effects
    worklist: list[
        tuple[
            int, 
            tuple[int, ...], 
            tuple[
                SemanticPathEdge, 
                ...
            ], 
            tuple[Any, ...], 
            tuple[Any, ...], 
        ]
    ] = [
        (
            initial_state, 
            (), 
            (), 
            (), 
            (), 
        )
    ]

    while worklist: 

        (
            block_id, 
            blocks, 
            edges, 
            state_writes, 
            effects, 
        ) = worklist.pop()

        # ----------------------------------------------------
        # Reaching the next GPIO sample ends the semantic path.
        #
        # The GPIO_READ operation in the target block belongs to
        # the next semantic step, so that block is not included
        # in "blocks".
        # ----------------------------------------------------

        if block_id in sample_blocks: 

            result.append(
                SemanticTransitionPath(
                    region_name = 
                        region_name, 

                    start_sample = 
                        start_sample, 

                    target_sample = 
                        block_id, 

                    blocks = 
                        blocks, 

                    edges = 
                        edges, 

                    state_writes = 
                        state_writes, 

                    gpio_effects = 
                        effects, 
                )
            )

            if len(result) > max_paths: 

                raise RuntimeError(
                    f"{region_name}: "
                    f"semantic path count "
                    f"exceeded {max_paths}"
                )

            continue

        # ----------------------------------------------------
        # Internal cycles are forbidden here.
        #
        # semantic_microstate.py has already audited that none
        # exist. Keep this check so future programs cannot
        # silently turn an unsampled temporal loop into
        # combinational logic.
        # ----------------------------------------------------

        if block_id in blocks: 

            raise RuntimeError(
                f"{region_name}: "
                f"internal CFG cycle at "
                f"BB{block_id:03d}"
            )

        next_blocks = (
            blocks
            + (
                block_id, 
            )
        )

        next_effects = (
            effects
            + _block_gpio_effects(
                gpio_effects, 
                block_id, 
            )
        )

        successor_edges = (
            _successor_edges(
                fsm, 
                block_id, 
            )
        )

        # ----------------------------------------------------
        # Terminal path
        # ----------------------------------------------------

        if not successor_edges: 

            result.append(
                SemanticTransitionPath(
                    region_name = 
                        region_name, 

                    start_sample = 
                        start_sample, 

                    target_sample = 
                        None, 

                    blocks = 
                        next_blocks, 

                    edges = 
                        edges, 

                    state_writes = 
                        state_writes, 

                    gpio_effects = 
                        next_effects, 
                )
            )

            if len(result) > max_paths: 

                raise RuntimeError(
                    f"{region_name}: "
                    f"semantic path count "
                    f"exceeded {max_paths}"
                )

            continue

        # ----------------------------------------------------
        # Continue along every possible CFG edge
        # ----------------------------------------------------

        for edge in reversed(
            successor_edges
        ): 

            edge_writes = (
                _edge_state_writes(
                    next_state_expr, 
                    edge.source, 
                    edge.target, 
                )
            )

            worklist.append(
                (
                    edge.target, 

                    next_blocks, 

                    edges
                    + (
                        edge, 
                    ), 

                    state_writes
                    + edge_writes, 

                    next_effects, 
                )
            )

    return result


# ============================================================
# Whole-program construction
# ============================================================


def build_semantic_transitions(
    fsm: FSMIR, 
    microstates: SemanticMicrostateResult, 
    next_state_expr: NextStateExprIR, 
    gpio_effects: Any, 
    max_paths_per_region: int = 10000, 
) -> SemanticTransitionResult: 

    sample_blocks = set(
        microstates.sample_blocks
    )

    regions: dict[
        str, 
        SemanticTransitionRegion, 
    ] = {}

    all_paths: list[
        SemanticTransitionPath
    ] = []

    # --------------------------------------------------------
    # ENTRY region
    # --------------------------------------------------------

    entry_paths = (
        _enumerate_region_paths(
            region_name = "ENTRY", 
            start_sample = None, 
            initial_state = 
                fsm.entry_state, 
            fsm = fsm, 
            sample_blocks = 
                sample_blocks, 
            next_state_expr = 
                next_state_expr, 
            gpio_effects = 
                gpio_effects, 
            max_paths = 
                max_paths_per_region, 
        )
    )

    entry_region = (
        SemanticTransitionRegion(
            name = "ENTRY", 
            start_sample = None, 
            paths = entry_paths, 
        )
    )

    regions[
        "ENTRY"
    ] = entry_region

    all_paths.extend(
        entry_paths
    )

    # --------------------------------------------------------
    # GPIO-sample regions
    # --------------------------------------------------------

    for sample_block in sorted(
        sample_blocks
    ): 

        name = (
            f"SAMPLE_BB"
            f"{sample_block:03d}"
        )

        paths: list[
            SemanticTransitionPath
        ] = []

        successor_edges = (
            _successor_edges(
                fsm, 
                sample_block, 
            )
        )

        if not successor_edges: 

            raise RuntimeError(
                f"{name}: sample block "
                f"has no successor"
            )

        # Any GPIO effects in the sampling block itself occur
        # after that sample and therefore belong to this semantic
        # transition.
        sample_effects = (
            _block_gpio_effects(
                gpio_effects, 
                sample_block, 
            )
        )

        for initial_edge in successor_edges: 

            initial_writes = (
                _edge_state_writes(
                    next_state_expr, 
                    initial_edge.source, 
                    initial_edge.target, 
                )
            )

            subpaths = (
                _enumerate_region_paths(
                    region_name = name, 
                    start_sample = 
                        sample_block, 
                    initial_state = 
                        initial_edge.target, 
                    fsm = fsm, 
                    sample_blocks = 
                        sample_blocks, 
                    next_state_expr = 
                        next_state_expr, 
                    gpio_effects = 
                        gpio_effects, 
                    max_paths = 
                        max_paths_per_region, 
                )
            )

            for subpath in subpaths: 

                subpath.edges = (
                    (
                        initial_edge, 
                    )
                    + subpath.edges
                )

                subpath.state_writes = (
                    initial_writes
                    + subpath.state_writes
                )

                subpath.gpio_effects = (
                    sample_effects
                    + subpath.gpio_effects
                )

                paths.append(
                    subpath
                )

        if len(paths) > max_paths_per_region: 

            raise RuntimeError(
                f"{name}: "
                f"semantic path count "
                f"exceeded "
                f"{max_paths_per_region}"
            )

        region = (
            SemanticTransitionRegion(
                name = name, 
                start_sample = 
                    sample_block, 
                paths = paths, 
            )
        )

        regions[
            name
        ] = region

        all_paths.extend(
            paths
        )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    max_path_blocks = max(
        (
            len(path.blocks)

            for path
            in all_paths
        ), 
        default = 0, 
    )

    max_path_edges = max(
        (
            len(path.edges)

            for path
            in all_paths
        ), 
        default = 0, 
    )

    paths_with_state_write = sum(
        1

        for path
        in all_paths

        if path.state_writes
    )

    paths_with_gpio_effect = sum(
        1

        for path
        in all_paths

        if path.gpio_effects
    )

    target_samples = {
        path.target_sample

        for path
        in all_paths

        if path.target_sample
        is not None
    }

    # --------------------------------------------------------
    # Duplicate path sanity check
    # --------------------------------------------------------

    signatures: set[
        tuple[
            str, 
            tuple[
                tuple[
                    int, 
                    int, 
                    str, 
                ], 
                ...
            ], 
        ]
    ] = set()

    duplicate_cfg_paths = 0

    for path in all_paths: 

        signature = (
            path.region_name, 

            tuple(
                (
                    edge.source, 
                    edge.target, 
                    edge.kind, 
                )

                for edge
                in path.edges
            ), 
        )

        if signature in signatures: 

            duplicate_cfg_paths += 1

        else: 

            signatures.add(
                signature
            )

    return SemanticTransitionResult(
        regions = 
            regions, 

        total_paths = 
            len(all_paths), 

        max_path_blocks = 
            max_path_blocks, 

        max_path_edges = 
            max_path_edges, 

        paths_with_state_write = 
            paths_with_state_write, 

        paths_with_gpio_effect = 
            paths_with_gpio_effect, 

        target_samples = 
            target_samples, 

        duplicate_cfg_paths = 
            duplicate_cfg_paths, 
    )


# ============================================================
# Report helpers
# ============================================================


def _edge_text(
    edge: SemanticPathEdge, 
) -> str: 

    return (
        f"BB{edge.source:03d}"
        f"-{edge.kind}->"
        f"BB{edge.target:03d}"
    )


# ============================================================
# Report
# ============================================================


def print_semantic_transitions(
    result: SemanticTransitionResult, 
    max_paths_to_print: int = 30, 
) -> None: 

    print()

    print(
        "=" * 72
    )

    print(
        "SEMANTIC TRANSITIONS"
    )

    print(
        "=" * 72
    )

    for name, region in (
        result.regions.items()
    ): 

        print(
            name
        )

        print(
            "    paths              : "
            f"{len(region.paths)}"
        )

        target_counts: dict[
            int | None, 
            int, 
        ] = {}

        for path in region.paths: 

            target_counts[
                path.target_sample
            ] = (
                target_counts.get(
                    path.target_sample, 
                    0, 
                )
                + 1
            )

        target_text: list[
            str
        ] = []

        for target, count in sorted(
            target_counts.items(), 
            key = lambda item: (
                -1
                if item[0] is None
                else item[0]
            ), 
        ): 

            if target is None: 

                target_text.append(
                    f"TERMINAL:{count}"
                )

            else: 

                target_text.append(
                    f"BB{target:03d}:"
                    f"{count}"
                )

        print(
            "    targets            : "
            + ", ".join(
                target_text
            )
        )

        state_paths = sum(
            1

            for path
            in region.paths

            if path.state_writes
        )

        gpio_paths = sum(
            1

            for path
            in region.paths

            if path.gpio_effects
        )

        print(
            "    paths with state wr: "
            f"{state_paths}"
        )

        print(
            "    paths with GPIO eff: "
            f"{gpio_paths}"
        )

    print()

    print(
        "=" * 72
    )

    print(
        "SEMANTIC TRANSITION SAMPLE"
    )

    print(
        "=" * 72
    )

    shown = 0

    for name, region in (
        result.regions.items()
    ): 

        for path_index, path in enumerate(
            region.paths
        ): 

            if shown >= max_paths_to_print: 
                break

            print(
                f"{name} path "
                f"{path_index}"
            )

            if path.target_sample is None: 

                print(
                    "    target       : "
                    "TERMINAL"
                )

            else: 

                print(
                    "    target       : "
                    f"BB"
                    f"{path.target_sample:03d}"
                )

            print(
                "    blocks       : "
                f"{len(path.blocks)}"
            )

            print(
                "    edges        : "
                f"{len(path.edges)}"
            )

            if path.edges: 

                print(
                    "    route        : "
                    + " ".join(
                        _edge_text(
                            edge
                        )

                        for edge
                        in path.edges
                    )
                )

            print(
                "    state writes : "
                f"{len(path.state_writes)}"
            )

            print(
                "    GPIO effects : "
                f"{len(path.gpio_effects)}"
            )

            shown += 1

        if shown >= max_paths_to_print: 
            break

    print()

    print(
        "=" * 72
    )

    print(
        "SEMANTIC TRANSITION SUMMARY"
    )

    print(
        "=" * 72
    )

    print(
        "regions              : "
        f"{len(result.regions)}"
    )

    print(
        "semantic paths       : "
        f"{result.total_paths}"
    )

    print(
        "max path blocks      : "
        f"{result.max_path_blocks}"
    )

    print(
        "max path edges       : "
        f"{result.max_path_edges}"
    )

    print(
        "paths with state wr  : "
        f"{result.paths_with_state_write}"
    )

    print(
        "paths with GPIO eff  : "
        f"{result.paths_with_gpio_effect}"
    )

    print(
        "target sample points : "
        f"{len(result.target_samples)}"
    )

    print(
        "duplicate CFG paths  : "
        f"{result.duplicate_cfg_paths}"
    )

    print()

    if (
        result.duplicate_cfg_paths == 0
        and
        result.target_samples
    ): 

        print(
            "SEMANTIC TRANSITION: PASS"
        )

    else: 

        print(
            "SEMANTIC TRANSITION: FAIL"
        )