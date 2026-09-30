from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path
from .edge_event_analysis import EdgeEventAnalysisResult

@dataclass
class CanonicalHardwareEvent: 
    event_id: str
    region: str
    latch: str
    gpio_bit: int
    edge: str
    qualifiers: list[str]
    source_candidates: list[str]
    detector_blocks: list[int]
    success_targets: list[int]

@dataclass
class CanonicalEventAnalysisResult: 
    raw_candidates: int
    canonical_events: list[CanonicalHardwareEvent]
    canonical_count: int
    reduction_pct: float
    notes: list[str]


def analyze_canonical_events(events: EdgeEventAnalysisResult) -> CanonicalEventAnalysisResult: 
    groups = defaultdict(list)
    for e in events.event_candidates: 
        groups[(e.region, e.family, e.gpio_bit, e.edge)].append(e)
    out = []
    for i, (key, items) in enumerate(sorted(groups.items()), 1): 
        region, family, bit, edge = key
        qsets = [set(x.qualifiers) for x in items]
        shared = sorted(set.intersection(*qsets)) if qsets else []
        out.append(CanonicalHardwareEvent(
            event_id = f'HEVT{i:03d}', region = region, latch = family, gpio_bit = bit, edge = edge, 
            qualifiers = shared, source_candidates = [x.event_id for x in items], 
            detector_blocks = sorted({x.detector_block for x in items}), 
            success_targets = sorted({x.success_target for x in items}), 
        ))
    n = len(out)
    raw = events.candidate_events
    return CanonicalEventAnalysisResult(raw_candidates = raw, canonical_events = out, canonical_count = n, 
        reduction_pct = (0.0 if not raw else 100*(raw-n)/raw), notes = [
            'Candidates with the same previous-sample storage, GPIO bit and edge direction are one physical edge event.', 
            'Only qualifiers common to every CFG spelling are retained; path-specific qualifiers are treated as software control-flow artifacts.', 
            'No I2C pin names or protocol labels are used.', 
        ])


def write_canonical_event_report(result: CanonicalEventAnalysisResult, path: Path)->None: 
    lines = ['CANONICAL HARDWARE EVENT DIAGNOSTIC', '='*72, 
           f'raw CFG event candidates : {result.raw_candidates}', 
           f'canonical hardware events: {result.canonical_count}', 
           f'candidate reduction      : {result.reduction_pct:.2f}%', '']
    for e in result.canonical_events: 
        lines += [f'{e.event_id}: {e.edge} GPIO[{e.gpio_bit}] previous={e.latch} in {e.region}', 
                  f'  qualifiers       : {", ".join(e.qualifiers) or "-"}', 
                  f'  CFG candidates   : {", ".join(e.source_candidates)}', 
                  f'  detector blocks  : {", ".join(f"BB{x:03d}" for x in e.detector_blocks)}', 
                  f'  success targets  : {", ".join(f"BB{x:03d}" for x in e.success_targets)}', '']
    lines += ['Notes', '-'*72]+[f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
