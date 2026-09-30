from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_analysis import DefinitionSiteAnalysis
from .edge_event_analysis import EdgeEventAnalysisResult
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .write_guard_analysis import (_successors, _condition_category, _literal_text, 
    _event_constituent_blocks, _edge_dominates, RequiredLiteral)
from .definition_schedule_analysis import _edge_dominates_block

@dataclass
class DefinitionGuardSite: 
    register: str
    operation_kind: str
    block: int
    region: str|None
    canonical_events: list[str]
    required_literals: list[str]
    residual_literals: list[str]

@dataclass
class DefinitionGuardRule: 
    register: str
    operation_kind: str
    expression_key: str
    edge_occurrences: int
    definition_sites: int
    canonical_event: str|None
    raw_exit_predicate_blocks: int
    definition_required_literal_uses: int
    definition_residual_literal_uses: int
    residual_signatures: list[list[str]]
    sites: list[DefinitionGuardSite]

@dataclass
class DefinitionGuardAnalysis: 
    rules: list[DefinitionGuardRule]
    multi_path_rules: int
    edge_occurrences: int
    definition_sites: int
    definition_required_literal_uses: int
    definition_residual_literal_uses: int
    notes: list[str]


def _required_for_block(region, target_block, semantic_ssa): 
    succ = _successors(region)
    out = []
    for block, cond in sorted(semantic_ssa.branch_conditions.items()): 
        outgoing = [(dst, kind) for dst, kind in succ.get(block, []) if kind in {'TRUE', 'FALSE'}]
        if len(outgoing)<2: 
            continue
        dominated = []
        for dst, kind in outgoing: 
            if _edge_dominates_block(region, (block, dst), target_block): 
                dominated.append((dst, kind))
        if len(dominated)!=1: 
            continue
        _, kind = dominated[0]
        truth = kind == 'TRUE'
        out.append(RequiredLiteral(block, truth, _condition_category(cond), _literal_text(block, truth, cond)))
    return out


def analyze_definition_guards(reach: SemanticReachResult, semantic_ssa: SemanticSSAResult, 
    edge_events: EdgeEventAnalysisResult, canonical_events: CanonicalEventAnalysisResult, 
    definitions: DefinitionSiteAnalysis, behavior: HardwareBehaviorIR): 
    raw_to_canon = {raw: ce.event_id for ce in canonical_events.canonical_events for raw in ce.source_candidates}
    raw_by_region = defaultdict(list)
    for e in edge_events.event_candidates: 
        raw_by_region[e.region].append(e)
    regions_by_block = defaultdict(list)
    for rn, r in reach.regions.items(): 
        for b in r.blocks: 
            regions_by_block[b].append(rn)
    def_lookup = {(f.family, f.expression_key): f for f in definitions.forms}
    rows = []
    for rule in behavior.rules: 
        ek = repr(expr_key(rule.expression))
        form = def_lookup.get((rule.register, ek))
        blocks = form.definition_blocks if form else []
        sites = []
        sigs = set()
        common_event = None
        all_event_sets = []
        reqn = resn = 0
        for b in blocks: 
            rn = regions_by_block.get(b, [None])[0]
            required = []
            events = []
            covered = set()
            if rn is not None: 
                r = reach.regions[rn]
                required = _required_for_block(r, b, semantic_ssa)
                for e in raw_by_region.get(rn, []): 
                    if _edge_dominates_block(r, (e.detector_block, e.success_target), b): 
                        cid = raw_to_canon.get(e.event_id)
                        if cid: 
                            events.append(cid)
                            covered |= _event_constituent_blocks(e)
            residual = [x for x in required if x.block not in covered]
            reqn+=len(required)
            resn+=len(residual)
            sig = tuple(sorted(f'{x.category}:{x.text}' for x in residual))
            sigs.add(sig)
            all_event_sets.append(set(events))
            sites.append(DefinitionGuardSite(rule.register, rule.operation_kind, b, rn, sorted(set(events)), 
                [f'{x.category}:{x.text}' for x in required], [f'{x.category}:{x.text}' for x in residual]))
        common = set(all_event_sets[0]) if all_event_sets else set()
        for e in all_event_sets[1:]: 
            common &= e
        ce = next(iter(common)) if len(common) == 1 else None
        # For comparison, count distinct predicate blocks remaining in exact grouped exit-enable report proxy:
        exit_blocks = set()
        for ge in rule.guard_edges: 
            # source/target are CFG ids, not predicate ids; use occurrence count as conservative proxy below.
            pass
        rows.append(DefinitionGuardRule(rule.register, rule.operation_kind, ek, rule.occurrence_count, len(blocks), ce, 
            0, reqn, resn, [list(x) for x in sorted(sigs)], sites))
    multi = [r for r in rows if r.edge_occurrences>1]
    return DefinitionGuardAnalysis(rows, len(multi), sum(r.edge_occurrences for r in multi), sum(r.definition_sites for r in multi), 
        sum(r.definition_required_literal_uses for r in multi), sum(r.definition_residual_literal_uses for r in multi), [
        'Guards are measured at original STATE_DEF blocks, so downstream CPU branches after an assignment do not contaminate the hardware enable.', 
        'Recovered physical-event constituent predicates are removed only when the event success edge dominates the definition block.', 
        'Residual predicates are retained as the dedicated hardware operation enable.', 
    ])

def write_definition_guard_report(result, path: Path): 
    lines = ['DEFINITION-SITE HARDWARE ENABLE DIAGNOSTIC', '='*78, 
      f'multi-path rules                    : {result.multi_path_rules}', 
      f'edge-write occurrences              : {result.edge_occurrences}', 
      f'original definition sites           : {result.definition_sites}', 
      f'definition guard literal uses       : {result.definition_required_literal_uses}', 
      f'after hardware-event abstraction    : {result.definition_residual_literal_uses}', '', 
      'Multi-path operations', '-'*78]
    for r in sorted(result.rules, key = lambda x: (-x.edge_occurrences, x.register)): 
      if r.edge_occurrences<=1: 
          continue
      lines += [f'{r.register}: {r.operation_kind}', f'  edge writes       : {r.edge_occurrences}', f'  definition sites  : {r.definition_sites}', 
                f'  common event      : {r.canonical_event or "-"}', f'  def guard literals: {r.definition_required_literal_uses} -> {r.definition_residual_literal_uses}', 
                f'  residual forms    : {len(r.residual_signatures)}']
      for s in r.sites: 
        lines += [f'    BB{s.block:03d} event={",".join(s.canonical_events) or "-"}', f'      residual: {" && ".join(s.residual_literals) or "<event only>"}']
      lines.append('')
    lines += ['Notes', '-'*78]+[f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
