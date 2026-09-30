from __future__ import annotations
from pathlib import Path
from collections import Counter
import json, re
from .boolean_mapper import BooleanMapper, V, N, A, O, X, XN, M, canon

_OUTPINS = {'Y', 'Q', 'QB'}

def _expr_for_cell(typ: str, p: dict, env: dict): 
    def E(pin): 
        net = p[pin]
        if net == "1'b0": 
            return ('CONST', 0)
        if net == "1'b1": 
            return ('CONST', 1)
        return env.get(net, V(net))
    if typ.startswith('INV'): 
        return N(E('A'))
    m = re.fullmatch(r'AND([234])(?:_X1)?', typ)
    if m: 
        return A(*(E(chr(65+i)) for i in range(int(m.group(1)))))
    m = re.fullmatch(r'OR([234])', typ)
    if m: 
        return O(*(E(chr(65+i)) for i in range(int(m.group(1)))))
    m = re.fullmatch(r'NAND([234])', typ)
    if m: 
        return N(A(*(E(chr(65+i)) for i in range(int(m.group(1))))))
    m = re.fullmatch(r'NOR([234])', typ)
    if m: 
        return N(O(*(E(chr(65+i)) for i in range(int(m.group(1))))))
    if typ == 'XOR2': 
        return X(E('A'), E('B'))
    if typ == 'XNOR2': 
        return XN(E('A'), E('B'))
    if typ == 'MUX2': 
        return M(E('S'), E('A'), E('B'))
    raise ValueError(f'unsupported Boolean-contract cell {typ}')

def extract_boolean_sequential_contract(recipe: dict, selector_sha256: str|None = None, *, allow_primitives: bool = False, preserve_clock_nets: set[str]|None = None)->dict: 
    """Extract a technology-neutral Boolean+DFF contract from a mapped component.

    This is a bootstrap utility. The resulting contract contains no standard-cell
    choices; later mapping may target a different gate decomposition/technology.
    """
    preserve_clock_nets = set(preserve_clock_nets or ())
    env = {n: V(n) for n in recipe.get('interface_inputs', [])}
    dffs = []
    latches = []
    latch_skip = set()
    # Detect cross-coupled NOR state structurally.  This is not treated as a
    # combinational cycle: it is a technology-neutral set/reset latch primitive.
    if allow_primitives: 
        nor = []
        for i, c in enumerate(recipe.get('cells', [])): 
            if re.fullmatch(r'NOR[234]', c.get('type', '')) and c.get('ports', {}).get('Y'): 
                nor.append((i, c))
        pairs = []
        for ai, (i, a) in enumerate(nor): 
            ay = a['ports']['Y']; ains = {v for k, v in a['ports'].items() if k!='Y'}
            for j, b in nor[ai+1:]: 
                by = b['ports']['Y']; bins = {v for k, v in b['ports'].items() if k!='Y'}
                if ay in bins and by in ains: 
                    pairs.append((i, a, j, b))
        used = set()
        iface_out = set(recipe.get('interface_outputs', []))
        for i, a, j, b in pairs: 
            if i in used or j in used: 
                continue
            # Prefer the externally observable member as Q; otherwise deterministic name order.
            if (a['ports']['Y'] in iface_out) != (b['ports']['Y'] in iface_out): 
                qcell, qidx, qbcell, qbidx = (a, i, b, j) if a['ports']['Y'] in iface_out else (b, j, a, i)
            else: 
                qcell, qidx, qbcell, qbidx = (a, i, b, j) if a['ports']['Y'] < b['ports']['Y'] else (b, j, a, i)
            q = qcell['ports']['Y']; qb = qbcell['ports']['Y']
            reset_inputs = [v for k, v in qcell['ports'].items() if k!='Y' and v!=qb]
            set_inputs = [v for k, v in qbcell['ports'].items() if k!='Y' and v!=q]
            if not reset_inputs or not set_inputs: 
                continue
            latches.append({'kind': 'set_reset_latch', 'implementation_class': 'cross_coupled_nor', 'q': q, 'qb': qb, 
                            'set_inputs': set_inputs, 'reset_inputs': reset_inputs, 'semantic': 'cross_coupled_state_preserved'})
            latch_skip|={qidx, qbidx};used|={qidx, qbidx};env[q] = V(q);env[qb] = V(qb)
    # State Q/QB are primary variables for combinational recurrence extraction.
    for i, c in enumerate(recipe['cells']): 
        if i in latch_skip: 
            continue
        if c['type'] == 'DFFR': 
            p = c['ports']; env[p['Q']] = V(p['Q']); env[p['QB']] = V(p['QB'])
    pending = []
    primitives = []
    for i, c in enumerate(recipe['cells']): 
        if i in latch_skip: 
            continue
        if c['type'] == 'DFFR': 
            pending.append(c); continue
        typ = c['type']; p = c['ports']
        if allow_primitives and typ == 'INV_X4' and p.get('Y') in preserve_clock_nets: 
            primitives.append({'kind': 'clock_inverter', 'implementation_class': 'X4', 'input': p['A'], 'output': p['Y'], 
                               'input_expr': env.get(p['A'], V(p['A'])), 'semantic': 'clock_path_inversion_and_drive_preserved'})
            env[p['Y']] = V(p['Y'])
            continue
        if allow_primitives and typ == 'DEL4': 
            primitives.append({'kind': 'delay_element', 'implementation_class': 'DEL4', 'input': p['A'], 'output': p['Y'], 
                               'input_expr': env.get(p['A'], V(p['A'])), 'semantic': 'temporal_delay_preserved'})
            env[p['Y']] = V(p['Y'])
            continue
        if allow_primitives and typ == 'CLKBUF_X4': 
            primitives.append({'kind': 'clock_buffer', 'implementation_class': 'X4', 'input': p['A'], 'output': p['Y'], 
                               'input_expr': env.get(p['A'], V(p['A'])), 'semantic': 'clock_path_buffer_preserved'})
            env[p['Y']] = V(p['Y'])
            continue
        y = p.get('Y')
        if not y: 
            raise ValueError(f'no Y on comb cell {c}')
        env[y] = canon(_expr_for_cell(typ, p, env))
    for l in latches: 
        l['set_input_exprs'] = {n: env.get(n, V(n)) for n in l['set_inputs']}
        l['reset_input_exprs'] = {n: env.get(n, V(n)) for n in l['reset_inputs']}
    for c in pending: 
        p = c['ports']
        dnet = p['D']; de = env.get(dnet, V(dnet))
        dffs.append({'q': p['Q'], 'qb': p['QB'], 'd_expr': de, 'clock': p['CK'], 'reset': p['RST'], 'cell': 'DFFR'})
    comb_outputs = {}
    dffouts = {x['q'] for x in dffs}|{x['qb'] for x in dffs}
    latchouts = {x['q'] for x in latches}|{x['qb'] for x in latches}
    stateouts = dffouts|latchouts
    for net in recipe.get('interface_outputs', []): 
        if net not in stateouts: 
            comb_outputs[net] = env.get(net, V(net))
    return {
      'version': 'bio2rtl-neutral-boolean-sequential-contract-v1', 
      'component_class': recipe['component_class'], 
      'selector_sha256': selector_sha256 or recipe.get('selector_sha256'), 
      'interface_inputs': recipe.get('interface_inputs', []), 
      'interface_outputs': recipe.get('interface_outputs', []), 
      'combinational_outputs': comb_outputs, 
      'dffs': dffs, 
      'primitives': primitives, 
      'latches': latches, 
      'bootstrap_source': 'mapped component symbolic extraction; contract itself is technology-neutral', 
    }

def _tupleize(x): 
    if isinstance(x, list): 
        return tuple(_tupleize(v) for v in x)
    if isinstance(x, dict): 
        return {k: _tupleize(v) for k, v in x.items()}
    return x


def _expr_vars_contract(e, out = None): 
    out = set() if out is None else out
    e = _tupleize(e)
    if not isinstance(e, tuple) or not e: 
        return out
    if e[0] == 'VAR': 
        out.add(str(e[1]))
        return out
    if e[0] in ('AND', 'OR'): 
        for x in e[1]: 
            _expr_vars_contract(x, out)
    else: 
        for x in e[1:]: 
            if isinstance(x, tuple): 
                _expr_vars_contract(x, out)
    return out


def _topological_comb_outputs(comb: dict)->list[str]: 
    """Order named combinational nodes before consumers; reject combinational cycles."""
    names = set(map(str, comb))
    deps = {str(k): (_expr_vars_contract(v)&names)-{str(k)} for k, v in comb.items()}
    done = []; remain = set(names)
    while remain: 
        ready = sorted(n for n in remain if deps[n] <= set(done))
        if not ready: 
            cycle = {n: sorted(deps[n]&remain) for n in sorted(remain)}
            raise ValueError(f'combinational named-node dependency cycle: {cycle}')
        done.extend(ready); remain-=set(ready)
    return done

def map_boolean_sequential_contract(contract: dict, area: dict[str, float], component: str|None = None, name_prefix = 'fb')->list[dict]: 
    comp = component or contract['component_class']
    # Free complements supplied by DFF QBs.
    complements = {str(k): str(v) for k, v in contract.get('complements', {}).items()}
    complements.update({d['q']: d['qb'] for d in contract.get('dffs', [])})
    complements.update({d['qb']: d['q'] for d in contract.get('dffs', [])})
    m = BooleanMapper(area, complements)
    # Map external combinational outputs first so shared subexpressions are memoized.
    desired = {}
    comb = contract.get('combinational_outputs', {})
    for out in _topological_comb_outputs(comb): 
        desired[out] = m.map(_tupleize(comb[out]))
    # A preserved primitive may be driven by derived Boolean logic (e.g. gated clock).
    # That driver is part of the neutral contract even when it is neither a top output
    # nor a DFF-D cone, so map it explicitly to the primitive input net.
    for p in contract.get('primitives', []): 
        ie = p.get('input_expr')
        if ie is None: 
            continue
        te = _tupleize(ie)
        if canon(te) != canon(V(p['input'])): 
            desired[p['input']] = m.map(te)
    for l in contract.get('latches', []): 
        for n, e in {**l.get('set_input_exprs', {}), **l.get('reset_input_exprs', {})}.items(): 
            te = _tupleize(e)
            if canon(te) != canon(V(n)): 
                desired[n] = m.map(te)
    dnets = []
    for d in contract.get('dffs', []): 
        dnets.append((d, m.map(_tupleize(d['d_expr']))))

    # Rename internal mapper nets that represent required exposed combinational outputs.
    ren = {src: dst for dst, src in desired.items() if src!=dst and isinstance(src, str) and not src.startswith("1'b")}
    # Avoid attempting to rename a primary input directly; these would need a top-level alias.
    primary = set(contract.get('interface_inputs', []))|{d['q'] for d in contract.get('dffs', [])}|{d['qb'] for d in contract.get('dffs', [])}|{p['output'] for p in contract.get('primitives', [])}|{l['q'] for l in contract.get('latches', [])}|{l['qb'] for l in contract.get('latches', [])}
    bad = {s: d for s, d in ren.items() if s in primary}
    if bad: 
        raise ValueError(f'contract output aliases primary nets; unsupported without assignment: {bad}')
    # Every fallback component is flattened into one module, so mapper-local nets
    # must be namespaced as well as instance names.  Without this, simultaneous
    # fallbacks silently short n_0001/n_0002 across unrelated components.
    internal = {}
    for c in m.cells: 
        if c.out not in ren and isinstance(c.out, str) and c.out not in primary: 
            internal[c.out] = f'{name_prefix}_{c.out}'
    def R(n): 
        return ren.get(n, internal.get(n, n))
    cells = []
    for i, c in enumerate(m.cells): 
        ports = {k: R(v) for k, v in c.pins.items()}
        cells.append({'component': comp, 'name': f'{name_prefix}_{i:03d}_{c.typ.lower()}', 'type': c.typ, 'ports': ports})
    base = len(cells)
    for j, (d, dnet) in enumerate(dnets): 
        cells.append({'component': comp, 'name': f'{name_prefix}_{base+j:03d}_dffr', 'type': 'DFFR', 
                      'ports': {'CK': d['clock'], 'D': R(dnet), 'Q': d['q'], 'QB': d['qb'], 'RST': d['reset']}})
    return cells

def map_neutral_component_contract(contract: dict, area: dict[str, float], primitive_library: dict, component: str|None = None, name_prefix = 'fb')->list[dict]: 
    """Map Boolean/DFF logic plus explicitly preserved non-Boolean technology-neutral primitives."""
    cells = map_boolean_sequential_contract(contract, area, component = component, name_prefix = name_prefix)
    comp = component or contract['component_class']
    for i, p in enumerate(contract.get('primitives', [])): 
        kind = p['kind']; cls = p['implementation_class']
        try: 
            impl = primitive_library['primitives'][kind]['implementations'][cls]
        except KeyError as e: 
            raise ValueError(f'no technology mapping for neutral primitive {kind}:{cls}') from e
        typ = impl['cell']; pm = impl['ports']
        cells.append({'component': comp, 'name': f'{name_prefix}_prim_{i:03d}_{kind}', 'type': typ, 
                      'ports': {pm['input']: p['input'], pm['output']: p['output']}})
    for i, l in enumerate(contract.get('latches', [])): 
        kind = 'set_reset_latch'; cls = l['implementation_class']
        try: 
            impl = primitive_library['primitives'][kind]['implementations'][cls]
        except KeyError as e: 
            raise ValueError(f'no technology mapping for neutral latch {kind}:{cls}') from e
        fam = impl['family']; byn = impl['cells_by_total_inputs']; pins = impl['input_pins']; ypin = impl['output_pin']
        for side, outsig, feedback in [('reset', l['q'], l['qb']), ('set', l['qb'], l['q'])]: 
            ext = list(l[side+'_inputs']); nets = ext+[feedback]; n = len(nets)
            typ = byn.get(str(n))
            if typ is None: 
                raise ValueError(f'{kind}:{cls} {side} side requires {n} inputs, outside direct primitive map')
            ports = {pins[j]: net for j, net in enumerate(nets)};ports[ypin] = outsig
            cells.append({'component': comp, 'name': f'{name_prefix}_latch_{i:03d}_{side}', 'type': typ, 'ports': ports})
    return cells

def save_contract(path: Path|str, d: dict): 
    Path(path).write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
