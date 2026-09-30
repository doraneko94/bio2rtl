from __future__ import annotations
import hashlib, itertools, json, sys, os
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).resolve().parent
SEM = ROOT/'build/semantic'
OUT = Path(os.environ.get('BIO2RTL_CERT_OUT', ROOT/'build/generated_certificates'))
OUT.mkdir(parents = True, exist_ok = True)
sys.path.insert(0, str(ROOT/'semantic_frontend'))
from bio2rtl.dedicated_event_reachability import _eval_expr
from tools.analyze_protocol_counter_ownership import refs, build_derived_rows, augment_derived, event_environment

def load(p): 
    return json.loads(Path(p).read_text())
def dump(p, d): 
    Path(p).write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def insert_bits(base, bits, value): 
    z = int(base)
    for i, b in enumerate(bits): 
        z = (z & ~(1<<int(b))) | (((int(value)>>i)&1)<<int(b))
    return z

def extract_bits(value, bits): 
    z = 0
    for i, b in enumerate(bits): 
        z |= ((int(value)>>int(b))&1)<<i
    return z

phase = load(SEM/'phase40.ir.json')
q = load(SEM/'behavioral_quotient.json')
tab = load(SEM/'directfsm_table.json')
lp = load(SEM/'legal_product.json')
tracked = list(map(str, q['tracked_registers']))
members = {int(c['code']): [dict(zip(tracked, map(int, s))) for s in c['states']] for c in q['classes']}
rows = {int(k): v for k, v in tab['rows'].items()}
derived = build_derived_rows(phase)
basis = {str(x['id']): x['expression'] for x in phase['predicate_basis']}
arch = {str(r['id']): r for r in phase['architectural_registers']}
storage = {x['register']: x for x in phase['storage_optimization']['register_storage']}

def reset_value(rid): 
    e = arch[rid]['reset']
    if e[0]!='CONST': 
        raise RuntimeError(f'non-constant reset for {rid}')
    return int(e[1])

# Discover the protocol bit-completion event from the scheduler contract.
completion = [d for d in phase['scheduler_detectors'] if d.get('kind') == 'POLLING_PHASE_COMPLETION' and int(d.get('phase_before', -1)) == 0 and int(d.get('phase_after', -1)) == 1]
if len(completion)!=1: 
    raise RuntimeError(f'expected unique low->high polling completion event: {completion}')
commit_event = str(completion[0]['event_id'])

# Discover GPIO payload registers and their shared payload bit positions.  Qualified edge-owned
# direction bits are excluded automatically from the payload mirror domain.
gpios = [r for r in phase['architectural_registers'] if r.get('kind') == 'GPIO']
byprov = {str(r.get('provenance', '')).lower(): str(r['id']) for r in gpios}
data = [rid for prov, rid in byprov.items() if 'data' in prov]
dir_ = [rid for prov, rid in byprov.items() if ('direction' in prov or 'dir' in prov)]
if len(data)!=1 or len(dir_)!=1: 
    raise RuntimeError(f'ambiguous GPIO data/direction roles {byprov}')
data_reg, dir_reg = data[0], dir_[0]
qualified_bits = {int(d['input_bit']) for d in phase['scheduler_detectors'] if d.get('kind') == 'QUALIFIED_INPUT_EDGE'}
data_bits = sorted(map(int, storage[data_reg].get('stored_bits', [])))
dir_bits = sorted(set(map(int, storage[dir_reg].get('stored_bits', [])))-qualified_bits)
payload_bits = sorted(set(data_bits)&set(dir_bits))
if len(payload_bits)!=2 or data_bits!=payload_bits or dir_bits!=payload_bits: 
    raise RuntimeError(f'expected matched 2-bit GPIO payload data={data_bits} dir={dir_bits} shared={payload_bits}')
payload_mask = (1<<len(payload_bits))-1

# Discover untracked physical history registers that capture the same two-bit serial word
# {old SHIFT[0], sampled-data}.  Tracked capture registers (e.g. a selector) are not mirrors.
def capture_signature(e): 
    if not (isinstance(e, list) and len(e)>=3 and e[0] == 'OP' and e[1] == 'OR'): 
        return None
    gpio_bits = []
    sh = []
    for a in e[2]: 
        if isinstance(a, list) and len(a) == 3 and a[0] == 'BIT_VALUE' and a[1] == ['GPIO_INPUT']: 
            gpio_bits.append(int(a[2]))
        elif isinstance(a, list) and len(a)>=3 and a[0] == 'OP' and a[1] == 'SHL': 
            xs = a[2]
            if len(xs) == 2 and xs[1] == ['CONST', 1] and isinstance(xs[0], list) and len(xs[0]) == 3 and xs[0][0] == 'BIT_VALUE' and xs[0][1][0] == 'REG' and int(xs[0][2]) == 0: 
                sh.append(str(xs[0][1][1]))
    if len(gpio_bits) == 1 and len(sh) == 1: 
        return (sh[0], gpio_bits[0])
    return None
captures = []
for r in phase['update_rules']: 
    tgt = str(r['target'])
    ar = arch.get(tgt, {})
    if str(r.get('event_class'))!=commit_event or tgt in tracked or ar.get('kind')!='PHYSICAL' or int(ar.get('width', 0))!=len(payload_bits): 
        continue
    sig = capture_signature(r.get('outcome'))
    if sig: 
        captures.append((tgt, sig, r))
if len(captures)!=2 or len({x[1] for x in captures})!=1: 
    raise RuntimeError(f'expected two untracked history captures with shared signature, got {[(x[0],x[1]) for x in captures]}')
hist_regs = sorted(x[0] for x in captures)
(shift_reg, sampled_bit) = captures[0][1]
shift_width = int(arch[shift_reg]['width'])

# Find commit transition IDs that write the GPIO payload and the legal quotient classes that
# actually expose those IDs on the low->high completion event.
gpio_commit_tids = set()
for r in phase['update_rules']: 
    if str(r['target']) in (data_reg, dir_reg) and str(r.get('event_class')) == commit_event: 
        gpio_commit_tids.update(map(str, r.get('source_transition_ids', [])))
classes = []
legal_sources = {(int(e[0]), str(e[7])) for e in lp['unique_edges']}
for k, row in rows.items(): 
    if (int(k), commit_event) not in legal_sources: 
        continue
    if any(str(ent['transition_id']) in gpio_commit_tids for ent in row.get(commit_event, [])): 
        classes.append(int(k))
classes = sorted(classes)
if len(classes)!=2: 
    raise RuntimeError(f'expected two legal payload-commit classes, got {classes}')
for c in classes: 
    if len(members[c])!=1: 
        raise RuntimeError(f'commit class {c} not canonical singleton')

# Identify selector context structurally as the unique tracked stored register that differs between
# the two commit-class representatives. This is audit metadata only, not a proof assumption.
a, b = members[classes[0]][0], members[classes[1]][0]
diffs = [r for r in tracked if int(a[r])!=int(b[r]) and int(storage.get(r, {}).get('storage_bits', 0))>0]
selector_reg = diffs[0] if len(diffs) == 1 else None

# Discover COUNT state used by direct guards in commit classes and terminal pre-value from legal edges.
count_regs = sorted(r for r, d in storage.items() if int(d.get('recurrence_classes', {}).get('COUNT', 0))>0)
guard_count = Counter()
for c in classes: 
    for ent in rows[c].get(commit_event, []): 
        if str(ent['transition_id']) not in gpio_commit_tids: 
            continue
        dep = set()
        for g in ent.get('guard', []): 
            dep |= refs(g['expression'])
        for r in count_regs: 
            if r in dep: 
                guard_count[r]+=1
terminal_regs = [r for r, n in guard_count.items() if n]
if len(terminal_regs)!=1: 
    raise RuntimeError(f'commit guard COUNT ambiguity: {guard_count}')
terminal_reg = terminal_regs[0]
ei = {n: i for i, n in enumerate(lp['edge_tuple'])}
if terminal_reg not in ei: 
    raise RuntimeError(f'legal edge tuple lacks terminal count register {terminal_reg}')
terminal_value = max(int(e[ei[terminal_reg]]) for e in lp['unique_edges'] if int(e[ei['class']]) in classes and str(e[ei['event']]) == commit_event)

# Phase update rules by target/provenance.
by_tid = defaultdict(lambda: defaultdict(list))
for r in phase['update_rules']: 
    for tid in r.get('source_transition_ids', []): 
        by_tid[str(tid)][str(r['target'])].append(r)

def rule_enabled(r, regs, sched, gpio): 
    return all(bool(_eval_expr(basis[str(g['basis'])], regs, sched, gpio)) == bool(g['polarity']) for g in r.get('enable', []))

def next_target(tid, tgt, old, regs, sched, gpio): 
    cand = [r for r in by_tid.get(str(tid), {}).get(tgt, []) if str(r.get('event_class')) == commit_event and rule_enabled(r, regs, sched, gpio)]
    if not cand: 
        return int(old)
    vals = []
    for r in cand: 
        rr = dict(regs)
        rr[tgt] = int(old)
        vals.append(int(_eval_expr(r['outcome'], rr, sched, gpio)))
    if len(set(vals))!=1: 
        raise RuntimeError(f'ambiguous update target={tgt} tid={tid} vals={vals}')
    return vals[0]

# Initialize every architectural register from its reset value; quotient/counter/history values then override.
base_regs = {rid: reset_value(rid) for rid in arch}

# Search all bijective history<->GPIO-payload relations and inversion polarities. The unique inductive
# invariant is selected by proof, not by semantic register names.
relation_candidates = []
for perm in itertools.permutations(hist_regs): 
  for inv_data, inv_dir in itertools.product((False, True), repeat = 2): 
    mapping = [(perm[0], data_reg, payload_bits, inv_data), (perm[1], dir_reg, payload_bits, inv_dir)]
    # Base case.
    if any((reset_value(h)&payload_mask) != (extract_bits(reset_value(g), bits) ^ (payload_mask if inv else 0)) for h, g, bits, inv in mapping): 
        continue
    failures = []
    contexts = 0
    tids = Counter()
    for c in classes: 
      mem = members[c][0]
      for shift in range(1<<shift_width): 
       for hv0 in range(1<<len(payload_bits)): 
        for hv1 in range(1<<len(payload_bits)): 
         for sampled in (0, 1): 
          contexts+=1
          histvals = {hist_regs[0]: hv0, hist_regs[1]: hv1}
          scl, sched, nsda = event_environment(commit_event, sampled)
          gpio = (int(scl)<<int(completion[0]['input_bit'])) | (int(nsda)<<int(sampled_bit))
          regs = dict(base_regs)
          regs.update(mem)
          regs[terminal_reg] = terminal_value
          regs[shift_reg] = shift
          for h, v in histvals.items(): 
              regs[h] = v
          for h, g, bits, inv in mapping: 
              gv = histvals[h] ^ (payload_mask if inv else 0)
              regs[g] = insert_bits(reset_value(g), bits, gv)
          regs = augment_derived(regs, derived, sched, gpio)
          hits = []
          for ent in rows[c].get(commit_event, []): 
            ok = True
            for gd in ent.get('guard', []): 
                rr = refs(gd['expression'])
                if not rr.issubset(regs): 
                    ok = False
                    break
                if bool(_eval_expr(gd['expression'], regs, sched, gpio))!=bool(gd['polarity']): 
                    ok = False
                    break
            if ok: 
                hits.append(str(ent['transition_id']))
          # Only payload-commit direct rows are relevant. At a legal terminal context there is exactly one.
          hits = [t for t in hits if t in gpio_commit_tids]
          if len(hits)!=1: 
              failures.append(('ROW_CARDINALITY', c, shift, hv0, hv1, sampled, hits))
              continue
          tid = hits[0]
          tids[tid]+=1
          nxt = {}
          for h, g, bits, inv in mapping: 
              nxt[h] = next_target(tid, h, regs[h], regs, sched, gpio)&payload_mask
              ng = next_target(tid, g, regs[g], regs, sched, gpio)
              rhs = extract_bits(ng, bits) ^ (payload_mask if inv else 0)
              if nxt[h]!=rhs: 
                  failures.append(('INVARIANT', c, tid, h, g, inv, shift, hv0, hv1, sampled, nxt[h], rhs))
    if not failures: 
        relation_candidates.append({'mapping': mapping, 'contexts': contexts, 'transition_ids': sorted(tids)})
if len(relation_candidates)!=1: 
    raise RuntimeError(f'mirror invariant search expected one solution, got {len(relation_candidates)}: {[x["mapping"] for x in relation_candidates]}')
sol = relation_candidates[0]
mapping = sol['mapping']

# Structural preservation outside commit: history registers never write; payload bits in GPIO updates hold.
structural_checks = 0
structural_fail = []
for h in hist_regs: 
    for r in phase['update_rules']: 
        if str(r['target']) == h: 
            structural_checks+=1
            if str(r['event_class'])!=commit_event: 
                structural_fail.append(('HISTORY_NONCOMMIT_WRITE', h, r['rule_id']))
for _, g, bits, _ in mapping: 
    for r in phase['update_rules']: 
        if str(r['target'])!=g or str(r['event_class']) == commit_event: 
            continue
        for pv in range(1<<len(bits)): 
          old = insert_bits(reset_value(g), bits, pv)
          try: 
              nv = int(_eval_expr(r['outcome'], {g: old}, 0, 0))
          except Exception as ex: 
              structural_fail.append(('NONCOMMIT_EVAL', g, r['rule_id'], str(ex)))
              continue
          structural_checks+=1
          if extract_bits(nv, bits)!=pv: 
              structural_fail.append(('NONCOMMIT_PAYLOAD_CHANGE', g, r['rule_id'], pv, extract_bits(nv, bits)))
if structural_fail: 
    raise RuntimeError(f'structural mirror preservation failed {structural_fail[:5]}')

# Fresh phase40 already carries the generic single-source derived-state elimination proof.
der = phase['storage_optimization']['derived_state_elimination']
if not der.get('source_relation_preserved') or der.get('expression_kind')!='NONZERO': 
    raise RuntimeError(f'unexpected derived-state proof {der}')
p09_target = str(der['target'])
p09_source = str(der['source'])
p09_checks = int(der.get('transition_checks', 0))

def relation_text(h, g, bits, inv): 
    sl = f'{max(bits)}:{min(bits)}' if len(bits)>1 else str(bits[0])
    return f'{h} = {"~" if inv else ""}{g}[{sl}]'
relations = [relation_text(*x) for x in mapping]
checks = int(sol['contexts'])+structural_checks+1
cert = {
 'version': 'storage-relations-proof-v4-generated-generic-search', 'status': 'PASS', 'relation_sha256': sha(SEM/'corrected_relation.json'), 
 'inputs': {'phase40_ir_sha256': sha(SEM/'phase40.ir.json'), 'behavioral_quotient_sha256': sha(SEM/'behavioral_quotient.json'), 'direct_table_sha256': sha(SEM/'directfsm_table.json'), 'legal_product_sha256': sha(SEM/'legal_product.json')}, 
 'gpio_mirror_fusion': {
   'status': 'PASS', 'checks': checks, 'counterexamples': [], 'relations': relations, 
   'base_case': {'status': 'PASS'}, 'induction_contexts': sol['contexts'], 'structural_preservation_checks': structural_checks, 
   'transition_ids_hit': sol['transition_ids'], 'commit_event': commit_event, 'commit_classes': classes, 
   'terminal_count_source': terminal_reg, 'terminal_value': terminal_value, 'selector_context_candidate': selector_reg, 
   'history_capture_registers': hist_regs, 'shift_source': shift_reg, 'sampled_input_bit': sampled_bit, 'payload_bits': payload_bits, 
   'relation_candidates_checked': 8, 'unique_solution': True, 
   'proof_model': 'STRUCTURAL_DISCOVERY + BASE_CASE + EXHAUSTIVE_INDUCTIVE_RELATION_SEARCH + NONCOMMIT_PAYLOAD_PRESERVATION'}, 
 'p09_derived': {'status': 'PASS', 'checks': p09_checks, 'counterexamples': [], 'relation': f'{p09_target} = 1 iff {p09_source} != 0', 'source': 'fresh_phase40.storage_optimization.derived_state_elimination'}
}
dump(OUT/'storage_relations.json', cert)
report = {'status': 'PASS', 'relations': relations, 'contexts': sol['contexts'], 'checks': checks, 'commit_event': commit_event, 'commit_classes': classes, 'terminal_count_source': terminal_reg, 'terminal_value': terminal_value, 'selector_context_candidate': selector_reg, 'hardcoded_semantic_register_names': False}
dump(ROOT/'build/stage7_storage_relation_generic_report.json', report)
print(json.dumps(report, indent = 2, sort_keys = True))
