from __future__ import annotations
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from collections import defaultdict

from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .sample_barrier_analysis import SampleBarrierAnalysis

@dataclass
class BarrierFusionCandidate: 
    sample_block: int
    region: str
    crossing_registers: list[str]
    branch_blocks: list[int]
    gpio_effect_blocks: list[int]
    sample_dependent_registers: list[str]
    pending_state_read_by_sample_updates: list[str]
    unconditional_region: bool
    fusable: bool

@dataclass
class SampleBarrierFusionAnalysis: 
    barriers: list[BarrierFusionCandidate]
    fusable_barriers: int
    crossing_rules_fusable: int
    notes: list[str]

def _deps(expr: Expr, states: set[str], gpios: set[str]): 
    if expr.kind == 'STATE' and expr.state_family: 
        states.add(expr.state_family)
    if expr.kind == 'GPIO' and expr.gpio_value: 
        gpios.add(expr.gpio_value)
    for a in expr.args: 
        _deps(a, states, gpios)

def analyze_sample_barrier_fusion(reach: SemanticReachResult, semantic_ssa: SemanticSSAResult, 
    next_state: NextStateExprIR, gpio_effects, barriers: SampleBarrierAnalysis, blocks): 
    crossing = defaultdict(set)
    gpio_def_block = {}
    for block in blocks: 
        for op in block.ops: 
            if op.kind == 'GPIO_READ' and op.dst is not None: 
                gpio_def_block[op.dst] = block.id
    for r in barriers.rules: 
        for b in r.sample_barriers: 
            crossing[b].add(r.register)
    out = []
    for sample, regs in sorted(crossing.items()): 
        rn = next((name for name, r in reach.regions.items() if r.start_sample == sample), None)
        if rn is None: 
            continue
        region = reach.regions[rn]
        blocks = set(region.blocks)|{sample}
        branch = sorted(b for b in semantic_ssa.branch_conditions if b in blocks)
        effect_blocks = sorted({e.block_id for e in getattr(gpio_effects, 'effects', []) if e.block_id in blocks})
        # A region is straight-line for our purposes when all internal edges are unconditional.
        unconditional = all(e.kind not in {'TRUE', 'FALSE'} for e in region.edges)
        sample_regs = set()
        pending_reads = set()
        for e, writes in next_state.by_edge.items(): 
            if e[0] not in blocks: 
                continue
            for w in writes: 
                st = set()
                gp = set()
                _deps(w.expression, st, gp)
                # GPIO Expr does not retain block id, so use known region fact: in a pure
                # sample region any GPIO leaf belongs to its start sample.
                if any(gpio_def_block.get(g) == sample for g in gp): 
                    sample_regs.add(w.family)
                    pending_reads |= (st & regs)
        fusable = unconditional and not branch and not effect_blocks and not pending_reads and not (sample_regs & regs)
        out.append(BarrierFusionCandidate(sample, rn, sorted(regs), branch, effect_blocks, sorted(sample_regs), sorted(pending_reads), unconditional, fusable))
    fusable_blocks = {b.sample_block for b in out if b.fusable}
    return SampleBarrierFusionAnalysis(out, sum(b.fusable for b in out), sum(any(sb in fusable_blocks for sb in r.sample_barriers) for r in barriers.rules), [
      'A sample barrier is fusable only when its semantic region is straight-line, has no branch predicate, has no GPIO output side effect, and sampled-data updates neither overwrite nor read the pending crossing states.', 
      'Fusion retimes internal state across an input-only sample barrier; it is an observational transformation, not cycle-identical BIO execution.', 
      'No protocol or pin names are used.', 
    ])

def write_sample_barrier_fusion_report(r, path: Path): 
    lines = ['PURE SAMPLE-BARRIER FUSION DIAGNOSTIC', '='*78, f'fusable barriers       : {r.fusable_barriers}', f'crossing rules fusable : {r.crossing_rules_fusable}', '']
    for b in r.barriers: 
      lines += [f'BB{b.sample_block:03d} / {b.region}', f'  crossing state       : {", ".join(b.crossing_registers)}', f'  straight-line        : {b.unconditional_region}', 
                f'  branch blocks        : {", ".join(f"BB{x:03d}" for x in b.branch_blocks) or "-"}', f'  GPIO effect blocks   : {", ".join(f"BB{x:03d}" for x in b.gpio_effect_blocks) or "-"}', 
                f'  sample updates       : {", ".join(b.sample_dependent_registers) or "-"}', f'  pending-state reads  : {", ".join(b.pending_state_read_by_sample_updates) or "-"}', 
                f'  FUSABLE              : {"YES" if b.fusable else "NO"}', '']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
