from dataclasses import dataclass, field
import re

from .state_extract import (
    StateExtractionResult, 
    PhiClassification, 
)


# ============================================================
# SSA value patterns
# ============================================================

STACK_STATE_SSA_RE = re.compile(
    r"^(stack_[A-Za-z0-9_]+_(?:m|p)\d+)_(\d+)$"
)

REGISTER_SSA_RE = re.compile(
    r"^(x(?:[1-9]|[12][0-9]|3[01]))_(\d+)$"
)


# ============================================================
# Result structures
# ============================================================

@dataclass
class LogicalStateFamily: 
    base_name: str

    source_kind: str
    # "STACK" or "REGISTER"

    members: list[
        PhiClassification
    ] = field(
        default_factory = list
    )

    destinations: list[str] = field(
        default_factory = list
    )

    header_blocks: list[int] = field(
        default_factory = list
    )

    initial_values: set[str] = field(
        default_factory = set
    )

    feedback_values: set[str] = field(
        default_factory = set
    )


@dataclass
class LogicalStateAnalysis: 
    stack_families: dict[
        str, 
        LogicalStateFamily
    ]

    register_families: dict[
        str, 
        LogicalStateFamily
    ]

    unknown_state_phis: list[
        PhiClassification
    ]

    total_state_phis: int

    grouped_state_phis: int


# ============================================================
# Value classification
# ============================================================

def stack_state_base(
    value: str, 
) -> str | None: 

    match = STACK_STATE_SSA_RE.match(
        value
    )

    if not match: 
        return None

    return match.group(1)


def register_state_base(
    value: str, 
) -> str | None: 

    match = REGISTER_SSA_RE.match(
        value
    )

    if not match: 
        return None

    return match.group(1)


# ============================================================
# PHI input classification
# ============================================================

def split_phi_inputs(
    phi: PhiClassification, 
) -> tuple[
    list[str], 
    list[str], 
]: 
    """
    Return:

        forward-edge inputs
        back-edge inputs
    """

    backedge_set = set(
        phi.backedge_predecessors
    )

    forward = []
    feedback = []


    for predecessor, value in sorted(
        phi.incoming.items()
    ): 

        if predecessor in backedge_set: 

            feedback.append(
                value
            )

        else: 

            forward.append(
                value
            )


    return (
        forward, 
        feedback, 
    )


# ============================================================
# Main analysis
# ============================================================

def analyze_logical_states(
    extracted: StateExtractionResult, 
) -> LogicalStateAnalysis: 

    stack_families = {}
    register_families = {}

    unknown = []

    grouped = 0


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

            family_map = (
                stack_families
            )

            base_name = stack_base
            source_kind = "STACK"


        elif register_base is not None: 

            family_map = (
                register_families
            )

            base_name = register_base
            source_kind = "REGISTER"


        else: 

            unknown.append(
                phi
            )

            continue


        if base_name not in family_map: 

            family_map[
                base_name
            ] = LogicalStateFamily(
                base_name = 
                    base_name, 

                source_kind = 
                    source_kind, 
            )


        family = family_map[
            base_name
        ]


        family.members.append(
            phi
        )


        family.destinations.append(
            destination
        )


        family.header_blocks.append(
            phi.block_id
        )


        (
            initial_values, 
            feedback_values, 
        ) = split_phi_inputs(
            phi
        )


        family.initial_values.update(
            initial_values
        )


        family.feedback_values.update(
            feedback_values
        )


        grouped += 1


    # Deterministic ordering inside each family.

    for family in list(
        stack_families.values()
    ) + list(
        register_families.values()
    ): 

        family.members.sort(
            key = lambda phi: 
                (
                    phi.block_id, 
                    phi.destination, 
                )
        )

        family.destinations.sort()

        family.header_blocks = sorted(
            set(
                family.header_blocks
            )
        )


    return LogicalStateAnalysis(
        stack_families = 
            stack_families, 

        register_families = 
            register_families, 

        unknown_state_phis = 
            unknown, 

        total_state_phis = 
            len(
                extracted.state_phis
            ), 

        grouped_state_phis = 
            grouped, 
    )


# ============================================================
# Summary helpers
# ============================================================

def count_family_members(
    families: dict[
        str, 
        LogicalStateFamily
    ], 
) -> int: 

    return sum(
        len(
            family.members
        )

        for family
        in families.values()
    )


def multi_header_families(
    families: dict[
        str, 
        LogicalStateFamily
    ], 
) -> list[
    LogicalStateFamily
]: 

    return [
        family

        for family
        in families.values()

        if len(
            family.header_blocks
        ) > 1
    ]