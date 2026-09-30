from __future__ import annotations

from dataclasses import dataclass, field

from .fsm_ir import (
    FSMIR, 
)

from .semantic_boundary import (
    SemanticBoundaryResult, 
)


# ============================================================
# Region structures
# ============================================================


@dataclass
class SemanticRegion: 
    name: str

    # None means the artificial ENTRY region.
    start_block: int | None

    # Blocks executed after the sample point and before the next
    # external-input sample.
    internal_blocks: set[int] = field(
        default_factory = set
    )

    # GPIO_READ blocks at which execution stops.
    exit_sample_blocks: set[int] = field(
        default_factory = set
    )

    # GPIO output side effects encountered inside this region.
    gpio_action_blocks: set[int] = field(
        default_factory = set
    )

    # CFG branches that remain inside this semantic region.
    branch_blocks: set[int] = field(
        default_factory = set
    )

    # Terminal CFG states reached without another sample.
    terminal_blocks: set[int] = field(
        default_factory = set
    )

    # A CFG cycle occurring entirely between sample points.
    #
    # This matters because such a cycle cannot blindly be collapsed
    # into combinational logic. It may represent a wait/poll loop or
    # another genuine temporal construct.
    internal_cycle_blocks: set[int] = field(
        default_factory = set
    )

    # Closed cycles with no sampling or GPIO side effect are semantic
    # quiescent terminals.  The CPU may spin forever, but hardware has
    # no further observable work to perform and must not invent a clock.
    quiescent_terminal_cycle_blocks: set[int] = field(
        default_factory = set
    )


@dataclass
class SemanticMicrostateResult: 
    regions: dict[
        str, 
        SemanticRegion, 
    ]

    sample_blocks: set[int]

    covered_blocks: set[int]

    uncovered_blocks: set[int]

    regions_with_internal_cycles: int

    total_gpio_action_blocks: set[int]


# ============================================================
# FSM helpers
# ============================================================


def _state_successors(
    fsm: FSMIR, 
    state_id: int, 
) -> tuple[int, ...]: 

    state = fsm.states.get(
        state_id
    )

    if state is None: 
        return ()

    transition = state.transition

    if transition is None: 
        return ()

    targets: list[int] = []

    if transition.true_target is not None: 
        targets.append(
            transition.true_target
        )

    if (
        transition.false_target is not None
        and transition.false_target
        not in targets
    ): 
        targets.append(
            transition.false_target
        )

    return tuple(
        targets
    )


def _is_branch_state(
    fsm: FSMIR, 
    state_id: int, 
) -> bool: 

    state = fsm.states.get(
        state_id
    )

    if state is None: 
        return False

    transition = state.transition

    if transition is None: 
        return False

    return (
        transition.true_target is not None
        and
        transition.false_target is not None
        and
        transition.true_target
        != transition.false_target
    )


# ============================================================
# Region traversal
# ============================================================


def _build_region(
    name: str, 
    start_block: int | None, 
    initial_blocks: tuple[int, ...], 
    fsm: FSMIR, 
    sample_blocks: set[int], 
    gpio_action_blocks: set[int], 
) -> SemanticRegion: 

    region = SemanticRegion(
        name = name, 
        start_block = start_block, 
    )

    # Each stack item carries the current CFG path.
    #
    # We need the path, not merely a global visited set, because
    # revisiting a block along one path proves an internal cycle.
    worklist: list[
        tuple[
            int, 
            tuple[int, ...], 
        ]
    ] = [
        (
            block_id, 
            (), 
        )

        for block_id
        in initial_blocks
    ]

    # A block only needs its outgoing CFG explored once for region
    # coverage. Path-local cycle detection is handled separately.
    expanded: set[int] = set()

    while worklist: 

        block_id, path = (
            worklist.pop()
        )

        # --------------------------------------------------------
        # Reaching a GPIO_READ ends the current semantic region.
        #
        # This applies even when it returns to its own sample block:
        # that is exactly one polling/sample iteration.
        # --------------------------------------------------------

        if block_id in sample_blocks: 

            region.exit_sample_blocks.add(
                block_id
            )

            continue

        # --------------------------------------------------------
        # Path-local loop
        # --------------------------------------------------------

        if block_id in path: 

            # Recover the actual path-local cycle.  Only a closed cycle
            # containing neither an input sample nor a GPIO side effect is
            # allowed to collapse to a quiescent terminal.  Any observable
            # or open-ended unsampled cycle remains fail-closed.
            cycle_start = path.index(block_id)
            cycle_blocks = set(path[cycle_start:])

            closed = True
            for cycle_block in cycle_blocks: 
                successors = _state_successors(fsm, cycle_block)
                if any(successor not in cycle_blocks for successor in successors): 
                    closed = False
                    break

            quiescent = (
                closed
                and not (cycle_blocks & sample_blocks)
                and not (cycle_blocks & gpio_action_blocks)
            )

            if quiescent: 
                region.terminal_blocks.update(cycle_blocks)
                region.quiescent_terminal_cycle_blocks.update(cycle_blocks)
                region.internal_blocks.update(cycle_blocks)
            else: 
                region.internal_cycle_blocks.update(cycle_blocks)

            continue

        region.internal_blocks.add(
            block_id
        )

        if block_id in gpio_action_blocks: 

            region.gpio_action_blocks.add(
                block_id
            )

        if _is_branch_state(
            fsm, 
            block_id, 
        ): 

            region.branch_blocks.add(
                block_id
            )

        successors = _state_successors(
            fsm, 
            block_id, 
        )

        if not successors: 

            region.terminal_blocks.add(
                block_id
            )

            continue

        # Coverage has already been expanded from this block.
        #
        # We still detected any direct path-local cycle above, so
        # repeatedly expanding it is unnecessary and could explode
        # exponentially in branch-heavy CFGs.
        if block_id in expanded: 
            continue

        expanded.add(
            block_id
        )

        next_path = (
            path
            + (
                block_id, 
            )
        )

        for successor in successors: 

            worklist.append(
                (
                    successor, 
                    next_path, 
                )
            )

    return region


# ============================================================
# Whole-program semantic microstate analysis
# ============================================================


def build_semantic_microstates(
    fsm: FSMIR, 
    boundaries: SemanticBoundaryResult, 
) -> SemanticMicrostateResult: 

    sample_blocks = set(
        boundaries.input_blocks
    )

    gpio_action_blocks = set(
        boundaries.output_blocks
    )

    regions: dict[
        str, 
        SemanticRegion, 
    ] = {}

    # ------------------------------------------------------------
    # ENTRY region
    #
    # Initialization before the first external-input sample is
    # represented separately. This is not a steady-state microstate;
    # it is reset/initialization logic.
    # ------------------------------------------------------------

    entry_region = _build_region(
        name = "ENTRY", 
        start_block = None, 
        initial_blocks = (
            fsm.entry_state, 
        ), 
        fsm = fsm, 
        sample_blocks = sample_blocks, 
        gpio_action_blocks = 
            gpio_action_blocks, 
    )

    regions[
        entry_region.name
    ] = entry_region

    # ------------------------------------------------------------
    # One semantic region per GPIO sample point
    # ------------------------------------------------------------

    for sample_block in sorted(
        sample_blocks
    ): 

        successors = _state_successors(
            fsm, 
            sample_block, 
        )

        region = _build_region(
            name = (
                f"SAMPLE_BB"
                f"{sample_block:03d}"
            ), 
            start_block = sample_block, 
            initial_blocks = successors, 
            fsm = fsm, 
            sample_blocks = sample_blocks, 
            gpio_action_blocks = 
                gpio_action_blocks, 
        )

        regions[
            region.name
        ] = region

    # ------------------------------------------------------------
    # Coverage
    # ------------------------------------------------------------

    covered_blocks: set[int] = set()

    for region in regions.values(): 

        if region.start_block is not None: 

            covered_blocks.add(
                region.start_block
            )

        covered_blocks.update(
            region.internal_blocks
        )

        covered_blocks.update(
            region.exit_sample_blocks
        )

    all_blocks = set(
        fsm.states
    )

    uncovered_blocks = (
        all_blocks
        - covered_blocks
    )

    regions_with_internal_cycles = sum(
        1

        for region
        in regions.values()

        if region.internal_cycle_blocks
    )

    total_gpio_action_blocks: set[
        int
    ] = set()

    for region in regions.values(): 

        total_gpio_action_blocks.update(
            region.gpio_action_blocks
        )

    return SemanticMicrostateResult(
        regions = regions, 
        sample_blocks = sample_blocks, 
        covered_blocks = covered_blocks, 
        uncovered_blocks = 
            uncovered_blocks, 
        regions_with_internal_cycles = 
            regions_with_internal_cycles, 
        total_gpio_action_blocks = 
            total_gpio_action_blocks, 
    )


# ============================================================
# Report
# ============================================================


def print_semantic_microstates(
    result: SemanticMicrostateResult, 
) -> None: 

    print()

    print(
        "=" * 72
    )

    print(
        "SEMANTIC MICROSTATE REGIONS"
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

        if region.start_block is None: 

            print(
                "    start sample       : ENTRY"
            )

        else: 

            print(
                "    start sample       : "
                f"BB{region.start_block:03d}"
            )

        print(
            "    internal blocks    : "
            f"{len(region.internal_blocks)}"
        )

        if region.internal_blocks: 

            print(
                "    blocks             : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.internal_blocks
                    )
                )
            )

        if region.exit_sample_blocks: 

            print(
                "    next samples       : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.exit_sample_blocks
                    )
                )
            )

        else: 

            print(
                "    next samples       : -"
            )

        if region.gpio_action_blocks: 

            print(
                "    GPIO actions       : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.gpio_action_blocks
                    )
                )
            )

        else: 

            print(
                "    GPIO actions       : -"
            )

        if region.branch_blocks: 

            print(
                "    internal branches  : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.branch_blocks
                    )
                )
            )

        else: 

            print(
                "    internal branches  : -"
            )

        if region.quiescent_terminal_cycle_blocks: 

            print(
                "    QUIESCENT TERMINAL : "
                + ", ".join(
                    f"BB{x:03d}"
                    for x in sorted(region.quiescent_terminal_cycle_blocks)
                )
            )

        if region.internal_cycle_blocks: 

            print(
                "    INTERNAL CYCLES    : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.internal_cycle_blocks
                    )
                )
            )

        else: 

            print(
                "    internal cycles    : -"
            )

        if region.terminal_blocks: 

            print(
                "    terminal blocks    : "
                + ", ".join(
                    f"BB{x:03d}"

                    for x
                    in sorted(
                        region.terminal_blocks
                    )
                )
            )

    print()

    print(
        "=" * 72
    )

    print(
        "SEMANTIC MICROSTATE SUMMARY"
    )

    print(
        "=" * 72
    )

    steady_regions = (
        len(result.regions)
        - 1
    )

    print(
        "CFG states              : "
        f"{len(result.covered_blocks) + len(result.uncovered_blocks)}"
    )

    print(
        "GPIO sample points      : "
        f"{len(result.sample_blocks)}"
    )

    print(
        "steady semantic regions : "
        f"{steady_regions}"
    )

    print(
        "initialization regions  : 1"
    )

    print(
        "regions with int. cycle : "
        f"{result.regions_with_internal_cycles}"
    )

    print(
        "GPIO action blocks seen : "
        f"{len(result.total_gpio_action_blocks)}"
    )

    print(
        "covered CFG blocks      : "
        f"{len(result.covered_blocks)}"
    )

    print(
        "uncovered CFG blocks    : "
        f"{len(result.uncovered_blocks)}"
    )

    if result.uncovered_blocks: 

        print(
            "uncovered              : "
            + ", ".join(
                f"BB{x:03d}"

                for x
                in sorted(
                    result.uncovered_blocks
                )
            )
        )

    print()

    if (
        not result.uncovered_blocks
        and
        result.regions_with_internal_cycles == 0
    ): 

        print(
            "SEMANTIC MICROSTATE: PASS"
        )

    else: 

        print(
            "SEMANTIC MICROSTATE: FAIL"
        )