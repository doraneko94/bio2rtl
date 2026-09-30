from __future__ import annotations
from dataclasses import asdict, dataclass
import json, re
from pathlib import Path
from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_guard_analysis import DefinitionGuardAnalysis
from .definition_site_equivalence import _paths
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult, SemanticExpr, SemanticCondition

@dataclass
class EventVectorPredicateAnalysis: 
    event_vectors: int
    covered_definition_sites: int
    predicate_uses: int
    unique_predicates: int
    predicates_with_bb_identity: int
    predicates_with_phi_identity: int
    predicates_with_semantic_state: int
    rows: list[dict]
    notes: list[str]

def _event_success(events, region): 
    actual = {(e.source, e.target) for e in region.edges}
    return {ev.event_id: {(a, b) for a in ev.detector_blocks for b in ev.success_targets if (a, b) in actual} for ev in events.canonical_events}

def _vector_paths(reach, events, region_name = 'SAMPLE_BB006'): 
    region = reach.regions[region_name]
    success = _event_success(events, region)
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('event-vector predicate path enumeration truncated')
    groups = {}
    for nodes, edges in paths: 
        es = set(edges)
        sig = tuple(sorted(eid for eid, se in success.items() if es&se))
        if sig: 
            groups.setdefault(sig, []).append((tuple(nodes), tuple(edges)))
    return region, groups

def _phi_pred_for_group(paths): 
    d = {}
    for nodes, edges in paths: 
        for a, b in edges: 
            d.setdefault(b, set()).add(a)
    return {b: next(iter(v)) for b, v in d.items() if len(v) == 1}

def _expr(e: SemanticExpr, pred, stats): 
    if e.kind == 'CONST': 
        return ('CONST', e.value)
    if e.kind == 'STATE': 
        return ('STATE', e.state_family)
    if e.kind == 'SEMANTIC_STATE': 
        stats['sem']+=1
        return ('HW_STATE', e.semantic_state_name)
    if e.kind == 'GPIO': 
        return ('GPIO_CURRENT', e.gpio_value)
    if e.kind == 'LIVEIN': 
        return ('LIVEIN', e.livein_name)
    if e.kind == 'OP': 
        return ('OP', e.operation, tuple(_expr(a, pred, stats) for a in e.args))
    if e.kind == 'PHI': 
        p = pred.get(int(e.phi_block)) if e.phi_block is not None else None
        if p is not None: 
            for q, x in e.phi_inputs: 
                if q == p: 
                    return _expr(x, pred, stats)
        stats['phi']+=1
        vals = tuple(sorted({_expr(x, pred, stats) for _, x in e.phi_inputs}, key = repr))
        return ('UNRESOLVED_VALUE_MUX', vals)
    return (e.kind,)

def _cond(c: SemanticCondition, truth, pred, stats): 
    op = c.operation if truth else {'EQ': 'NE', 'NE': 'EQ', 'ULT': 'UGE', 'UGE': 'ULT', 'SLT': 'NOT_SLT'}.get(c.operation, 'NOT_'+c.operation)
    return (op, _expr(c.lhs, pred, stats), _expr(c.rhs, pred, stats))

def _direct(t): 
    m = re.search(r'STATE\(([^)]+)\)=([01])$', t)
    if m: 
        return ('EQ', ('STATE', m.group(1)), ('CONST', int(m.group(2))))
    m = re.search(r'GPIO@BB\d+\[(\d+)\]=([01])$', t)
    if m: 
        return ('EQ', ('GPIO_BIT', int(m.group(1))), ('CONST', int(m.group(2))))

def analyze_event_vector_predicates(reach: SemanticReachResult, events: CanonicalEventAnalysisResult, ssa: SemanticSSAResult, defs: DefinitionGuardAnalysis)->EventVectorPredicateAnalysis: 
    region, groups = _vector_paths(reach, events)
    pred_by_sig = {sig: _phi_pred_for_group(ps) for sig, ps in groups.items()}
    block_by_sig = {sig: set(n for nodes, _ in ps for n in nodes) for sig, ps in groups.items()}
    rows = {}
    covered = set()
    uses = 0
    stats = {'phi': 0, 'sem': 0}
    for rule in defs.rules: 
      for site in rule.sites: 
        if site.region!='SAMPLE_BB006': 
            continue
        for sig, blocks in block_by_sig.items(): 
          if site.block not in blocks: 
              continue
          # A definition site may be reached by a vector that contains extra simultaneous events.
          if site.canonical_events and not set(site.canonical_events).issubset(set(sig)): 
              continue
          covered.add((rule.register, rule.expression_key, site.block, sig))
          pred = pred_by_sig[sig]
          for item in site.residual_literals: 
            uses+=1
            _cat, text = item.split(':', 1) if ':' in item else ('OTHER', item)
            k = _direct(text)
            if k is None: 
              m = re.search(r'BB(\d{3})', text)
              if m and int(m.group(1)) in ssa.branch_conditions: 
                k = _cond(ssa.branch_conditions[int(m.group(1))], ':NOT_' not in text, pred, stats)
              else: 
                  k = ('UNRESOLVED_TEXT', text)
            key = (sig, k)
            rows[key] = rows.get(key, 0)+1
    out = [{'uses': n, 'event_vector': list(k[0]), 'predicate': repr(k[1])} for k, n in sorted(rows.items(), key = lambda kv: (-kv[1], repr(kv[0])))]
    return EventVectorPredicateAnalysis(len(groups), len(covered), uses, len(rows), sum('BB' in x['predicate'] for x in out), sum('UNRESOLVED_VALUE_MUX' in x['predicate'] or 'PHI' in x['predicate'] for x in out), sum('HW_STATE' in x['predicate'] for x in out), out, [
      'Predicates are conditioned on the complete simultaneous hardware-event vector, not on one event in isolation.', 
      'This resolves event-local PHIs whose predecessor depends on another event occurring in the same polling iteration.', 
      'No CFG edge/reach identity is part of a normalized predicate.', 
    ])

def write_event_vector_predicate_report(r, path: Path): 
    lines = ['EVENT-VECTOR CONDITIONED HARDWARE PREDICATES', '='*78, f'event vectors                  : {r.event_vectors}', f'covered definition/vector sites: {r.covered_definition_sites}', f'predicate uses                 : {r.predicate_uses}', f'unique predicates              : {r.unique_predicates}', f'predicates with BB identity    : {r.predicates_with_bb_identity}', f'predicates with PHI identity   : {r.predicates_with_phi_identity}', f'predicates with HW sem state   : {r.predicates_with_semantic_state}', '', 'Predicates', '-'*78]
    for x in r.rows: 
        lines.append(f"{x['uses']:3d}x {'+'.join(x['event_vector'])} {x['predicate']}")
    lines += ['', 'Notes', '-'*78]+[f'- {x}' for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
