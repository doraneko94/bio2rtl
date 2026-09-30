from dataclasses import dataclass, field
import ast
import operator
import re

from .ir import (
    IROp, 
    IRBlock, 
)

from .ssa_opt import (
    parse_phi_arg, 
)

from .logical_state import (
    stack_state_base, 
)

from .dff_candidates import (
    DffCandidateResult, 
)

from .bitwidth import (
    BitWidthResult, 
)

from .width_trace import (
    trace_family_definitions, 
)


# ============================================================
# Supported integer constant expressions
# ============================================================

BINOPS = {
    ast.Add: operator.add, 
    ast.Sub: operator.sub, 
    ast.LShift: operator.lshift, 
    ast.RShift: operator.rshift, 
    ast.BitOr: operator.or_, 
    ast.BitAnd: operator.and_, 
    ast.BitXor: operator.xor, 
}

UNARYOPS = {
    ast.USub: operator.neg, 
    ast.UAdd: operator.pos, 
    ast.Invert: operator.invert, 
}


def eval_constant(
    text: str, 
) -> int | None: 
    """
    Handles expressions seen in BIO disassembly, e.g.

        3
        0xff
        -1
        -1 # objdump comment
        (0x20 << 12)

    Only integer arithmetic/bitwise AST nodes are accepted.
    """

    text = text.split(
        "#", 
        1, 
    )[0].strip()

    if not text: 
        return None


    try: 
        node = ast.parse(
            text, 
            mode = "eval", 
        ).body

    except SyntaxError: 
        return None


    def evaluate(
        item, 
    ): 

        if (
            isinstance(
                item, 
                ast.Constant, 
            )
            and isinstance(
                item.value, 
                int, 
            )
        ): 
            return item.value


        if isinstance(
            item, 
            ast.UnaryOp, 
        ): 

            function = UNARYOPS.get(
                type(item.op)
            )

            if function is None: 
                raise ValueError

            return function(
                evaluate(
                    item.operand
                )
            )


        if isinstance(
            item, 
            ast.BinOp, 
        ): 

            function = BINOPS.get(
                type(item.op)
            )

            if function is None: 
                raise ValueError

            return function(
                evaluate(item.left), 
                evaluate(item.right), 
            )


        raise ValueError


    try: 
        return evaluate(
            node
        )

    except (
        ValueError, 
        OverflowError, 
    ): 
        return None


def bits_for_unsigned(
    value: int, 
) -> int: 

    if value <= 0: 
        return 1

    return value.bit_length()


# ============================================================
# Structures
# ============================================================

@dataclass
class ValueWidth: 
    bits: int

    recurrence: bool = False
    unknown: bool = False

    evidence: set[str] = field(
        default_factory = set
    )


@dataclass
class DffWidthFamily: 
    family: str

    observed_bits: int
    source_bits: int
    inferred_bits: int

    recurrence: bool
    unknown: bool

    status: str
    # PROVEN
    # BOUND_REQUIRED
    # UNKNOWN

    evidence: list[str] = field(
        default_factory = list
    )


@dataclass
class DffWidthInferenceResult: 
    families: dict[
        str, 
        DffWidthFamily
    ]

    proven_families: list[str]
    bound_required_families: list[str]
    unknown_families: list[str]

    proven_total_bits: int

    iterations: int


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

            result[
                op.dst
            ] = (
                block.id, 
                op, 
            )


    return result


# ============================================================
# SSA constant propagation
# ============================================================

def resolve_constant_value(
    value: str, 
    definition_map: 
        dict[str, tuple[int, IROp]], 
    visiting: set[str] | None = None, 
) -> int | None: 

    direct = eval_constant(
        value
    )

    if direct is not None: 
        return direct


    if visiting is None: 
        visiting = set()


    if value in visiting: 
        return None


    visiting = set(
        visiting
    )

    visiting.add(
        value
    )


    definition = definition_map.get(
        value
    )


    if definition is None: 
        return None


    _, op = definition


    if op.kind == "CONST": 

        if not op.args: 
            return None

        return eval_constant(
            op.args[0]
        )


    if (
        op.kind == "ASSIGN"
        and len(op.args) == 1
    ): 

        return resolve_constant_value(
            op.args[0], 
            definition_map, 
            visiting, 
        )


    return None


# ============================================================
# Consumer-side true lower bound
# ============================================================

def observed_lower_bound(
    family: str, 
    observed: BitWidthResult, 
) -> int: 
    """
    Only evidence that genuinely requires source bits is used.

    Important:

        state & 0xff

    does NOT prove that state itself requires 8 bits.
    It only says that bits above bit 7 are ignored.

    Likewise generic ADD use does not establish a minimum
    storage width.

    BRANCH against a literal and right-shift observation can
    establish a real lower bound.
    """

    entry = observed.families.get(
        family
    )


    if entry is None: 
        return 1


    bits = 1


    for evidence in entry.evidence: 

        if evidence.kind in {
            "BRANCH_CONST", 
            "SHIFT_RIGHT", 
        }: 

            bits = max(
                bits, 
                evidence.required_bits, 
            )


    return bits


# ============================================================
# Width combination helpers
# ============================================================

def merge_widths(
    values: list[ValueWidth], 
) -> ValueWidth: 

    if not values: 

        return ValueWidth(
            bits = 1, 
            unknown = True, 
        )


    result = ValueWidth(
        bits = max(
            value.bits
            for value in values
        ), 

        recurrence = any(
            value.recurrence
            for value in values
        ), 

        unknown = any(
            value.unknown
            for value in values
        ), 
    )


    for value in values: 

        result.evidence.update(
            value.evidence
        )


    return result


# ============================================================
# Generic SSA expression width evaluation
# ============================================================

def evaluate_value_width(
    value: str, 
    root_family: str, 
    definition_map: 
        dict[str, tuple[int, IROp]], 
    family_widths: dict[str, int], 
    visiting: set[str], 
) -> ValueWidth: 

    # --------------------------------------------------------
    # x0
    # --------------------------------------------------------

    if value == "x0": 

        return ValueWidth(
            bits = 1, 
            evidence = {
                "ZERO"
            }, 
        )


    # --------------------------------------------------------
    # Literal
    # --------------------------------------------------------

    constant = eval_constant(
        value
    )


    if constant is not None: 

        return ValueWidth(
            bits = bits_for_unsigned(
                constant
            ), 

            evidence = {
                f"CONST({constant})"
            }, 
        )


    # --------------------------------------------------------
    # Stack-state SSA value
    # --------------------------------------------------------

    family = stack_state_base(
        value
    )


    if family is not None: 

        bits = family_widths.get(
            family, 
            1, 
        )


        # Same logical state appearing in its own update
        # equation means a recurrence.
        if family == root_family: 

            return ValueWidth(
                bits = bits, 
                recurrence = True, 
                evidence = {
                    f"SELF({family})"
                }, 
            )


        return ValueWidth(
            bits = bits, 
            evidence = {
                f"STATE({family})"
            }, 
        )


    # --------------------------------------------------------
    # Live-in
    # --------------------------------------------------------

    if value.endswith(
        "_0"
    ): 

        return ValueWidth(
            bits = 32, 
            unknown = True, 
            evidence = {
                f"LIVEIN({value})"
            }, 
        )


    # --------------------------------------------------------
    # SSA cycle protection
    # --------------------------------------------------------

    if value in visiting: 

        return ValueWidth(
            bits = family_widths.get(
                root_family, 
                1, 
            ), 
            recurrence = True, 
            evidence = {
                f"SSA_CYCLE({value})"
            }, 
        )


    definition = definition_map.get(
        value
    )


    if definition is None: 

        return ValueWidth(
            bits = 32, 
            unknown = True, 
            evidence = {
                f"NO_DEF({value})"
            }, 
        )


    block_id, op = definition


    new_visiting = set(
        visiting
    )

    new_visiting.add(
        value
    )


    location = (
        f"BB{block_id}:"
        + (
            "-"
            if op.address is None
            else f"0x{op.address:04x}"
        )
    )


    # ========================================================
    # CONST
    # ========================================================

    if op.kind == "CONST": 

        if not op.args: 

            return ValueWidth(
                bits = 32, 
                unknown = True, 
                evidence = {
                    f"{location}:CONST(no-arg)"
                }, 
            )


        constant = eval_constant(
            op.args[0]
        )


        if constant is None: 

            return ValueWidth(
                bits = 32, 
                unknown = True, 
                evidence = {
                    f"{location}:CONST({op.args[0]})"
                }, 
            )


        return ValueWidth(
            bits = bits_for_unsigned(
                constant
            ), 

            evidence = {
                f"{location}:CONST={constant}"
            }, 
        )


    # ========================================================
    # GPIO input
    # ========================================================

    if op.kind == "GPIO_READ": 

        return ValueWidth(
            bits = 32, 
            evidence = {
                f"{location}:GPIO_READ"
            }, 
        )


    # ========================================================
    # PHI
    # ========================================================

    if op.kind == "PHI": 

        incoming_widths = []


        for arg in op.args: 

            parsed = parse_phi_arg(
                arg
            )

            if parsed is None: 
                continue


            _, incoming = parsed


            incoming_widths.append(
                evaluate_value_width(
                    incoming, 
                    root_family, 
                    definition_map, 
                    family_widths, 
                    new_visiting, 
                )
            )


        result = merge_widths(
            incoming_widths
        )

        result.evidence.add(
            f"{location}:PHI"
        )

        return result


    # ========================================================
    # ASSIGN
    # ========================================================

    if op.kind == "ASSIGN": 

        if not op.args: 

            return ValueWidth(
                bits = 32, 
                unknown = True, 
                evidence = {
                    f"{location}:ASSIGN(no-arg)"
                }, 
            )


        result = evaluate_value_width(
            op.args[0], 
            root_family, 
            definition_map, 
            family_widths, 
            new_visiting, 
        )

        result.evidence.add(
            f"{location}:ASSIGN"
        )

        return result


    # ========================================================
    # Generic argument widths
    # ========================================================

    argument_widths = []


    for arg in op.args: 

        constant = eval_constant(
            arg
        )


        if constant is not None: 

            argument_widths.append(
                ValueWidth(
                    bits = bits_for_unsigned(
                        constant
                    ), 
                    evidence = {
                        f"CONST({constant})"
                    }, 
                )
            )

        else: 

            argument_widths.append(
                evaluate_value_width(
                    arg, 
                    root_family, 
                    definition_map, 
                    family_widths, 
                    new_visiting, 
                )
            )


    # ========================================================
    # AND
    #
    # x & mask can never require bits above highest mask bit.
    # ========================================================

    if op.kind == "AND": 

        resolved_constants = []


        for arg in op.args: 

            constant = (
                resolve_constant_value(
                    arg, 
                    definition_map, 
                )
            )


            if (
                constant is not None
                and constant >= 0
            ): 

                resolved_constants.append(
                    constant
                )


        if resolved_constants: 

            # For an AND, every constant operand can limit
            # the result. The narrowest mask is sufficient.
            mask = min(
                resolved_constants, 
                key = lambda item: 
                    bits_for_unsigned(
                        item
                    ), 
            )


            # Evaluate only non-constant data operands.
            data_widths = []


            for arg in op.args: 

                constant = (
                    resolve_constant_value(
                        arg, 
                        definition_map, 
                    )
                )


                if constant is not None: 
                    continue


                data_widths.append(
                    evaluate_value_width(
                        arg, 
                        root_family, 
                        definition_map, 
                        family_widths, 
                        new_visiting, 
                    )
                )


            if data_widths: 

                result = merge_widths(
                    data_widths
                )

            else: 

                result = ValueWidth(
                    bits = 
                        bits_for_unsigned(
                            mask
                        )
                )


            result.bits = min(
                result.bits, 
                bits_for_unsigned(
                    mask
                ), 
            )


            # Masking bounds any upstream width growth.
            result.recurrence = False


            result.evidence.add(
                f"{location}:AND mask="
                f"0x{mask:x}"
            )

            return result


        result = merge_widths(
            argument_widths
        )

        result.evidence.add(
            f"{location}:AND"
        )

        return result
    

    # ========================================================
    # SHR
    # ========================================================

    if op.kind == "SHR": 

        if len(op.args) < 2: 

            result = merge_widths(
                argument_widths
            )

            result.unknown = True

            return result


        source = evaluate_value_width(
            op.args[0], 
            root_family, 
            definition_map, 
            family_widths, 
            new_visiting, 
        )


        shift = resolve_constant_value(
            op.args[1], 
            definition_map, 
        )


        if (
            shift is None
            or shift < 0
        ): 

            source.unknown = True

            source.evidence.add(
                f"{location}:SHR unknown-shift"
            )

            return source


        source.bits = max(
            1, 
            source.bits - shift, 
        )


        # Right shift itself does not create width.
        source.evidence.add(
            f"{location}:SHR {shift}"
        )

        return source


    # ========================================================
    # SHL
    # ========================================================

    if op.kind == "SHL": 

        if len(op.args) < 2: 

            result = merge_widths(
                argument_widths
            )

            result.unknown = True

            return result


        source = evaluate_value_width(
            op.args[0], 
            root_family, 
            definition_map, 
            family_widths, 
            new_visiting, 
        )


        shift = eval_constant(
            op.args[1]
        )


        if (
            shift is None
            or shift < 0
        ): 

            source.unknown = True

            source.evidence.add(
                f"{location}:SHL unknown-shift"
            )

            return source


        # If source already contains this logical state's
        # recurrence, do not let width grow forever.
        #
        # Mark BOUND_REQUIRED instead.
        if source.recurrence: 

            source.evidence.add(
                f"{location}:SHL {shift} "
                f"(recurrence)"
            )

            return source


        source.bits = min(
            32, 
            source.bits + shift, 
        )


        source.evidence.add(
            f"{location}:SHL {shift}"
        )

        return source


    # ========================================================
    # OR
    # ========================================================

    if op.kind == "OR": 

        result = merge_widths(
            argument_widths
        )

        result.evidence.add(
            f"{location}:OR"
        )

        return result


    # ========================================================
    # ADD
    # ========================================================

    if op.kind == "ADD": 

        result = merge_widths(
            argument_widths
        )


        constants = [
            eval_constant(arg)

            for arg in op.args
        ]


        constants = [
            value
            for value in constants
            if value is not None
        ]


        # Self-recurrent counter:
        #
        #     state := state + 1
        #     state := state - 1
        #
        # Width cannot be proven from this equation alone.
        # Keep current lower bound and mark recurrence.
        if result.recurrence: 

            result.evidence.add(
                f"{location}:ADD recurrence"
            )

            return result


        # General addition can create one carry bit.
        result.bits = min(
            32, 
            result.bits + 1, 
        )


        result.evidence.add(
            f"{location}:ADD"
        )

        return result


    # ========================================================
    # Unknown arithmetic op
    # ========================================================

    result = merge_widths(
        argument_widths
    )

    result.unknown = True

    result.evidence.add(
        f"{location}:UNSUPPORTED_WIDTH_OP("
        f"{op.kind})"
    )

    return result


# ============================================================
# Main family inference
# ============================================================

def infer_dff_widths(
    blocks: list[IRBlock], 
    dff_candidates: 
        DffCandidateResult, 
    observed: 
        BitWidthResult, 
) -> DffWidthInferenceResult: 

    definition_map = (
        build_definition_map(
            blocks
        )
    )


    families = sorted(
        dff_candidates.stack_candidates
    )


    # Start from already proven observation-side minima.

    family_widths = {}


    for family in families: 

        family_widths[
            family
        ] = observed_lower_bound(
            family, 
            observed, 
        )

    # Source lists are generic for every family.

    family_sources = {}


    for family in families: 

        trace = (
            trace_family_definitions(
                blocks, 
                family, 
            )
        )


        family_sources[
            family
        ] = sorted(
            trace.source_values
        )


    # ========================================================
    # Fixed point for cross-family width propagation.
    # ========================================================

    max_iterations = 64

    iterations = 0


    for iteration in range(
        max_iterations
    ): 

        iterations = iteration + 1

        changed = False


        for family in families: 

            source_width = 1


            for source in (
                family_sources[
                    family
                ]
            ): 

                result = (
                    evaluate_value_width(
                        source, 
                        family, 
                        definition_map, 
                        family_widths, 
                        set(), 
                    )
                )


                source_width = max(
                    source_width, 
                    result.bits, 
                )


            new_width = max(
                family_widths[
                    family
                ], 
                source_width, 
            )


            new_width = min(
                32, 
                new_width, 
            )


            if (
                new_width
                != family_widths[
                    family
                ]
            ): 

                family_widths[
                    family
                ] = new_width

                changed = True


        if not changed: 
            break


    # ========================================================
    # Final classification
    # ========================================================

    results = {}


    proven = []
    bound_required = []
    unknown = []


    for family in families: 

        observed_bits = (
            observed_lower_bound(
                family, 
                observed, 
            )
        )


        source_bits = 1

        has_recurrence = False
        has_unknown = False

        evidence = set()


        for source in (
            family_sources[
                family
            ]
        ): 

            result = (
                evaluate_value_width(
                    source, 
                    family, 
                    definition_map, 
                    family_widths, 
                    set(), 
                )
            )


            source_bits = max(
                source_bits, 
                result.bits, 
            )


            has_recurrence |= (
                result.recurrence
            )

            has_unknown |= (
                result.unknown
            )


            evidence.update(
                result.evidence
            )


        inferred_bits = max(
            observed_bits, 
            source_bits, 
            family_widths[
                family
            ], 
        )


        if has_unknown: 

            status = "UNKNOWN"

            unknown.append(
                family
            )


        elif has_recurrence: 

            status = (
                "BOUND_REQUIRED"
            )

            bound_required.append(
                family
            )


        else: 

            status = "PROVEN"

            proven.append(
                family
            )


        results[
            family
        ] = DffWidthFamily(
            family = family, 

            observed_bits = 
                observed_bits, 

            source_bits = 
                source_bits, 

            inferred_bits = 
                inferred_bits, 

            recurrence = 
                has_recurrence, 

            unknown = 
                has_unknown, 

            status = 
                status, 

            evidence = 
                sorted(
                    evidence
                ), 
        )


    proven_total_bits = sum(
        results[
            family
        ].inferred_bits

        for family in proven
    )


    return DffWidthInferenceResult(
        families = results, 

        proven_families = 
            proven, 

        bound_required_families = 
            bound_required, 

        unknown_families = 
            unknown, 

        proven_total_bits = 
            proven_total_bits, 

        iterations = 
            iterations, 
    )