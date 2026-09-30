from dataclasses import dataclass, field

from .observable_cone import (
    ObservableConeResult, 
)

from .logical_state import (
    LogicalStateAnalysis, 
)

from .carrier_update import (
    RegisterUpdateResult, 
    stack_state_base
)


@dataclass
class DffCandidateResult: 
    stack_candidates: set[str]

    excluded_register_families: set[str]

    unresolved_register_families: set[str]

    dead_stack_families: set[str]

    total_stack_candidates: int

    total_excluded_registers: int


def select_dff_candidates(
    observable: ObservableConeResult, 
    logical: LogicalStateAnalysis, 
    reg_updates: RegisterUpdateResult, 
) -> DffCandidateResult: 

    # ========================================================
    # Stack families:
    #
    # observable coneに残っているものだけをDFF候補とする。
    # ========================================================

    stack_candidates = set(
        observable.live_stack_families
    )


    dead_stack_families = (
        set(
            logical.stack_families
        )
        - stack_candidates
    )


    # ========================================================
    # Register families:
    #
    # carrier candidateはCPU一時状態なのでDFF候補から除外。
    #
    # x1だけはlive-in問題があったが、
    # direct observable useが無くPHIのみだったことを
    # 別ステップで確認済み。
    #
    # したがって今回、LIVE register familiesはすべて
    # DFF候補から除外する。
    # ========================================================

    excluded_register_families = set(
        observable.live_register_families
    )


    # 念のため、carrier解析で未分類のregisterが無いか確認。

    known_registers = (
        set(
            reg_updates.carrier_candidates
        )
        |
        set(
            reg_updates.true_state_candidates
        )
    )


    unresolved_register_families = (
        set(
            observable.live_register_families
        )
        - known_registers
    )


    return DffCandidateResult(
        stack_candidates = 
            stack_candidates, 

        excluded_register_families = 
            excluded_register_families, 

        unresolved_register_families = 
            unresolved_register_families, 

        dead_stack_families = 
            dead_stack_families, 

        total_stack_candidates = 
            len(
                stack_candidates
            ), 

        total_excluded_registers = 
            len(
                excluded_register_families
            ), 
    )
