from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .definition_site_analysis import DefinitionSiteAnalysis
from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .semantic_reach import SemanticReachResult

@dataclass
class BarrierCrossingRule: 
    register: str
    operation_kind: str
    definition_blocks: list[int]
    edge_occurrences: int
    definition_regions: list[str]
    occurrence_regions: list[str]
    cross_region_occurrences: int
    sample_barriers: list[int]

@dataclass
class SampleBarrierAnalysis: 
    rules: list[BarrierCrossingRule]
    crossing_rules: int
    crossing_occurrences: int
    notes: list[str]

def analyze_sample_barriers(reach: SemanticReachResult, behavior: HardwareBehaviorIR, defs: DefinitionSiteAnalysis): 
    region_by_block = {}
    for rn, r in reach.regions.items(): 
        for b in r.blocks: 
            region_by_block.setdefault(b, set()).add(rn)
        if r.start_sample is not None: 
            region_by_block.setdefault(r.start_sample, set()).add(rn)
    defmap = {(f.family, f.expression_key): f for f in defs.forms}
    rows = []
    for rule in behavior.rules: 
        ek = repr(expr_key(rule.expression))
        f = defmap.get((rule.register, ek))
        db = f.definition_blocks if f else []
        dregs = sorted({rn for b in db for rn in region_by_block.get(b, set())})
        occ_regs = []
        cross = 0
        barriers = set()
        for e in rule.guard_edges: 
            regs = region_by_block.get(e.source_block, set())
            occ_regs.extend(regs)
            if dregs and not any(r in dregs for r in regs): 
                cross+=1
                for rn in regs: 
                    r = reach.regions[rn]
                    if r.start_sample is not None: 
                        barriers.add(r.start_sample)
        if cross: 
            rows.append(BarrierCrossingRule(rule.register, rule.operation_kind, db, rule.occurrence_count, dregs, sorted(set(occ_regs)), cross, sorted(barriers)))
    return SampleBarrierAnalysis(rows, len(rows), sum(r.cross_region_occurrences for r in rows), [
      'A cross-region occurrence is an edge-exit copy of an update expression in a semantic sampling region that contains no original definition of that expression.', 
      'Such occurrences are direct evidence of a CPU-order sample barrier carrying a computed value before it becomes persistent hardware state.', 
      'Eliminating a barrier requires retiming/data-dependency proof; this report does not alter RTL.', 
    ])

def write_sample_barrier_report(r, path: Path): 
    lines = ['CROSS-SAMPLE PENDING UPDATE DIAGNOSTIC', '='*78, f'crossing rules      : {r.crossing_rules}', f'crossing occurrences: {r.crossing_occurrences}', '']
    for x in sorted(r.rules, key = lambda x: -x.cross_region_occurrences): 
      lines += [f'{x.register}: {x.operation_kind}', f'  definition BBs   : {", ".join(f"BB{b:03d}" for b in x.definition_blocks)}', 
                f'  definition regions: {", ".join(x.definition_regions)}', f'  occurrence regions: {", ".join(x.occurrence_regions)}', 
                f'  cross-region copies: {x.cross_region_occurrences}', f'  sample barriers   : {", ".join(f"BB{b:03d}" for b in x.sample_barriers) or "-"}', '']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
