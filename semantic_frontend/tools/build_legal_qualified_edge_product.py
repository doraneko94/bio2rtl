#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, hashlib, json
from pathlib import Path


def sha(p: Path) -> str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()


def const1(x): 
    return int(x[1]) if isinstance(x, list) and len(x) >= 2 and x[0] == 'CONST' else None



def _succ(rows, code, event): 
    out = set()
    for e in rows.get(code, {}).get(event, []): 
        for m in e.get('next_map', []): 
            out.add(int(m['code']))
    return out or {code}


def _write_product(a, payload, lines): 
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0




def _gpio_bits_in_expr(e): 
    """Recover one-hot GPIO_INPUT bit dependencies from generic expression IR."""
    out = set()
    if not isinstance(e, list) or not e: 
        return out
    if e[0] == 'OP' and len(e)>=3 and e[1] == 'AND': 
        args = e[2]
        has_gpio = any(isinstance(a, list) and a and a[0] == 'GPIO_INPUT' for a in args)
        consts = [const1(a) for a in args if const1(a) is not None]
        if has_gpio: 
            for m in consts: 
                if m and (m & (m-1)) == 0: 
                    out.add(m.bit_length()-1)
    for x in e[1:]: 
        if isinstance(x, list): 
            if x and isinstance(x[0], list): 
                for y in x: 
                    out.update(_gpio_bits_in_expr(y))
            else: 
                out.update(_gpio_bits_in_expr(x))
    return out

def _eval_expr(e, gpio): 
    if not isinstance(e, list) or not e: 
        raise ValueError(e)
    tag = e[0]
    if tag == 'CONST': 
        return int(e[1])
    if tag == 'GPIO_INPUT': 
        return int(gpio)
    if tag == 'OP': 
        op = e[1]
        vals = [_eval_expr(x, gpio) for x in e[2]]
        if op == 'AND': 
            z = (1<<32)-1
            for v in vals: 
                z&=v
            return z
        if op == 'OR': 
            z = 0
            for v in vals: 
                z|=v
            return z
        if op == 'XOR': 
            z = 0
            for v in vals: 
                z^=v
            return z
        if op == 'ADD': 
            return sum(vals)&0xffffffff
        if op == 'SUB': 
            z = vals[0]
            for v in vals[1:]: 
                z-=v
            return z&0xffffffff
        raise ValueError(op)
    if tag in ('EQ', 'NE', 'ULT', 'UGE'): 
        a = _eval_expr(e[1], gpio)
        b = _eval_expr(e[2], gpio)
        return int({'EQ': a == b, 'NE': a!=b, 'ULT': a<b, 'UGE': a>=b}[tag])
    raise ValueError(tag)

def _detector_free_input_reactive_product(a, ir, tab, bits): 
    bits = sorted(map(int, bits))
    rows = {int(k): v for k, v in tab.get('rows', {}).items()}
    reset = int(tab.get('reset_code', 0))
    start = (reset,)
    seen = {start}
    q = collections.deque([start])
    edges = []
    for_limit = max(1, len(rows)*max(1, 2**len(bits))*8)
    steps = 0
    while q: 
        (code,) = q.popleft()
        steps+=1
        if steps>for_limit: 
            raise SystemExit('detector-free input-reactive product did not converge')
        erows = rows.get(code, {}).get('EVENT_FREE', [])
        for vals in __import__('itertools').product((0, 1), repeat = len(bits)): 
            gpio = sum(v<<b for b, v in zip(bits, vals))
            matched = []
            for row in erows: 
                ok = True
                for g in row.get('guard', []): 
                    try: 
                        truth = bool(_eval_expr(g['expression'], gpio))
                    except ValueError as ex: 
                        raise SystemExit(f'UNSUPPORTED detector-free input guard: {ex}: {g}')
                    if truth != bool(g.get('polarity', True)): 
                        ok = False
                        break
                if ok: 
                    matched.append(row)
            if not matched: 
                continue
            for row in matched: 
                for m in row.get('next_map', []) or [{'code': code}]: 
                    nc = int(m.get('code', code))
                    ns = (nc,)
                    edges.append((code, *vals, 'EVENT_FREE', nc))
                    if ns not in seen: 
                        seen.add(ns)
                        q.append(ns)
    names = ['class']+[f'gpio{b}' for b in bits]
    payload = {
      'version': 'legal-detector-free-input-reactive-v1', 'proof_model': 'DETECTOR_FREE_INPUT_REACTIVE', 
      'inputs': {'ir_sha256': sha(a.ir), 'direct_table_sha256': sha(a.direct_table)}, 
      'topology': {'input_bits': bits, 'scheduler_sources': 0, 'scheduler_detectors': 0}, 
      'assumptions': ['External GPIO values are environmental inputs, not scheduler state.', 
                     'EVENT_FREE labels an asynchronous input-reactive semantic relation and is never a synthesized hardware clock.'], 
      'state_tuple': ['class'], 'edge_tuple': names+['event', 'next_class'], 
      'states': [list(x) for x in sorted(seen)], 'edges': [list(x) for x in sorted(set(edges))], 
      'raw_edge_count': len(edges), 'unique_edge_count': len(set(edges)), 
      'by_event': {'EVENT_FREE': len(set(edges))}, 'reachable_classes': len(seen), 'proof_result': 'PASS'}
    return _write_product(a, payload, ['GENERIC EVENT LEGAL PRODUCT', '='*88, 
      'strategy                  : DETECTOR_FREE_INPUT_REACTIVE', f'input bits                : {bits}', 
      f'reachable classes         : {len(seen)}', f'raw / unique edges        : {len(edges)} / {len(set(edges))}', 
      'EVENT_FREE hardware clock : NO', 'RESULT                    : PASS'])

def _static_quiescent_product(a, ir, tab): 
    """Represent a startup-only program with no recovered scheduler event.

    The single EVENT_FREE row is semantic completion of initialization into a
    quiescent terminal, not a recurring hardware clock/event.
    """
    rows = {int(k): v for k, v in tab.get('rows', {}).items()}
    reset = int(tab.get('reset_code', 0))
    if set(rows) not in ({reset}, set()): 
        raise SystemExit(f'UNSUPPORTED detector-free control classes: {sorted(rows)}')
    event_rows = rows.get(reset, {}).get('EVENT_FREE', [])
    next_codes = set()
    for row in event_rows: 
        for m in row.get('next_map', []): 
            next_codes.add(int(m.get('code', reset)))
    if next_codes and next_codes != {reset}: 
        raise SystemExit(f'UNSUPPORTED detector-free dynamic control transition: {sorted(next_codes)}')
    payload = {
      'version': 'legal-static-quiescent-product-v1', 
      'proof_model': 'STATIC_QUIESCENT', 
      'inputs': {'ir_sha256': sha(a.ir), 'direct_table_sha256': sha(a.direct_table)}, 
      'topology': {'scheduler_sources': 0, 'scheduler_detectors': 0}, 
      'assumptions': [
        'No sampled steady region or scheduler detector exists.', 
        'EVENT_FREE denotes completion of startup into a proved quiescent terminal, not a synthesized recurring clock.'
      ], 
      'state_tuple': ['class'], 
      'edge_tuple': ['class', 'event', 'next_class'], 
      'states': [[reset]], 
      'edges': [[reset, 'EVENT_FREE', reset]], 
      'raw_edge_count': 1, 'unique_edge_count': 1, 
      'by_event': {'EVENT_FREE': 1}, 'reachable_classes': 1, 'proof_result': 'PASS'}
    return _write_product(a, payload, [
      'GENERIC EVENT LEGAL PRODUCT', '='*88, 
      'strategy                  : STATIC_QUIESCENT', 
      'scheduler sources         : 0', 'scheduler detectors       : 0', 
      'reachable states          : 1', 'raw / unique edges        : 1 / 1', 
      'RESULT                    : PASS'])

def _edge_only_product(a, ir, tab, hist_sources, dets): 
    # Track each recovered previous-sample bit directly.  This is a real
    # scheduler state recovered from software history, not a fabricated phase.
    hs = sorted(hist_sources, key = lambda x: str(x.get('id')))
    ids = [str(x['id']) for x in hs]
    bits = [int(x['input_bit']) for x in hs]
    inits = [const1(x.get('initial_value')) for x in hs]
    if any(x not in (0, 1) for x in inits): 
        raise SystemExit('UNSUPPORTED non-binary history initial state')
    by_bit = collections.defaultdict(list)
    for d in dets: 
        if str(d.get('kind'))!='QUALIFIED_INPUT_EDGE': 
            continue
        by_bit[int(d.get('input_bit', -1))].append(d)
    # Every physical history bit may have zero or more canonical edge detectors.
    # Unknown qualifier-only GPIO inputs are environmental conditions and do not
    # require invented state; qualifiers over tracked bits are checked below.
    rows = {int(k): v for k, v in tab['rows'].items()}
    reset = int(tab['reset_code'])
    start = (reset, *[int(x) for x in inits])
    seen = {start}
    q = collections.deque([start])
    edges = []
    while q: 
        st = q.popleft()
        code = int(st[0])
        hvals = list(map(int, st[1:]))
        # EVENT_FREE is the stable-input case.  Include it only when represented
        # by the direct relation; otherwise it is not an architectural event.
        if 'EVENT_FREE' in rows.get(code, {}): 
            for nc in _succ(rows, code, 'EVENT_FREE'): 
                ns = (nc, *hvals)
                edges.append((code, *hvals, 'EVENT_FREE', nc, *hvals))
                if ns not in seen: 
                    seen.add(ns)
                    q.append(ns)
        for hi, (hid, bit) in enumerate(zip(ids, bits)): 
            cur = hvals[hi]
            for d in by_bit.get(bit, []): 
                edge = str(d.get('edge', '')).upper()
                before = 0 if edge == 'RISE' else 1 if edge == 'FALL' else None
                after = 1 if edge == 'RISE' else 0 if edge == 'FALL' else None
                if before is None or cur!=before: 
                    continue
                quals = d.get('qualifiers', [])
                ok = True
                for qq in quals: 
                    if str(qq.get('source'))!='GPIO_INPUT': 
                        continue
                    qb = int(qq.get('bit', -1))
                    qlev = int(qq.get('level', 0))
                    if qb in bits: 
                        # If multiple history sources observe the same input they
                        # must agree in the reachable product.
                        for jj, bj in enumerate(bits): 
                            if bj == qb and hvals[jj]!=qlev: 
                                ok = False
                if not ok: 
                    continue
                ev = str(d['event_id'])
                nh = list(hvals)
                # A canonical edge captures the new logical input value in every
                # recovered history source for that physical GPIO bit.
                for jj, bj in enumerate(bits): 
                    if bj == bit: 
                        nh[jj] = after
                for nc in _succ(rows, code, ev): 
                    ns = (nc, *nh)
                    edges.append((code, *hvals, ev, nc, *nh))
                    if ns not in seen: 
                        seen.add(ns)
                        q.append(ns)
    names = ['class']+ids
    next_names = ['next_class']+['next_'+x for x in ids]
    edge_tuple = names+['event']+next_names
    by_event = collections.Counter(row[len(names)] for row in edges)
    payload = {
      'version': 'legal-generic-event-product-v2', 
      'proof_model': 'CANONICAL_EDGE_HISTORY_ONLY', 
      'inputs': {'ir_sha256': sha(a.ir), 'direct_table_sha256': sha(a.direct_table)}, 
      'topology': {'history_sources': [{'id': i, 'input_bit': b} for i, b in zip(ids, bits)], 
                  'data_events': {str(d['event_id']): {'edge': str(d.get('edge')), 'input_bit': int(d.get('input_bit', -1))} for d in dets if str(d.get('kind')) == 'QUALIFIED_INPUT_EDGE'}}, 
      'assumptions': ['Canonical edge detectors update only recovered previous-sample history; no polling phase or free-running clock is synthesized.', 
                     'EVENT_FREE denotes stable tracked input history when that class exists in the proofed direct relation.'], 
      'state_tuple': names, 
      'edge_tuple': edge_tuple, 
      'states': [list(x) for x in sorted(seen)], 
      'edges': [list(x) for x in sorted(set(edges))], 
      'raw_edge_count': len(edges), 'unique_edge_count': len(set(edges)), 
      'by_event': dict(sorted(by_event.items())), 
      'reachable_classes': len({x[0] for x in seen}), 'proof_result': 'PASS'}
    lines = ['GENERIC EVENT LEGAL PRODUCT', '='*88, 'strategy                  : CANONICAL_EDGE_HISTORY_ONLY', 
           f'history sources           : {len(ids)}', f'reachable states          : {len(seen)}', 
           f'reachable classes         : {payload["reachable_classes"]}', f'raw / unique edges        : {len(edges)} / {len(set(edges))}', 
           f'by event                  : {payload["by_event"]}', 'RESULT                    : PASS']
    return _write_product(a, payload, lines)

def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Build a legal sampled-input product from recovered scheduler detector roles, without event-name constants.')
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--direct-table', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    tab = json.loads(a.direct_table.read_text())
    sources = list(ir.get('scheduler_owned_sources', []))
    dets = list(ir.get('scheduler_detectors', []))
    phase_sources = [s for s in sources if str(s.get('maintenance')) == 'EVENT_PHASE_AUTOMATON']
    hist_sources = [s for s in sources if str(s.get('maintenance')) == 'CAPTURE_GPIO_INPUT']
    # Preserve the original I2C phase+history proof model exactly.  Other BIO
    # shapes select a different generic product strategy rather than fabricating
    # the missing scheduler source.
    if not phase_sources and not hist_sources and not dets: 
        dep = set()
        for byevent in tab.get('rows', {}).values(): 
            for rr in byevent.get('EVENT_FREE', []): 
                for g in rr.get('guard', []): 
                    dep.update(_gpio_bits_in_expr(g.get('expression')))
                for ex in (rr.get('preserved_outcomes') or {}).values(): 
                    dep.update(_gpio_bits_in_expr(ex))
        if dep: 
            return _detector_free_input_reactive_product(a, ir, tab, dep)
        return _static_quiescent_product(a, ir, tab)
    if not phase_sources and hist_sources: 
        return _edge_only_product(a, ir, tab, hist_sources, dets)
    if len(phase_sources)!=1 or len(hist_sources)!=1: 
        raise SystemExit(f'UNSUPPORTED detector topology: phase_sources={len(phase_sources)} history_sources={len(hist_sources)}')
    ps, hs = phase_sources[0], hist_sources[0]
    phase_id = str(ps['id'])
    phase_bit = int(ps['input_bit'])
    data_id = str(hs['id'])
    data_bit = int(hs['input_bit'])
    phase_init = const1(ps.get('initial_value'))
    data_init = const1(hs.get('initial_value'))
    if phase_init not in (0, 1) or data_init not in (0, 1): 
        raise SystemExit('UNSUPPORTED non-binary scheduler initial state')
    pd = [d for d in dets if str(d.get('kind')) == 'POLLING_PHASE_COMPLETION' and str(d.get('phase_state')) == phase_id and int(d.get('input_bit')) == phase_bit]
    if len(pd)<2: 
        raise SystemExit('UNSUPPORTED missing phase completion detector pair')
    by_before = collections.defaultdict(list)
    for d in pd: 
        b = int(d['phase_before'])
        af = int(d['phase_after'])
        if b not in (0, 1) or af not in (0, 1): 
            raise SystemExit('UNSUPPORTED non-binary phase detector')
        by_before[b].append(d)
    if any(len(by_before[b])!=1 for b in (0, 1)): 
        raise SystemExit(f'UNSUPPORTED phase transition fanout: {dict((k,len(v)) for k,v in by_before.items())}')
    # Qualified data-edge detectors must be enabled by the phase input at one common active level.
    qd = []
    active_levels = set()
    for d in dets: 
        if str(d.get('kind'))!='QUALIFIED_INPUT_EDGE' or int(d.get('input_bit', -1))!=data_bit: 
            continue
        qs = [q for q in d.get('qualifiers', []) if str(q.get('source')) == 'GPIO_INPUT' and int(q.get('bit')) == phase_bit]
        if len(qs)!=1: 
            continue
        active_levels.add(int(qs[0]['level']))
        qd.append(d)
    if not qd or len(active_levels)!=1 or next(iter(active_levels)) not in (0, 1): 
        raise SystemExit('UNSUPPORTED qualified-edge topology or active phase')
    active = next(iter(active_levels))
    q_by_edge = {str(d['edge']).upper(): d for d in qd}
    if not {'RISE', 'FALL'} <= set(q_by_edge): 
        raise SystemExit('UNSUPPORTED missing qualified data rise/fall detector pair')
    rows = {int(k): v for k, v in tab['rows'].items()}
    reset = int(tab['reset_code'])
    def succ(code, event): 
        out = set()
        for e in rows.get(code, {}).get(event, []): 
            for m in e.get('next_map', []): 
                out.add(int(m['code']))
        return out or {code}
    start = (reset, phase_init, data_init)
    seen = {start}
    q = collections.deque([start])
    edges = []
    while q: 
        code, phase, data = q.popleft()
        pdet = by_before[phase][0]
        pevent = str(pdet['event_id'])
        next_phase = int(pdet['phase_after'])
        # Stable-at-active-edge protocol rule recovered from qualified detector topology:
        # entering active phase samples arbitrary data accumulated while inactive;
        # leaving active phase keeps data stable at the phase edge.
        next_data_values = (0, 1) if phase != active and next_phase == active else (data,)
        for nd in next_data_values: 
            for nc in succ(code, pevent): 
                st = (nc, next_phase, int(nd))
                edges.append((code, phase, data, pevent, nc, next_phase, int(nd)))
                if st not in seen: 
                    seen.add(st)
                    q.append(st)
        if phase == active: 
            # Data edge may occur while qualifier remains active, never simultaneously with phase completion.
            edge = 'FALL' if data else 'RISE'
            ddet = q_by_edge[edge]
            devent = str(ddet['event_id'])
            nd = 0 if data else 1
            for nc in succ(code, devent): 
                st = (nc, phase, nd)
                edges.append((code, phase, data, devent, nc, phase, nd))
                if st not in seen: 
                    seen.add(st)
                    q.append(st)
    by_event = collections.Counter(e[3] for e in edges)
    payload = {
      'version': 'legal-qualified-edge-product-v1', 
      'proof_model': 'RECOVERED_PHASE_AUTOMATON_PLUS_QUALIFIED_EDGE_STABILITY', 
      'inputs': {'ir_sha256': sha(a.ir), 'direct_table_sha256': sha(a.direct_table)}, 
      'topology': {'phase_source': phase_id, 'phase_input_bit': phase_bit, 'data_history_source': data_id, 'data_input_bit': data_bit, 'active_phase_level': active, 
                  'phase_events': {str(b): str(by_before[b][0]['event_id']) for b in (0, 1)}, 
                  'data_events': {k: str(v['event_id']) for k, v in sorted(q_by_edge.items())}}, 
      'assumptions': ['Qualified data edges occur only while their recovered GPIO qualifier is active.', 
                     'At a phase-completion edge, qualified data is stable; no qualified-data detector fires simultaneously.', 
                     'While the qualifier is inactive, data may change arbitrarily and is sampled nondeterministically when the active phase begins.'], 
      'state_tuple': ['class', 'phase', 'sampled_data'], 
      'edge_tuple': ['class', 'phase', 'sampled_data', 'event', 'next_class', 'next_phase', 'next_sampled_data'], 
      'states': [list(x) for x in sorted(seen)], 
      'edges': [list(x) for x in sorted(set(edges))], 
      'raw_edge_count': len(edges), 
      'unique_edge_count': len(set(edges)), 
      'by_event': dict(sorted(by_event.items())), 
      'reachable_classes': len({x[0] for x in seen}), 
      'proof_result': 'PASS'}
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
    lines = ['GENERIC QUALIFIED-EDGE LEGAL PRODUCT', '='*88, 
           f'phase source / input bit : {phase_id} / {phase_bit}', f'data source / input bit  : {data_id} / {data_bit}', f'active phase level       : {active}', 
           f'reachable states         : {len(seen)}', f'reachable classes        : {payload["reachable_classes"]}', f'raw / unique edges       : {len(edges)} / {len(set(edges))}', f'by event                 : {payload["by_event"]}', 'RESULT                   : PASS']
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
