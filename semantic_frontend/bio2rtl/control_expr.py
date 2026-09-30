from __future__ import annotations

from dataclasses import dataclass, field
import re

from .ir import (
    IROp, 
    IRBlock, 
)

from .logical_state import (
    stack_state_base, 
)

from .structural_netlist import (
    StructuralNetlist, 
)

from .control_netlist import (
    ControlNetlist, 
    ControlCondition, 
)

from .ssa_opt import (
    parse_phi_arg, 
    values_used_by_op, 
)

from .dff_width_infer import (
    eval_constant, 
)

from .fsm_ir import FSMIR


# ============================================================
# Generic control expression
# ============================================================

@dataclass(frozen = True)
class ControlExpr: 
    kind: str
    # CONST
    # STATE
    # CONTROL_STATE
    # GPIO
    # OP
    # PHI_MUX
    # LIVEIN

    value: int | None = None

    state_family: str | None = None

    control_state_name: str | None = None

    gpio_value: str | None = None

    operation: str | None = None

    args: tuple["ControlExpr", ...] = ()

    # PHI_MUX:
    #
    # (predecessor_state, expression)
    phi_inputs: tuple[
        tuple[int, "ControlExpr"], 
        ...
    ] = ()

    livein_name: str | None = None


@dataclass(frozen = True)
class LoweredCondition: 
    operation: str
    # EQ
    # NE
    # ULT
    # UGE
    # SLT

    lhs: ControlExpr
    rhs: ControlExpr


@dataclass
class LoweredControlCondition: 
    source_state: int

    kind: str
    # ALWAYS
    # TRUE_BRANCH
    # FALSE_BRANCH

    expression: LoweredCondition | None


@dataclass
class ControlPhiState: 
    name: str
    phi_value: str
    defining_block: int

    # predecessor BB -> value loaded when entering defining_block
    incoming: tuple[
        tuple[int, ControlExpr], 
        ...
    ]


@dataclass
class ControlExprResult: 
    conditions: dict[
        tuple[
            int, 
            int, 
            str, 
        ], 
        LoweredControlCondition, 
    ]

    branch_conditions: dict[
        int, 
        LoweredCondition, 
    ]

    unique_branch_expressions: int

    phi_mux_count: int

    control_states: dict[
        str, 
        ControlPhiState, 
    ]

    livein_values: set[str]

    unresolved: list[str]


# ============================================================
# Definition map
# ============================================================

def build_definition_map(
    blocks: list[IRBlock], 
) -> dict[str, tuple[int, IROp]]: 

    result = {}

    for block in blocks: 

        for op in block.ops: 

            if op.dst is not None: 

                result[
                    op.dst
                ] = (
                    block.id, 
                    op, 
                )

    return result


# ============================================================
# PHI predecessor parsing
# ============================================================

def predecessor_id(
    text: str, 
) -> int: 

    if text.startswith("BB"): 
        return int(
            text[2:]
        )

    return int(
        text
    )


# ============================================================
# SSA value -> generic hardware expression
# ============================================================

class ControlExprBuilder: 

    def __init__(
        self, 
        blocks: list[IRBlock], 
        structural: StructuralNetlist, 
    ): 

        self.definition_map = (
            build_definition_map(
                blocks
            )
        )

        self.structural = structural

        self.cache: dict[
            str, 
            ControlExpr, 
        ] = {}

        self.phi_mux_count = 0

        self.liveins: set[str] = set()

        self.unresolved: list[str] = []

        self.control_states: dict[
            str, 
            ControlPhiState, 
        ] = {}

        self.active_phi_values: set[str] = set()

        self.persistent_phi_values: set[str] = set()


    def build(
        self, 
        value: str, 
    ) -> ControlExpr: 

        value = value.strip()


        # ----------------------------------------------------
        # x0
        # ----------------------------------------------------

        if value == "x0": 

            return ControlExpr(
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

            return ControlExpr(
                kind = "CONST", 
                value = constant, 
            )


        # ----------------------------------------------------
        # Already lowered
        # ----------------------------------------------------

        cached = self.cache.get(
            value
        )

        if cached is not None: 
            return cached


        # ----------------------------------------------------
        # Stack physical state
        # ----------------------------------------------------

        family = stack_state_base(
            value
        )

        if (
            family is not None
            and family
            in self.structural.state_nodes
        ): 

            result = ControlExpr(
                kind = "STATE", 
                state_family = family, 
            )

            self.cache[
                value
            ] = result

            return result


        # ----------------------------------------------------
        # Missing definition = architectural live-in
        # ----------------------------------------------------

        definition = (
            self.definition_map.get(
                value
            )
        )

        if definition is None: 

            # SSA version zero is a legitimate architectural
            # live-in. Do not guess its value.

            if re.match(
                r"^x\d+_0$", 
                value, 
            ): 

                self.liveins.add(
                    value
                )

                result = ControlExpr(
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
                f"missing control SSA definition: "
                f"{value}"
            )


        # ----------------------------------------------------
        # Cycle protection
        # ----------------------------------------------------


        block_id, op = definition

        result = self._build_from_op(
            value, 
            block_id, 
            op, 
        )


        self.cache[
            value
        ] = result

        return result


    def _build_from_op(
        self, 
        value: str, 
        block_id: int, 
        op: IROp, 
    ) -> ControlExpr: 

        # ----------------------------------------------------
        # CONST
        # ----------------------------------------------------

        if op.kind == "CONST": 

            if not op.args: 

                raise RuntimeError(
                    f"{value}: CONST without args"
                )


            constant = eval_constant(
                op.args[0]
            )

            if constant is None: 

                raise RuntimeError(
                    f"{value}: cannot evaluate "
                    f"CONST {op.args[0]!r}"
                )


            return ControlExpr(
                kind = "CONST", 
                value = constant, 
            )


        # ----------------------------------------------------
        # GPIO
        # ----------------------------------------------------

        if op.kind == "GPIO_READ": 

            return ControlExpr(
                kind = "GPIO", 
                gpio_value = value, 
            )


        # ----------------------------------------------------
        # PHI
        #
        # Do NOT guess an alias.
        #
        # A PHI naturally becomes a hardware mux selected by
        # the predecessor control state.
        # ----------------------------------------------------

        if op.kind == "PHI": 

            # =================================================
            # PHI semantics in FSM-style RTL
            #
            # A PHI value is selected by the CFG edge that
            # ENTERS the PHI's defining block.
            #
            # Once execution leaves that block, the predecessor
            # information is no longer represented by
            # fsm_state.
            #
            # Therefore every control PHI that reaches this
            # lowering stage becomes a persistent control
            # register updated on incoming CFG edges.
            #
            # Stack-state PHIs have already been converted to
            # StructuralNetlist state before reaching here.
            # =================================================

            state_name = (
                "control_"
                + value
            )


            # -------------------------------------------------
            # Recursive reference while lowering incoming
            # expressions means "current value of this PHI
            # register".
            # -------------------------------------------------

            if value in self.active_phi_values: 

                return ControlExpr(
                    kind = "CONTROL_STATE", 
                    control_state_name = 
                        state_name, 
                )


            # -------------------------------------------------
            # Parse incoming edge values.
            # -------------------------------------------------

            raw_inputs: list[
                tuple[int, str]
            ] = []


            for arg in op.args: 

                parsed = parse_phi_arg(
                    arg
                )


                if parsed is None: 

                    raise RuntimeError(
                        f"{value}: malformed PHI arg "
                        f"{arg!r}"
                    )


                predecessor_text, incoming = (
                    parsed
                )


                pred = predecessor_id(
                    predecessor_text
                )


                raw_inputs.append(
                    (
                        pred, 
                        incoming, 
                    )
                )


            # -------------------------------------------------
            # Lower incoming expressions.
            # -------------------------------------------------

            self.active_phi_values.add(
                value
            )


            try: 

                lowered_inputs: list[
                    tuple[int, ControlExpr]
                ] = []


                for pred, incoming in raw_inputs: 

                    incoming_expr = self.build(
                        incoming
                    )


                    lowered_inputs.append(
                        (
                            pred, 
                            incoming_expr, 
                        )
                    )


            finally: 

                self.active_phi_values.remove(
                    value
                )


            # -------------------------------------------------
            # Materialize the PHI as hardware state.
            # -------------------------------------------------

            self.control_states[
                value
            ] = ControlPhiState(
                name = 
                    state_name, 

                phi_value = 
                    value, 

                defining_block = 
                    block_id, 

                incoming = tuple(
                    lowered_inputs
                ), 
            )


            return ControlExpr(
                kind = "CONTROL_STATE", 
                control_state_name = 
                    state_name, 
            )
        
        # ----------------------------------------------------
        # Normal combinational operations
        # ----------------------------------------------------

        supported = {
            "ADD", 
            "AND", 
            "OR", 
            "SHL", 
            "SHR", 
        }


        if op.kind in supported: 

            # =================================================
            # IMPORTANT:
            #
            # Do not use values_used_by_op() here.
            #
            # values_used_by_op() intentionally returns only
            # SSA value dependencies and therefore drops
            # literal operands such as:
            #
            #     q + 1
            #     q << 3
            #     q & 0xff
            #
            # For RTL expression reconstruction we must
            # preserve every operand exactly.
            # =================================================

            lowered_args: list[
                ControlExpr
            ] = []


            for raw_arg in op.args: 

                raw_arg = raw_arg.strip()


                # ---------------------------------------------
                # Literal operand
                # ---------------------------------------------

                constant = eval_constant(
                    raw_arg
                )


                if constant is not None: 

                    lowered_args.append(
                        ControlExpr(
                            kind = "CONST", 
                            value = constant, 
                        )
                    )

                    continue


                # ---------------------------------------------
                # SSA/state operand
                # ---------------------------------------------

                lowered_args.append(
                    self.build(
                        raw_arg
                    )
                )


            # ---------------------------------------------
            # Current supported operators are binary.
            # Fail here rather than much later in the
            # SystemVerilog emitter.
            # ---------------------------------------------

            if len(lowered_args) != 2: 

                raise RuntimeError(
                    f"{value}: "
                    f"{op.kind} expected 2 operands, "
                    f"got {len(lowered_args)} "
                    f"from {op.args!r}"
                )


            return ControlExpr(
                kind = "OP", 
                operation = op.kind, 
                args = tuple(
                    lowered_args
                ), 
            )


        # ----------------------------------------------------
        # ASSIGN / copies that survived optimization
        # ----------------------------------------------------

        if (
            op.kind == "ASSIGN"
            and op.args
        ): 

            return self.build(
                op.args[0]
            )


        raise RuntimeError(
            f"{value}: unsupported control "
            f"operation {op.kind}"
        )


# ============================================================
# Branch parser
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


def parse_condition(
    text: str, 
) -> tuple[
    str, 
    str, 
    str, 
]: 

    for pattern, operation in (
        _BRANCH_PATTERNS
    ): 

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
        f"unsupported branch condition: "
        f"{text}"
    )


# ============================================================
# Whole ControlNetlist lowering
# ============================================================

def lower_control_expressions(
    blocks: list[IRBlock], 
    structural: StructuralNetlist, 
    control: ControlNetlist, 
    fsm: FSMIR, 
) -> ControlExprResult: 

    builder = ControlExprBuilder(
        blocks, 
        structural, 
    )

    # ========================================================
    # Lower every FSM branch.
    #
    # This must not depend on whether an edge happens to write
    # a hardware state register. FSM control flow itself must
    # be preserved.
    # ========================================================

    branch_conditions: dict[
        int, 
        LoweredCondition, 
    ] = {}


    for state_id, state in sorted(
        fsm.states.items()
    ): 

        transition = state.transition

        if transition is None: 
            continue

        if transition.kind != "BRANCH": 
            continue

        if transition.condition is None: 
            raise RuntimeError(
                f"BB{state_id}: "
                "BRANCH without condition"
            )

        (
            operation, 
            lhs_text, 
            rhs_text, 
        ) = parse_condition(
            str(
                transition.condition
            )
        )

        lhs = builder.build(
            lhs_text
        )

        rhs = builder.build(
            rhs_text
        )

        branch_conditions[
            state_id
        ] = LoweredCondition(
            operation = operation, 
            lhs = lhs, 
            rhs = rhs, 
        )


    conditions = {}


    for write in control.writes: 

        condition = write.condition


        key = (
            write.source_block, 
            write.target_block, 
            condition.kind, 
        )


        # ----------------------------------------------------
        # Unconditional edge
        # ----------------------------------------------------

        if condition.kind == "ALWAYS": 

            conditions[
                key
            ] = LoweredControlCondition(
                source_state = 
                    condition.source_state, 

                kind = "ALWAYS", 

                expression = None, 
            )

            continue


        # ----------------------------------------------------
        # Conditional edge
        # ----------------------------------------------------

        if condition.condition_text is None: 

            raise RuntimeError(
                f"BB{condition.source_state}: "
                "conditional write has no "
                "condition text"
            )


        lowered = branch_conditions.get(
            condition.source_state
        )

        if lowered is None: 

            raise RuntimeError(
                f"BB{condition.source_state}: "
                "controlled branch write "
                "has no lowered FSM condition"
            )


        conditions[
            key
        ] = LoweredControlCondition(
            source_state = 
                condition.source_state, 

            kind = 
                condition.kind, 

            expression = 
                lowered, 
        )


    return ControlExprResult(
        conditions = conditions, 

        branch_conditions = 
            branch_conditions, 

        unique_branch_expressions = 
            len(branch_conditions), 

        phi_mux_count = 
            builder.phi_mux_count, 

        control_states = 
            builder.control_states, 

        livein_values = 
            builder.liveins, 

        unresolved = 
            builder.unresolved, 
    )