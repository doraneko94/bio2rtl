from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json

from .semantic_ssa import SemanticSSAResult, SemanticExpr

@dataclass
class SemanticStateRoleAnalysis: 
    state_count: int
    rows: list[dict]
    notes: list[str]


def _shape(e: SemanticExpr, self_name: str): 
    if e.kind == 'SEMANTIC_STATE' and e.semantic_state_name == self_name: 
        return ('SELF',)
    if e.kind == 'CONST': 
        return ('CONST', e.value)
    if e.kind == 'STATE': 
        return ('PHYSICAL_STATE', e.state_family)
    if e.kind == 'GPIO': 
        return ('GPIO_SAMPLE', e.gpio_block, e.gpio_value)
    if e.kind == 'LIVEIN': 
        return ('LIVEIN', e.livein_name)
    if e.kind == 'SEMANTIC_STATE': 
        return ('OTHER_SEM_STATE', e.semantic_state_name)
    if e.kind == 'OP': 
        return ('OP', e.operation, tuple(_shape(a, self_name) for a in e.args))
    if e.kind == 'PHI': 
        return ('PHI', e.phi_block, tuple((p, _shape(x, self_name)) for p, x in e.phi_inputs))
    return (e.kind,)


def analyze_semantic_state_roles(ssa: SemanticSSAResult) -> SemanticStateRoleAnalysis: 
    rows = []
    for name, d in sorted(ssa.semantic_state_definitions.items()): 
        incoming = [(p, _shape(e, name)) for p, e in d.incoming]
        nonself = [s for _p, s in incoming if s != ('SELF',)]
        uniq = sorted(set(nonself), key = repr)
        has_self = any(s == ('SELF',) for _p, s in incoming)
        if not nonself: 
            role = 'PURE_HOLD'
        elif all(s and s[0] == 'CONST' for s in nonself) and len(uniq) == 1: 
            role = 'CONST_LATCH'
        elif all(s and s[0] == 'GPIO_SAMPLE' for s in nonself) and len(uniq) == 1: 
            role = 'GPIO_HISTORY_LATCH'
        elif len(uniq) == 1: 
            role = 'SINGLE_SOURCE_LATCH'
        else: 
            role = 'CONTROL_HISTORY_STATE'
        rows.append({
            'name': name, 'block_id': d.block_id, 'incoming': incoming, 
            'has_self_hold': has_self, 'nonself_unique': uniq, 'role': role, 
        })
    return SemanticStateRoleAnalysis(len(rows), rows, [
        'Classification is structural and independent of I2C signal names.', 
        'SELF means the PHI carries the previous boundary value; non-self inputs are the actual update sources.', 
    ])


def write_semantic_state_role_report(r: SemanticStateRoleAnalysis, path: Path): 
    lines = ['SEMANTIC STATE ROLE ANALYSIS', '='*78, f'states: {r.state_count}', '']
    for x in r.rows: 
        lines += [f"{x['name']} @ BB{x['block_id']:03d}: {x['role']}", f"  self hold : {x['has_self_hold']}"]
        for p, s in x['incoming']: 
            lines.append(f'  BB{p:03d} -> {s!r}')
        lines.append('')
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
