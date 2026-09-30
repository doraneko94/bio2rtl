from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_analysis import DefinitionSiteAnalysis
from .edge_event_analysis import EdgeEventAnalysisResult
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .semantic_reach import ReachRegion, SemanticReachResult
from .write_guard_analysis import _edge_reachable_without


@dataclass
class DefinitionScheduleSite: 
    register: str
    operation_kind: str
    expression_key: str
    block: int
    region: str | None
    canonical_events: list[str]


@dataclass
class DefinitionScheduledRule: 
    register: str
    operation_kind: str
    expression_key: str
    definition_blocks: list[int]
    edge_occurrences: int
    canonical_event: str | None
    schedule_kind: str
    eliminated_edge_occurrences: int


@dataclass
class DefinitionScheduleAnalysis: 
    sites: list[DefinitionScheduleSite]
    rules: list[DefinitionScheduledRule]
    event_scheduled_rules: int
    event_scheduled_definition_sites: int
    event_scheduled_edge_occurrences: int
    eliminated_edge_occurrences: int
    notes: list[str]


def _block_reachable_without(region: ReachRegion, target_block: int, removed_edge: tuple[int, int] | None) -> bool: 
    # Reuse the write-guard graph semantics, but target a block rather than an edge.
    from collections import defaultdict, deque
    succ = defaultdict(list)
    for e in region.edges: 
        succ[e.source].append(e.target)
    if region.start_sample is not None: 
        roots = [region.start_sample]
    else: 
        internal = set(region.blocks)
        indeg = {b: 0 for b in internal}
        for e in region.edges: 
            if e.source in internal and e.target in internal: 
                indeg[e.target] += 1
        roots = sorted(b for b, d in indeg.items() if d == 0)
    q = deque(roots)
    seen = set()
    while q: 
        n = q.popleft()
        if n in seen: 
            continue
        seen.add(n)
        if n == target_block: 
            return True
        for dst in succ.get(n, []): 
            if removed_edge is not None and (n, dst) == removed_edge: 
                continue
            if dst not in seen: 
                q.append(dst)
    return False


def _edge_dominates_block(region: ReachRegion, edge: tuple[int, int], block: int) -> bool: 
    if not _block_reachable_without(region, block, None): 
        return False
    return not _block_reachable_without(region, block, edge)


def analyze_definition_schedules(
    reach: SemanticReachResult, 
    edge_events: EdgeEventAnalysisResult, 
    canonical_events: CanonicalEventAnalysisResult, 
    definitions: DefinitionSiteAnalysis, 
    behavior: HardwareBehaviorIR, 
) -> DefinitionScheduleAnalysis: 
    region_by_block = {}
    for name, r in reach.regions.items(): 
        for b in r.blocks: 
            region_by_block.setdefault(b, []).append(name)

    raw_to_canon = {}
    for ce in canonical_events.canonical_events: 
        for raw in ce.source_candidates: 
            raw_to_canon[raw] = ce.event_id
    raw_by_region = defaultdict(list)
    for e in edge_events.event_candidates: 
        raw_by_region[e.region].append(e)

    def_lookup = {(f.family, f.expression_key): f for f in definitions.forms}
    sites = []
    rules = []
    for rule in behavior.rules: 
        ek = repr(expr_key(rule.expression))
        form = def_lookup.get((rule.register, ek))
        blocks = list(form.definition_blocks) if form else []
        site_events = []
        for b in blocks: 
            # Prefer sampled region containing the block. In practice there is one semantic region.
            names = region_by_block.get(b, [])
            found = set()
            chosen = None
            for rn in names: 
                r = reach.regions[rn]
                for e in raw_by_region.get(rn, []): 
                    if _edge_dominates_block(r, (e.detector_block, e.success_target), b): 
                        cid = raw_to_canon.get(e.event_id)
                        if cid: 
                            found.add(cid)
                if found: 
                    chosen = rn
                    break
            if chosen is None and names: 
                chosen = names[0]
            sites.append(DefinitionScheduleSite(rule.register, rule.operation_kind, ek, b, chosen, sorted(found)))
            site_events.append(found)
        common = set(site_events[0]) if site_events else set()
        for s in site_events[1:]: 
            common &= s
        union = set().union(*site_events) if site_events else set()
        if len(common) == 1: 
            kind = 'EVENT_SCHEDULED'
            ce = next(iter(common))
        elif union: 
            kind = 'MIXED_EVENT'
            ce = None
        else: 
            kind = 'NO_EVENT'
            ce = None
        eliminated = max(0, rule.occurrence_count-len(blocks)) if kind == 'EVENT_SCHEDULED' and blocks else 0
        rules.append(DefinitionScheduledRule(rule.register, rule.operation_kind, ek, blocks, rule.occurrence_count, ce, kind, eliminated))

    scheduled = [r for r in rules if r.schedule_kind == 'EVENT_SCHEDULED']
    return DefinitionScheduleAnalysis(
        sites = sites, rules = rules, 
        event_scheduled_rules = len(scheduled), 
        event_scheduled_definition_sites = sum(len(r.definition_blocks) for r in scheduled), 
        event_scheduled_edge_occurrences = sum(r.edge_occurrences for r in scheduled), 
        eliminated_edge_occurrences = sum(r.eliminated_edge_occurrences for r in scheduled), 
        notes = [
            'Scheduling is recovered at original STATE_DEF blocks, before state-SSA exit-edge duplication.', 
            'An event is accepted only if its success edge CFG-dominates the original definition block.', 
            'No protocol names, reference RTL signals, or stack-offset special cases are used.', 
        ], 
    )


def write_definition_schedule_report(result: DefinitionScheduleAnalysis, path: Path)->None: 
    lines = ['DEFINITION-SITE HARDWARE SCHEDULE RECOVERY', '='*78, 
           f'event-scheduled rules            : {result.event_scheduled_rules}', 
           f'event-scheduled definition sites : {result.event_scheduled_definition_sites}', 
           f'covered edge-write occurrences   : {result.event_scheduled_edge_occurrences}', 
           f'eliminable duplicated occurrences: {result.eliminated_edge_occurrences}', '', 
           'Event-scheduled rules', '-'*78]
    for r in sorted(result.rules, key = lambda x: (x.schedule_kind!='EVENT_SCHEDULED', -x.eliminated_edge_occurrences, x.register)): 
        if r.schedule_kind!='EVENT_SCHEDULED': 
            continue
        lines += [f'{r.register}: {r.operation_kind}', 
                  f'  definition BBs : {", ".join(f"BB{x:03d}" for x in r.definition_blocks)}', 
                  f'  edge writes    : {r.edge_occurrences}', 
                  f'  schedule       : {r.canonical_event}', 
                  f'  duplicate edge writes removable: {r.eliminated_edge_occurrences}', 
                  f'  expression     : {r.expression_key}', '']
    lines += ['Notes', '-'*78]+[f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
