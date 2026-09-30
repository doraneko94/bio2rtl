from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
from collections import Counter
import json

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_site_equivalence import _paths
from .edge_event_analysis import _predicate_atom
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult
from .polling_phase_event_analysis import PollingPhaseEventAnalysis

@dataclass
class HardwareEventPartitionAnalysis: 
    cpu_paths: int
    classified_paths: int
    wait_only_paths: int
    event_classes: int
    rows: list[dict]
    notes: list[str]


def analyze_hardware_event_partition(reach: SemanticReachResult, ssa: SemanticSSAResult, history_events: CanonicalEventAnalysisResult, polling: PollingPhaseEventAnalysis, region_name = 'SAMPLE_BB006'): 
    region = reach.regions[region_name]
    paths, trunc = _paths(region, 200000)
    if trunc: 
        raise RuntimeError('hardware-event partition path enumeration truncated')
    actual = {(e.source, e.target) for e in region.edges}
    hist = {e.event_id: {(a, b) for a in e.detector_blocks for b in e.success_targets if (a, b) in actual} for e in history_events.canonical_events}
    atoms = {b: a for b, c in ssa.branch_conditions.items() if (a:=_predicate_atom(c)) is not None}
    kinds = {(e.source, e.target): e.kind for e in region.edges}
    phase = polling.rows[0] if len(polling.rows) == 1 else None
    counts = Counter()
    examples = {}
    for nodes, edges in paths: 
        es = set(edges)
        labels = []
        for eid, se in hist.items(): 
            if es & se: 
                labels.append(eid)
        if phase is not None: 
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
        sig = tuple(sorted(labels))
        counts[sig]+=1
        examples.setdefault(sig, list(nodes))
    rows = [{'events': list(sig), 'paths': n, 'example': examples[sig]} for sig, n in sorted(counts.items(), key = lambda kv: (-kv[1], kv[0]))]
    wait = counts.get((), 0)
    return HardwareEventPartitionAnalysis(len(paths), len(paths)-wait, wait, len(counts)-int(() in counts), rows, [
        'History-latch events and polling-phase edge completions are kept as distinct recovery mechanisms.', 
        'PHEVT_FALL/RISE are inferred from wait-level software control, not from a previous-sample register.', 
        'An empty event class is a polling iteration that observes no completed hardware event.', 
    ])

def write_hardware_event_partition_report(r, path: Path): 
    lines = ['HARDWARE EVENT PARTITION', '='*78, f'CPU paths       : {r.cpu_paths}', f'classified paths: {r.classified_paths}', f'wait-only paths : {r.wait_only_paths}', f'event classes   : {r.event_classes}', '']
    for x in r.rows: 
        lines += [f"{'+'.join(x['events']) or 'WAIT_ONLY'}: {x['paths']} paths"]
    lines += ['', 'Notes', '-'*78]+[f'- {n}' for n in r.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
