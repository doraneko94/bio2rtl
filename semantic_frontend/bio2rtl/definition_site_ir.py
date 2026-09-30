from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass

from .final_state import FinalStateResult
from .next_state_expr import Expr, build_definition_map, lower_value
from .state_ssa import StateSSAResult

@dataclass
class DefinitionSiteWrite: 
    block: int
    family: str
    expression: Expr
    order: int

@dataclass
class DefinitionSiteIR: 
    writes: list[DefinitionSiteWrite]
    by_block: dict[int, list[DefinitionSiteWrite]]


def build_definition_site_ir(state_ssa: StateSSAResult, final_state: FinalStateResult) -> DefinitionSiteIR: 
    defmap = build_definition_map(state_ssa.blocks)
    definition_by_name = state_ssa.definitions
    writes = []
    for block in state_ssa.blocks: 
        order = 0
        for op in block.ops: 
            if op.dst is None or op.kind!='ASSIGN' or len(op.args)!=1: 
                continue
            d = definition_by_name.get(op.dst)
            if d is None or d.kind!='STATE_DEF' or d.base_state not in final_state.states: 
                continue
            try: 
                expr = lower_value(op.args[0], defmap, final_state, set())
            except Exception: 
                continue
            writes.append(DefinitionSiteWrite(block.id, d.base_state, expr, order))
            order+=1
    by = defaultdict(list)
    for w in writes: 
        by[w.block].append(w)
    return DefinitionSiteIR(writes = writes, by_block = dict(by))
