from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult

@dataclass
class EventVectorAnalysis: 
    region: str
    cpu_paths: int
    nonempty_event_paths: int
    unique_event_vectors: int
    vectors: list[dict]
    phi_blocks: int
    vector_phi_choices_total: int
    vector_phi_choices_unique: int
    notes: list[str]

def analyze_event_vectors(reach: SemanticReachResult, events: CanonicalEventAnalysisResult, ssa: SemanticSSAResult, 
                          region_name: str = 'SAMPLE_BB006', max_paths: int = 200000)->EventVectorAnalysis: 
    region = reach.regions[region_name]
    actual = {(e.source, e.target) for e in region.edges}
    success = {}
    for ev in events.canonical_events: 
        success[ev.event_id] = {(a, b) for a in ev.detector_blocks for b in ev.success_targets if (a, b) in actual}
    paths, trunc = _paths(region, max_paths)
    if trunc: 
        raise RuntimeError('event vector path enumeration truncated')
    groups = {}
    for nodes, edges in paths: 
        es = set(edges)
        sig = tuple(sorted(eid for eid, se in success.items() if es & se))
        if not sig: 
            continue
        groups.setdefault(sig, []).append((nodes, edges))
    phi_blocks = sorted({int(e.phi_block) for e in ssa.expressions.values() if e.kind == 'PHI' and e.phi_block is not None})
    rows = []
    total = unique = 0
    for sig, ps in sorted(groups.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        pchoices = {}
        for pb in phi_blocks: 
            vals = set()
            for nodes, edges in ps: 
                for a, b in edges: 
                    if b == pb: 
                        vals.add(a)
            if vals: 
                pchoices[pb] = sorted(vals)
                total+=1
                if len(vals) == 1: 
                    unique+=1
        rows.append({'events': list(sig), 'paths': len(ps), 'phi_predecessors': {f'PHI_BB{k:03d}': v for k, v in pchoices.items()}, 
                     'all_phi_predecessors_unique': all(len(v) == 1 for v in pchoices.values())})
    return EventVectorAnalysis(region_name, len(paths), sum(len(v) for v in groups.values()), len(groups), rows, len(phi_blocks), total, unique, [
      'One software polling iteration may satisfy more than one recovered physical event; individual event IDs are therefore not assumed mutually exclusive.', 
      'The event vector is the simultaneous set of recovered event predicates true on one CPU path.', 
      'A PHI predecessor is removable from hardware identity only when the complete event vector uniquely determines it.', 
    ])

def write_event_vector_report(r: EventVectorAnalysis, path: Path): 
    lines = ['SIMULTANEOUS HARDWARE EVENT-VECTOR DIAGNOSTIC', '='*78, 
      f'region                         : {r.region}', f'CPU paths                      : {r.cpu_paths}', 
      f'paths with >=1 event           : {r.nonempty_event_paths}', f'unique event vectors           : {r.unique_event_vectors}', 
      f'PHI blocks                     : {r.phi_blocks}', f'vector/PHI choices              : {r.vector_phi_choices_total}', 
      f'uniquely selected by vector    : {r.vector_phi_choices_unique}', '', 'Vectors', '-'*78]
    for x in r.vectors: 
      lines += [f"{x['paths']:4d} paths  {'+'.join(x['events'])}", f"  all PHI predecessors unique: {x['all_phi_predecessors_unique']}"]
      for p, v in x['phi_predecessors'].items(): 
          lines.append(f"  {p}: {v}")
    lines += ['', 'Notes', '-'*78]+[f'- {x}' for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
