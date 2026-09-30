from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import Counter
from pathlib import Path
import json

from .definition_site_equivalence import _paths
from .edge_event_analysis import _predicate_atom
from .hardware_event_transition_analysis import _apply_path
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .structural_netlist import StructuralNetlist
from .canonical_event_analysis import CanonicalEventAnalysisResult
from .polling_phase_event_analysis import PollingPhaseEventAnalysis

@dataclass
class EpsilonTransitionAnalysis: 
    epsilon_paths: int
    unique_physical_outcomes: int
    identity_physical_paths: int
    progress_paths: int
    changed_family_counts: dict[str, int]
    outcome_multiplicity: list[dict]
    notes: list[str]

def _labels(edges, region, atoms, hist, phase): 
    es = set(edges)
    out = []
    kinds = {(e.source, e.target): e.kind for e in region.edges}
    for eid, se in hist.items(): 
        if es&se: 
            out.append(eid)
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
            if (st[0], gp[-1]) == (1, 0): 
                out.append('PHEVT_FALL')
            elif (st[0], gp[-1]) == (0, 1): 
                out.append('PHEVT_RISE')
    return tuple(sorted(out))

def analyze_epsilon_transitions(reach: SemanticReachResult, next_state: NextStateExprIR, structural: StructuralNetlist, ssa: SemanticSSAResult, history: CanonicalEventAnalysisResult, polling: PollingPhaseEventAnalysis, region_name = 'SAMPLE_BB006'): 
    region = reach.regions[region_name]
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('epsilon path enumeration truncated')
    actual = {(e.source, e.target) for e in region.edges}
    hist = {e.event_id: {(a, b) for a in e.detector_blocks for b in e.success_targets if (a, b) in actual} for e in history.canonical_events}
    atoms = {b: a for b, c in ssa.branch_conditions.items() if (a:=_predicate_atom(c)) is not None}
    phase = polling.rows[0] if len(polling.rows) == 1 else None
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
    outs = Counter()
    changed = Counter()
    identity = progress = eps = 0
    for nodes, edges in paths: 
        if _labels(edges, region, atoms, hist, phase): 
            continue
        eps+=1
        st = _apply_path(ident, region, edges, next_state)
        key = tuple((r, expr_key(st[r])) for r in regs)
        outs[repr(key)]+=1
        ch = [r for r in regs if expr_key(st[r])!=expr_key(ident[r])]
        if not ch: 
            identity+=1
        else: 
            progress+=1
            for r in ch: 
                changed[r]+=1
    mult = [{'paths': n, 'outcome': k} for k, n in outs.most_common(12)]
    return EpsilonTransitionAnalysis(eps, len(outs), identity, progress, dict(changed), mult, [
        'Epsilon transitions are SAMPLE_BB006 paths that consume neither a trusted history-edge event nor a recovered polling-phase edge completion.', 
        'Identity epsilon paths are pure waits at the physical-state level; progress epsilon paths are CPU continuation steps that must be composed into an event macro-transition.', 
    ])

def write_epsilon_transition_report(r, path: Path): 
    lines = ['EPSILON TRANSITION DIAGNOSTIC', '='*78, f'epsilon paths            : {r.epsilon_paths}', f'unique physical outcomes : {r.unique_physical_outcomes}', f'identity physical paths  : {r.identity_physical_paths}', f'progress paths           : {r.progress_paths}', '', 'Changed families', '-'*78]+[f'{k}: {v}' for k, v in sorted(r.changed_family_counts.items(), key = lambda kv: -kv[1])]
    lines += ['', 'Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
