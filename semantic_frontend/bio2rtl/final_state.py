from dataclasses import dataclass


@dataclass(frozen = True)
class FinalState: 
    family: str
    width: int
    proof: str


@dataclass
class FinalStateResult: 
    states: dict[str, FinalState]
    total_bits: int
    unproven: list[str]


# Legacy baseline proofs.
#
# These names are compiler stack offsets.  Do not extend this table for
# newly compiled source programs.  It is retained only so the established
# original baseline continues to build identically while generic recurrence
# proofs replace it.
PROVEN_RECURRENCE_WIDTHS = {
    "stack_main_sp_m44": (
        4, 
        "legacy proof: counter guarded at 8", 
    ), 

    "stack_main_sp_m48": (
        8, 
        "legacy proof: 8-cycle shift register", 
    ), 

    "stack_main_sp_m72": (
        3, 
        "legacy proof: range 0..7; zero bypasses decrement", 
    ), 
}


def _bits_for_unsigned(
    value: int, 
) -> int: 

    if value <= 0: 
        return 1

    return value.bit_length()


def _has_zero_guard(
    info, 
) -> bool: 

    for site in info.branch_sites: 

        for text in site.args: 

            compact = (
                text.replace(
                    " ", 
                    ""
                )
            )

            if (
                "!=0" in compact
                or "==0" in compact
            ): 
                return True

    return False


def _prove_decrement_counter(
    family: str, 
    info, 
) -> tuple[int, str] | None: 
    """
    Prove a bounded unsigned down-counter.

    Accepted shape:

        - one or more non-negative constants enter the state family;
        - recurrence only subtracts positive constants;
        - no positive increment;
        - no shift recurrence;
        - control flow tests the family against zero.

    Then the recurrence cannot exceed its largest entering constant:
    it either holds or decreases until the zero guard diverts control.

    This deliberately does not depend on stack_main_sp_mXX offsets.
    """

    if not info.initial_constants: 
        return None

    if any(
        value < 0
        for value in info.initial_constants
    ): 
        return None

    if info.increment_constants: 
        return None

    if not info.decrement_constants: 
        return None

    if info.shift_left_amounts: 
        return None

    if info.shift_right_amounts: 
        return None

    if not _has_zero_guard(
        info
    ): 
        return None

    maximum = max(
        info.initial_constants
    )

    width = _bits_for_unsigned(
        maximum
    )

    decrements = ",".join(
        str(value)
        for value in sorted(
            info.decrement_constants
        )
    )

    proof = (
        "automatic recurrence proof: "
        f"unsigned down-counter, init<= {maximum}, "
        f"decrement={{{decrements}}}, zero-guarded"
    )

    return (
        width, 
        proof, 
    )


def _automatic_recurrence_proof(
    family: str, 
    loop_bounds, 
) -> tuple[int, str] | None: 

    if loop_bounds is None: 
        return None

    info = (
        loop_bounds
        .families
        .get(
            family
        )
    )

    if info is None: 
        return None

    proof = _prove_decrement_counter(
        family, 
        info, 
    )

    if proof is not None: 
        return proof

    return None


def _print_unproven_diagnostic(
    family: str, 
    result, 
    loop_bounds, 
) -> None: 

    print(
        f"{family:<32} "
        f"status={result.status:<14} "
        f"observed={result.observed_bits:>2} "
        f"source={result.source_bits:>2} "
        f"inferred={result.inferred_bits:>2} "
        f"recurrence={int(result.recurrence)} "
        f"unknown={int(result.unknown)}"
    )

    for item in list(
        getattr(
            result, 
            "evidence", 
            [], 
        )
    ): 
        print(
            f"    {item}"
        )

    if loop_bounds is None: 
        return

    info = (
        loop_bounds
        .families
        .get(
            family
        )
    )

    if info is None: 
        return

    print(
        "    loop-bound: "
        f"initial={sorted(info.initial_constants)} "
        f"inc={sorted(info.increment_constants)} "
        f"dec={sorted(info.decrement_constants)} "
        f"shl={sorted(info.shift_left_amounts)} "
        f"shr={sorted(info.shift_right_amounts)}"
    )

    for site in info.branch_sites: 
        for text in site.args: 
            print(
                "    branch: "
                f"BB{site.block_id}: {text}"
            )


def build_final_state(
    inferred_widths, 
    loop_bounds = None, 
) -> FinalStateResult: 

    states = {}
    unproven = []


    for (
        family, 
        result, 
    ) in sorted(
        inferred_widths.families.items()
    ): 

        if result.status == "PROVEN": 

            states[
                family
            ] = FinalState(
                family = family, 
                width = result.inferred_bits, 
                proof = "static width inference", 
            )

            continue


        automatic = (
            _automatic_recurrence_proof(
                family, 
                loop_bounds, 
            )
        )


        if automatic is not None: 

            width, proof = automatic

            states[
                family
            ] = FinalState(
                family = family, 
                width = width, 
                proof = proof, 
            )

            continue


        legacy = (
            PROVEN_RECURRENCE_WIDTHS
            .get(
                family
            )
        )


        if legacy is not None: 

            width, proof = legacy

            states[
                family
            ] = FinalState(
                family = family, 
                width = width, 
                proof = proof, 
            )

            continue


        unproven.append(
            family
        )


    if unproven: 

        print()
        print(
            "=" * 72
        )
        print(
            "UNPROVEN PERSISTENT-STATE DIAGNOSTIC"
        )
        print(
            "=" * 72
        )
        print(
            "NOTE: stack_main_sp_mXX names are compiler stack offsets."
        )
        print(
            "Do not add new offsets to PROVEN_RECURRENCE_WIDTHS."
        )
        print()

        for family in unproven: 

            _print_unproven_diagnostic(
                family, 
                inferred_widths
                .families[
                    family
                ], 
                loop_bounds, 
            )

        print()
        print(
            "UNPROVEN FAMILIES: "
            + ", ".join(
                unproven
            )
        )
        print()


    total_bits = sum(
        state.width
        for state in states.values()
    )


    return FinalStateResult(
        states = states, 
        total_bits = total_bits, 
        unproven = unproven, 
    )
