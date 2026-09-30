from __future__ import annotations

from dataclasses import dataclass
import re

from .ir import (
    IRBlock, 
    IROp, 
)

from .fsm_ir import (
    FSMIR, 
)

from .ssa_opt import (
    parse_phi_arg, 
)

from .dff_width_infer import (
    eval_constant, 
)

from .logical_state import (
    stack_state_base, 
)

from .structural_netlist import (
    StructuralNetlist, 
)

from .semantic_reach import (
    SemanticReachResult, 
)


# ============================================================
# Observable operations
# ============================================================


GPIO_EFFECT_KINDS = {
    "GPIO_SET", 
    "GPIO_CLEAR_N", 
    "GPIO_DIR_SET", 
    "GPIO_DIR_CLEAR", 
    "GPIO_MASK", 
}


# ============================================================
# Semantic expressions
# ============================================================


@dataclass(frozen = True)
class SemanticExpr: 
    kind: str

    # CONST
    # STATE
    # SEMANTIC_STATE
    # GPIO
    # LIVEIN
    # OP
    # PHI

    value: int | None = None

    state_family: str | None = None

    semantic_state_name: str | None = None

    livein_name: str | None = None

    # GPIO_READ source information.
    gpio_block: int | None = None
    gpio_value: str | None = None

    operation: str | None = None

    args: tuple[
        "SemanticExpr", 
        ...
    ] = ()

    phi_block: int | None = None

    phi_inputs: tuple[
        tuple[
            int, 
            "SemanticExpr", 
        ], 
        ...
    ] = ()


@dataclass(frozen = True)
class SemanticCondition: 
    operation: str

    lhs: SemanticExpr
    rhs: SemanticExpr


@dataclass(frozen = True)
class SemanticStateDefinition: 
    name: str

    block_id: int

    incoming: tuple[
        tuple[
            int, 
            SemanticExpr, 
        ], 
        ...
    ]


@dataclass
class SemanticSSAResult: 
    expressions: dict[
        str, 
        SemanticExpr, 
    ]

    branch_conditions: dict[
        int, 
        SemanticCondition, 
    ]

    gpio_arguments: dict[
        tuple[
            int, 
            int, 
        ], 
        SemanticExpr, 
    ]

    semantic_state_definitions: dict[
        str, 
        SemanticStateDefinition, 
    ]

    gpio_sample_blocks: set[int]

    roots: set[str]

    liveins: set[str]

    phi_values: set[str]

    semantic_states: set[str]

    unresolved: list[str]

    cycles: list[str]


# ============================================================
# Helpers
# ============================================================


def _predecessor_id(
    text: str, 
) -> int: 

    text = text.strip()

    if text.startswith(
        "BB"
    ): 

        return int(
            text[2:]
        )

    return int(
        text
    )


def _build_definition_map(
    blocks: list[IRBlock], 
) -> dict[
    str, 
    tuple[
        int, 
        IROp, 
    ], 
]: 

    result: dict[
        str, 
        tuple[
            int, 
            IROp, 
        ], 
    ] = {}

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
# Branch parsing
# ============================================================


_BRANCH_PATTERNS = [
    (
        re.compile(
            r"^\s*(.+?)\s*==\s*(.+?)\s*$"
        ), 
        "EQ", 
    ), 
    (
        re.compile(
            r"^\s*(.+?)\s*!=\s*(.+?)\s*$"
        ), 
        "NE", 
    ), 
    (
        re.compile(
            r"^\s*(.+?)\s*<u\s*(.+?)\s*$"
        ), 
        "ULT", 
    ), 
    (
        re.compile(
            r"^\s*(.+?)\s*>=u\s*(.+?)\s*$"
        ), 
        "UGE", 
    ), 
    (
        re.compile(
            r"^\s*signed\((.+?)\)\s*"
            r"<\s*"
            r"signed\((.+?)\)\s*$"
        ), 
        "SLT", 
    ), 
]


def _parse_branch_condition(
    text: str, 
) -> tuple[
    str, 
    str, 
    str, 
]: 

    for (
        pattern, 
        operation, 
    ) in _BRANCH_PATTERNS: 

        match = pattern.match(
            text
        )

        if match is None: 
            continue

        return (
            operation, 
            match.group(1).strip(), 
            match.group(2).strip(), 
        )

    raise RuntimeError(
        "unsupported semantic branch condition: "
        f"{text}"
    )


# ============================================================
# Builder
# ============================================================


class SemanticSSABuilder: 

    def __init__(
        self, 
        blocks: list[IRBlock], 
        structural: StructuralNetlist, 
        sample_blocks: set[int], 
        collapse_state_versions: bool = True, 
    ): 

        self.definition_map = (
            _build_definition_map(
                blocks
            )
        )

        self.structural = (
            structural
        )

        self.sample_blocks = set(
            sample_blocks
        )

        # Legacy semantic RTL collapses every versioned stack-state SSA value
        # back to its persistent family.  Dedicated-hardware recovery must not:
        # versions defined inside one hardware event are transition-local SSA
        # values and must be followed to their definitions.
        self.collapse_state_versions = collapse_state_versions

        self.cache: dict[
            str, 
            SemanticExpr, 
        ] = {}

        self.liveins: set[
            str
        ] = set()

        self.phi_values: set[
            str
        ] = set()

        self.semantic_states: set[
            str
        ] = set()

        self.gpio_sample_blocks: set[
            int
        ] = set()

        self.semantic_state_definitions: dict[
            str, 
            SemanticStateDefinition, 
        ] = {}

        self.unresolved: list[
            str
        ] = []

        self.cycles: list[
            str
        ] = []

        self.active: set[
            str
        ] = set()


    # ========================================================
    # Value lowering
    # ========================================================


    def build(
        self, 
        value: str, 
    ) -> SemanticExpr: 

        value = value.strip()

        # ----------------------------------------------------
        # x0
        # ----------------------------------------------------

        if value == "x0": 

            return SemanticExpr(
                kind = "CONST", 
                value = 0, 
            )

        # ----------------------------------------------------
        # Literal
        # ----------------------------------------------------

        constant = eval_constant(
            value
        )

        if constant is not None: 

            return SemanticExpr(
                kind = "CONST", 
                value = constant, 
            )

        # ----------------------------------------------------
        # Recovered persistent structural state
        # ----------------------------------------------------

        family = stack_state_base(
            value
        )

        if (
            self.collapse_state_versions
            and family is not None
            and family in self.structural.state_nodes
        ): 
            return SemanticExpr(
                kind = "STATE", 
                state_family = family, 
            )

        # ----------------------------------------------------
        # Cache
        # ----------------------------------------------------

        cached = self.cache.get(
            value
        )

        if cached is not None: 

            return cached

        # ----------------------------------------------------
        # Definition
        # ----------------------------------------------------

        definition = (
            self.definition_map.get(
                value
            )
        )

        if definition is None: 

            # In transition-local mode only an undefined state SSA version
            # (normally family_0) is the persistent hardware live-in.  Defined
            # versions are followed through ASSIGN/PHI above instead of being
            # collapsed to the family.
            if (
                not self.collapse_state_versions
                and family is not None
                and family in self.structural.state_nodes
            ): 
                result = SemanticExpr(kind = "STATE", state_family = family)
                self.cache[value] = result
                return result

            # Architectural register live-in.
            #
            # stack_*_0 is deliberately not accepted.
            if re.match(
                r"^x\d+_0$", 
                value, 
            ): 

                self.liveins.add(
                    value
                )

                result = SemanticExpr(
                    kind = "LIVEIN", 
                    livein_name = value, 
                )

                self.cache[
                    value
                ] = result

                return result

            self.unresolved.append(
                value
            )

            raise RuntimeError(
                "unresolved observable semantic SSA "
                f"value {value}"
            )

        block_id, op = (
            definition
        )

        # ----------------------------------------------------
        # Sample-boundary PHI
        #
        # These values survive from one external-input sample
        # step to another.
        # ----------------------------------------------------

        if (
            op.kind == "PHI"
            and block_id in self.sample_blocks
        ): 
            # For dedicated-HW recovery, a state-SSA PHI at the event/sample
            # boundary is not a second semantic register.  It is precisely the
            # persistent hardware state carried into the transition.
            family = stack_state_base(value)
            if (
                not self.collapse_state_versions
                and family is not None
                and family in self.structural.state_nodes
            ): 
                result = SemanticExpr(kind = "STATE", state_family = family)
                self.cache[value] = result
                return result

            self.phi_values.add(value)
            self.semantic_states.add(value)
            result = SemanticExpr(
                kind = "SEMANTIC_STATE", 
                semantic_state_name = value, 
            )
            self.cache[value] = result
            return result

        # ----------------------------------------------------
        # Remaining recurrence is invalid at this stage.
        # ----------------------------------------------------

        if value in self.active: 

            # Dedicated-HW recovery follows transition-local state SSA
            # definitions.  A recursive state-SSA cycle is therefore not an
            # illegal combinational cycle: it is a loop-carried recurrence and
            # denotes persistent hardware state at the event boundary.
            family = stack_state_base(value)
            if (
                not self.collapse_state_versions
                and family is not None
                and family in self.structural.state_nodes
            ): 
                return SemanticExpr(kind = "STATE", state_family = family)

            self.cycles.append(
                value
            )

            raise RuntimeError(
                "observable semantic SSA cycle "
                f"at {value} "
                f"(BB{block_id})"
            )

        old_active = (
            self.active
        )

        self.active = (
            set(
                old_active
            )
            | {
                value
            }
        )

        try: 

            result = (
                self._build_op(
                    value, 
                    block_id, 
                    op, 
                )
            )

        finally: 

            self.active = (
                old_active
            )

        self.cache[
            value
        ] = result

        return result


    # ========================================================
    # Operation lowering
    # ========================================================


    def _build_op(
        self, 
        value: str, 
        block_id: int, 
        op: IROp, 
    ) -> SemanticExpr: 

        # ----------------------------------------------------
        # GPIO_READ
        #
        # Preserve WHICH sample point produced the value.
        # ----------------------------------------------------

        if op.kind == "GPIO_READ": 

            if (
                block_id
                not in self.sample_blocks
            ): 

                raise RuntimeError(
                    f"{value}: GPIO_READ at "
                    f"non-sample BB{block_id:03d}"
                )

            self.gpio_sample_blocks.add(
                block_id
            )

            return SemanticExpr(
                kind = "GPIO", 
                gpio_block = block_id, 
                gpio_value = value, 
            )

        # ----------------------------------------------------
        # Constant
        # ----------------------------------------------------

        if op.kind == "CONST": 

            if not op.args: 

                raise RuntimeError(
                    f"{value}: CONST "
                    "without argument"
                )

            constant = eval_constant(
                op.args[0]
            )

            if constant is None: 

                raise RuntimeError(
                    f"{value}: invalid CONST "
                    f"{op.args[0]}"
                )

            return SemanticExpr(
                kind = "CONST", 
                value = constant, 
            )

        # ----------------------------------------------------
        # Copy
        # ----------------------------------------------------

        if op.kind == "ASSIGN": 

            if len(
                op.args
            ) != 1: 

                raise RuntimeError(
                    f"{value}: ASSIGN expected "
                    "1 operand, got "
                    f"{len(op.args)}"
                )

            return self.build(
                op.args[0]
            )

        # ----------------------------------------------------
        # Ordinary PHI
        # ----------------------------------------------------

        if op.kind == "PHI": 

            self.phi_values.add(
                value
            )

            incoming: list[
                tuple[
                    int, 
                    SemanticExpr, 
                ]
            ] = []

            for raw in op.args: 

                parsed = parse_phi_arg(
                    raw
                )

                if parsed is None: 

                    raise RuntimeError(
                        f"{value}: malformed "
                        f"PHI input {raw}"
                    )

                (
                    predecessor_text, 
                    incoming_value, 
                ) = parsed

                predecessor = (
                    _predecessor_id(
                        predecessor_text
                    )
                )

                incoming.append(
                    (
                        predecessor, 
                        self.build(
                            incoming_value
                        ), 
                    )
                )

            return SemanticExpr(
                kind = "PHI", 
                phi_block = block_id, 
                phi_inputs = tuple(
                    incoming
                ), 
            )

        # ----------------------------------------------------
        # Combinational operations
        # ----------------------------------------------------

        if op.kind in {
            "ADD", 
            "AND", 
            "OR", 
            "SHL", 
            "SHR", 
        }: 

            lowered_args: list[
                SemanticExpr
            ] = []

            for raw_arg in op.args: 

                raw_arg = (
                    raw_arg.strip()
                )

                constant = eval_constant(
                    raw_arg
                )

                if constant is not None: 

                    lowered_args.append(
                        SemanticExpr(
                            kind = "CONST", 
                            value = constant, 
                        )
                    )

                else: 

                    lowered_args.append(
                        self.build(
                            raw_arg
                        )
                    )

            if len(
                lowered_args
            ) != 2: 

                raise RuntimeError(
                    f"{value}: "
                    f"{op.kind} expected "
                    "2 operands"
                )

            return SemanticExpr(
                kind = "OP", 
                operation = op.kind, 
                args = tuple(
                    lowered_args
                ), 
            )

        raise RuntimeError(
            f"{value}: unsupported "
            f"semantic SSA op "
            f"{op.kind} "
            f"at BB{block_id}"
        )


    # ========================================================
    # Register every GPIO_READ
    #
    # next_state_expr may refer to a GPIO_READ value that does
    # not participate in a branch or GPIO output side effect.
    #
    # GPIO_READ itself is a real external sample and therefore
    # must always exist in semantic SSA.
    # ========================================================


    def build_all_gpio_reads(
        self, 
    ) -> None: 

        for (
            value, 
            (
                block_id, 
                op, 
            ), 
        ) in sorted(
            self.definition_map.items()
        ): 

            if op.kind != "GPIO_READ": 
                continue

            if (
                block_id
                not in self.sample_blocks
            ): 

                raise RuntimeError(
                    f"{value}: GPIO_READ "
                    "definition is not a "
                    "semantic sample boundary: "
                    f"BB{block_id:03d}"
                )

            self.build(
                value
            )


    # ========================================================
    # Semantic-state next values
    # ========================================================


    def build_semantic_state_definitions(
        self, 
    ) -> None: 

        while True: 

            pending = [
                name

                for name
                in sorted(
                    self.semantic_states
                )

                if name
                not in self.semantic_state_definitions
            ]

            if not pending: 
                break

            for name in pending: 

                definition = (
                    self.definition_map.get(
                        name
                    )
                )

                if definition is None: 

                    raise RuntimeError(
                        "semantic state has "
                        "no definition: "
                        f"{name}"
                    )

                block_id, op = (
                    definition
                )

                if op.kind != "PHI": 

                    raise RuntimeError(
                        f"{name}: semantic "
                        "state is not PHI"
                    )

                incoming: list[
                    tuple[
                        int, 
                        SemanticExpr, 
                    ]
                ] = []

                for raw in op.args: 

                    parsed = parse_phi_arg(
                        raw
                    )

                    if parsed is None: 

                        raise RuntimeError(
                            f"{name}: malformed "
                            "semantic-state PHI "
                            f"{raw}"
                        )

                    (
                        predecessor_text, 
                        incoming_value, 
                    ) = parsed

                    predecessor = (
                        _predecessor_id(
                            predecessor_text
                        )
                    )

                    incoming.append(
                        (
                            predecessor, 
                            self.build(
                                incoming_value
                            ), 
                        )
                    )

                self.semantic_state_definitions[
                    name
                ] = SemanticStateDefinition(
                    name = name, 
                    block_id = block_id, 
                    incoming = tuple(
                        incoming
                    ), 
                )


# ============================================================
# Branch roots
# ============================================================


def _lower_branch_conditions(
    fsm: FSMIR, 
    builder: SemanticSSABuilder, 
    roots: set[str], 
) -> dict[
    int, 
    SemanticCondition, 
]: 

    result: dict[
        int, 
        SemanticCondition, 
    ] = {}

    for (
        state_id, 
        state, 
    ) in sorted(
        fsm.states.items()
    ): 

        transition = (
            state.transition
        )

        if transition is None: 
            continue

        if (
            transition.kind
            != "BRANCH"
        ): 

            continue

        if (
            transition.condition
            is None
        ): 

            raise RuntimeError(
                f"BB{state_id}: BRANCH "
                "without condition"
            )

        (
            operation, 
            lhs_text, 
            rhs_text, 
        ) = _parse_branch_condition(
            str(
                transition.condition
            )
        )

        if eval_constant(
            lhs_text
        ) is None: 

            roots.add(
                lhs_text
            )

        if eval_constant(
            rhs_text
        ) is None: 

            roots.add(
                rhs_text
            )

        result[
            state_id
        ] = SemanticCondition(
            operation = operation, 
            lhs = builder.build(
                lhs_text
            ), 
            rhs = builder.build(
                rhs_text
            ), 
        )

    return result


# ============================================================
# GPIO output-effect roots
# ============================================================


def _lower_gpio_arguments(
    blocks: list[IRBlock], 
    builder: SemanticSSABuilder, 
    roots: set[str], 
) -> dict[
    tuple[
        int, 
        int, 
    ], 
    SemanticExpr, 
]: 

    result: dict[
        tuple[
            int, 
            int, 
        ], 
        SemanticExpr, 
    ] = {}

    for block in blocks: 

        effect_index = 0

        for op in block.ops: 

            if (
                op.kind
                not in GPIO_EFFECT_KINDS
            ): 

                continue

            if len(
                op.args
            ) != 1: 

                raise RuntimeError(
                    f"BB{block.id}: "
                    f"{op.kind} expected "
                    "1 argument"
                )

            raw_value = (
                op.args[0].strip()
            )

            if eval_constant(
                raw_value
            ) is None: 

                roots.add(
                    raw_value
                )

            result[
                (
                    block.id, 
                    effect_index, 
                )
            ] = builder.build(
                raw_value
            )

            effect_index += 1

    return result


# ============================================================
# Whole semantic SSA
# ============================================================


def build_semantic_ssa(
    blocks: list[IRBlock], 
    structural: StructuralNetlist, 
    reach: SemanticReachResult, 
    fsm: FSMIR, 
    extra_roots: set[str] | None = None, 
    collapse_state_versions: bool = True, 
) -> SemanticSSAResult: 

    sample_blocks: set[
        int
    ] = {
        region.start_sample

        for region
        in reach.regions.values()

        if (
            region.start_sample
            is not None
        )
    }

    builder = SemanticSSABuilder(
        blocks, 
        structural, 
        sample_blocks, 
        collapse_state_versions = collapse_state_versions, 
    )

    roots: set[
        str
    ] = set()

    # --------------------------------------------------------
    # Crucial:
    #
    # Register every GPIO_READ first.
    #
    # This guarantees that structural next-state expressions can
    # resolve GPIO SSA values even when those reads are outside
    # the branch/GPIO-output observable root cone.
    # --------------------------------------------------------

    builder.build_all_gpio_reads()

    branch_conditions = (
        _lower_branch_conditions(
            fsm, 
            builder, 
            roots, 
        )
    )

    gpio_arguments = (
        _lower_gpio_arguments(
            blocks, 
            builder, 
            roots, 
        )
    )

    # Physical next-state expressions may depend on ordinary register
    # SSA PHIs that are not otherwise observable through a branch or GPIO
    # effect.  Build them explicitly so the backend can resolve the
    # symbolic SEMANTIC leaves emitted by next_state_expr.py.
    if extra_roots: 
        for value in sorted(extra_roots): 
            roots.add(value)
            builder.build(value)

    builder.build_semantic_state_definitions()

    return SemanticSSAResult(
        expressions = dict(
            builder.cache
        ), 

        branch_conditions = 
            branch_conditions, 

        gpio_arguments = 
            gpio_arguments, 

        semantic_state_definitions = dict(
            builder.semantic_state_definitions
        ), 

        gpio_sample_blocks = set(
            builder.gpio_sample_blocks
        ), 

        roots = set(
            roots
        ), 

        liveins = set(
            builder.liveins
        ), 

        phi_values = set(
            builder.phi_values
        ), 

        semantic_states = set(
            builder.semantic_states
        ), 

        unresolved = list(
            builder.unresolved
        ), 

        cycles = list(
            builder.cycles
        ), 
    )


# ============================================================
# Report
# ============================================================


def print_semantic_ssa(
    result: SemanticSSAResult, 
) -> None: 

    print()

    print("=" * 72)
    print("SEMANTIC SSA")
    print("=" * 72)

    print(
        "observable roots     : "
        f"{len(result.roots)}"
    )

    print(
        "expressions          : "
        f"{len(result.expressions)}"
    )

    print(
        "branch conditions    : "
        f"{len(result.branch_conditions)}"
    )

    print(
        "GPIO arguments       : "
        f"{len(result.gpio_arguments)}"
    )

    print(
        "GPIO sample blocks   : "
        f"{len(result.gpio_sample_blocks)}"
    )

    print(
        "PHI values           : "
        f"{len(result.phi_values)}"
    )

    print(
        "semantic states      : "
        f"{len(result.semantic_states)}"
    )

    print(
        "semantic state defs  : "
        f"{len(result.semantic_state_definitions)}"
    )

    print(
        "live-ins             : "
        f"{len(result.liveins)}"
    )

    print(
        "unresolved           : "
        f"{len(result.unresolved)}"
    )

    print(
        "cycles               : "
        f"{len(result.cycles)}"
    )

    if result.gpio_sample_blocks: 

        print(
            "GPIO sample BBs     : "
            + ", ".join(
                f"BB{x:03d}"

                for x
                in sorted(
                    result.gpio_sample_blocks
                )
            )
        )

    if result.semantic_states: 

        print(
            "semantic state names: "
            + ", ".join(
                sorted(
                    result.semantic_states
                )
            )
        )

    if result.semantic_state_definitions: 

        print(
            "semantic state PHIs :"
        )

        for (
            name, 
            definition, 
        ) in sorted(
            result
            .semantic_state_definitions
            .items()
        ): 

            predecessors = ", ".join(
                f"BB{predecessor:03d}"

                for (
                    predecessor, 
                    _expression, 
                ) in definition.incoming
            )

            print(
                f"    {name} "
                f"@ BB{definition.block_id:03d} "
                f"<- {predecessors}"
            )

    if result.liveins: 

        print(
            "live-in names       : "
            + ", ".join(
                sorted(
                    result.liveins
                )
            )
        )

    if result.unresolved: 

        print(
            "unresolved names    : "
            + ", ".join(
                sorted(
                    set(
                        result.unresolved
                    )
                )
            )
        )

    if result.cycles: 

        print(
            "cycle names         : "
            + ", ".join(
                sorted(
                    set(
                        result.cycles
                    )
                )
            )
        )

    print()

    if (
        not result.unresolved
        and
        not result.cycles
        and
        (
            len(
                result.semantic_states
            )
            == 
            len(
                result.semantic_state_definitions
            )
        )
    ): 

        print(
            "SEMANTIC SSA: PASS"
        )

    else: 

        print(
            "SEMANTIC SSA: FAIL"
        )

def build_transition_semantic_ssa(
    blocks: list[IRBlock], 
    structural: StructuralNetlist, 
    reach: SemanticReachResult, 
    fsm: FSMIR, 
    extra_roots: set[str] | None = None, 
) -> SemanticSSAResult: 
    """Semantic SSA for CPU->dedicated-HW recovery.

    Unlike the legacy semantic RTL model, versioned physical-state SSA values
    are followed through their definitions inside a semantic region.  Only
    undefined live-in versions denote persistent hardware state.
    """
    return build_semantic_ssa(
        blocks, structural, reach, fsm, extra_roots, 
        collapse_state_versions = False, 
    )
