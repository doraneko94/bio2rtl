from __future__ import annotations
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from .definition_site_equivalence import _paths, _edge_order
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .structural_netlist import StructuralNetlist

@dataclass
class BoundaryRangeRow: 
    register: str
    width: int
    path_outcomes: int
    const_values: list[int]
    hold_count: int
    copy_sources: list[str]
    complex_tags: list[str]
    boolean_candidate: bool
    constant_zero_candidate: bool

@dataclass
class BoundaryRangeAnalysis: 
    rows: list[BoundaryRangeRow]
    total_paths: int
    boolean_candidates: list[str]
    notes: list[str]

def _widths(s): 
 return {f: (32 if s.nodes[n].width is None else int(s.nodes[n].width)) for f, n in s.state_nodes.items()}

def analyze_boundary_ranges(reach: SemanticReachResult, nse: NextStateExprIR, structural: StructuralNetlist, max_paths_per_region = 200000): 
 widths = _widths(structural)
 counts = {r: Counter() for r in widths}
 total = 0
 for region in reach.regions.values(): 
  paths, trunc = _paths(region, max_paths_per_region)
  if trunc: 
      raise RuntimeError(f'{region.sample_block}: path enumeration truncated')
  order = _edge_order(region)
  for _nodes, edges in paths: 
   total+=1
   es = set(edges)
   state = {r: Expr(kind = 'STATE', state_family = r) for r in widths}
   for e in order: 
    ek = (e.source, e.target)
    if ek not in es: 
        continue
    for w in nse.by_edge.get(ek, []): 
        state[w.family] = w.expression
   for r, e in state.items(): 
       counts[r][expr_key(e)] += 1
 rows = []
 bc = []
 for r in sorted(widths): 
  const = set()
  hold = 0
  copies = set()
  complex = set()
  for key, n in counts[r].items(): 
   tag = key[0]
   if tag == 'CONST': 
       const.add(int(key[1]))
   elif tag == 'STATE': 
    if key[1] == r: 
        hold += n
    else: 
        copies.add(str(key[1]))
   else: 
       complex.add(str(tag))
  candidate = widths[r]>1 and not copies and not complex and all(v in (0, 1) for v in const)
  const_zero = (not copies and not complex and all(v == 0 for v in const))
  # Holds are safe inductively from reset-zero if every non-hold assignment is boolean/zero.
  if candidate: 
      bc.append(r)
  rows.append(BoundaryRangeRow(r, widths[r], sum(counts[r].values()), sorted(const), hold, sorted(copies), sorted(complex), candidate, const_zero))
 return BoundaryRangeAnalysis(rows, total, bc, [
  'All semantic-region paths are evaluated to their boundary persistent-state outcomes.', 
  'A boolean candidate has only HOLD or CONST(0/1) outcomes at every semantic boundary; with reset-zero this is an inductive <=1 invariant.', 
  'No path-guard simplification is assumed for this proof.', 
 ])

def write_boundary_range_report(r, path: Path): 
 lines = ['SEMANTIC-BOUNDARY RANGE / BOOLEAN-STATE PROOF', '='*78, f'paths checked      : {r.total_paths}', f'boolean candidates : {len(r.boolean_candidates)}', '  '+(', '.join(r.boolean_candidates) or '-'), '']
 for x in r.rows: 
  if x.width<=1 and not x.boolean_candidate: 
      continue
  lines += [f'{x.register} [{x.width} bit] {"CONST-ZERO-PROOF" if x.constant_zero_candidate else ("BOOLEAN-PROOF" if x.boolean_candidate else "-")}', f'  const values : {x.const_values or "-"}', f'  hold outcomes: {x.hold_count}', f'  copy sources : {", ".join(x.copy_sources) or "-"}', f'  complex tags : {", ".join(x.complex_tags) or "-"}', '']
 lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
 path.parent.mkdir(parents = True, exist_ok = True)
 path.write_text('\n'.join(lines)+'\n')
 path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
