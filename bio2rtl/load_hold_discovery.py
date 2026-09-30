from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import json, re, sys


def _load(p: Path): 
    return json.loads(Path(p).read_text())

def _capture_signature(e): 
    """Recognize a compact serial capture word: sampled GPIO bit at bit0 + old shift bit0 at bit1.

    This is deliberately structural and register-name agnostic.  It returns the semantic
    source identifiers rather than physical net spellings.
    """
    if not (isinstance(e, list) and len(e)>=3 and e[0] == 'OP' and e[1] == 'OR'): 
        return None
    gpio_bits = []; shifts = []
    for a in e[2]: 
        if isinstance(a, list) and len(a) == 3 and a[0] == 'BIT_VALUE' and a[1] == ['GPIO_INPUT']: 
            gpio_bits.append(int(a[2]))
        elif isinstance(a, list) and len(a)>=3 and a[0] == 'OP' and a[1] == 'SHL': 
            xs = a[2]
            if (len(xs) == 2 and xs[1] == ['CONST', 1] and isinstance(xs[0], list) and len(xs[0]) == 3
                and xs[0][0] == 'BIT_VALUE' and isinstance(xs[0][1], list) and xs[0][1][0] == 'REG'
                and int(xs[0][2]) == 0): 
                shifts.append(str(xs[0][1][1]))
    if len(gpio_bits) == 1 and len(shifts) == 1: 
        return {'kind': 'serial_capture_lsb_first_2bit', 'sampled_input_bit': gpio_bits[0], 
                'shift_source': shifts[0], 'shift_bit': 0, 'width': 2}
    return None

def _mirror_relations(stor: dict): 
    out = []
    pat = re.compile(r'^\s*([A-Za-z_]\w*)\s*=\s*(~)?([A-Za-z_]\w*)\[(\d+):(\d+)\]\s*$')
    for s in stor.get('gpio_mirror_fusion', {}).get('relations', []): 
        m = pat.match(str(s))
        if not m: 
            continue
        hi, lo = int(m.group(4)), int(m.group(5))
        out.append({'history': m.group(1), 'payload': m.group(3), 'invert': bool(m.group(2)), 
                    'bits': list(range(lo, hi+1)), 'relation': str(s)})
    return out

def _class_members(q: dict): 
    tracked = list(map(str, q['tracked_registers']))
    return tracked, {int(c['code']): [dict(zip(tracked, map(int, s))) for s in c['states']] for c in q['classes']}

def _transition_classes(tab: dict, event: str, tids: set[str])->set[int]: 
    out = set()
    for k, row in tab['rows'].items(): 
        if any(str(a.get('transition_id')) in tids for a in row.get(event, [])): 
            out.add(int(k))
    return out


def _legal_transition_classes(tab: dict, lp: dict, event: str, tids: set[str], counter_source: str|None = None, counter_value: int|None = None)->set[int]: 
    direct = _transition_classes(tab, event, tids); ix = {str(n): i for i, n in enumerate(lp.get('edge_tuple', []))}; out = set()
    if 'class' not in ix or 'event' not in ix: 
        return direct
    for e in lp.get('unique_edges', []): 
        c = int(e[ix['class']]); ev = str(e[ix['event']])
        if c not in direct or ev!=event: 
            continue
        if counter_source is not None and counter_source in ix and int(e[ix[counter_source]])!=int(counter_value): 
            continue
        out.add(c)
    return out

def _invariants(classes: set[int], members: dict[int, list[dict]]): 
    vals = defaultdict(set)
    for c in classes: 
        for m in members.get(c, []): 
            for k, v in m.items(): 
                vals[str(k)].add(int(v))
    return {k: next(iter(v)) for k, v in vals.items() if len(v) == 1}

def _control_state_map(ctrl: dict): 
    regs = list(map(str, ctrl.get('control_registers', [])))
    m = {tuple(map(int, r['pattern'])): int(r['state']) for r in ctrl.get('state_rows', [])}
    return regs, m

def _control_states(classes: set[int], members: dict[int, list[dict]], ctrl: dict)->list[int]: 
    regs, mp = _control_state_map(ctrl); out = set()
    for c in classes: 
        for x in members.get(c, []): 
            p = tuple(int(x[r]) for r in regs)
            if p in mp: 
                out.add(mp[p])
    return sorted(out)

def _payload_selector_actions(root: Path, phase, q, tab, lp, stor, mirrors): 
    """Exhaustively prove which payload bank captures the serial word at each commit selector.

    This is the same inductive semantic domain used by storage-relations, but the result is
    normalized as load/hold recurrence metadata.  No physical recipe/netlist is consulted.
    """
    def u32(v): 
        return int(v)&0xffffffff
    def s32(v): 
        v = u32(v); return v-(1<<32) if v&(1<<31) else v
    def eval_expr(e, regs, sched, gpio): 
        tag = e[0]
        if tag == 'CONST': 
            return int(e[1])
        if tag == 'REG': 
            return int(regs[str(e[1])])
        if tag == 'SCHED_REG': 
            return int(sched[str(e[1])])
        if tag == 'GPIO_INPUT': 
            return int(gpio)
        if tag == 'BIT_VALUE': 
            return (eval_expr(e[1], regs, sched, gpio)>>int(e[2]))&1
        if tag == 'EQ_CONST': 
            return int(u32(eval_expr(e[1], regs, sched, gpio)) == u32(int(e[2])))
        if tag in ('EQ', 'NE', 'ULT', 'UGE', 'SLT', 'NOT_SLT'): 
            a = eval_expr(e[1], regs, sched, gpio);b = eval_expr(e[2], regs, sched, gpio)
            if tag == 'EQ': 
                return int(u32(a) == u32(b))
            if tag == 'NE': 
                return int(u32(a)!=u32(b))
            if tag == 'ULT': 
                return int(u32(a)<u32(b))
            if tag == 'UGE': 
                return int(u32(a)>=u32(b))
            if tag == 'SLT': 
                return int(s32(a)<s32(b))
            return int(not(s32(a)<s32(b)))
        if tag == 'OP': 
            op = str(e[1]); vals = [eval_expr(x, regs, sched, gpio) for x in e[2]]
            if op == 'AND': 
                return u32(vals[0])&u32(vals[1])
            if op == 'OR': 
                return u32(vals[0])|u32(vals[1])
            if op == 'XOR': 
                return u32(vals[0])^u32(vals[1])
            if op == 'ADD': 
                return u32(vals[0]+vals[1])
            if op == 'SUB': 
                return u32(vals[0]-vals[1])
            if op == 'SHL': 
                return u32(vals[0]<<(u32(vals[1])&31))
            if op == 'SHR': 
                return u32(vals[0])>>(u32(vals[1])&31)
        raise ValueError(e)
    def refs(e): 
        if not isinstance(e, list) or not e: 
            return set()
        if e[0] == 'REG': 
            return {str(e[1])}
        z = set()
        for x in e[1:]: 
            if isinstance(x, list): 
                if x and isinstance(x[0], list): 
                    for y in x: 
                        z|=refs(y)
                else: 
                    z|=refs(x)
        return z
    def build_derived_rows(ir): 
        out = {}
        for sp in ir.get('storage_optimization', {}).get('register_storage', []): 
            if str(sp.get('storage_kind')) == 'DERIVED_EXPR' and sp.get('derived_expression') is not None: 
                out[str(sp['register'])] = {'expression': sp['derived_expression']}
        d = ir.get('storage_optimization', {}).get('derived_state_elimination')
        if isinstance(d, dict) and d.get('target') and d.get('expression'): 
            out.setdefault(str(d['target']), {'expression': d['expression']})
        return out
    def augment_derived(base, derived, sched, gpio): 
        regs = dict(base);pending = dict(derived)
        while pending: 
            progress = False
            for rid, row in list(pending.items()): 
                if refs(row['expression']).issubset(regs): 
                    regs[rid] = int(eval_expr(row['expression'], regs, sched, gpio));del pending[rid];progress = True
            if not progress: 
                break
        return regs
    def event_environment(event, event_sample): 
        det = next(d for d in phase.get('scheduler_detectors', []) if str(d.get('event_id')) == str(event))
        gpio = 0;sched = {}
        if det.get('kind')!='POLLING_PHASE_COMPLETION': 
            raise ValueError(f'commit event must be phase completion, got {det}')
        phase_bit = int(det['input_bit']); gpio|=(int(det['phase_after'])&1)<<phase_bit
        sched[str(det['phase_state'])] = int(det['phase_before'])
        # Populate scheduler history sources from the sampled input used by the proven serial capture.
        for row in phase.get('scheduler_owned_sources', []): 
            if row.get('maintenance') == 'CAPTURE_GPIO_INPUT': 
                b = int(row['input_bit']); val = int(event_sample)&1 if b == sampled_input_bit else int(row.get('initial_value', ['CONST', 0])[1])
                sched[str(row['id'])] = val; gpio|=val<<b
            elif row.get('maintenance') == 'EVENT_PHASE_AUTOMATON': 
                sched.setdefault(str(row['id']), int(det['phase_before']) if int(row.get('input_bit', -1)) == phase_bit else int(row.get('initial_value', ['CONST', 0])[1]))
        return gpio, sched

    info = stor['gpio_mirror_fusion']; event = str(info['commit_event']); bits = list(map(int, info['payload_bits']))
    classes = list(map(int, info['commit_classes'])); term = str(info['terminal_count_source']); tv = int(info['terminal_value'])
    shift = str(info['shift_source']); sampled_input_bit = int(info['sampled_input_bit']); selector = str(info['selector_context_candidate'])
    tracked, members = _class_members(q); rows = {int(k): v for k, v in tab['rows'].items()}
    derived = build_derived_rows(phase); basis = {str(x['id']): x['expression'] for x in phase['predicate_basis']}
    arch = {str(r['id']): r for r in phase['architectural_registers']}
    def reset(r): 
        e = arch[r]['reset']
        if not (isinstance(e, list) and e and e[0] == 'CONST'): 
            raise ValueError(f'nonconstant reset {r}')
        return int(e[1])
    def insert(base, bits, val): 
        z = int(base)
        for i, b in enumerate(bits): 
            z = (z&~(1<<b))|(((int(val)>>i)&1)<<b)
        return z
    def extract(v, bits): 
        z = 0
        for i, b in enumerate(bits): 
            z|=((int(v)>>b)&1)<<i
        return z
    bytid = defaultdict(lambda: defaultdict(list))
    for r in phase['update_rules']: 
        for tid in r.get('source_transition_ids', []): 
            bytid[str(tid)][str(r['target'])].append(r)
    def enabled(r, regs, sched, gpio): 
        return all(bool(eval_expr(basis[str(g['basis'])], regs, sched, gpio)) == bool(g['polarity']) for g in r.get('enable', []))
    def next_target(tid, tgt, old, regs, sched, gpio): 
        cand = [r for r in bytid.get(str(tid), {}).get(tgt, []) if str(r.get('event_class')) == event and enabled(r, regs, sched, gpio)]
        if not cand: 
            return int(old)
        vals = []
        for r in cand: 
            rr = dict(regs);rr[tgt] = int(old);vals.append(int(eval_expr(r['outcome'], rr, sched, gpio)))
        if len(set(vals))!=1: 
            raise ValueError(f'ambiguous semantic update {tid} {tgt} {vals}')
        return vals[0]
    base = {rid: reset(rid) for rid in arch}; shift_width = int(arch[shift]['width']); mask = (1<<len(bits))-1
    out = []
    for c in classes: 
        if len(members[c])!=1: 
            raise ValueError(f'commit class {c} must be singleton')
        rep = members[c][0]; sel = int(rep[selector]); counts = {m['payload']: {'hold': 0, 'capture': 0, 'other': 0} for m in mirrors}
        hitcount = 0
        for sh in range(1<<shift_width): 
            for hvals_tuple in __import__('itertools').product(range(mask+1), repeat = len(mirrors)): 
                hvals = {m['history']: int(v) for m, v in zip(mirrors, hvals_tuple)}
                for sd in (0, 1): 
                    gpio, sched = event_environment(event, sd)
                    regs = dict(base);regs.update(rep);regs[term] = tv;regs[shift] = sh;regs.update(hvals)
                    for m in mirrors: 
                        pv = hvals[m['history']]^(mask if m['invert'] else 0)
                        regs[m['payload']] = insert(reset(m['payload']), bits, pv)
                    regs = augment_derived(regs, derived, sched, gpio)
                    hits = []
                    for ent in rows[c].get(event, []): 
                        ok = True
                        for gd in ent.get('guard', []): 
                            rr = refs(gd['expression'])
                            if not rr.issubset(regs): 
                                ok = False
                                break
                            if bool(eval_expr(gd['expression'], regs, sched, gpio))!=bool(gd['polarity']): 
                                ok = False
                                break
                        if ok and any(t in bytid.get(str(ent['transition_id']), {}) for t in [m['payload'] for m in mirrors]): 
                            hits.append(str(ent['transition_id']))
                    if len(hits)!=1: 
                        raise ValueError(f'payload commit row cardinality class={c} hits={hits}')
                    hitcount+=1; tid = hits[0]; capture = int(sd)|((int(sh)&1)<<1)
                    for m in mirrors: 
                        p = m['payload'];old = extract(regs[p], bits);nv = extract(next_target(tid, p, regs[p], regs, sched, gpio), bits)
                        want = capture^(mask if m['invert'] else 0)
                        if nv == old: 
                            counts[p]['hold']+=1
                        elif nv == want: 
                            counts[p]['capture']+=1
                        else: 
                            counts[p]['other']+=1
        active = [p for p, x in counts.items() if x['capture'] and not x['other']]
        # A bank may occasionally already equal the incoming word, so HOLD observations do not
        # disqualify it.  Inactive banks must never differ from old value.
        inactive = [p for p, x in counts.items() if x['capture'] == 0 and x['other'] == 0]
        if len(active)!=1 or len(inactive)!=len(mirrors)-1: 
            raise ValueError(f'payload selector action ambiguous selector={sel} counts={counts}')
        out.append({'class': c, 'selector_value': sel, 'active_payload': active[0], 
                    'context_checks': hitcount, 'counts': counts})
    return sorted(out, key = lambda x: x['selector_value'])



def _legal_rising_samples(q: dict, lp: dict, ctrl: dict, event: str, extra_axes: list[str]|None = None): 
    """Enumerate unique reachable semantic projections at an event.

    Quotient class numbers are proof bookkeeping, not state variables.  Multiple classes may
    project to the same tracked-state + legal-product scalar valuation; merge those samples
    and retain the contributing class set for provenance.
    """
    tracked, members = _class_members(q)
    ix = {str(n): i for i, n in enumerate(lp.get('edge_tuple', []))}
    if not {'class', 'event'}.issubset(ix): 
        raise ValueError('legal_product lacks class/event axes')
    ctrl_regs, ctrl_map = _control_state_map(ctrl)
    extra_axes = set(map(str, extra_axes or []))
    merged = {}
    for e in lp.get('unique_edges', []): 
        if str(e[ix['event']])!=str(event): 
            continue
        c = int(e[ix['class']]); extra = {}
        for name in extra_axes: 
            if name not in ix: 
                raise ValueError(f'legal_product lacks requested semantic axis {name}')
            v = e[ix[name]]
            if not isinstance(v, (bool, int)): 
                raise ValueError(f'non-scalar semantic axis {name}: {v!r}')
            extra[name] = int(v)
        for m in members.get(c, []): 
            d = dict(m);d.update(extra)
            pat = tuple(int(d[r]) for r in ctrl_regs)
            if pat in ctrl_map: 
                d['CONTROL_STATE'] = int(ctrl_map[pat])
            key = tuple(sorted((str(k), int(v)) for k, v in d.items()))
            row = merged.setdefault(key, {'values': d, 'classes': set()}); row['classes'].add(c)
    return [{'values': x['values'], 'classes': sorted(x['classes'])} for _, x in sorted(merged.items())]

def _minimal_exact_conjunction(samples: list[dict], target_fn, preferred_sources: list[str]|None = None): 
    """Find a minimum-size conjunction of equality atoms exactly matching target_fn.

    Candidate atoms are target invariants.  Exhaustive subset search is practical because
    each bank exposes only the handful of state axes invariant over its proven load set.
    Deterministic tie-breaking favors caller-provided semantic sources, then lexical order.
    """
    import itertools
    targets = [s for s in samples if target_fn(s)]
    if not targets: 
        raise ValueError('empty target set for load predicate synthesis')
    keys = set(targets[0]['values'])
    for s in targets: 
        keys &= set(s['values'])
    inv = []
    pref = {str(k): i for i, k in enumerate(preferred_sources or [])}
    for k in sorted(keys): 
        vals = {int(s['values'][k]) for s in targets}
        if len(vals) == 1: 
            inv.append((str(k), next(iter(vals))))
    # Do not use compiler-derived CONTROL_STATE if raw semantic registers already distinguish
    # the target; it is retained as a last-resort atom for truly factorized control domains.
    inv.sort(key = lambda kv: (0 if kv[0] in pref else (2 if kv[0] == 'CONTROL_STATE' else 1), pref.get(kv[0], 999), kv[0], kv[1]))
    checks = 0;solutions = []
    for n in range(0, len(inv)+1): 
        for comb in itertools.combinations(inv, n): 
            checks+=len(samples)
            ok = True
            for s in samples: 
                pred = all(int(s['values'].get(k, -0x7fffffff)) == int(v) for k, v in comb)
                if bool(pred)!=bool(target_fn(s)): 
                    ok = False;break
            if ok: 
                solutions.append(comb)
        if solutions: 
            break
    if not solutions: 
        raise ValueError('no exact conjunction found on legal semantic domain')
    def score(comb): 
        return tuple((0 if k in pref else (2 if k == 'CONTROL_STATE' else 1), pref.get(k, 999), k, v) for k, v in comb)
    solutions = sorted(solutions, key = score);best = solutions[0]
    return {
        'kind': 'semantic_equality_conjunction', 
        'atoms': [{'kind': 'EQ_CONST', 'source': k, 'value': int(v)} for k, v in best], 
        'atom_count': len(best), 
        'minimum_solution_count': len(solutions), 
        'domain_samples': len(samples), 
        'target_samples': len(targets), 
        'exact_checks': len(samples), 
        'status': 'PASS', 
    }

def discover_load_hold_semantic_recurrence(root: Path)->dict: 
    root = Path(root); b = root/'build'; g = b/'generated_certificates'
    phase = _load(b/'semantic/phase40.ir.json'); q = _load(b/'semantic/behavioral_quotient.json'); tab = _load(b/'semantic/directfsm_table.json'); lp = _load(b/'semantic/legal_product.json')
    stor = _load(g/'storage_relations.json'); obsproj = _load(g/'observation_source_projection.json'); ctrl = _load(g/'control_factorization.json'); snapcert = _load(g/'observation_snapshot.json')
    if any(x.get('status')!='PASS' for x in (stor, obsproj, ctrl, snapcert)): 
        return {'version': 'bio2rtl-load-hold-semantic-recurrence-v1', 'status': 'N_A', 'reason': 'required proofs not PASS'}
    mirrors = _mirror_relations(stor); info = stor['gpio_mirror_fusion']; selector = str(info['selector_context_candidate']); event = str(info['commit_event'])
    if not mirrors or not selector: 
        return {'version': 'bio2rtl-load-hold-semantic-recurrence-v1', 'status': 'N_A', 'reason': 'no mirror/selector specialization'}
    tracked, members = _class_members(q)

    # Discover the serial capture rule for the selector context from phase semantics.
    capture = {'kind': 'serial_capture_lsb_first_2bit', 'sampled_input_bit': int(info['sampled_input_bit']), 
             'shift_source': str(info['shift_source']), 'shift_bit': 0, 'width': len(info['payload_bits'])}
    selrules = []
    for r in phase['update_rules']: 
        if str(r.get('target')) == selector and str(r.get('event_class')) == event and _capture_signature(r.get('outcome')) == capture: 
            selrules.append(r)
    if len(selrules)!=1: 
        raise ValueError(f'expected unique selector serial-capture rule, got {[r.get("rule_id") for r in selrules]}')
    sr = selrules[0]; stids = set(map(str, sr.get('source_transition_ids', []))); sclasses = _legal_transition_classes(tab, lp, event, stids, str(info['terminal_count_source']), int(info['terminal_value']))
    sinv = _invariants(sclasses, members); sctrl = _control_states(sclasses, members, ctrl)

    # Observation-local relaxation: the semantic register itself only needs the external-input
    # selector load, but the snapshot proof establishes that capturing external input on every
    # observation-load edge is trace-safe because unused selector modes never consume P11.
    extvals = list(map(int, obsproj.get('external_input_selector_values', [])))
    relax = snapcert.get('safe_update_relaxation', {})
    if relax.get('status')!='PASS': 
        raise ValueError('observation snapshot relaxation proof unavailable')
    rtids = set(map(str, relax.get('transition_ids', [])))
    if not rtids: 
        raise ValueError('snapshot relaxation has empty transition provenance')
    rclasses = _legal_transition_classes(tab, lp, event, rtids, str(info['terminal_count_source']), int(info['terminal_value']))
    rinv = _invariants(rclasses, members); rctrl = _control_states(rclasses, members, ctrl)

    actions = _payload_selector_actions(root, phase, q, tab, lp, stor, mirrors)
    relation_by_payload = {m['payload']: m for m in mirrors}
    legal_samples = _legal_rising_samples(q, lp, ctrl, event, [str(info['terminal_count_source'])])
    preferred = [str(info['terminal_count_source']), str(selector)]
    # Recurrence-role sources that are already independently proved should be preferred over
    # low-level control-storage bits when multiple equal-size predicates exist.
    dp = root/'build/generated_certificates/direct_state_recurrence.json'
    if dp.exists(): 
        dd = _load(dp)
        ds = str(dd.get('semantic_source', '')) if dd.get('status') == 'PASS' else ''
        if ds and ds in q.get('tracked_registers', []): 
            preferred.append(ds)
    def synth(classes): 
        cs = set(map(int, classes)); term = str(info['terminal_count_source']); tv = int(info['terminal_value'])
        def target(s): 
            flags = {int(c) in cs and int(s['values'].get(term, -1)) == tv for c in s.get('classes', [])}
            if len(flags)>1: 
                raise ValueError(f'load predicate is not a function of projected semantic state: classes={s.get("classes")}')
            return bool(next(iter(flags))) if flags else False
        return _minimal_exact_conjunction(legal_samples, target, preferred_sources = preferred)
    banks = []
    banks.append({'role': 'selector_context', 'semantic_source': selector, 'width': capture['width'], 'event': event, 
                  'load_semantics': {'kind': 'serial_capture', 'capture': capture, 'transition_ids': sorted(stids), 
                                    'source_classes': sorted(sclasses), 'tracked_invariants': sinv, 'control_states': sctrl, 
                                    'terminal_counter': {'source': str(info['terminal_count_source']), 'value': int(info['terminal_value'])}, 
                                    'minimal_load_predicate': synth(sclasses)}, 
                  'data_semantics': capture})
    snap_src = obsproj['source_by_selector'][str(extvals[0])]
    banks.append({'role': 'observation_snapshot', 'semantic_source': str(obsproj['observation_register']), 
                  'semantic_bits': list(map(int, obsproj.get('snapshot_bits', []))), 'event': event, 
                  'load_semantics': {'kind': 'external_snapshot_relaxed', 'semantic_selector_values': extvals, 'safe_capture_selector_values': list(map(int, relax.get('selector_values', []))), 'transition_ids': sorted(rtids), 
                                    'source_classes': sorted(rclasses), 'tracked_invariants': rinv, 'control_states': rctrl, 
                                    'terminal_counter': {'source': str(info['terminal_count_source']), 'value': int(info['terminal_value'])}, 
                                    'minimal_load_predicate': synth(rclasses)}, 
                  'data_semantics': snap_src})
    for a in actions: 
        m = relation_by_payload[a['active_payload']]
        banks.append({'role': 'gpio_payload', 'semantic_source': m['payload'], 'semantic_bits': m['bits'], 'event': event, 
                      'load_semantics': {'kind': 'selector_commit', 'selector_register': selector, 'selector_values': [int(a['selector_value'])], 
                                        'commit_class': int(a['class']), 'tracked_invariants': _invariants({int(a['class'])}, members), 
                                        'control_states': _control_states({int(a['class'])}, members, ctrl), 
                                        'terminal_counter': {'source': str(info['terminal_count_source']), 'value': int(info['terminal_value'])}, 
                                        'context_checks': int(a['context_checks']), 
                                        'minimal_load_predicate': synth({int(a['class'])})}, 
                      'data_semantics': {**capture, 'invert': bool(m['invert']), 'history_relation': m['relation']}})
    total_checks = sum(int(a['context_checks']) for a in actions)
    return {'version': 'bio2rtl-load-hold-semantic-recurrence-v1', 'status': 'PASS', 'banks': banks, 
            'payload_selector_actions': actions, 'payload_exhaustive_checks': total_checks, 
            'authority': ['phase40.update_rules', 'behavioral_quotient', 'directfsm_table', 'legal_product', 'storage_relations', 'observation_source_projection', 'observation_snapshot.safe_update_relaxation', 'control_factorization'], 
            'physical_recipe_used': False, 'interface_binding_cache_used': False, 'protocol_names_used_for_discovery': False}

def emit_load_hold_semantic_recurrence(root: Path)->dict: 
    root = Path(root);d = discover_load_hold_semantic_recurrence(root);p = root/'build/generated_certificates/load_hold_semantic_recurrence.json';p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n');return d
