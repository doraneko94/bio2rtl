from __future__ import annotations
from dataclasses import dataclass, asdict
from collections import Counter
from pathlib import Path
import json

from .semantic_ssa import SemanticSSAResult, SemanticExpr
from .semantic_reach import SemanticReachResult, select_primary_sample_region
from .edge_event_analysis import _predicate_atom, _semantic_gpio_bit_from_expr
from .definition_site_equivalence import _paths
from .next_state_expr import NextStateExprIR

@dataclass
class PollingPhaseEventAnalysis: 
    candidates: int
    rows: list[dict]
    notes: list[str]


def _has_self(e: SemanticExpr, name: str)->bool: 
    if e.kind == 'SEMANTIC_STATE' and e.semantic_state_name == name: 
        return True
    return any(_has_self(a, name) for a in e.args) or any(_has_self(a, name) for _p, a in e.phi_inputs)

def _gpio_sources(e: SemanticExpr): 
    out = []
    hit = _semantic_gpio_bit_from_expr(e)
    if hit is not None: 
        out.append(hit)
    for a in e.args: 
        out.extend(_gpio_sources(a))
    for _p, a in e.phi_inputs: 
        out.extend(_gpio_sources(a))
    return out


def analyze_polling_phase_events(reach: SemanticReachResult, ssa: SemanticSSAResult, next_state: NextStateExprIR, region_name: str|None = None)->PollingPhaseEventAnalysis: 
    if region_name is None: 
        sampled = [name for name, region in reach.regions.items() if region.start_sample is not None]
        if not sampled: 
            return PollingPhaseEventAnalysis(0, [], [
                'No sampled steady semantic region exists; polling-phase recovery is not applicable.', 
                'A startup-only quiescent program must not acquire a synthetic polling phase or hardware clock.', 
            ])
        region_name = select_primary_sample_region(reach)
    atoms = {b: a for b, c in ssa.branch_conditions.items() if (a:=_predicate_atom(c)) is not None}
    region = reach.regions[region_name]
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('polling-phase path enumeration truncated')
    write_edges = {(w.source_block, w.target_block) for w in next_state.writes}
    rows = []
    for name, d in sorted(ssa.semantic_state_definitions.items()): 
        # Derived phase-state candidates: sampled from exactly one GPIO bit on
        # some paths, used in a boolean branch, but not a pure loop-carried
        # previous-sample latch.
        sources = []
        has_self = False
        for _p, e in d.incoming: 
            sources.extend(_gpio_sources(e))
            has_self = has_self or _has_self(e, name)
        uniq = sorted(set(sources))
        if len(uniq)!=1: 
            continue
        sb, bit = uniq[0]
        state_blocks = [b for b, a in atoms.items() if a.source == 'SEMANTIC_STATE_BIT' and a.family == name]
        gpio_blocks = [b for b, a in atoms.items() if a.source == 'GPIO_BIT' and a.bit == bit]
        if not state_blocks or not gpio_blocks: 
            continue
        combos = Counter()
        write_counts = Counter()
        examples = {}
        for nodes, edges in paths: 
            edge_kind = {(e.source, e.target): e.kind for e in region.edges}
            st = []
            gp = []
            for a, b in edges: 
                atom = atoms.get(a)
                kind = edge_kind.get((a, b))
                if atom is None or kind not in ('TRUE', 'FALSE'): 
                    continue
                truth = (kind == 'TRUE')
                level = atom.level if truth else 1-atom.level
                if atom.source == 'SEMANTIC_STATE_BIT' and atom.family == name: 
                    st.append(level)
                if atom.source == 'GPIO_BIT' and atom.bit == bit: 
                    gp.append(level)
            if not st or not gp: 
                continue
            combo = (st[0], gp[-1])
            combos[combo]+=1
            wc = sum(1 for e in edges if e in write_edges)
            write_counts[combo]+=wc
            examples.setdefault(combo, list(nodes))
        # Complementary phase/level combinations indicate a wait-low/wait-high
        # software automaton.  Both opposite transitions must be observed.
        fall = combos.get((1, 0), 0)
        rise = combos.get((0, 1), 0)
        if not fall or not rise: 
            continue
        rows.append({
            'state': name, 'sample_block': sb, 'gpio_bit': bit, 'has_self_hold': has_self, 
            'state_test_blocks': state_blocks, 'gpio_test_blocks': gpio_blocks, 
            'combination_paths': {f'{a}->{b}': n for (a, b), n in sorted(combos.items())}, 
            'fall_paths': fall, 'rise_paths': rise, 
            'fall_avg_write_edges': write_counts[(1, 0)]/fall, 
            'rise_avg_write_edges': write_counts[(0, 1)]/rise, 
            'fall_example': examples.get((1, 0), []), 'rise_example': examples.get((0, 1), []), 
            'classification': 'POLLING_PHASE_EDGE_PAIR', 
        })
    return PollingPhaseEventAnalysis(len(rows), rows, [
        'A polling-phase edge pair is inferred from a 1-bit semantic control value that is assigned from one GPIO bit on some paths and tested together with that GPIO bit.', 
        'Both phase=1/current=0 and phase=0/current=1 paths must exist; these are generic wait-high->low and wait-low->high completions.', 
        'This pass does not use I2C signal names or stack offsets.', 
    ])

def write_polling_phase_event_report(r, path: Path): 
    lines = ['POLLING-PHASE HARDWARE EVENT RECOVERY', '='*78, f'candidates: {r.candidates}', '']
    for x in r.rows: 
        lines += [f"{x['state']}: GPIO[{x['gpio_bit']}] @ BB{x['sample_block']:03d} -> FALL/RISE pair", f"  self hold        : {x['has_self_hold']}", f"  phase tests      : {x['state_test_blocks']}", f"  GPIO tests       : {x['gpio_test_blocks']}", f"  path combinations: {x['combination_paths']}", f"  FALL paths       : {x['fall_paths']}", f"  RISE paths       : {x['rise_paths']}", '']
    lines += ['Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
