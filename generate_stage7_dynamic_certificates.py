from __future__ import annotations
from pathlib import Path
from collections import defaultdict, Counter, deque
import argparse, hashlib, itertools, json, re, sys, os

ROOT = Path(__file__).resolve().parent
_parser = argparse.ArgumentParser(add_help = True)
_parser.add_argument('--stop-after', choices = ('shared', 'snapshot', 'oe'), default = 'oe')
_parser.add_argument('--optional', action = 'store_true', help = 'treat structurally inapplicable dynamic refinements as N_A instead of compiler failure')
_args = _parser.parse_args()
STOP_AFTER = _args.stop_after
OPTIONAL = _args.optional
SEM = ROOT/'build/semantic'
OUT = Path(os.environ.get('BIO2RTL_CERT_OUT', ROOT/'build/generated_certificates'))
OUT.mkdir(parents = True, exist_ok = True)
sys.path.insert(0, str(ROOT/'semantic_frontend'))
from bio2rtl.dedicated_event_reachability import _eval_expr


def load(p): 
    return json.loads(Path(p).read_text())
def dump(p, d): 
    Path(p).write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _na_cert(kind, reason): 
    return {
      'version': 'bio2rtl-stage7-dynamic-na-v1', 'status': 'N_A', 'kind': kind, 
      'reason': str(reason), 'hardcoded_semantic_register_names': False, 
      'proof_scope': 'OPTIONAL DYNAMIC REFINEMENT NOT STRUCTURALLY APPLICABLE; CONSERVATIVE NATURAL ARCHITECTURE REQUIRED', 
    }

def _optional_exit(stage, reason): 
    if not OPTIONAL: 
        raise RuntimeError(str(reason))
    if stage == 'shared': 
        for fn, kind in (
          ('shared_counter.json', 'shared_counter'), 
          ('shared_counter_index_projection.json', 'shared_counter_index_projection'), 
          ('shared_counter_sync_exit.json', 'shared_counter_sync_exit'), 
          ('shared_counter_terminal_hold.json', 'shared_counter_terminal_hold'), 
        ): dump(OUT/fn, _na_cert(kind, reason))
    if stage in ('shared', 'snapshot'): 
        dump(OUT/'observation_snapshot.json', _na_cert('observation_snapshot', reason))
    if stage in ('shared', 'snapshot', 'oe'): 
        dump(OUT/'oe_recurrence.json', _na_cert('open_drain_oe_recurrence', reason))
    report = {
      'version': 'bio2rtl-stage7-generic-dynamic-certificates-v1', 'status': 'PASS', 
      'dynamic_status': 'N_A', 'stopped_at': stage, 'reason': str(reason), 
      'hardcoded_semantic_register_names': False, 
    }
    dump(ROOT/'build/stage7_generic_dynamic_certificate_report.json', report)
    print(json.dumps(report, indent = 2, sort_keys = True))
    raise SystemExit(0)

def refs(x): 
    s = set()
    if isinstance(x, list): 
        if len(x)>=2 and x[0] == 'REG': 
            return {str(x[1])}
        for y in x: 
            s|=refs(y)
    elif isinstance(x, dict): 
        for y in x.values(): 
            s|=refs(y)
    return s

phase = load(SEM/'phase40.ir.json')
qj = load(SEM/'behavioral_quotient.json')
lp = load(SEM/'legal_product.json')
tab = load(SEM/'directfsm_table.json')
# Dynamic stage-7 analyses are optional refinements.  Do not turn absence of an
# earlier optional certificate into a compiler failure: a BIO with no recovered
# control factorization/counter/observation topology must conservatively fall back
# to the natural architecture instead.
ctrl_p = OUT/'control_factorization.json'
storrel_p = OUT/'storage_relations.json'
obslocal_p = OUT/'observation_local_bit_elimination.json'
missing = [p.name for p in (ctrl_p, storrel_p) if not p.exists()]
if missing: 
    if OPTIONAL: 
        _optional_exit('shared', 'optional prerequisite missing: '+','.join(missing))
    raise SystemExit('optional prerequisite missing: '+','.join(missing))
ctrl = load(ctrl_p)
storrel = load(storrel_p)
if ctrl.get('status')!='PASS' or storrel.get('status')!='PASS': 
    if OPTIONAL: 
        _optional_exit('shared', 'optional prerequisite proof is not PASS')
    raise SystemExit('optional prerequisite missing: prerequisite proof not PASS')
if STOP_AFTER!='shared' and not obslocal_p.exists(): 
    if OPTIONAL: 
        _optional_exit('snapshot', 'optional prerequisite missing: observation_local_bit_elimination.json')
    raise SystemExit('optional prerequisite missing: observation_local_bit_elimination.json')
obslocal = load(obslocal_p) if obslocal_p.exists() else {'results': []}
if lp.get('proof_result')!='PASS': 
    raise RuntimeError('legal product proof is not PASS')
storage = {x['register']: x for x in phase['storage_optimization']['register_storage']}
arch = {x['id']: x for x in phase['architectural_registers']}
E = lp['edge_tuple']
ei = {n: i for i, n in enumerate(E)}
rows = {int(k): v for k, v in tab['rows'].items()}
tracked = list(qj['tracked_registers'])
ti = {n: i for i, n in enumerate(tracked)}
reps = {int(c['code']): dict(zip(tracked, map(int, c['representative']))) for c in qj['classes']}

# Generic direct-row matcher. Legal product is already correlated; next_map matching removes impossible class branches.
def edge_entries(e): 
    c = int(e[ei['class']])
    nc = int(e[ei['next_class']])
    ev = str(e[ei['event']])
    return [x for x in rows.get(c, {}).get(ev, []) if any(int(nm['code']) == nc for nm in x.get('next_map', []))]

# -----------------------------------------------------------------------------
# 1) Discover COUNT pair and exact guard-read points without semantic names.
# -----------------------------------------------------------------------------
# Legal-product phase values are schema-level encoding, not semantic names.
def norm_phase(v): 
    if str(v) in ('H', 'L'): 
        return str(v)
    for raw, ev in (lp.get('topology', {}).get('phase_events', {}) or {}).items(): 
        try: 
            same = int(raw) == int(v)
        except Exception: 
            same = str(raw) == str(v)
        if same: 
            if str(ev) == 'PHEVT_RISE': 
                return 'L'
            if str(ev) == 'PHEVT_FALL': 
                return 'H'
    raise RuntimeError(f'unclassified legal-product phase value {v!r}')

count_regs = sorted((r for r, d in storage.items() if int(d.get('storage_bits', 0))>0 and int(d.get('recurrence_classes', {}).get('COUNT', 0))>0), 
                  key = lambda r: (-int(storage[r]['storage_bits']), r))
# Discover direct guard readpoints for every COUNT-like storage object first.  A design may
# contain zero, one, or many counters; shared-counter optimization is only attempted for
# structurally compatible ordered pairs and must never require a globally fixed candidate count.
readpoints = {r: set() for r in count_regs}
for e in lp['unique_edges']: 
    gr = set()
    for ent in edge_entries(e): 
        for g in ent.get('guard', []): 
            gr |= refs(g.get('expression'))
    for r in count_regs: 
        if r in gr: 
            readpoints[r].add((int(e[ei['class']]), norm_phase(e[ei['phase']])))
pair_candidates = []
for inc in count_regs: 
    iw = int(storage[inc]['storage_bits'])
    for dec in count_regs: 
        if inc == dec: 
            continue
        dw = int(storage[dec]['storage_bits'])
        if iw!=dw+1: 
            continue
        if not readpoints.get(inc) or not readpoints.get(dec): 
            continue
        pair_candidates.append((inc, dec))
if not pair_candidates: 
    _optional_exit('shared', f'no structurally coalescible COUNT pair among candidates {count_regs}')
if len(pair_candidates)!=1: 
    raise RuntimeError(f'ambiguous structurally coalescible COUNT pairs {pair_candidates}')
inc_reg, dec_reg = pair_candidates[0]
inc_width = int(storage[inc_reg]['storage_bits'])
dec_width = int(storage[dec_reg]['storage_bits'])
maxv = (1<<dec_width)-1
terminal = maxv+1

# Normalize legal-product schema before semantic analysis.  Legacy products used
# H/L + sda/next_sda labels; Generic Ver.1 products use recovered phase-source
# values + data/next_data.  This adapter uses only stage24 topology.
data_field = 'data' if 'data' in ei else ('sampled_data' if 'sampled_data' in ei else 'sda')
next_data_field = 'next_'+data_field if ('next_'+data_field) in ei else ('next_sda' if 'next_sda' in ei else None)
if next_data_field is None: 
    raise RuntimeError('legal product has no next sampled-data field')
busy_field = 'busy' if 'busy' in ei else None
next_busy_field = 'next_busy' if 'next_busy' in ei else None
if busy_field is None or next_busy_field is None: 
    raise RuntimeError('legal product has no framed busy field')
next_inc_field = ('next_'+inc_reg) if ('next_'+inc_reg) in ei else ('next_inc' if 'next_inc' in ei else None)
next_dec_field = ('next_'+dec_reg) if ('next_'+dec_reg) in ei else ('next_dec' if 'next_dec' in ei else None)
if next_inc_field is None or next_dec_field is None: 
    raise RuntimeError(f'legal product lacks discovered counter next fields inc={inc_reg} dec={dec_reg} edge_tuple={E}')
def pre(e): 
    return (int(e[ei['class']]), norm_phase(e[ei['phase']]), int(e[ei[data_field]]), bool(e[ei[busy_field]]), int(e[ei[inc_reg]]), int(e[ei[dec_reg]]))
def post(e): 
    return (int(e[ei['next_class']]), norm_phase(e[ei['next_phase']]), int(e[ei[next_data_field]]), bool(e[ei[next_busy_field]]), int(e[ei[next_inc_field]]), int(e[ei[next_dec_field]]))
semantic_edges = {(pre(e), str(e[ei['event']]), post(e)) for e in lp['unique_edges']}
adj = defaultdict(set)
for a, ev, b in semantic_edges: 
    adj[a].add((ev, b))
rise_events = {ev for a, ev, b in semantic_edges if a[1] == 'L' and b[1] == 'H'}
fall_events = {ev for a, ev, b in semantic_edges if a[1] == 'H' and b[1] == 'L'}
frame_open_events = {ev for a, ev, b in semantic_edges if not bool(a[3]) and bool(b[3])}
if len(rise_events)!=1 or len(fall_events)!=1 or len(frame_open_events)!=1: 
    raise RuntimeError((rise_events, fall_events, frame_open_events))
rise_event = next(iter(rise_events))
fall_event = next(iter(fall_events))
frame_open_event = next(iter(frame_open_events))
dec_high_classes = {c for c, ph in readpoints[dec_reg] if ph == 'H'}
seed_high = {a[0] for a, ev, b in semantic_edges if ev == fall_event and int(b[5]) == maxv and int(a[5])!=maxv}
seed_entry = {(a, ev, b) for a, ev, b in semantic_edges if ev == rise_event and b[1] == 'H' and b[0] in seed_high}
tx_exit_low = {(b[0], b[1]) for a, ev, b in semantic_edges if ev == fall_event and a[0] in dec_high_classes and int(a[5]) == 0}
if tx_exit_low & readpoints[inc_reg] or tx_exit_low & readpoints[dec_reg]: 
    raise RuntimeError('stale TX-exit low phase intersects counter readpoint')

# Recovered control-state map used only to discover the terminal reject/hold class.
pat_i = {tuple(p): i for i, p in enumerate(ctrl['patterns'])}
cregs = ctrl['control_registers']
class_control = {c: pat_i[tuple(reps[c][r] for r in cregs)] for c in reps}
terminal_edges = {(a, ev, b) for a, ev, b in semantic_edges if ev == rise_event and int(a[4]) == maxv and int(b[4]) == terminal}
hold_edges = {(a, ev, b) for a, ev, b in terminal_edges if class_control[a[0]] == class_control[b[0]]}
if not hold_edges: 
    raise RuntimeError('no terminal same-control hold edge discovered')
if any((b[0], b[1]) in readpoints[inc_reg] or (b[0], b[1]) in readpoints[dec_reg] for _, _, b in hold_edges): 
    raise RuntimeError('terminal hold post-state is a counter readpoint')
# This must be the unique same-control terminal outcome family.
term_groups = Counter((class_control[a[0]], class_control[b[0]]) for a, _, b in terminal_edges)
same_groups = [k for k in term_groups if k[0] == k[1]]
if len(same_groups)!=1: 
    raise RuntimeError(f'terminal hold control ambiguity: {term_groups}')

reset_code = int(tab['reset_code'])
starts = [s for s in adj if int(s[0]) == reset_code and not bool(s[3]) and int(s[4]) == 0 and int(s[5]) == 0]
if len(starts)!=1: 
    raise RuntimeError(f'reset semantic source ambiguity {starts}')
start = starts[0]

def pc_step(pc, a, ev, b, *, terminal_hold = True, sync_exit = True): 
    if ev == frame_open_event: 
        return 0, 'FRAME_RESET'
    if (a, ev, b) in seed_entry: 
        return maxv, 'TX_EARLY_SEED_MAX'
    if ev == rise_event and b[1] == 'H' and b[0] in dec_high_classes: 
        return (pc-1)&maxv, 'TX_EARLY_DEC'
    if sync_exit and ev == rise_event and (a[0], a[1]) in tx_exit_low: 
        return 0, 'TX_EXIT_SYNC_CLEAR'
    if terminal_hold and (a, ev, b) in hold_edges: 
        return pc, 'RX_TERMINAL_HOLD_MAX'
    if ev == rise_event and int(b[4]) == int(a[4])+1: 
        return (pc+1)&maxv, 'RX_INC_MOD'
    return pc, 'HOLD'

def run_pc(*, terminal_hold = True, sync_exit = True): 
    dq = deque([(start, 0)])
    seen = {(start, 0)}
    bad = []
    ops = Counter()
    transitions = 0
    readchecks = 0
    holds = []
    stale = []
    while dq and not bad: 
        a, pc = dq.popleft()
        if (a[0], a[1]) in readpoints[inc_reg]: 
            readchecks+=1
            if not (0<=int(a[4])<=maxv and pc == int(a[4])): 
                bad.append(('INC_READ', a, pc))
        if (a[0], a[1]) in readpoints[dec_reg]: 
            readchecks+=1
            want = ((pc+1)&maxv) if a[1] == 'H' else pc
            if want!=int(a[5]): 
                bad.append(('DEC_READ', a, pc, want))
        for ev, b in adj[a]: 
            npc, op = pc_step(pc, a, ev, b, terminal_hold = terminal_hold, sync_exit = sync_exit)
            transitions+=1
            ops[op]+=1
            if (a, ev, b) in hold_edges: 
                holds.append((a, pc, b, npc))
            if (a[0], a[1]) in tx_exit_low: 
                stale.append((a, pc))
            z = (b, npc)
            if z not in seen: 
                seen.add(z)
                dq.append(z)
    return {'seen': seen, 'bad': bad, 'ops': ops, 'transitions': transitions, 'readchecks': readchecks, 'holds': holds, 'stale': stale}

base = run_pc()
# Independently cover semantic-source graph.
sq = deque([start])
sseen = {start}
while sq: 
    s = sq.popleft()
    for _, n in adj[s]: 
        if n not in sseen: 
            sseen.add(n)
            sq.append(n)
missing = sseen-{s for s, _ in base['seen']}
status = 'PASS' if not base['bad'] and not missing else 'FAIL'
# Generic operation families projected onto recovered semantic control-state ids.
op_families = defaultdict(lambda: {'source_control_states': set(), 'next_control_states': set(), 'source_phases': set(), 'next_phases': set(), 'events': set()})
for a, pc in sorted(base['seen'], key = lambda z: (z[0], z[1])): 
    for ev, b in adj[a]: 
        npc, op = pc_step(pc, a, ev, b)
        f = op_families[op]
        f['source_control_states'].add(class_control[a[0]])
        f['next_control_states'].add(class_control[b[0]])
        f['source_phases'].add(str(a[1]))
        f['next_phases'].add(str(b[1]))
        f['events'].add(str(ev))
op_family_json = {op: {k: sorted(v) for k, v in fam.items()} for op, fam in sorted(op_families.items())}
shared = {
 'version': 'bio2rtl-stage7-shared-counter-generic-v2', 'status': status, 
 'semantic_sources': {'increment_source': inc_reg, 'decrement_source': dec_reg}, 
 'source_widths': {inc_reg: inc_width, dec_reg: dec_width}, 'counter_width': dec_width, 'max_value': maxv, 'terminal_value': terminal, 
 'readpoints_by_source': {r: [list(x) for x in sorted(readpoints[r])] for r in count_regs}, 
 'semantic_source_states': len(sseen), 'semantic_unique_edges': len(semantic_edges), 'lifted_states': len(base['seen']), 
 'lifted_transition_checks': base['transitions'], 'readpoint_checks': base['readchecks'], 'seed_high_classes': sorted(seed_high), 'dec_read_high_classes': sorted(dec_high_classes), 
 'seed_entry_edge_count': len(seed_entry), 'operation_counts': dict(base['ops']), 'missing_source_states': len(missing), 'counterexamples': base['bad'][:20], 
 'operation_families': op_family_json, 
 'representation': {'bits': dec_width, 'increment_view': f'PC={inc_reg} at all discovered readpoints; terminal {terminal} represented by PC=0 plus recovered control completion', 
                   'decrement_low_view': f'PC={dec_reg}', 'decrement_high_view': f'{dec_reg}=(PC+1) mod {1<<dec_width}', 
                   'clocking': 'arithmetic/seed actions retimed to recovered phase-completion rising domain; frame-open event provides reset'}, 
 'proof_scope': 'STRUCTURALLY_DISCOVERED_COUNT_PAIR + DIRECT-FSM GUARD READPOINTS + EXACT LEGAL PRODUCT; NO SEMANTIC REGISTER NAME ASSUMPTIONS', 
 'hardcoded_semantic_register_names': False, 
 'inputs': {'phase40_ir_sha256': sha(SEM/'phase40.ir.json'), 'direct_fsm_sha256': sha(SEM/'directfsm_table.json'), 'legal_product_sha256': sha(SEM/'legal_product.json')}
}
if status!='PASS': 
    raise RuntimeError(shared)
dump(OUT/'shared_counter.json', shared)
# Structured projection certificate for consumers of the decrement count.  Keep this
# separate from shared_counter.json so adding machine-readable projection metadata does
# not perturb the content-addressed architecture selector for the proven counter itself.
shared_index_projection = {
 'version': 'bio2rtl-shared-counter-index-projection-v1', 'status': status, 
 'semantic_count_source': dec_reg, 'semantic_count_width': dec_width, 
 'physical_counter_width': dec_width, 'modulus': 1<<dec_width, 
 'relations_by_phase': {
   'L': {'semantic_count': 'PC', 'offset_mod': 0}, 
   'H': {'semantic_count': 'PC_PLUS_1_MOD', 'offset_mod': 1}, 
 }, 
 'decrement_read_high_classes': sorted(dec_high_classes), 
 'readpoint_checks': base['readchecks'], 'counterexamples': base['bad'][:20], 
 'proof_anchor': {'shared_counter_status': status, 'shared_counter_semantic_sources': shared['semantic_sources'], 
                 'legal_product_sha256': shared['inputs']['legal_product_sha256']}, 
 'hardcoded_semantic_register_names': False, 
}
if status!='PASS': 
    raise RuntimeError(shared_index_projection)
dump(OUT/'shared_counter_index_projection.json', shared_index_projection)

sync_bad = [x for x in base['stale'] if int(x[1])!=maxv]
sync = {
 'version': 'bio2rtl-stage7-shared-counter-sync-exit-generic-v2', 'status': 'PASS' if not sync_bad else 'FAIL', 
 'semantic_sources': shared['semantic_sources'], 'tx_exit_low_class_phase': [list(x) for x in sorted(tx_exit_low)], 
 'tx_exit_low_intersection_increment_read': [list(x) for x in sorted(tx_exit_low&readpoints[inc_reg])], 
 'tx_exit_low_intersection_decrement_read': [list(x) for x in sorted(tx_exit_low&readpoints[dec_reg])], 
 'reachable_stale_interval_states': len(base['stale']), 'stale_pc_nonmax_counterexamples': sync_bad[:20], 
 'stale_pc_non7_counterexamples': sync_bad[:20], # schema compatibility, value is generic max not assumed 7
 'operation_counts': dict(base['ops']), 'counterexamples': base['bad'][:20], 
 'representation': {'final_decrement_fall': f'PC remains {maxv}', 'following_phase_rise': 'PC synchronously loads 0 before subsequent counter readpoint'}, 
 'proof_scope': 'STRUCTURALLY_DISCOVERED FINAL-DECREMENT EXIT INTERVAL', 'hardcoded_semantic_register_names': False, 
 'inputs': shared['inputs']
}
if sync['status']!='PASS': 
    raise RuntimeError(sync)
dump(OUT/'shared_counter_sync_exit.json', sync)

hold_bad = [x for x in base['holds'] if int(x[1])!=maxv or int(x[3])!=maxv]
hold = {
 'version': 'bio2rtl-stage7-shared-counter-terminal-hold-generic-v2', 'status': 'PASS' if not hold_bad else 'FAIL', 
 'semantic_sources': shared['semantic_sources'], 'terminal_same_control_group': list(same_groups[0]), 'terminal_edge_groups': {f'{a}->{b}': n for (a, b), n in sorted(term_groups.items())}, 
 'mismatch_semantic_edges': len(hold_edges), 'mismatch_lifted_cases': len(base['holds']), 'mismatch_hold_failures': hold_bad[:20], 
 'counterexamples': base['bad'][:20], 'operation_counts': dict(base['ops']), 
 'physical_consequence': f'On the structurally discovered terminal reject path shared PC remains {maxv} through the following high phase; phase-qualified clear may occur after counter/observation state settles.', 
 'proof_scope': 'TERMINAL COUNT EDGE + RECOVERED CONTROL-STATE PRESERVATION + NO POST-HIGH COUNTER READPOINT', 'hardcoded_semantic_register_names': False, 
 'inputs': shared['inputs']
}
if hold['status']!='PASS': 
    raise RuntimeError(hold)
dump(OUT/'shared_counter_terminal_hold.json', hold)
if STOP_AFTER == 'shared': 
    print(json.dumps({'version': 'bio2rtl-stage7-generic-dynamic-part-v1', 'status': 'PASS', 'completed': ['shared_counter'], 'stop_after': STOP_AFTER}, indent = 2, sort_keys = True))
    raise SystemExit(0)

# -----------------------------------------------------------------------------
# 2) Observation snapshot specialization, structurally discovered.
# -----------------------------------------------------------------------------
passbits = [x for x in obslocal['results'] if x.get('classification') == 'PASS_OBSERVATION_LOCAL_BIT_ELIMINATION']
# A design may expose several observation-local packed registers.  Build candidates from
# each register independently and keep only candidates whose selector/load/path proof closes.
passbits_by_reg = defaultdict(list)
for x in passbits: 
    passbits_by_reg[str(x['register'])].append(x)

def prove_snapshot_candidate(obs_reg, obs_passbits): 
    selector_regs = {x.get('formula', {}).get('atom', {}).get('register') for x in obs_passbits if x.get('formula', {}).get('kind') == 'ATOM'}-{None}
    if len(selector_regs)!=1: 
        return None, f'selector set {sorted(selector_regs)}'
    selector_reg = next(iter(selector_regs))
    load_tids = {str(t) for r in phase['update_rules'] if str(r['target']) == obs_reg and r.get('materialize') for t in r.get('source_transition_ids', [])}
    if not load_tids: 
        return None, 'no observation load transition provenance'
    commit_tids = set(map(str, storrel['gpio_mirror_fusion']['transition_ids_hit']))
    selector_tids = {str(t) for r in phase['update_rules'] if str(r['target']) == selector_reg and r.get('materialize') for t in r.get('source_transition_ids', [])}

    # Dedup legal graph over old auxiliary product dimension.
    def matched_tids(e): 
        return {str(x['transition_id']) for x in edge_entries(e)}
    adj2 = defaultdict(set)
    for e in lp['unique_edges']: 
        a = pre(e)
        b = post(e)
        ev = str(e[ei['event']])
        tids = tuple(sorted(matched_tids(e)))
        adj2[a].add((ev, b, tids))
    load_edges = []
    for a, outs in adj2.items(): 
        for ev, b, tids in outs: 
            if set(tids)&load_tids: 
                load_edges.append((a, ev, b))
    read_states = {s for s in adj2 if (int(s[0]), str(s[1])) in readpoints[dec_reg]}

    load_by_selector = Counter()
    reached_by_selector = Counter()
    violations = []
    path_checks = 0
    max_nodes = 0
    selector_values = sorted({int(reps[int(a[0])][selector_reg]) for a, _, _ in load_edges})
    for a, ev, b in load_edges: 
        sv = int(reps[int(a[0])][selector_reg])
        load_by_selector[sv]+=1
        dq = deque([b])
        seen = {b}
        local_reads = 0
        while dq: 
            ss = dq.popleft()
            max_nodes = max(max_nodes, len(seen))
            if ss in read_states: 
                local_reads+=1
            for ev2, n, tids_t in adj2[ss]: 
                path_checks+=1
                tids = set(tids_t)
                if tids&load_tids: 
                    continue
                if bool(ss[3])!=bool(n[3]) or not str(ev2).startswith('PHEVT_'): 
                    continue
                if (int(ss[0]), str(ss[1])) in readpoints[dec_reg] and str(ss[1]) == 'H' and int(ss[5]) == 0 and str(n[1]) == 'L': 
                    continue
                if tids&commit_tids: 
                    violations.append(('GPIO_PAYLOAD_WRITE_AFTER_LOAD', sv, ss, ev2, n, sorted(tids&commit_tids)))
                    continue
                if tids&selector_tids: 
                    violations.append(('SELECTOR_WRITE_AFTER_LOAD', sv, ss, ev2, n, sorted(tids&selector_tids)))
                    continue
                if n not in seen: 
                    seen.add(n)
                    dq.append(n)
        reached_by_selector[sv]+=local_reads
    coverage = bool(selector_values) and all(load_by_selector[v]>0 and reached_by_selector[v]>0 for v in selector_values)
    mirror_sources = []
    for rel in storrel['gpio_mirror_fusion']['relations']: 
        m = re.match(r'^\s*([A-Za-z_]\w*)\s*=\s*(~)?([A-Za-z_]\w*)\[(\d+):(\d+)\]\s*$', rel)
        if m: 
            mirror_sources.append({'history': m.group(1), 'source': m.group(3), 'invert': bool(m.group(2)), 'hi': int(m.group(4)), 'lo': int(m.group(5))})
    load_source_classes = sorted({int(a[0]) for a, _, _ in load_edges})
    cert = {
     'version': 'bio2rtl-stage7-observation-snapshot-generic-v3', 'status': 'PASS' if coverage and not violations else 'FAIL', 
     'observation_register': obs_reg, 'selector_register': selector_reg, 'selector_values': selector_values, 
     'load_edges': len(load_edges), 'load_edges_by_selector': {str(k): v for k, v in sorted(load_by_selector.items())}, 
     'load_transition_ids': sorted(load_tids), 'load_source_classes': load_source_classes, 
     'safe_update_relaxation': {
       'kind': 'observation_local_broadened_capture_domain', 
       'selector_values': selector_values, 
       'source_classes': load_source_classes, 
       'transition_ids': sorted(load_tids), 
       'proof': 'all selector modes reach a readpoint; selector/payload remain stable until read; snapshot is only semantically consumed by external-input selector modes', 
       'status': 'PASS' if coverage and not violations else 'FAIL'
     }, 
     'tx_read_states': len(read_states), 'read_reach_count_by_selector': {str(k): v for k, v in sorted(reached_by_selector.items())}, 
     'path_edge_checks': path_checks, 'max_local_reachable_states': max_nodes, 'violations': violations[:20], 'coverage_ok': coverage, 
     'payload_mirror_sources': mirror_sources, 'snapshot_bits': sorted(set(storage[obs_reg].get('stored_bits', []))-{int(x['semantic_bit']) for x in obs_passbits}), 
     'claims': {'live_payload_sources_stable_until_read': True, 'external_input_source_requires_snapshot': True, 'selector_stable_until_read': True, 
               'snapshot_update': 'Capture external input payload on every discovered observation-load edge; unused selector modes are observationally irrelevant.'}, 
     'proof_scope': 'STRUCTURAL OBSERVATION-LOAD PROVENANCE + EXACT LEGAL GRAPH + DIRECT-GUARD TX READPOINTS + PROVEN GPIO COMMIT TIDS', 
     'hardcoded_semantic_register_names': False, 
     'inputs': {'phase40_ir_sha256': sha(SEM/'phase40.ir.json'), 'behavioral_quotient_sha256': sha(SEM/'behavioral_quotient.json'), 'direct_fsm_sha256': sha(SEM/'directfsm_table.json'), 'legal_product_sha256': sha(SEM/'legal_product.json')}
    }
    return (cert, None) if cert['status'] == 'PASS' else (None, f"coverage={coverage} violations={len(violations)}")

snapshot_proven = []
snapshot_rejected = []
for obs_reg, obs_passbits in sorted(passbits_by_reg.items()): 
    cert, why = prove_snapshot_candidate(obs_reg, obs_passbits)
    if cert is not None: 
        snapshot_proven.append(cert)
    else: 
        snapshot_rejected.append((obs_reg, why))
if not snapshot_proven: 
    _optional_exit('snapshot', f'no proven observation snapshot candidate; rejected={snapshot_rejected}')
if len(snapshot_proven)!=1: 
    raise RuntimeError(f'ambiguous proven observation snapshot candidates {[x["observation_register"] for x in snapshot_proven]}')
snap = snapshot_proven[0]
obs_reg = str(snap['observation_register'])
selector_reg = str(snap['selector_register'])
dump(OUT/'observation_snapshot.json', snap)
if STOP_AFTER == 'snapshot': 
    print(json.dumps({'version': 'bio2rtl-stage7-generic-dynamic-part-v1', 'status': 'PASS', 'completed': ['shared_counter', 'observation_snapshot'], 'stop_after': STOP_AFTER}, indent = 2, sort_keys = True))
    raise SystemExit(0)

# -----------------------------------------------------------------------------
# 3) Open-drain/OE recurrence by structural GPIO-bit + phase + residual-predicate discovery.
# -----------------------------------------------------------------------------
qualified_bits = {int(x['input_bit']) for x in phase['scheduler_detectors'] if x.get('kind') == 'QUALIFIED_INPUT_EDGE'}
oe_candidates = []
for rid, a in arch.items(): 
    if a.get('kind')!='GPIO': 
        continue
    for b in storage.get(rid, {}).get('stored_bits', []): 
        if int(b) in qualified_bits: 
            oe_candidates.append((rid, int(b)))
basis = {str(x['id']): x['expression'] for x in phase['predicate_basis']}
falls = sorted({(int(e[ei['class']]), str(e[ei['phase']]), int(e[ei[dec_reg]])) for e in lp['unique_edges'] if str(e[ei['event']]) == fall_event})

# Search every structurally qualified output-enable bit.  The old implementation required
# exactly one raw candidate; the generic implementation proves candidates independently and
# only exposes a scalar certificate while exactly one proof closes.  Multi-proof architecture
# selection is a later cost-search concern, not a parser/shape assumption.
def prove_oe_candidate(oe_reg, oe_bit): 
    oe_rules = [r for r in phase['update_rules'] if str(r['target']) == oe_reg and str(r['event_class']) == fall_event and r.get('materialize')]
    if not oe_rules: 
        return None, 'no fall-domain OE rules'
    used_basis = sorted({str(a['basis']) for r in oe_rules for a in r.get('enable', [])})
    data_basis_candidates = [b for b in used_basis if obs_reg in refs(basis[b])]
    if len(data_basis_candidates)!=1: 
        return None, f'data predicate set {data_basis_candidates}'
    data_basis = data_basis_candidates[0]

    def outbit(expr, oe): 
        op = expr[0]
        if op == 'REG': 
            if str(expr[1])!=oe_reg: 
                raise ValueError(expr)
            return int(oe)
        if op == 'CONST': 
            return (int(expr[1])>>oe_bit)&1
        if op == 'OP': 
            vals = [outbit(x, oe) for x in expr[2]]
            k = expr[1]
            if k == 'AND': 
                z = 1
                for v in vals: 
                    z&=v
                return z
            if k == 'OR': 
                z = 0
                for v in vals: 
                    z|=v
                return z
        raise ValueError(expr)

    def pred(pid, rep, pdec, data): 
        if pid == data_basis: 
            return bool(data)
        rr = refs(basis[pid])
        regs = dict(rep)
        regs[dec_reg] = int(pdec)
        if not rr.issubset(regs): 
            raise RuntimeError(f'OE predicate {pid} has undiscovered dependencies {sorted(rr-set(regs))}')
        return bool(_eval_expr(basis[pid], regs, 0, 0))
    def enabled(rule, rep, pdec, data): 
        return all(pred(str(a['basis']), rep, pdec, data) == bool(a['polarity']) for a in rule.get('enable', []))

    truth = defaultdict(dict)
    oe_bad = []
    overlaps = 0
    checks = 0
    state_counts = Counter()
    try: 
        for c, ph, pdec in falls: 
            rep = reps[c]
            st = class_control[c]
            state_counts[st]+=1
            for oe, data in itertools.product((0, 1), repeat = 2): 
                matches = [r for r in oe_rules if enabled(r, rep, pdec, data)]
                outs = {outbit(r['outcome'], oe) for r in matches}
                if len(matches)>1: 
                    overlaps+=1
                actual = oe if not matches else (next(iter(outs)) if len(outs) == 1 else None)
                key = (int(pdec == 0), int(data), int(oe))
                checks+=1
                if key in truth[st] and truth[st][key]!=actual: 
                    oe_bad.append(('NONFUNCTIONAL', st, key, truth[st][key], actual, c, pdec))
                else: 
                    truth[st][key] = actual
                if len(outs)>1: 
                    oe_bad.append(('OVERLAP_OUTCOME', st, key, sorted(outs)))
    except (ValueError, RuntimeError) as exc: 
        return None, str(exc)

    def fn_hold(z, d, o): 
        return o
    def fn_zero(z, d, o): 
        return 0
    def fn_one(z, d, o): 
        return 1
    def fn_data(z, d, o): 
        return d
    def fn_notdata(z, d, o): 
        return 1-d
    def fn_nzdata(z, d, o): 
        return 0 if z else d
    fn_candidates = [('hold', fn_hold), ('0', fn_zero), ('1', fn_one), ('data_predicate', fn_data), ('not_data_predicate', fn_notdata), ('count_zero ? 0 : data_predicate', fn_nzdata)]
    recurrence = {}
    for st, t in sorted(truth.items()): 
        fits = [name for name, fn in fn_candidates if all(fn(*k) == v for k, v in t.items())]
        if not fits: 
            return None, f'no simple OE recurrence fit state={st}'
        recurrence[f'state{st}'] = fits[0]
    status = 'PASS' if not oe_bad else 'FAIL'
    cert = {
     'version': 'bio2rtl-stage7-open-drain-oe-recurrence-generic-v2', 'status': status, 'oe_semantic_source': {'register': oe_reg, 'bit': oe_bit}, 
     'fall_event': fall_event, 'fall_source_points': len(falls), 'checks': checks, 'matching_rule_overlaps': overlaps, 
     'control_state_source_point_counts': {str(k): v for k, v in sorted(state_counts.items())}, 'data_predicate_basis': data_basis, 
     'decrement_count_source': dec_reg, 'natural_recurrence': recurrence, 
     'truth_table_by_control_state': {str(st): [{'count_zero': k[0], 'data_predicate': k[1], 'oe': k[2], 'next_oe': v} for k, v in sorted(t.items())] for st, t in sorted(truth.items())}, 
     'counterexamples': oe_bad[:20], 'proof_scope': 'STRUCTURAL OE-BIT DISCOVERY + RECOVERED CONTROL FACTORIZATION + EXACT FALL SOURCE POINTS + ABSTRACT DATA-SELECT PREDICATE', 
     'hardcoded_semantic_register_names': False, 
     'inputs': {'phase40_ir_sha256': sha(SEM/'phase40.ir.json'), 'legal_product_sha256': sha(SEM/'legal_product.json'), 'behavioral_quotient_sha256': sha(SEM/'behavioral_quotient.json'), 'control_factorization_sha256': sha(OUT/'control_factorization.json')}
    }
    return (cert, None) if status == 'PASS' else (None, f'counterexamples={len(oe_bad)}')

oe_proven = []
oe_rejected = []
for oe_reg, oe_bit in sorted(oe_candidates): 
    cert, why = prove_oe_candidate(oe_reg, oe_bit)
    if cert is not None: 
        oe_proven.append(cert)
    else: 
        oe_rejected.append(((oe_reg, oe_bit), why))
if not oe_proven: 
    _optional_exit('oe', f'no proven OE recurrence candidate; rejected={oe_rejected}')
if len(oe_proven)!=1: 
    raise RuntimeError(f'ambiguous proven OE recurrence candidates {[x["oe_semantic_source"] for x in oe_proven]}')
oe_cert = oe_proven[0]
oe_reg = str(oe_cert['oe_semantic_source']['register'])
oe_bit = int(oe_cert['oe_semantic_source']['bit'])
recurrence = dict(oe_cert['natural_recurrence'])
checks = int(oe_cert['checks'])
dump(OUT/'oe_recurrence.json', oe_cert)

report = {
 'version': 'bio2rtl-stage7-generic-dynamic-certificates-v1', 'status': 'PASS', 'hardcoded_semantic_register_names': False, 
 'shared_counter': {'sources': [inc_reg, dec_reg], 'bits': dec_width, 'lifted_states': len(base['seen']), 'transition_checks': base['transitions'], 'readpoint_checks': base['readchecks'], 'terminal_hold_edges': len(hold_edges)}, 
 'snapshot': {'observation_register': obs_reg, 'selector_register': selector_reg, 'load_edges': snap['load_edges'], 'path_edge_checks': snap['path_edge_checks'], 'violations': len(snap['violations'])}, 
 'oe': {'source': {'register': oe_reg, 'bit': oe_bit}, 'fall_source_points': oe_cert['fall_source_points'], 'checks': checks, 'states': recurrence}
}
dump(ROOT/'build/stage7_generic_dynamic_certificate_report.json', report)
print(json.dumps(report, indent = 2, sort_keys = True))
