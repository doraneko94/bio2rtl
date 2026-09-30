from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths
from .hardware_behavior_ir import expr_key
from .hardware_event_transition_analysis import _apply_path, _path_target
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .structural_netlist import StructuralNetlist
from .hardware_temporal_region import HardwareTemporalRegionIR

@dataclass
class EventVectorTransitionAnalysis: 
    cpu_paths: int
    fused_paths: int
    event_vectors: int
    total_vector_outcomes: int
    rows: list[dict]
    all_return_to_wait: bool
    notes: list[str]

def _success(events, region): 
    actual = {(e.source, e.target) for e in region.edges}
    return {ev.event_id: {(a, b) for a in ev.detector_blocks for b in ev.success_targets if (a, b) in actual} for ev in events.canonical_events}

def analyze_event_vector_transitions(reach: SemanticReachResult, next_state: NextStateExprIR, structural: StructuralNetlist, events: CanonicalEventAnalysisResult, temporal: HardwareTemporalRegionIR, region_name = 'SAMPLE_BB006'): 
    region = reach.regions[region_name]
    root = region.start_sample
    success = _success(events, region)
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('event-vector transition path enumeration truncated')
    transparent = set()
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)
    cache = {}
    for b in transparent: 
      rn = f'SAMPLE_BB{b:03d}'
      ps, t = _paths(reach.regions[rn], 200000)
      if t: 
          raise RuntimeError(f'{rn}: truncated')
      cache[b] = ps
    groups = {}
    for nodes, edges in paths: 
      es = set(edges)
      sig = tuple(sorted(eid for eid, se in success.items() if es&se))
      if sig: 
          groups.setdefault(sig, []).append((nodes, edges))
    rows = []
    totalf = 0
    totalo = 0
    allwait = True
    for sig, ps in sorted(groups.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
      fused = []
      for nodes, edges in ps: 
        s1 = _apply_path(ident, region, edges, next_state)
        t1 = _path_target(region, nodes, edges)
        if t1 in transparent: 
          r2 = reach.regions[f'SAMPLE_BB{t1:03d}']
          for n2, e2 in cache[t1]: 
              fused.append((_apply_path(s1, r2, e2, next_state), _path_target(r2, n2, e2)))
        else: 
            fused.append((s1, t1))
      outs = {tuple((r, expr_key(st[r])) for r in regs) for st, _ in fused}
      wait = all(t == root for _, t in fused)
      allwait &= wait
      rows.append({'events': list(sig), 'software_paths': len(ps), 'fused_paths': len(fused), 'unique_physical_outcomes': len(outs), 'collapse_pct': 0 if not fused else 100*(len(fused)-len(outs))/len(fused), 'returns_to_wait': wait})
      totalf+=len(fused)
      totalo+=len(outs)
    return EventVectorTransitionAnalysis(sum(len(v) for v in groups.values()), totalf, len(groups), totalo, rows, allwait, [
      'Transition identity is the complete simultaneous event vector, not an individual event.', 
      'Transparent GPIO_READ barriers are fused before outcomes are compared.', 
      'Outcome identity contains persistent-state expressions only; CFG path identity is absent.', 
    ])

def write_event_vector_transition_report(r, path: Path): 
    lines = ['EVENT-VECTOR HARDWARE TRANSITION COLLAPSE', '='*78, f'CPU paths              : {r.cpu_paths}', f'fused paths            : {r.fused_paths}', f'event vectors           : {r.event_vectors}', f'total vector outcomes   : {r.total_vector_outcomes}', f'all return to event wait: {r.all_return_to_wait}', '', 'Vectors', '-'*78]
    for x in r.rows: 
        lines += [f"{'+'.join(x['events'])}", f"  software paths : {x['software_paths']}", f"  fused paths    : {x['fused_paths']}", f"  outcomes       : {x['unique_physical_outcomes']}", f"  collapse       : {x['collapse_pct']:.2f}%", f"  returns to wait: {x['returns_to_wait']}", '']
    lines += ['Notes', '-'*78]+[f'- {x}' for x in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
