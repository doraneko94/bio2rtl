from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import json

from .event_vector_decision_dag import _vector_paths, _branch_trace, _fold_value, _semantic_next_outcome
from .definition_site_equivalence import _paths
from .hardware_event_transition_analysis import _apply_path, _path_target
from .hardware_behavior_ir import expr_key
from .next_state_expr import Expr, NextStateExprIR

@dataclass
class EventVectorSequenceDagAnalysis: 
    event_vectors: int
    cpu_paths: int
    fused_paths: int
    complete_outcomes: int
    normalized_predicates: int
    raw_test_occurrences: int
    trie_nodes: int
    reduced_suffix_nodes: int
    leaves: int
    sequence_conflicts: int
    rows: list[dict]
    predicate_categories: dict[str, int]
    notes: list[str]

class TNode: 
    __slots__ = ('edges', 'outs')
    def __init__(self): 
        self.edges = {}
        self.outs = set()


def _intern_reduce(node, memo, intern, leaves, conflicts): 
    if not node.edges: 
        if len(node.outs)!=1: 
            conflicts[0]+=1
            key = ('CONFLICT', tuple(sorted(node.outs, key = repr)))
        else: 
            key = ('LEAF', next(iter(node.outs)))
        if key not in leaves: 
            leaves[key] = ('L', len(leaves))
        return leaves[key]
    kids = []
    for label, ch in sorted(node.edges.items(), key = lambda kv: repr(kv[0])): 
        kids.append((label, _intern_reduce(ch, memo, intern, leaves, conflicts)))
    key = ('SEQ', tuple(kids), tuple(sorted(node.outs, key = repr)))
    if key in memo: 
        return memo[key]
    if key not in intern: 
        intern[key] = ('N', len(intern), tuple(kids))
    memo[key] = intern[key]
    return intern[key]


def analyze_event_vector_sequence_dag(reach, next_state, structural, events, temporal, ssa, def_ir, region_name = 'SAMPLE_BB006'): 
    region, groups = _vector_paths(reach, events, region_name)
    regs = sorted(structural.state_nodes)
    ident = {r: Expr(kind = 'STATE', state_family = r) for r in regs}
    transparent = set()
    cache = {}
    for tr in temporal.event_regions: 
        transparent.update(tr.fused_transparent_samples)
    for b in transparent: 
        rn = f'SAMPLE_BB{b:03d}'
        ps, trunc = _paths(reach.regions[rn], 200000)
        if trunc: 
            raise RuntimeError(f'{rn}: sequence-DAG path enumeration truncated')
        cache[b] = ps
    rows = []
    totalp = totalf = raw = tn = rn = lf = cf = 0
    allp = set()
    allouts = set()
    cats = {}
    for sig, ps in sorted(groups.items(), key = lambda kv: (-len(kv[1]), kv[0])): 
        traces = []
        for nodes, edges in ps: 
            pred = {b: a for a, b in edges}
            seq1, env1 = _branch_trace(region, nodes, edges, ssa, pred, def_ir)
            st1 = _apply_path(ident, region, edges, next_state)
            t1 = _path_target(region, nodes, edges)
            if t1 in transparent: 
                r2 = reach.regions[f'SAMPLE_BB{t1:03d}']
                for n2, e2 in cache[t1]: 
                    seq2, _ = _branch_trace(r2, n2, e2, ssa, {b: a for a, b in e2}, def_ir, env1)
                    st = _apply_path(st1, r2, e2, next_state)
                    out = (tuple((r, expr_key(st[r])) for r in regs), _semantic_next_outcome(ssa, tuple(edges)+tuple(e2)))
                    traces.append((seq1+seq2, out))
            else: 
                out = (tuple((r, expr_key(st1[r])) for r in regs), _semantic_next_outcome(ssa, tuple(edges)))
                traces.append((seq1, out))
        # Remove only predicates evaluated with the same value on every trace.
        vals = {}
        presence = {}
        for seq, _ in traces: 
            seen = set()
            for p, v in seq: 
                vals.setdefault(p, set()).add(v)
                seen.add(p)
            for p in seen: 
                presence[p] = presence.get(p, 0)+1
        constants = {p for p, vs in vals.items() if len(vs) == 1 and presence.get(p, 0) == len(traces)}
        root = TNode()
        nodes_created = 1
        outcomes = set()
        for seq, out in traces: 
            cur = root
            filtered = [(p, v) for p, v in seq if p not in constants]
            raw+=len(filtered)
            outcomes.add(out)
            allouts.add(out)
            for item in filtered: 
                allp.add(item[0])
                pred_s = item[0]
                if "HW_STATE" in pred_s: 
                    cat = 'SEMANTIC_CONTROL'
                elif "stack_main_sp_m44" in pred_s or "stack_main_sp_m72" in pred_s: 
                    cat = 'COUNTER'
                elif "stack_main_sp_m48" in pred_s: 
                    cat = 'SHIFT_DATAPATH'
                elif "GPIO_" in pred_s: 
                    cat = 'GPIO_LEVEL'
                elif "STATE" in pred_s: 
                    cat = 'PHYSICAL_CONTROL'
                else: 
                    cat = 'OTHER'
                cats[cat] = cats.get(cat, 0)+1
                if item not in cur.edges: 
                    cur.edges[item] = TNode()
                    nodes_created+=1
                cur = cur.edges[item]
            cur.outs.add(out)
        memo = {}
        intern = {}
        leaves = {}
        conf = [0]
        _intern_reduce(root, memo, intern, leaves, conf)
        rows.append({'events': list(sig), 'software_paths': len(ps), 'fused_paths': len(traces), 'outcomes': len(outcomes), 'trie_nodes': nodes_created, 'reduced_suffix_nodes': len(intern), 'leaves': len(leaves), 'sequence_conflicts': conf[0], 'event_implied_predicates': len(constants)})
        totalp+=len(ps)
        totalf+=len(traces)
        tn+=nodes_created
        rn+=len(intern)
        lf+=len(leaves)
        cf+=conf[0]
    return EventVectorSequenceDagAnalysis(len(groups), totalp, totalf, len(allouts), len(allp), raw, tn, rn, lf, cf, rows, cats, [
        'Predicates are normalized hardware expressions; BB/reach/edge identity is not part of the DAG key.', 
        'Unlike unordered path cubes, the sequence DAG preserves whether a later predicate is conditionally evaluated.', 
        'Identical suffix subgraphs are hash-consed across each event vector.', 
    ])

def write_event_vector_sequence_dag_report(r, path: Path): 
    lines = ['EVENT-VECTOR ORDERED HARDWARE DECISION DAG', '='*78, f'event vectors          : {r.event_vectors}', f'CPU paths              : {r.cpu_paths}', f'fused paths            : {r.fused_paths}', f'complete outcomes       : {r.complete_outcomes}', f'normalized predicates  : {r.normalized_predicates}', f'raw test occurrences   : {r.raw_test_occurrences}', f'trie nodes             : {r.trie_nodes}', f'reduced suffix nodes    : {r.reduced_suffix_nodes}', f'leaves                 : {r.leaves}', f'sequence conflicts     : {r.sequence_conflicts}', '', 'Predicate-use categories', '-'*78] + [f'{k:20s}: {v}' for k, v in sorted(r.predicate_categories.items())] + ['']
    for x in r.rows: 
        lines += ['+'.join(x['events']), f"  paths/reduced nodes: {x['fused_paths']} / {x['reduced_suffix_nodes']}", f"  outcomes           : {x['outcomes']}", f"  sequence conflicts : {x['sequence_conflicts']}", '']
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text('\n'.join(lines)+'\n')
    path.with_suffix(path.suffix+'.json').write_text(json.dumps(asdict(r), indent = 2, sort_keys = True))
