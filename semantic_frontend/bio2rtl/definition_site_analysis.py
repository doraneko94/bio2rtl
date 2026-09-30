from __future__ import annotations
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .next_state_expr import build_definition_map, lower_value, Expr
from .state_ssa import StateSSAResult
from .final_state import FinalStateResult

@dataclass
class DefinitionForm: 
    family: str
    expression_key: str
    definition_blocks: list[int]
    definition_count: int
    edge_occurrences: int
    inflation: float | None

@dataclass
class DefinitionSiteAnalysis: 
    state_definitions: int
    edge_selected_writes: int
    forms: list[DefinitionForm]
    duplicated_forms: int
    duplicated_edge_occurrences: int
    recoverable_edge_occurrences: int
    notes: list[str]


def _find_assign_rhs(state_ssa: StateSSAResult, name: str) -> str | None: 
    for block in state_ssa.blocks: 
        for op in block.ops: 
            if op.dst == name and op.kind == 'ASSIGN' and len(op.args) == 1: 
                return op.args[0]
    return None


def analyze_definition_sites(state_ssa: StateSSAResult, final_state: FinalStateResult, behavior: HardwareBehaviorIR) -> DefinitionSiteAnalysis: 
    defmap = build_definition_map(state_ssa.blocks)
    defs_by_key: dict[tuple[str, tuple], list[int]] = defaultdict(list)
    total_defs = 0
    for name, d in state_ssa.definitions.items(): 
        if d.kind != 'STATE_DEF' or d.base_state not in final_state.states: 
            continue
        rhs = _find_assign_rhs(state_ssa, name)
        if rhs is None: 
            continue
        try: 
            expr = lower_value(rhs, defmap, final_state, set())
        except Exception: 
            continue
        defs_by_key[(d.base_state, expr_key(expr))].append(d.block_id)
        total_defs += 1

    forms = []
    dup = 0
    dupocc = 0
    recover = 0
    for rule in behavior.rules: 
        key = (rule.register, expr_key(rule.expression))
        blocks = sorted(defs_by_key.get(key, []))
        dc = len(blocks)
        eo = rule.occurrence_count
        if dc and eo>dc: 
            dup += 1
            dupocc += eo
            recover += eo-dc
        forms.append(DefinitionForm(rule.register, repr(key[1]), blocks, dc, eo, (eo/dc if dc else None)))
    return DefinitionSiteAnalysis(
        state_definitions = total_defs, 
        edge_selected_writes = behavior.original_edge_writes, 
        forms = forms, 
        duplicated_forms = dup, 
        duplicated_edge_occurrences = dupocc, 
        recoverable_edge_occurrences = recover, 
        notes = [
            'STATE_DEF sites are recovered before edge-exit next-state expansion.', 
            'A form with fewer definition sites than edge occurrences is direct evidence that state SSA/exit lowering duplicated one CPU assignment across multiple CFG exits.', 
            'This report is diagnostic only and does not change RTL.', 
        ], 
    )


def write_definition_site_report(result: DefinitionSiteAnalysis, path: Path)->None: 
    lines = ['DEFINITION-SITE -> EDGE-WRITE INFLATION DIAGNOSTIC', '='*76, 
           f'original lowered STATE_DEF sites : {result.state_definitions}', 
           f'edge-selected state writes       : {result.edge_selected_writes}', 
           f'duplicated update forms          : {result.duplicated_forms}', 
           f'edge occurrences in dup forms    : {result.duplicated_edge_occurrences}', 
           f'excess edge occurrences          : {result.recoverable_edge_occurrences}', '', 
           'Forms with edge-write inflation', '-'*76]
    for r in sorted(result.forms, key = lambda x: ((x.inflation or 0), x.edge_occurrences), reverse = True): 
        if not r.definition_count or r.edge_occurrences<=r.definition_count: 
            continue
        lines += [f'{r.family}', f'  definition blocks : {", ".join(f"BB{x:03d}" for x in r.definition_blocks)}', 
                  f'  definition sites  : {r.definition_count}', f'  edge occurrences  : {r.edge_occurrences}', 
                  f'  inflation         : {r.inflation:.2f}x', f'  expression        : {r.expression_key}', '']
    lines += ['Notes', '-'*76]+[f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
