from dataclasses import dataclass, field
import re

from .ir import (
    IROp, 
    IRBlock, 
)

from .ssa_opt import (
    values_used_by_op, 
    parse_phi_arg, 
)

from .logical_state import (
    stack_state_base, 
)

from .dff_candidates import (
    DffCandidateResult, 
)


# ============================================================
# Result structures
# ============================================================

@dataclass
class WidthEvidence: 
    block_id: int
    address: int | None

    kind: str
    detail: str

    required_bits: int


@dataclass
class FamilyWidth: 
    family: str

    required_bits: int

    evidence: list[
        WidthEvidence
    ] = field(
        default_factory = list
    )


@dataclass
class BitWidthResult: 
    families: dict[
        str, 
        FamilyWidth
    ]

    total_bits: int

    unresolved_families: list[str]


# ============================================================
# Helpers
# ============================================================

def bits_for_constant(
    value: int, 
) -> int: 

    if value < 0: 
        # Conservative signed width.
        return max(
            1, 
            value.bit_length() + 1, 
        )

    return max(
        1, 
        value.bit_length(), 
    )


def parse_int(
    value: str, 
) -> int | None: 

    try: 
        return int(
            value, 
            0, 
        )

    except ValueError: 
        return None


def family_of_value(
    value: str, 
) -> str | None: 

    return stack_state_base(
        value
    )


def add_evidence(
    widths: dict[str, FamilyWidth], 
    family: str, 
    block_id: int, 
    address: int | None, 
    kind: str, 
    detail: str, 
    bits: int, 
): 

    if family not in widths: 

        widths[
            family
        ] = FamilyWidth(
            family = family, 
            required_bits = 1, 
        )


    entry = widths[
        family
    ]


    entry.required_bits = max(
        entry.required_bits, 
        bits, 
    )


    entry.evidence.append(
        WidthEvidence(
            block_id = 
                block_id, 

            address = 
                address, 

            kind = 
                kind, 

            detail = 
                detail, 

            required_bits = 
                bits, 
        )
    )


# ============================================================
# Main analysis
# ============================================================

def analyze_bit_widths(
    blocks: list[IRBlock], 
    dff_candidates: 
        DffCandidateResult, 
) -> BitWidthResult: 

    widths = {
        family: 
            FamilyWidth(
                family = family, 
                required_bits = 1, 
            )

        for family
        in dff_candidates.stack_candidates
    }


    # ========================================================
    # Scan all operations
    # ========================================================

    for block in blocks: 

        for op in block.ops: 

            # ------------------------------------------------
            # PHI inputs
            # ------------------------------------------------

            if op.kind == "PHI": 

                destination_family = (
                    family_of_value(
                        op.dst
                    )
                    if op.dst
                    else None
                )


                if (
                    destination_family
                    in widths
                ): 

                    for arg in op.args: 

                        parsed = parse_phi_arg(
                            arg
                        )

                        if parsed is None: 
                            continue


                        _, value = parsed


                        constant = parse_int(
                            value
                        )


                        if constant is not None: 

                            bits = bits_for_constant(
                                constant
                            )


                            add_evidence(
                                widths, 
                                destination_family, 
                                block.id, 
                                op.address, 
                                "PHI_CONST", 
                                value, 
                                bits, 
                            )


                continue


            # ------------------------------------------------
            # Analyze every SSA value used by op.
            # ------------------------------------------------

            used_values = (
                values_used_by_op(
                    op
                )
            )


            for value in used_values: 

                family = family_of_value(
                    value
                )


                if (
                    family is None
                    or family not in widths
                ): 
                    continue


                # ============================================
                # AND with constant mask
                # ============================================

                if op.kind == "AND": 

                    for arg in op.args: 

                        constant = parse_int(
                            arg
                        )

                        if (
                            constant is None
                            or constant < 0
                        ): 
                            continue


                        if constant == 0: 

                            bits = 1

                        else: 

                            bits = (
                                constant.bit_length()
                            )


                        add_evidence(
                            widths, 
                            family, 
                            block.id, 
                            op.address, 
                            "AND_MASK", 
                            f"mask={arg}", 
                            bits, 
                        )


                # ============================================
                # SHL
                # ============================================

                elif op.kind == "SHL": 

                    shift = None


                    for arg in op.args: 

                        constant = parse_int(
                            arg
                        )

                        if constant is not None: 

                            shift = constant


                    if (
                        shift is not None
                        and shift >= 0
                    ): 

                        # Conservative:
                        # input width at least enough for
                        # one bit before shifting.

                        add_evidence(
                            widths, 
                            family, 
                            block.id, 
                            op.address, 
                            "SHIFT_LEFT", 
                            f"shift={shift}", 
                            1, 
                        )


                # ============================================
                # SHR
                # ============================================

                elif op.kind == "SHR": 

                    shift = None


                    for arg in op.args: 

                        constant = parse_int(
                            arg
                        )

                        if constant is not None: 

                            shift = constant


                    if (
                        shift is not None
                        and shift >= 0
                    ): 

                        # To observe bit after right shift n,
                        # source must contain at least n+1 bits.

                        add_evidence(
                            widths, 
                            family, 
                            block.id, 
                            op.address, 
                            "SHIFT_RIGHT", 
                            f"shift={shift}", 
                            shift + 1, 
                        )


                # ============================================
                # GPIO use
                # ============================================

                elif op.kind in {
                    "GPIO_SET", 
                    "GPIO_CLEAR_N", 
                    "GPIO_DIR_SET", 
                    "GPIO_DIR_CLEAR", 
                    "GPIO_MASK", 
                }: 

                    # GPIO data is 32-bit in BIO, but that
                    # does not automatically imply that the
                    # logical state itself requires 32 bits.
                    #
                    # Record conservatively as at least 1 bit.

                    add_evidence(
                        widths, 
                        family, 
                        block.id, 
                        op.address, 
                        "GPIO_USE", 
                        op.kind, 
                        1, 
                    )


                # ============================================
                # BRANCH
                # ============================================

                elif op.kind == "BRANCH": 

                    # Comparison against a literal may imply
                    # minimum width.

                    constants = re.findall(
                        r"\b(?:0x[0-9A-Fa-f]+|\d+)\b", 
                        op.comment, 
                    )


                    for constant_text in constants: 

                        constant = parse_int(
                            constant_text
                        )

                        if constant is None: 
                            continue


                        bits = bits_for_constant(
                            constant
                        )


                        add_evidence(
                            widths, 
                            family, 
                            block.id, 
                            op.address, 
                            "BRANCH_CONST", 
                            op.comment, 
                            bits, 
                        )


                # ============================================
                # Generic arithmetic
                # ============================================

                elif op.kind in {
                    "ADD", 
                    "OR", 
                }: 

                    # Conservative minimum.
                    add_evidence(
                        widths, 
                        family, 
                        block.id, 
                        op.address, 
                        op.kind, 
                        str(op), 
                        1, 
                    )


    # ========================================================
    # Propagate width across same-family PHI chains
    #
    # All SSA versions of one logical family share one
    # physical DFF width.
    # ========================================================

    unresolved = []


    for (
        family, 
        width, 
    ) in widths.items(): 

        if not width.evidence: 

            unresolved.append(
                family
            )


    total_bits = sum(
        width.required_bits

        for width
        in widths.values()
    )


    return BitWidthResult(
        families = widths, 

        total_bits = 
            total_bits, 

        unresolved_families = 
            unresolved, 
    )