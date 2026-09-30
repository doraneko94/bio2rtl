from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json

from .definition_site_equivalence import _paths, _block_order
from .edge_event_analysis import _predicate_atom
from .hardware_event_partition_analysis import analyze_hardware_event_partition
from .hardware_event_transition_analysis import _apply_path
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist
from .gpio_effect import GPIOEffectResult
from .canonical_event_analysis import CanonicalEventAnalysisResult
from .polling_phase_event_analysis import PollingPhaseEventAnalysis

@dataclass
class WaitPollEliminationAnalysis: 
    wait_paths: int
    physical_hold_paths: int
    gpio_effect_free_paths: int
    removable_paths: int
    all_wait_paths_removable: bool
    changed_family_counts: dict[str, int]
    gpio_effect_kind_counts: dict[str, int]
    notes: list[str]

def analyze_wait_poll_elimination(reach: SemanticReachResult, next_state: NextStateExprIR, structural: StructuralNetlist, ssa: SemanticSSAResult, gpio_effects: GPIOEffectResult, history_events: CanonicalEventAnalysisResult, polling: PollingPhaseEventAnalysis, region_name = 'SAMPLE_BB006'): 
    region = reach.regions[region_name]
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('wait-poll path enumeration truncated')
    actual = {(e.source, e.target) for e in region.edges}
    hist = {e.event_id: {(a, b) for a in e.detector_blocks for b in e.success_targets if (a, b) in actual} for e in history_events.canonical_events}
    atoms = {b: a for b, c in ssa.branch_conditions.items() if (a:=_predicate_atom(c)) is not None}
    kinds = {(e.source, e.target): e.kind for e in region.edges}
    phase = polling.rows[0] if len(polling.rows) == 1 else None
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
    border = _block_order(region)
    wait = ph = gf = rem = 0
    changed = {}
    effect_kinds = {}
    for nodes, edges in paths: 
        es = set(edges)
        labels = []
        for eid, se in hist.items(): 
            if es&se: 
                labels.append(eid)
        if phase: 
            st = []
            gp = []
            for a, b in edges: 
                atom = atoms.get(a)
                kind = kinds.get((a, b))
                if atom is None or kind not in ('TRUE', 'FALSE'): 
                    continue
                truth = kind == 'TRUE'
                level = atom.level if truth else 1-atom.level
                if atom.source == 'SEMANTIC_STATE_BIT' and atom.family == phase['state']: 
                    st.append(level)
                if atom.source == 'GPIO_BIT' and atom.bit == phase['gpio_bit']: 
                    gp.append(level)
            if st and gp: 
                combo = (st[0], gp[-1])
                if combo == (1, 0): 
                    labels.append('PHEVT_FALL')
                elif combo == (0, 1): 
                    labels.append('PHEVT_RISE')
        if labels: 
            continue
        wait+=1
        st = _apply_path(ident, region, edges, next_state)
        changed_here = [r for r in regs if expr_key(st[r])!=expr_key(ident[r])]
        for r in changed_here: 
            changed[r] = changed.get(r, 0)+1
        phys_hold = not changed_here
        if phys_hold: 
            ph+=1
        ns = set(nodes)
        effects = sum(len(gpio_effects.by_block.get(b, [])) for b in border if b in ns)
        effect_free = effects == 0
        if effect_free: 
            gf+=1
        if phys_hold and effect_free: 
            rem+=1
    return WaitPollEliminationAnalysis(wait, ph, gf, rem, wait == rem, changed, effect_kinds, [
        'A removable wait-only path changes no persistent physical state and performs no GPIO output effect.', 
        'Semantic polling-phase bookkeeping is intentionally excluded: it is replaced by the recovered hardware edge event itself.', 
    ])

def write_wait_poll_elimination_report(r, path: Path): 
    lines = ['WAIT-ONLY POLLING ELIMINATION PROOF', '='*78, f'wait paths              : {r.wait_paths}', f'physical-state HOLD paths: {r.physical_hold_paths}', f'GPIO-effect-free paths  : {r.gpio_effect_free_paths}', f'removable paths         : {r.removable_paths}', f'all removable           : {r.all_wait_paths_removable}', '', 'Changed physical families', '-'*78] + [f'{k}: {v}' for k, v in sorted(r.changed_family_counts.items(), key = lambda kv: -kv[1])] + ['', 'GPIO effect kinds', '-'*78] + [f'{k}: {v}' for k, v in sorted(r.gpio_effect_kind_counts.items(), key = lambda kv: -kv[1])] + ['']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
