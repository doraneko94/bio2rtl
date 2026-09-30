from __future__ import annotations

from dataclasses import dataclass, field

from .fsm_ir import FSMIR

from .structural_netlist import (
    StructuralNetlist, 
)


# ============================================================
# Control condition attached to one CFG/FSM edge
# ============================================================

@dataclass(frozen = True)
class ControlCondition: 
    kind: str
    # ALWAYS
    # TRUE_BRANCH
    # FALSE_BRANCH

    source_state: int

    condition_text: str | None = None


# ============================================================
# One structural state write with its control condition
# ============================================================

@dataclass
class ControlledWrite: 
    source_block: int
    target_block: int

    state_node: str
    expression_node: str

    condition: ControlCondition


# ============================================================
# All writes targeting one physical state node
# ============================================================

@dataclass
class ControlledState: 
    state_node: str

    writes: list[
        ControlledWrite
    ] = field(
        default_factory = list
    )


# ============================================================
# Complete control-annotated structural netlist
# ============================================================

@dataclass
class ControlNetlist: 
    states: dict[
        str, 
        ControlledState, 
    ]

    writes: list[
        ControlledWrite, 
    ]

    total_writes: int

    always_writes: int
    conditional_writes: int


# ============================================================
# FSM transition -> edge control condition
# ============================================================

def build_transition_conditions(
    fsm: FSMIR, 
) -> dict[
    tuple[int, int], 
    ControlCondition, 
]: 
    """
    Convert FSMTransition objects into control conditions
    associated with concrete CFG/FSM edges.

    Actual FSMTransition representation:

        source
        kind
        true_target
        false_target
        condition
        address

    GOTO:
        kind == "GOTO"
        true_target = destination
        false_target = None

    BRANCH:
        kind == "BRANCH"
        true_target
        false_target
        condition
    """

    result: dict[
        tuple[int, int], 
        ControlCondition, 
    ] = {}


    for state_id, state in sorted(
        fsm.states.items()
    ): 

        transition = state.transition


        # ====================================================
        # Terminal state
        # ====================================================

        if transition is None: 
            continue


        # ====================================================
        # Internal consistency
        # ====================================================

        if transition.source != state_id: 

            raise RuntimeError(
                f"BB{state_id}: "
                f"transition source mismatch: "
                f"{transition.source}"
            )


        # ====================================================
        # GOTO
        #
        # The FSM IR stores the destination in true_target.
        # ====================================================

        if transition.kind == "GOTO": 

            if transition.true_target is None: 

                raise RuntimeError(
                    f"BB{state_id}: "
                    "GOTO without true_target"
                )


            if transition.false_target is not None: 

                raise RuntimeError(
                    f"BB{state_id}: "
                    "GOTO unexpectedly has "
                    "false_target"
                )


            edge = (
                state_id, 
                transition.true_target, 
            )


            if edge in result: 

                raise RuntimeError(
                    f"duplicate FSM edge: "
                    f"BB{edge[0]} -> BB{edge[1]}"
                )


            result[
                edge
            ] = ControlCondition(
                kind = "ALWAYS", 
                source_state = state_id, 
                condition_text = None, 
            )

            continue


        # ====================================================
        # BRANCH
        # ====================================================

        if transition.kind == "BRANCH": 

            if transition.true_target is None: 

                raise RuntimeError(
                    f"BB{state_id}: "
                    "BRANCH without true_target"
                )


            if transition.false_target is None: 

                raise RuntimeError(
                    f"BB{state_id}: "
                    "BRANCH without false_target"
                )


            if transition.condition is None: 

                raise RuntimeError(
                    f"BB{state_id}: "
                    "BRANCH without condition"
                )


            true_edge = (
                state_id, 
                transition.true_target, 
            )

            false_edge = (
                state_id, 
                transition.false_target, 
            )


            if true_edge in result: 

                raise RuntimeError(
                    f"duplicate FSM edge: "
                    f"BB{true_edge[0]} "
                    f"-> BB{true_edge[1]}"
                )


            if false_edge in result: 

                raise RuntimeError(
                    f"duplicate FSM edge: "
                    f"BB{false_edge[0]} "
                    f"-> BB{false_edge[1]}"
                )


            result[
                true_edge
            ] = ControlCondition(
                kind = "TRUE_BRANCH", 
                source_state = state_id, 
                condition_text = str(
                    transition.condition
                ), 
            )


            result[
                false_edge
            ] = ControlCondition(
                kind = "FALSE_BRANCH", 
                source_state = state_id, 
                condition_text = str(
                    transition.condition
                ), 
            )

            continue


        # ====================================================
        # Unsupported transition kind
        # ====================================================

        raise RuntimeError(
            f"BB{state_id}: "
            f"unsupported transition kind "
            f"{transition.kind!r}"
        )


    return result


# ============================================================
# Main lowering
# ============================================================

def build_control_netlist(
    structural: StructuralNetlist, 
    fsm: FSMIR, 
) -> ControlNetlist: 
    """
    Attach an FSM edge condition to every structural state
    write.

    This is application-independent. It uses only:

        structural next-state writes
        +
        CFG/FSM edge semantics

    No I2C-specific knowledge is used.
    """

    transitions = (
        build_transition_conditions(
            fsm
        )
    )


    controlled_states: dict[
        str, 
        ControlledState, 
    ] = {}


    controlled_writes: list[
        ControlledWrite
    ] = []


    always_count = 0
    conditional_count = 0


    # ========================================================
    # Attach edge conditions to structural writes
    # ========================================================

    for write in structural.writes: 

        edge = (
            write.source_block, 
            write.target_block, 
        )


        condition = transitions.get(
            edge
        )


        if condition is None: 

            raise RuntimeError(
                "No FSM transition found for "
                "structural state write: "
                f"BB{write.source_block} "
                f"-> BB{write.target_block}"
            )


        controlled = ControlledWrite(
            source_block = 
                write.source_block, 

            target_block = 
                write.target_block, 

            state_node = 
                write.state_node, 

            expression_node = 
                write.expression_node, 

            condition = 
                condition, 
        )


        controlled_writes.append(
            controlled
        )


        controlled_state = (
            controlled_states.setdefault(
                write.state_node, 
                ControlledState(
                    state_node = 
                        write.state_node
                ), 
            )
        )


        controlled_state.writes.append(
            controlled
        )


        if condition.kind == "ALWAYS": 

            always_count += 1


        elif condition.kind in {
            "TRUE_BRANCH", 
            "FALSE_BRANCH", 
        }: 

            conditional_count += 1


        else: 

            raise RuntimeError(
                f"unknown control condition "
                f"{condition.kind!r}"
            )


    # ========================================================
    # Consistency checks
    # ========================================================

    if (
        len(controlled_writes)
        != len(structural.writes)
    ): 

        raise RuntimeError(
            "control-netlist lowering "
            "lost structural writes"
        )


    for (
        state_node, 
        controlled_state, 
    ) in controlled_states.items(): 

        if (
            state_node
            not in structural.nodes
        ): 

            raise RuntimeError(
                f"unknown controlled state node "
                f"{state_node}"
            )


        for write in (
            controlled_state.writes
        ): 

            if (
                write.expression_node
                not in structural.nodes
            ): 

                raise RuntimeError(
                    "controlled write references "
                    "unknown expression node "
                    f"{write.expression_node}"
                )


    return ControlNetlist(
        states = 
            controlled_states, 

        writes = 
            controlled_writes, 

        total_writes = 
            len(controlled_writes), 

        always_writes = 
            always_count, 

        conditional_writes = 
            conditional_count, 
    )