from __future__ import annotations
from pathlib import Path
import json
from .boolean_mapper import C, V, N, A, O, X, XN, M, canon

U32 = 32

def _load(p): 
    return json.loads(Path(p).read_text())

def _stored_bits(row: dict)->list[int]: 
    if 'stored_bits' in row: 
        return list(map(int, row['stored_bits']))
    n = int(row.get('storage_bits', 0)); return list(range(n))

def _or(xs): 
    return canon(O(*xs)) if xs else C(0)
def _and(xs): 
    return canon(A(*xs)) if xs else C(1)
def _xor(a, b): 
    return canon(X(a, b))
def _xnor(a, b): 
    return canon(XN(a, b))

def _bv_const(v: int, n = U32): 
    return tuple(C((int(v)>>i)&1) for i in range(n))
def _bv_zext(bits, n = U32): 
    return tuple(bits[i] if i<len(bits) else C(0) for i in range(n))
def _bv_and(a, b): 
    return tuple(canon(A(x, y)) for x, y in zip(a, b))
def _bv_or(a, b): 
    return tuple(canon(O(x, y)) for x, y in zip(a, b))
def _bv_xor(a, b): 
    return tuple(canon(X(x, y)) for x, y in zip(a, b))
def _bv_not(a): 
    return tuple(canon(N(x)) for x in a)
def _bv_eq(a, b): 
    return _and([_xnor(x, y) for x, y in zip(a, b)])
def _bv_ult(a, b): 
    eq = C(1); lt = C(0)
    for i in range(len(a)-1, -1, -1): 
        lt = canon(O(lt, A(eq, N(a[i]), b[i])))
        eq = canon(A(eq, XN(a[i], b[i])))
    return lt

def _bv_add(a, b, cin = C(0)): 
    out = []; c = cin
    for ai, bi in zip(a, b): 
        s = canon(X(X(ai, bi), c)); out.append(s)
        c = canon(O(A(ai, bi), A(ai, c), A(bi, c)))
    return tuple(out)
def _bv_sub(a, b): 
    return _bv_add(a, _bv_not(b), C(1))

def _bv_shift(a, sh, left: bool): 
    cur = tuple(a)
    for k in range(5): 
        d = 1<<k; nxt = []
        for i in range(len(cur)): 
            j = i-d if left else i+d
            shifted = cur[j] if 0<=j<len(cur) else C(0)
            nxt.append(canon(M(sh[k], cur[i], shifted)))
        cur = tuple(nxt)
    return cur

def _vars(e, out = None): 
    out = out if out is not None else set()
    if not isinstance(e, (list, tuple)) or not e: 
        return out
    if e[0] == 'VAR': 
        out.add(str(e[1]))
        return out
    for x in e[1:]: 
        if isinstance(x, (list, tuple)): 
            if x and isinstance(x[0], (list, tuple)): 
                for y in x: 
                    _vars(y, out)
            else: 
                _vars(x, out)
    return out

def _project_event_context(root: Path, phase: dict, event: str): 
    """Resolve GPIO_INPUT and scheduler-owned scalar values for one canonical event.

    Only project TOML GPIO bindings and semantic scheduler descriptors are used.  No
    protocol net names, free-running clock, or synthetic polling state are invented.
    The returned values describe the semantic values *observed by phase40 rules at
    the event boundary*.
    """
    from .semantic_projection import gpio_core_input_map
    bitnet = gpio_core_input_map(Path(root))
    gpio = [C(0) for _ in range(U32)]
    for b, n in bitnet.items(): 
        if 0 <= int(b) < U32: 
            gpio[int(b)] = V(str(n))
    detectors = {str(d['event_id']): d for d in phase.get('scheduler_detectors', [])}
    d = detectors.get(str(event))
    if d is None: 
        raise ValueError(f'canonical event {event} has no scheduler detector descriptor')
    sched = {}
    # First resolve phase automata from an explicit polling edge or from a hard-event
    # qualifier on the same physical input.  Ambiguous phase ownership is rejected.
    for src in phase.get('scheduler_owned_sources', []): 
        sid = str(src['id']); role = str(src.get('role', '')); bit = int(src.get('input_bit', -1))
        if role == 'POLLING_PHASE_AUTOMATON': 
            if str(d.get('kind')) == 'POLLING_PHASE_COMPLETION' and int(d.get('input_bit', -2)) == bit: 
                if 'phase_before' not in d: 
                    raise ValueError(f'polling event {event} lacks phase_before')
                sched[sid] = C(int(d['phase_before']))
            else: 
                levels = {int(q['level']) for q in d.get('qualifiers', []) if q.get('source') == 'GPIO_INPUT' and int(q.get('bit', -2)) == bit}
                if len(levels) == 1: 
                    sched[sid] = C(next(iter(levels)))
                else: 
                    raise ValueError(f'cannot resolve scheduler phase source {sid} at event {event}')
        elif role == 'EDGE_HISTORY_AUX' and str(src.get('maintenance')) == 'CAPTURE_GPIO_INPUT': 
            if bit not in bitnet: 
                raise ValueError(f'scheduler history source {sid} GPIO bit {bit} is unbound')
            # At an ordinary phase completion the stored history has already converged
            # to the stable input level.  At the qualified edge event itself, phase40
            # observes the pre-edge history value, i.e. the opposite edge level.
            if str(d.get('kind')) == 'QUALIFIED_INPUT_EDGE' and int(d.get('input_bit', -2)) == bit: 
                edge = str(d.get('edge', '')).upper()
                if edge == 'FALL': 
                    sched[sid] = C(1)
                elif edge == 'RISE': 
                    sched[sid] = C(0)
                else: 
                    raise ValueError(f'unsupported qualified edge {edge} for {event}')
            else: 
                sched[sid] = V(str(bitnet[bit]))
        else: 
            raise ValueError(f'unsupported scheduler-owned source in physical lowering: {src}')
    return tuple(gpio), sched

class DirectLowerer: 
    def __init__(self, phase: dict, event: str, target: str, target_q: dict[int, str]|None = None, 
                 reg_bindings: dict[tuple[str, int], str]|None = None, *, root: Path|None = None, 
                 gpio_values = None, sched_values: dict[str, tuple]|None = None): 
        self.phase = phase; self.event = event; self.target = target; self.target_q = target_q or {}; self.reg_bindings = reg_bindings or {}
        self.storage = {str(x['register']): x for x in phase['storage_optimization']['register_storage']}
        self.pbasis = {str(x['id']): x['expression'] for x in phase['predicate_basis']}
        self.alias = {}
        for row in self.storage.values(): 
            for bid, a in (row.get('phase_local_elision') or {}).items(): 
                old = self.alias.get(str(bid))
                if old is not None and old!=a: 
                    raise ValueError(f'conflicting phase-local alias for {bid}')
                self.alias[str(bid)] = a
        self._regmemo = {}; self._basismemo = {}; self._exprmemo = {}
        if gpio_values is None or sched_values is None: 
            if root is None: 
                raise ValueError('DirectLowerer requires project/scheduler context; pass root or explicit bindings')
            gpio_values, sched_values = _project_event_context(Path(root), phase, event)
        self.gpio = tuple(gpio_values); self.sched = {str(k): v for k, v in sched_values.items()}
    def regvec(self, r: str): 
        r = str(r)
        if r in self._regmemo: 
            return self._regmemo[r]
        row = self.storage[r]; kind = str(row.get('storage_kind'))
        if kind == 'DERIVED_EXPR': 
            v = self.expr(row['derived_expression']); self._regmemo[r] = v; return v
        if kind == 'PHASE_LOCAL_ELIDED': 
            raise ValueError(f'phase-local-elided REG {r} must be consumed via predicate alias')
        bits = [C(0) for _ in range(U32)]
        for b in map(int, row.get('constant_one_bits', [])): 
            bits[b] = C(1)
        for b in _stored_bits(row): 
            nm = self.reg_bindings.get((r, b), self.target_q[b] if r == self.target and b in self.target_q else None)
            if nm is None: 
                raise ValueError(f'unbound retained semantic storage bit {r}[{b}] at {self.event}')
            bits[b] = canon(tuple(nm) if isinstance(nm, list) else nm) if isinstance(nm, tuple) else (tuple(nm) if isinstance(nm, list) else V(str(nm)))
        v = tuple(bits); self._regmemo[r] = v; return v
    def expr(self, e): 
        key = repr(e)
        if key in self._exprmemo: 
            return self._exprmemo[key]
        tag = e[0]
        if tag == 'CONST': 
            z = _bv_const(int(e[1]))
        elif tag == 'REG': 
            z = self.regvec(str(e[1]))
        elif tag == 'SCHED_REG': 
            sid = str(e[1])
            if sid not in self.sched: 
                raise ValueError(f'unbound scheduler source {sid} at {self.event}')
            z = _bv_zext((self.sched[sid],))
        elif tag == 'GPIO_INPUT': 
            z = self.gpio
        elif tag == 'BIT_VALUE': 
            z = _bv_zext((self.expr(e[1])[int(e[2])],))
        elif tag == 'EQ_CONST': 
            z = _bv_zext((_bv_eq(self.expr(e[1]), _bv_const(int(e[2]))),))
        elif tag in ('EQ', 'NE', 'ULT', 'UGE', 'SLT', 'NOT_SLT'): 
            a = self.expr(e[1]); b = self.expr(e[2])
            if tag == 'EQ': 
                q = _bv_eq(a, b)
            elif tag == 'NE': 
                q = canon(N(_bv_eq(a, b)))
            elif tag == 'ULT': 
                q = _bv_ult(a, b)
            elif tag == 'UGE': 
                q = canon(N(_bv_ult(a, b)))
            else: 
                same = canon(XN(a[31], b[31])); sl = canon(O(A(a[31], N(b[31])), A(same, _bv_ult(a[:31], b[:31]))))
                q = sl if tag == 'SLT' else canon(N(sl))
            z = _bv_zext((q,))
        elif tag == 'OP': 
            op = str(e[1]); args = [self.expr(x) for x in e[2]]
            if op == 'AND': 
                z = args[0]
            elif op == 'OR': 
                z = args[0]
            elif op == 'XOR': 
                z = args[0]
            elif op == 'ADD': 
                z = args[0]
            elif op == 'SUB': 
                z = args[0]
            elif op in ('SHL', 'SHR'): 
                z = _bv_shift(args[0], args[1], op == 'SHL')
            else: 
                raise ValueError(op)
            if op == 'AND': 
                for x in args[1:]: 
                    z = _bv_and(z, x)
            elif op == 'OR': 
                for x in args[1:]: 
                    z = _bv_or(z, x)
            elif op == 'XOR': 
                for x in args[1:]: 
                    z = _bv_xor(z, x)
            elif op == 'ADD': 
                for x in args[1:]: 
                    z = _bv_add(z, x)
            elif op == 'SUB': 
                for x in args[1:]: 
                    z = _bv_sub(z, x)
        else: 
            raise ValueError(e)
        self._exprmemo[key] = z; return z
    def basis(self, bid: str): 
        bid = str(bid)
        if bid in self._basismemo: 
            return self._basismemo[bid]
        if bid in self.alias: 
            a = self.alias[bid]
            if a.get('kind') == 'CONST': 
                q = C(int(a['constant']))
            else: 
                q = self.basis(str(a['predicate']))
                if bool(a.get('invert')): 
                    q = canon(N(q))
        else: 
            q = self.expr(self.pbasis[bid])[0]
        self._basismemo[bid] = q; return q


def derive_direct_single_edge_contract(phase: dict, entry: dict, reset_input = 'reset'): 
    reg = str(entry['semantic_source']); dom = entry['natural_update_domain']; edges = list(dom.get('phase_edges', []))
    if len(edges)!=1: 
        return {'status': 'UNSUPPORTED_MULTI_EDGE', 'semantic_source': reg}
    edge = edges[0]; srow = next(x for x in phase['storage_optimization']['register_storage'] if x['register'] == reg)
    sbits = list(entry.get('semantic_bits') or _stored_bits(srow)); qmap = {b: f'nat_{reg}_b{b}' for b in sbits}
    raise ValueError('standalone direct single-edge lowering requires project context; use the production no-cert builder')
    rules = [r for r in phase['update_rules'] if r.get('materialize') and str(r['target']) == reg and str(r['event_class']) == edge]
    nex = {b: V(qmap[b]) for b in sbits}
    # Deterministic priority outside reachable space; phase40 verifier guarantees no
    # conflicting different outcomes on reachable semantic executions.
    for r in reversed(rules): 
        en = _and([L.basis(x['basis']) if bool(x['polarity']) else canon(N(L.basis(x['basis']))) for x in r.get('enable', [])])
        ov = L.expr(r['outcome'])
        for b in sbits: 
            nex[b] = canon(M(en, nex[b], ov[b]))
    # Discover asynchronous zero-reset events represented in the semantic relation.
    async_ev = []
    for ev in dom.get('async_event_candidates', []): 
        er = [r for r in phase['update_rules'] if r.get('materialize') and str(r['target']) == reg and str(r['event_class']) == str(ev)]
        if not er: 
            continue
        ok = True
        for r in er: 
            if r.get('enable'): 
                ok = False
                break
            o = r['outcome']
            if not (isinstance(o, list) and o[:1] == ['CONST'] and all(((int(o[1])>>b)&1) == 0 for b in sbits)): 
                ok = False
                break
        if ok: 
            async_ev.append(str(ev))
        else: 
            return {'status': 'UNSUPPORTED_ASYNC_UPDATE', 'semantic_source': reg, 'event': ev, 'rules': er}
    if set(async_ev) == {'HEVT001', 'HEVT002'}: 
        rst = 'proto_reset'
    elif async_ev == ['HEVT001'] or set(async_ev) == {'HEVT001'}: 
        rst = 'start_reset'
    elif not async_ev: 
        rst = reset_input
    else: 
        rst = 'async_reset_'+reg
    clock = 'scl' if edge == 'PHEVT_RISE' else 'scl_n'
    dffs = []; vars_used = set()
    for b in sbits: 
        q = qmap[b]; qb = q+'_n'; ex = nex[b]; vars_used|=_vars(ex)
        dffs.append({'q': q, 'qb': qb, 'd_expr': ex, 'clock': clock, 'reset': rst, 'cell': 'DFFR', 'semantic_bit': b})
    extern = sorted(v for v in vars_used if v not in set(qmap.values()))
    return {'version': 'bio2rtl-phase40-direct-single-edge-contract-v1', 'status': 'PASS', 'component_class': f'natural_{reg}', 
            'semantic_source': reg, 'semantic_bits': sbits, 'clock_domain': edge, 'clock': clock, 'reset': rst, 
            'async_zero_reset_events': async_ev, 'interface_inputs': sorted(set(extern+[clock, rst])), 
            'interface_outputs': [x for b in sbits for x in (qmap[b], qmap[b]+'_n')], 
            'combinational_outputs': {}, 'dffs': dffs, 'rule_count': len(rules), 
            'derivation': 'direct bit-blast of phase40 predicate/outcome AST; HOLD default; deterministic priority only outside reachable conflicts'}

def derive_direct_phase_split_contracts(phase: dict, dhir: dict, entry: dict): 
    """Directly bit-blast one multi-phase natural register into rise/fall bank contracts.

    Before a rise the LOW/fall-bank is the semantic current view; before a fall the
    HIGH/rise-bank is current.  Each destination bank always receives semantic next,
    so HOLD means copy the opposite active bank, never keep a stale destination bank.
    """
    reg = str(entry['semantic_source']); srow = next(x for x in phase['storage_optimization']['register_storage'] if x['register'] == reg)
    sbits = list(entry.get('semantic_bits') or _stored_bits(srow))
    multi = {str(e['semantic_source']): e for e in dhir.get('physical_state', []) if e.get('requires_phase_split_realization')}
    storage = {str(x['register']): x for x in phase['storage_optimization']['register_storage']}
    out = {}
    for edge, srcview, dstview, clock in [('PHEVT_RISE', 'low', 'high', 'scl'), ('PHEVT_FALL', 'high', 'low', 'scl_n')]: 
        bindings = {}
        for rr, ee in multi.items(): 
            rrrow = storage[rr]; rbits = list(ee.get('semantic_bits') or _stored_bits(rrrow))
            for b in rbits: 
                # target source view uses its bank Q; other multiphase states are explicit view inputs.
                bindings[(rr, b)] = (f'nat_{rr}_{srcview}_b{b}' if rr == reg else f'view_{srcview}_{rr}_b{b}')
        raise ValueError('standalone direct phase-split lowering requires project context; use the production no-cert builder')
        rules = [r for r in phase['update_rules'] if r.get('materialize') and str(r['target']) == reg and str(r['event_class']) == edge]
        nex = {b: V(bindings[(reg, b)]) for b in sbits}
        for r in reversed(rules): 
            en = _and([L.basis(x['basis']) if bool(x['polarity']) else canon(N(L.basis(x['basis']))) for x in r.get('enable', [])])
            ov = L.expr(r['outcome'])
            for b in sbits: 
                nex[b] = canon(M(en, nex[b], ov[b]))
        dffs = []; vars_used = set()
        for b in sbits: 
            q = f'nat_{reg}_{dstview}_b{b}'; ex = nex[b]; vars_used|=_vars(ex)
            dffs.append({'q': q, 'qb': q+'_n', 'd_expr': ex, 'clock': clock, 'reset': 'reset', 'cell': 'DFFR', 'semantic_bit': b, 'bank': dstview})
        ownsrc = {bindings[(reg, b)] for b in sbits}
        extern = sorted(v for v in vars_used if v not in ownsrc)
        out[edge] = {'version': 'bio2rtl-phase40-direct-phase-split-bank-v1', 'status': 'PASS', 'component_class': f'natural_{reg}_{dstview}_bank', 
                   'semantic_source': reg, 'semantic_bits': sbits, 'source_view': srcview, 'destination_view': dstview, 'clock_domain': edge, 'clock': clock, 
                   'interface_inputs': sorted(set(extern+[clock, 'reset']+list(ownsrc))), 
                   'interface_outputs': [x for b in sbits for x in (f'nat_{reg}_{dstview}_b{b}', f'nat_{reg}_{dstview}_b{b}_n')], 
                   'combinational_outputs': {}, 'dffs': dffs, 'rule_count': len(rules), 
                   'derivation': 'direct phase40 bit-blast; semantic HOLD copies active opposite bank; no synthetic dual-edge clock'}
    # Per-bit async event behavior for later physical reset/set binding.
    async_behavior = {}
    for b in sbits: 
        br = {}
        for ev in ('HEVT001', 'HEVT002'): 
            rr = [r for r in phase['update_rules'] if r.get('materialize') and str(r['target']) == reg and str(r['event_class']) == ev]
            if not rr: 
                br[ev] = 'HOLD'
                continue
            vals = []; supported = True
            for r in rr: 
                if r.get('enable'): 
                    supported = False
                    break
                try: 
                    raise ValueError('standalone async analysis requires project context; use the production no-cert builder')
                    ov = L.expr(r['outcome'])[b]
                    if ov == C(0): 
                        vals.append(0)
                    elif ov == C(1): 
                        vals.append(1)
                    else: 
                        vals.append('NONCONST')
                except Exception: 
                    supported = False
                    break
            if supported and vals and len(set(vals)) == 1: 
                br[ev] = vals[0]
            else: 
                br[ev] = 'GENERAL'
        async_behavior[b] = br
    startup = {x['register']: int(x['value'][1]) for x in phase['startup']['register_values']}
    return {'version': 'bio2rtl-phase40-direct-phase-split-v1', 'status': 'PASS', 'semantic_source': reg, 'semantic_bits': sbits, 
            'contracts': out, 'async_behavior': async_behavior, 'startup_bits': {b: (int(startup.get(reg, 0))>>b)&1 for b in sbits}, 
            'view_invariant': 'LOW=>fall-bank semantic view; HIGH=>rise-bank semantic view', 'no_synthetic_dual_edge_clock': True}
