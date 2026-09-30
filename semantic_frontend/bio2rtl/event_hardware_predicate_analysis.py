from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re

from .canonical_event_analysis import CanonicalEventAnalysisResult
from .definition_guard_analysis import DefinitionGuardAnalysis
from .definition_site_equivalence import _paths
from .semantic_reach import SemanticReachResult
from .semantic_ssa import SemanticSSAResult, SemanticExpr, SemanticCondition


@dataclass
class EventHardwarePredicateAnalysis: 
    residual_literal_uses: int
    unique_raw_literals: int
    unique_event_conditioned_predicates: int
    predicates_with_bb_identity: int
    predicates_with_phi_identity: int
    predicates_with_semantic_state: int
    phi_inputs_resolved_by_event: int
    phi_inputs_unresolved: int
    rows: list[dict]
    notes: list[str]


def _success_edges(events: CanonicalEventAnalysisResult, event_id: str, reach: SemanticReachResult) -> set[tuple[int, int]]: 
    e = next(x for x in events.canonical_events if x.event_id == event_id)
    region = reach.regions[e.region]
    actual = {(x.source, x.target) for x in region.edges}
    return {(a, b) for a in e.detector_blocks for b in e.success_targets if (a, b) in actual}


def _event_phi_predecessors(reach: SemanticReachResult, events: CanonicalEventAnalysisResult) -> dict[tuple[str, int], int]: 
    """Find PHI predecessor chosen on every CPU path belonging to one hardware event.

    A mapping is emitted only if every selected path that enters the PHI block uses
    the same incoming predecessor.  This removes CFG identity only where the event
    itself proves the choice.
    """
    out: dict[tuple[str, int], int] = {}
    for e in events.canonical_events: 
        region = reach.regions[e.region]
        success = _success_edges(events, e.event_id, reach)
        paths, truncated = _paths(region, 200_000)
        if truncated: 
            continue
        selected = [p for p in paths if set(p[1]) & success]
        preds_by_block: dict[int, set[int]] = {}
        for nodes, edges in selected: 
            for src, dst in edges: 
                preds_by_block.setdefault(dst, set()).add(src)
        for block, preds in preds_by_block.items(): 
            if len(preds) == 1: 
                out[(e.event_id, block)] = next(iter(preds))
    return out


def _expr_key(expr: SemanticExpr, event_id: str, phi_pred: dict[tuple[str, int], int], stats: dict[str, int]): 
    if expr.kind == 'CONST': 
        return ('CONST', expr.value)
    if expr.kind == 'STATE': 
        return ('STATE', expr.state_family)
    if expr.kind == 'SEMANTIC_STATE': 
        stats['sem'] += 1
        return ('HW_STATE', expr.semantic_state_name)
    if expr.kind == 'GPIO': 
        # Once the hardware event is the temporal boundary, GPIO_READ provenance
        # is not a hardware identity.  The value is the current external GPIO bus.
        return ('GPIO_CURRENT', expr.gpio_value)
    if expr.kind == 'LIVEIN': 
        return ('LIVEIN', expr.livein_name)
    if expr.kind == 'OP': 
        return ('OP', expr.operation, tuple(_expr_key(a, event_id, phi_pred, stats) for a in expr.args))
    if expr.kind == 'PHI': 
        pred = phi_pred.get((event_id, int(expr.phi_block))) if expr.phi_block is not None else None
        if pred is not None: 
            for incoming_pred, incoming_expr in expr.phi_inputs: 
                if incoming_pred == pred: 
                    stats['phi_resolved'] += 1
                    return _expr_key(incoming_expr, event_id, phi_pred, stats)
        stats['phi_unresolved'] += 1
        # CFG predecessor labels are deliberately removed from the identity.
        # Keep only the set of possible hardware values as a diagnostic frontier.
        values = tuple(sorted({_expr_key(x, event_id, phi_pred, stats) for _, x in expr.phi_inputs}, key = repr))
        return ('EVENT_PHI', values)
    return (expr.kind,)


def _condition_key(cond: SemanticCondition, truth: bool, event_id: str, phi_pred, stats): 
    op = cond.operation
    if not truth: 
        op = {'EQ': 'NE', 'NE': 'EQ', 'ULT': 'UGE', 'UGE': 'ULT', 'SLT': 'NOT_SLT'}.get(op, 'NOT_' + op)
    return (op, _expr_key(cond.lhs, event_id, phi_pred, stats), _expr_key(cond.rhs, event_id, phi_pred, stats))


def _truth_from_text(text: str) -> bool: 
    # _literal_text() prefixes NOT_ only for a false branch when the condition
    # cannot be reduced to a one-bit atom.
    return ':NOT_' not in text


def _direct_literal(text: str): 
    m = re.search(r'STATE\(([^)]+)\)=([01])$', text)
    if m: 
        return ('EQ', ('STATE', m.group(1)), ('CONST', int(m.group(2))))
    m = re.search(r'GPIO@BB\d+\[(\d+)\]=([01])$', text)
    if m: 
        return ('EQ', ('GPIO_BIT', int(m.group(1))), ('CONST', int(m.group(2))))
    return None


def analyze_event_hardware_predicates(
    reach: SemanticReachResult, 
    semantic_ssa: SemanticSSAResult, 
    canonical_events: CanonicalEventAnalysisResult, 
    definition_guards: DefinitionGuardAnalysis, 
) -> EventHardwarePredicateAnalysis: 
    phi_pred = _event_phi_predecessors(reach, canonical_events)
    rows: dict[tuple, int] = {}
    raw: set[str] = set()
    uses = 0
    stats = {'phi_resolved': 0, 'phi_unresolved': 0, 'sem': 0}

    for rule in definition_guards.rules: 
        for site in rule.sites: 
            if len(site.canonical_events) != 1: 
                continue
            event_id = site.canonical_events[0]
            for item in site.residual_literals: 
                uses += 1
                raw.add(item)
                _cat, text = item.split(':', 1) if ':' in item else ('OTHER', item)
                direct = _direct_literal(text)
                if direct is not None: 
                    key = (event_id, direct)
                else: 
                    m = re.search(r'BB(\d{3})', text)
                    if not m: 
                        key = (event_id, ('UNRESOLVED_TEXT', text))
                    else: 
                        block = int(m.group(1))
                        cond = semantic_ssa.branch_conditions.get(block)
                        if cond is None: 
                            key = (event_id, ('UNRESOLVED_BRANCH', text))
                        else: 
                            key = (event_id, _condition_key(cond, _truth_from_text(text), event_id, phi_pred, stats))
                rows[key] = rows.get(key, 0) + 1

    rendered = [{'uses': n, 'event': k[0], 'predicate': repr(k[1])}
                for k, n in sorted(rows.items(), key = lambda kv: (-kv[1], repr(kv[0])))]
    bb = sum('BB' in x['predicate'] for x in rendered)
    phi = sum("EVENT_PHI" in x['predicate'] or "PHI" in x['predicate'] for x in rendered)
    sem = sum("HW_STATE" in x['predicate'] for x in rendered)
    return EventHardwarePredicateAnalysis(
        residual_literal_uses = uses, 
        unique_raw_literals = len(raw), 
        unique_event_conditioned_predicates = len(rows), 
        predicates_with_bb_identity = bb, 
        predicates_with_phi_identity = phi, 
        predicates_with_semantic_state = sem, 
        phi_inputs_resolved_by_event = stats['phi_resolved'], 
        phi_inputs_unresolved = stats['phi_unresolved'], 
        rows = rendered, 
        notes = [
            'Predicate identity is conditioned on a recovered physical event, not on a CFG edge.', 
            'GPIO_READ block provenance is erased; steady-state GPIO observations become current external input predicates.', 
            'A PHI is replaced by one incoming value only when every CPU path for that event proves the same predecessor.', 
            'CFG predecessor numbers are never retained in EVENT_PHI fallback identities.', 
            'HW_STATE denotes a recovered persistent semantic value that still requires hardware-state interpretation before RTL lowering.', 
        ], 
    )


def write_event_hardware_predicate_report(result: EventHardwarePredicateAnalysis, path: Path) -> None: 
    lines = [
        'EVENT-CONDITIONED HARDWARE PREDICATE NORMALIZATION', 
        '=' * 78, 
        f'residual literal uses              : {result.residual_literal_uses}', 
        f'unique raw literal spellings       : {result.unique_raw_literals}', 
        f'unique event-conditioned predicates: {result.unique_event_conditioned_predicates}', 
        f'predicates retaining BB identity   : {result.predicates_with_bb_identity}', 
        f'predicates retaining PHI identity  : {result.predicates_with_phi_identity}', 
        f'predicates using HW semantic state : {result.predicates_with_semantic_state}', 
        f'PHI selections proven by event     : {result.phi_inputs_resolved_by_event}', 
        f'PHI selections still ambiguous     : {result.phi_inputs_unresolved}', 
        '', 'Predicates', '-' * 78, 
    ]
    for row in result.rows: 
        lines.append(f"{row['uses']:3d}x {row['event']} {row['predicate']}")
    lines += ['', 'Notes', '-' * 78] + [f'- {x}' for x in result.notes]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines) + '\n')
    path.with_suffix(path.suffix + '.json').write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
