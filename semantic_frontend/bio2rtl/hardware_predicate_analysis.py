from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .semantic_ssa import SemanticSSAResult, SemanticExpr, SemanticCondition
from .definition_guard_analysis import DefinitionGuardAnalysis

@dataclass
class HardwarePredicateAnalysis: 
    residual_literal_uses: int
    unique_hardware_predicates: int
    bb_named_literals: int
    semantic_state_atoms: int
    physical_state_atoms: int
    gpio_atoms: int
    predicates: list[dict]
    notes: list[str]

def _expr_key(e: SemanticExpr): 
    if e.kind == 'CONST': 
        return ('CONST', e.value)
    if e.kind == 'STATE': 
        return ('STATE', e.state_family)
    if e.kind == 'SEMANTIC_STATE': 
        return ('SEM_STATE', e.semantic_state_name)
    if e.kind == 'GPIO': 
        return ('GPIO', e.gpio_block, e.gpio_value)
    if e.kind == 'LIVEIN': 
        return ('LIVEIN', e.livein_name)
    if e.kind == 'OP': 
        return ('OP', e.operation, tuple(_expr_key(a) for a in e.args))
    if e.kind == 'PHI': 
        return ('PHI', tuple((p, _expr_key(x)) for p, x in e.phi_inputs))
    return (e.kind,)

def _condition_key(c: SemanticCondition, truth: bool): 
    op = c.operation
    if not truth: 
        op = {'EQ': 'NE', 'NE': 'EQ', 'ULT': 'UGE', 'UGE': 'ULT'}.get(op, 'NOT_'+op)
    return (op, _expr_key(c.lhs), _expr_key(c.rhs))

def _walk(k, counts): 
    if not isinstance(k, tuple): 
        return
    if k and k[0] == 'STATE': 
        counts['physical']+=1
    elif k and k[0] == 'SEM_STATE': 
        counts['semantic']+=1
    elif k and k[0] == 'GPIO': 
        counts['gpio']+=1
    for x in k[1:]: 
        if isinstance(x, tuple): 
            if x and isinstance(x[0], tuple): 
                for y in x: 
                    _walk(y, counts)
            else: 
                _walk(x, counts)

def analyze_hardware_predicates(ssa: SemanticSSAResult, defs: DefinitionGuardAnalysis)->HardwarePredicateAnalysis: 
    keys = {}
    uses = 0
    bb_named = 0
    for rule in defs.rules: 
        for site in rule.sites: 
            # map residual text back to branch block prefix BBxxx
            for text in site.residual_literals: 
                uses+=1
                try: 
                    payload = text.split(':', 1)[1] if ':' in text else text
                    b = int(payload[2:5])
                except Exception: 
                    bb_named+=1
                    continue
                cond = ssa.branch_conditions.get(b)
                if cond is None: 
                    bb_named+=1
                    continue
                # infer truth from textual EQ/NOT_EQ convention
                truth = ':EQ(' in text or (':NOT_' not in text and ':NE(' not in text)
                # write_guard text uses EQ for true branch and NOT_EQ for false for EQ conditions
                if ':NOT_EQ(' in text: 
                    truth = False
                elif ':EQ(' in text: 
                    truth = True
                elif ':NOT_NE(' in text: 
                    truth = False
                elif ':NE(' in text: 
                    truth = True
                k = _condition_key(cond, truth)
                keys[k] = keys.get(k, 0)+1
    counts = {'physical': 0, 'semantic': 0, 'gpio': 0}
    for k in keys: 
        _walk(k, counts)
    rows = [{'uses': n, 'predicate': repr(k)} for k, n in sorted(keys.items(), key = lambda kv: (-kv[1], repr(kv[0])))]
    return HardwarePredicateAnalysis(uses, len(keys), bb_named, counts['semantic'], counts['physical'], counts['gpio'], rows, [
      'Predicate identity is derived from semantic expression structure, not basic-block identity.', 
      'BB numbers remain only in GPIO sample provenance and PHI predecessor provenance; they are not predicate identities.', 
      'This is a diagnostic frontier: PHI/semantic-state atoms still need hardware-state recovery before CFG-free RTL lowering.'
    ])

def write_hardware_predicate_report(r: HardwarePredicateAnalysis, path: Path): 
    lines = ['HARDWARE PREDICATE NORMALIZATION', '='*78, 
      f'residual literal uses       : {r.residual_literal_uses}', 
      f'unique hardware predicates : {r.unique_hardware_predicates}', 
      f'unresolved BB literals      : {r.bb_named_literals}', 
      f'physical-state atoms        : {r.physical_state_atoms}', 
      f'semantic-state atoms        : {r.semantic_state_atoms}', 
      f'GPIO atoms                  : {r.gpio_atoms}', '', 'Predicates', '-'*78]
    for x in r.predicates: 
        lines.append(f"{x['uses']:3d}x {x['predicate']}")
    lines += ['', 'Notes', '-'*78]+[f'- {x}' for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
