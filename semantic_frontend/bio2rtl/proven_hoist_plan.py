from __future__ import annotations
from dataclasses import dataclass

from .definition_site_analysis import DefinitionSiteAnalysis
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .barrier_retime_analysis import BarrierRetimeAnalysis, build_retimed_next_state
from .sample_barrier_analysis import SampleBarrierAnalysis
from .sample_barrier_fusion_analysis import SampleBarrierFusionAnalysis
from .semantic_reach import SemanticReachResult
from .next_state_expr import Expr, NextStateExprIR

@dataclass
class ProvenHoistRule: 
    register: str
    expression_key: str
    expression: Expr
    definition_block: int
    operation_kind: str

@dataclass
class ProvenHoistPlan: 
    retimed_next_state: NextStateExprIR
    rules: list[ProvenHoistRule]


def build_proven_hoist_plan(reach: SemanticReachResult, next_state: NextStateExprIR, behavior: HardwareBehaviorIR, 
    definitions: DefinitionSiteAnalysis, barriers: SampleBarrierAnalysis, fusion: SampleBarrierFusionAnalysis, 
    retime_analysis: BarrierRetimeAnalysis)->ProvenHoistPlan: 
    retimed_ir, _ = build_retimed_next_state(reach, next_state, behavior, barriers, fusion)
    passing = {(c.register, c.operation_kind, c.expression_key, c.definition_block)
             for c in retime_analysis.post_retime_hoist.candidates if c.equivalent}
    rules = []
    defmap = {(f.family, f.expression_key): f for f in definitions.forms}
    for r in behavior.rules: 
        ek = repr(expr_key(r.expression))
        form = defmap.get((r.register, ek))
        if not form or form.definition_count!=1: 
            continue
        db = form.definition_blocks[0]
        if (r.register, r.operation_kind, ek, db) in passing: 
            rules.append(ProvenHoistRule(r.register, ek, r.expression, db, r.operation_kind))
    return ProvenHoistPlan(retimed_ir, rules)
