#!/usr/bin/env python3
from __future__ import annotations
import argparse, collections, hashlib, itertools, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: 
    sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_reachability import _eval_expr


def sha(p: Path)->str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()
def refs(x): 
    out = set()
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG': 
            out.add(str(x[1]))
            return out
        for y in x: 
            out |= refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            out |= refs(y)
    return out

def is_const(x, v = None): 
    return isinstance(x, list) and len(x)>=2 and x[0] == 'CONST' and (v is None or int(x[1]) == int(v))
def is_delta(x, op, rid): 
    if not (isinstance(x, list) and len(x)>=3 and x[0] == 'OP' and str(x[1]) == op): 
        return False
    a = x[2]
    if not isinstance(a, list) or len(a)!=2: 
        return False
    if op == 'ADD': 
        pairs = ((a[0], a[1]), (a[1], a[0]))
    else: 
        pairs = ((a[0], a[1]),)
    return any(x0 == ['REG', rid] and is_const(x1, 1) for x0, x1 in pairs)

def derived_rows(ir): 
    out = {}
    for sp in (ir.get('storage_optimization') or {}).get('register_storage', []): 
        if str(sp.get('storage_kind')) == 'DERIVED_EXPR' and sp.get('derived_expression') is not None: 
            out[str(sp['register'])] = sp['derived_expression']
    d = (ir.get('storage_optimization') or {}).get('derived_state_elimination')
    if isinstance(d, dict) and d.get('target') and d.get('expression'): 
        out.setdefault(str(d['target']), d['expression'])
    return out

def augment(regs, derived, sched, gpio): 
    regs = dict(regs)
    pending = dict(derived)
    while pending: 
        changed = False
        for rid, ex in list(pending.items()): 
            if refs(ex).issubset(regs): 
                regs[rid] = int(_eval_expr(ex, regs, sched, gpio))
                del pending[rid]
                changed = True
        if not changed: 
            break
    return regs

def discover_pairs(ir): 
    widths = {str(x['id']): int(x['width']) for x in ir['architectural_registers'] if x.get('kind') == 'PHYSICAL'}
    startup = {str(x['register']): int(x['value'][1]) for x in ir['startup']['register_values'] if is_const(x.get('value'))}
    by = collections.defaultdict(list)
    for r in ir['update_rules']: 
        if r.get('materialize'): 
            by[str(r['target'])].append(r)
    inc_regs = []
    dec_regs = []
    for rid, w in widths.items(): 
        outs = [r['outcome'] for r in by.get(rid, [])]
        if any(is_delta(x, 'ADD', rid) for x in outs) and any(is_const(x, 0) for x in outs): 
            inc_regs.append(rid)
        if any(is_delta(x, 'SUB', rid) for x in outs) and any(is_const(x, 0) for x in outs): 
            dec_regs.append(rid)
    pairs = []
    for inc, dec in itertools.product(inc_regs, dec_regs): 
        wi, wd = widths[inc], widths[dec]
        if wi!=wd+1 or startup.get(inc)!=0 or startup.get(dec)!=0: 
            continue
        outs_i = [r['outcome'] for r in by[inc]]
        outs_d = [r['outcome'] for r in by[dec]]
        maxv = (1<<wd)-1
        terminal = 1<<wd
        if not any(is_const(x, terminal) for x in outs_i): 
            continue
        if not any(is_const(x, maxv) for x in outs_d): 
            continue
        pairs.append({'inc_register': inc, 'dec_register': dec, 'inc_width': wi, 'counter_width': wd, 'max_value': maxv, 'terminal_value': terminal})
    return pairs

def classify_entry(entry, pair): 
    inc, dec = pair['inc_register'], pair['dec_register']
    maxv = pair['max_value']
    term = pair['terminal_value']
    oi = entry['preserved_outcomes'][inc]
    od = entry['preserved_outcomes'][dec]
    return {'seed': is_const(od, maxv), 'dec': is_delta(od, 'SUB', dec), 'inc': is_delta(oi, 'ADD', inc) or is_const(oi, term), 'clear': is_const(oi, 0) or is_const(od, 0), 'terminal': is_const(oi, term)}

def main()->int: 
    ap = argparse.ArgumentParser()
    ap.add_argument('--direct-table', type = Path, required = True)
    ap.add_argument('--quotient', type = Path, required = True)
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--legal-phase-product', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    ap.add_argument('--edge-cache', type = Path)
    a = ap.parse_args()
    tab = json.loads(a.direct_table.read_text())
    quo = json.loads(a.quotient.read_text())
    ir = json.loads(a.ir.read_text())
    lpp = json.loads(a.legal_phase_product.read_text())
    if lpp.get('proof_result')!='PASS': 
        raise SystemExit('FAIL legal phase product')
    pairs = discover_pairs(ir)
    if not pairs: 
        payload = {'version': 'protocol-counter-ownership-generic-v1', 'inputs': {'direct_table_sha256': sha(a.direct_table), 'quotient_sha256': sha(a.quotient), 'ir_sha256': sha(a.ir), 'legal_phase_product_sha256': sha(a.legal_phase_product)}, 
                 'discovered_pair': None, 'base': None, 'negative_selftests': {}, 'result': 'PASS', 'n_a': True, 
                 'n_a_reason': 'no structural counter pair candidate', 'proof_statement': 'Optional counter sharing is N/A; the proof-backed legal event product is preserved unchanged.'}
        a.output.parent.mkdir(parents = True, exist_ok = True)
        a.report.parent.mkdir(parents = True, exist_ok = True)
        a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
        if a.edge_cache: 
            cache = dict(lpp)
            cache['version'] = 'legal-event-product-counter-identity-v1'
            cache['proof_result'] = 'PASS'
            cache['n_a'] = True
            if 'unique_edges' not in cache: 
                cache['unique_edges'] = list(cache.get('edges', []))
            a.edge_cache.parent.mkdir(parents = True, exist_ok = True)
            a.edge_cache.write_text(json.dumps(cache, indent = 2, sort_keys = True)+'\n')
        lines = ['BIO2RTL GENERIC PROTOCOL-COUNTER OWNERSHIP PROOF', '='*96, 'candidate pairs          : 0', 'OPTIONAL RESULT           : N/A', 'identity legal product    : PASS', 'RESULT: PASS']
        a.report.write_text('\n'.join(lines)+'\n')
        print('\n'.join(lines))
        return 0
    topo = lpp['topology']
    phase_source = str(topo['phase_source'])
    hist_source = str(topo['data_history_source'])
    phase_bit = int(topo['phase_input_bit'])
    data_bit = int(topo['data_input_bit'])
    active = int(topo['active_phase_level'])
    phase_events = {int(k): str(v) for k, v in topo['phase_events'].items()}
    data_events = {str(k): str(v) for k, v in topo['data_events'].items()}
    # Data event pre/post levels derive from edge direction, not event spelling.
    data_edge = {data_events['FALL']: (1, 0), data_events['RISE']: (0, 1)}
    rows = {int(k): v for k, v in tab['rows'].items()}
    reset_code = int(tab['reset_code'])
    tracked = list(map(str, quo['tracked_registers']))
    members = {int(c['code']): [dict(zip(tracked, map(int, s))) for s in c['states']] for c in quo['classes']}
    derived = derived_rows(ir)
    arch = {str(x['id']): int(x['width']) for x in ir['architectural_registers']}
    phase_init = next(int(s['initial_value'][1]) for s in ir['scheduler_owned_sources'] if str(s['id']) == phase_source)
    data_init = next(int(s['initial_value'][1]) for s in ir['scheduler_owned_sources'] if str(s['id']) == hist_source)

    def env(current_phase, current_data, next_phase, next_data): 
        # The recovered scheduler samples the data-history source when a phase
        # completion edge enters the active phase. During the inactive phase
        # external data may change arbitrarily, so the just-sampled next_data
        # is the semantic history value on that completion edge.
        sampled_data = int(next_data) if int(current_phase)!=active and int(next_phase) == active else int(current_data)
        sched = {phase_source: int(current_phase), hist_source: sampled_data}
        gpio = (int(next_phase)&1)<<phase_bit | (int(next_data)&1)<<data_bit
        return sched, gpio
    def legal_actions(phase, data, busy, open_event): 
        close_event = next(e for e in data_edge if e!=open_event)
        if not busy: 
            pre, post = data_edge[open_event]
            return [(open_event, phase, post)] if phase == active and data == pre else []
        out = []
        p_ev = phase_events[int(phase)]
        next_phase = 1-int(phase)
        if phase!=active and next_phase == active: 
            out.extend((p_ev, next_phase, nd) for nd in (0, 1))
        else: 
            out.append((p_ev, next_phase, data))
        if phase == active: 
            for ev, (pre, post) in data_edge.items(): 
                if data == pre: 
                    out.append((ev, phase, post))
        return out
    def guard_entry_feasible(entry, code, pairvals, phase, data, nphase, ndata, pair): 
        sched, gpio = env(phase, data, nphase, ndata)
        base = dict(pairvals)
        counter_regs = {pair['inc_register'], pair['dec_register']}
        derived_targets = set(derived)
        deferred = []
        for g in entry.get('guard', []): 
            rr = refs(g['expression'])
            if rr and rr.issubset(counter_regs): 
                if bool(_eval_expr(g['expression'], base, sched, gpio))!=bool(g['polarity']): 
                    return False
            elif rr and rr.issubset(counter_regs | derived_targets) and (rr & derived_targets): 
                deferred.append(g)
        if deferred: 
            for member in members.get(code, []): 
                regs = dict(member)
                regs.update(base)
                regs = augment(regs, derived, sched, gpio)
                if all(refs(g['expression']).issubset(regs) and bool(_eval_expr(g['expression'], regs, sched, gpio)) == bool(g['polarity']) for g in deferred): 
                    return True
            return False
        return True
    def possible_rows(code, event, pairvals, phase, data, nphase, ndata, pair): 
        out = []
        inc, dec = pair['inc_register'], pair['dec_register']
        wi, wd = pair['inc_width'], pair['counter_width']
        sched, gpio = env(phase, data, nphase, ndata)
        for entry in rows.get(code, {}).get(event, []): 
            if not guard_entry_feasible(entry, code, pairvals, phase, data, nphase, ndata, pair): 
                continue
            values = []
            for rid, w in ((inc, wi), (dec, wd)): 
                ex = entry['preserved_outcomes'][rid]
                rr = refs(ex)
                if rr.issubset(pairvals): 
                    values.append([int(_eval_expr(ex, pairvals, sched, gpio)) & ((1<<w)-1)])
                else: 
                    values.append(list(range(1<<w)))
            for nm in entry.get('next_map', []): 
                for ni, nd in itertools.product(*values): 
                    out.append((int(nm['code']), ni, nd, entry))
        return out
    def entry_reads(entry, rid): 
        return any(rid in refs(g['expression']) for g in entry.get('guard', []))

    def run(pair, open_event, priority, *, disable = None, seed_override = None, save_edges = False): 
        inc, dec = pair['inc_register'], pair['dec_register']
        maxv = pair['max_value']
        start = (reset_code, phase_init, data_init, False, 0, 0, 0)
        q = collections.deque([start])
        seen = {start}
        edge_count = 0
        unique = set()
        violations = []
        readvals = {inc: set(), dec: set()}
        readpoints = {inc: set(), dec: set()}
        term = seedclear = 0
        while q and not violations: 
            code, phase, data, busy, vi, vd, count = q.popleft()
            pairvals = {inc: vi, dec: vd}
            actions = legal_actions(phase, data, busy, open_event)
            # Ownership equality is required at the phase-completion guard read
            # point for the current recovered phase.  Data-edge actions do not
            # constitute counter-consumption points.
            completion_event = phase_events[int(phase)]
            for rid, val in ((inc, vi), (dec, vd)): 
                if any(entry_reads(e, rid) for e in rows.get(code, {}).get(completion_event, [])): 
                    readpoints[rid].add((code, phase))
                    readvals[rid].add(val)
                    if val>maxv or count!=val: 
                        violations.append(('READ_MISMATCH', rid, (code, phase, data, busy, vi, vd, count)))
                        break
            if violations: 
                break
            for event, nphase, ndata in actions: 
                for nc, ni, nd, entry in possible_rows(code, event, pairvals, phase, data, nphase, ndata, pair): 
                    k = classify_entry(entry, pair)
                    flags = {x: bool(k[x]) and x!=disable for x in ('seed', 'dec', 'inc', 'clear')}
                    ncount = count
                    for op in priority: 
                        if not flags[op]: 
                            continue
                        if op == 'seed': 
                            ncount = maxv if seed_override is None else int(seed_override)
                        elif op == 'dec': 
                            ncount = max(0, count-1)
                        elif op == 'inc': 
                            ncount = min(maxv, count+1)
                        elif op == 'clear': 
                            ncount = 0
                        break
                    nbusy = True if event == open_event else (False if event!=open_event and event in data_edge else busy)
                    st = (nc, nphase, ndata, nbusy, ni, nd, ncount)
                    edge_count+=1
                    if save_edges: 
                        unique.add((code, phase, data, busy, vi, vd, count, event, nc, nphase, ndata, nbusy, ni, nd, ncount))
                    if k['terminal']: 
                        term+=1
                    if k['seed'] and k['clear']: 
                        seedclear+=1
                    if st not in seen: 
                        seen.add(st)
                        q.append(st)
        return {'pass': not violations, 'states': len(seen), 'edges': edge_count, 'violations': violations[:8], 
                'read_values': {k: sorted(v) for k, v in readvals.items()}, 'read_points': {k: sorted(v) for k, v in readpoints.items()}, 
                'terminal_edge_occurrences': term, 'seed_clear_edge_occurrences': seedclear, '_states': seen, '_edges': unique}

    trials = []
    for pair in pairs: 
        for open_event in sorted(data_edge): 
            for priority in itertools.permutations(('seed', 'dec', 'inc', 'clear')): 
                r = run(pair, open_event, priority)
                # Avoid vacuous success: both source registers must be read and cover the physical counter domain.
                domain = list(range(pair['max_value']+1))
                coverage = all(r['read_values'][x] == domain for x in (pair['inc_register'], pair['dec_register']))
                if r['pass'] and coverage: 
                    trials.append((pair, open_event, priority, r))
    if not trials: 
        raise SystemExit('FAIL no counter ownership policy passes non-vacuous read-domain proof')
    # Deterministic minimal proof: smaller state/edge product, then lexical structural IDs/policy.
    trials.sort(key = lambda x: (x[3]['states'], x[3]['edges'], x[0]['inc_register'], x[0]['dec_register'], x[1], x[2]))
    pair, open_event, priority, _ = trials[0]
    base = run(pair, open_event, priority, save_edges = True)
    passing_policies = [{'inc_register': p['inc_register'], 'dec_register': p['dec_register'], 'frame_open_event': oe, 'priority': list(pr), 'states': r['states'], 'edges': r['edges']} for p, oe, pr, r in trials]
    # Generic mutation/selftests: disable each arithmetic direction, wrong seed, and swap the first conflicting priority pair.
    mutations = {}
    for op in ('inc', 'dec'): 
        mutations['disable_'+op] = run(pair, open_event, priority, disable = op)
    mutations['wrong_seed'] = run(pair, open_event, priority, seed_override = max(0, pair['max_value']-1))
    swapped = list(priority)
    if len(swapped)>=2: 
        swapped[0], swapped[-1] = swapped[-1], swapped[0]
    mutations['priority_mutation'] = run(pair, open_event, tuple(swapped))
    mut_public = {k: {kk: vv for kk, vv in v.items() if not kk.startswith('_')} for k, v in mutations.items()}
    negatives_ok = all(not x['pass'] for x in mut_public.values())
    result = 'PASS' if negatives_ok else 'FAIL'
    payload = {'version': 'protocol-counter-ownership-generic-v1', 'inputs': {'direct_table_sha256': sha(a.direct_table), 'quotient_sha256': sha(a.quotient), 'ir_sha256': sha(a.ir), 'legal_phase_product_sha256': sha(a.legal_phase_product)}, 
             'discovered_pair': pair, 'frame_open_event': open_event, 'frame_close_event': next(e for e in data_edge if e!=open_event), 'priority': list(priority), 'passing_policy_count': len(trials), 'passing_policies': passing_policies, 
             'base': {k: v for k, v in base.items() if not k.startswith('_')}, 'negative_selftests': mut_public, 'result': result, 
             'proof_statement': 'Register identities, framing event role, and shared-counter priority are discovered from current semantic IR plus the recovered qualified-edge legal product; equality is required only at phase-completion guard read points derived from legal-product topology, with full 0..max read-domain coverage.'}
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(payload, indent = 2, sort_keys = True)+'\n')
    if a.edge_cache: 
        cache = {'version': 'legal-qualified-edge-counter-product-v2', 'proof_result': result, 
               'proof_model': 'GENERIC_STRUCTURAL_COUNTER_OWNERSHIP_OVER_RECOVERED_PHASE_TOPOLOGY', 
               'topology': topo, 
               'state_tuple': ['class', 'phase', 'data', 'busy', pair['inc_register'], pair['dec_register'], 'shared_counter'], 
               'states': [list(x) for x in sorted(base['_states'])], 
               'edge_tuple': ['class', 'phase', 'data', 'busy', pair['inc_register'], pair['dec_register'], 'shared_counter', 'event', 'next_class', 'next_phase', 'next_data', 'next_busy', 'next_'+pair['inc_register'], 'next_'+pair['dec_register'], 'next_shared_counter'], 
               'unique_edges': [list(x) for x in sorted(base['_edges'])], 'raw_edge_count': base['edges']}
        a.edge_cache.parent.mkdir(parents = True, exist_ok = True)
        a.edge_cache.write_text(json.dumps(cache, indent = 2, sort_keys = True)+'\n')
    lines = ['BIO2RTL GENERIC PROTOCOL-COUNTER OWNERSHIP PROOF', '='*96, 
           f'candidate pairs          : {len(pairs)}', f'passing policies         : {len(trials)}', f'discovered pair          : {pair["inc_register"]}({pair["inc_width"]}) + {pair["dec_register"]}({pair["counter_width"]})', 
           f'frame open / close       : {open_event} / {payload["frame_close_event"]}', f'priority                 : {list(priority)}', f'base states/edges        : {base["states"]}/{base["edges"]}', 
           f'read values {pair["inc_register"]:>8s} : {base["read_values"][pair["inc_register"]]}', f'read values {pair["dec_register"]:>8s} : {base["read_values"][pair["dec_register"]]}']
    for n, r in mut_public.items(): 
        lines.append(f'negative {n:18s}: rejected={not r["pass"]} violation={r["violations"][:1]}')
    lines.append('RESULT: '+result)
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0 if result == 'PASS' else 1
if __name__ == '__main__': 
    raise SystemExit(main())
