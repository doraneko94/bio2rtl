from __future__ import annotations
from pathlib import Path
import hashlib, json, re
from .recipe_keys import component_recipe_keys
from .project_inputs import load_config
from .physical_interface import resolve_core_interface


def _hash(obj)->str: 
    return hashlib.sha256(json.dumps(obj, sort_keys = True, separators = (',', ':')).encode()).hexdigest()


def neutral_graph_selector(root: Path)->str: 
    """Content selector for the architecture/physical-support *shape* only.

    Kept for audit/cache compatibility; graph generation itself no longer requires a
    benchmark-specific cached component order/port/assignment plan.
    """
    root = Path(root); _, cfg = load_config(root)
    ap = root/'build'/'architecture_ir_v16_stage3_seedless.json'
    arch = json.loads(ap.read_text()) if ap.exists() else {}
    states = []
    for e in arch.get('physical_state', []): 
        states.append({
          'role': e.get('role'), 'bits': int(e.get('bits', 0)), 
          'clock_domain': e.get('clock_domain'), 'proof': e.get('proof'), 
          'requires_phase_split_realization': bool(e.get('requires_phase_split_realization')), 
        })
    states.sort(key = lambda x: (str(x['role']), x['bits'], str(x['clock_domain']), str(x['proof'])))
    ps = resolve_core_interface(root, write_report = False)
    pads = []
    for p in ps.get('pads', []): 
        pads.append({'kind': p.get('kind'), 'bindings': sorted((p.get('bindings') or {}).keys()), 'has_core_net': bool(p.get('core_net'))})
    pads.sort(key = lambda x: (str(x['kind']), x['bindings'], x['has_core_net']))
    return _hash({
      'architecture_state_shape': states, 
      'non_dff_state_shape': [{k: x.get(k) for k in ('bits', 'role', 'implementation_class', 'proof')} for x in arch.get('non_dff_state', [])], 
      'clock': {
        'mode': cfg.get('clock', {}).get('mode'), 
        'source': cfg.get('clock', {}).get('source'), 
      }, 
      'core_interface_shape': {'has_reset': bool(ps.get('reset', {}).get('core_net')), 'pads': pads}, 
    })


def _load_semantic_contracts(root: Path)->dict[str, dict]: 
    out = {}; d = Path(root)/'build'/'semantic_neutral_contracts'
    if not d.exists(): 
        return out
    for p in sorted(d.glob('*.json')): 
        c = json.loads(p.read_text()); n = str(c.get('component_class', ''))
        if not n: 
            raise ValueError(f'neutral contract without component_class: {p}')
        if n in out: 
            raise ValueError(f'duplicate neutral component contract: {n}')
        out[n] = c
    return out


def _produced_nets(contract: dict)->set[str]: 
    """Return every net driven by a contract, not only its abbreviated interface list."""
    s = set(map(str, contract.get('interface_outputs', [])))
    s.update(map(str, (contract.get('combinational_outputs') or {}).keys()))
    for d in contract.get('dffs', []): 
        for k in ('q', 'qb'): 
            if d.get(k): 
                s.add(str(d[k]))
    for p in contract.get('primitives', []): 
        if p.get('output'): 
            s.add(str(p['output']))
    for l in contract.get('latches', []): 
        for k in ('q', 'qb'): 
            if l.get(k): 
                s.add(str(l[k]))
    return s


def _contract_inputs(contract: dict)->set[str]: 
    return set(map(str, contract.get('interface_inputs', [])))


def _scc_component_order(contracts: dict[str, dict])->list[str]: 
    """Deterministic dependency order, tolerant of sequential feedback cycles.

    Components are condensed into SCCs, SCCs are topologically ordered, and names are
    sorted inside each SCC.  No protocol/component-name priority table is used.
    """
    names = sorted(contracts); produced = {n: _produced_nets(contracts[n]) for n in names}
    edges = {n: set() for n in names}
    for a in names: 
        for b in names: 
            if a!=b and produced[a] & _contract_inputs(contracts[b]): 
                edges[a].add(b)
    # Tarjan SCC.
    idx = 0; stack = []; on = set(); ind = {}; low = {}; comps = []
    def visit(v): 
        nonlocal idx
        ind[v] = low[v] = idx; idx+=1; stack.append(v); on.add(v)
        for w in sorted(edges[v]): 
            if w not in ind: 
                visit(w); low[v] = min(low[v], low[w])
            elif w in on: 
                low[v] = min(low[v], ind[w])
        if low[v] == ind[v]: 
            c = []
            while True: 
                w = stack.pop(); on.remove(w); c.append(w)
                if w == v: 
                    break
            comps.append(sorted(c))
    for n in names: 
        if n not in ind: 
            visit(n)
    owner = {n: i for i, c in enumerate(comps) for n in c}
    dag = {i: set() for i in range(len(comps))}; indeg = {i: 0 for i in dag}
    for a in names: 
        for b in edges[a]: 
            x, y = owner[a], owner[b]
            if x!=y and y not in dag[x]: 
                dag[x].add(y)
                indeg[y]+=1
    ready = sorted([i for i, v in indeg.items() if v == 0], key = lambda i: tuple(comps[i])); ordered = []
    while ready: 
        i = ready.pop(0); ordered.extend(comps[i])
        for j in sorted(dag[i], key = lambda x: tuple(comps[x])): 
            indeg[j]-=1
            if indeg[j] == 0: 
                ready.append(j); ready.sort(key = lambda x: tuple(comps[x]))
    if len(ordered)!=len(names): 
        raise ValueError('internal SCC ordering failure')
    return ordered


def _core_boundary(root: Path, cfg: dict)->tuple[set[str], set[str]]:
    """Return the digital-core boundary required by recovered I/O semantics.

    This is intentionally independent of TR-1um support recipes.  `ins` are nets
    driven from outside the core; `outs` are nets driven by the core.
    """
    ps = resolve_core_interface(root, write_report=False); ins = set(); outs = set()
    reset = ps.get('reset', {})
    if reset.get('core_net'):
        ins.add(str(reset['core_net']))
    for pad in ps.get('pads', []):
        kind = str(pad.get('kind', ''))
        if kind == 'direct_input':
            if not pad.get('core_net'):
                raise ValueError(f'direct_input pad lacks core_net: {pad}')
            ins.add(str(pad['core_net'])); continue
        bindings = pad.get('bindings') or {}
        if kind == 'open_drain':
            role_map = {'IN': 'into_core', 'LOW': 'from_core'}
        elif kind in ('bidirectional_gpio', 'gpio_output'):
            role_map = {'INPUT_VALUE': 'into_core', 'OUT': 'from_core', 'OUT_B': 'from_core',
                        'DIR': 'from_core', 'DIR_B': 'from_core'}
        else:
            raise ValueError(f'unsupported core interface kind {kind!r} for pad {pad.get("name")}')
        for port, net in bindings.items():
            role = role_map.get(port); sn = str(net).strip()
            is_const = sn in ("1'b0", "1'h0", "1'd0", "1'b1", "1'h1", "1'd1")
            if is_const:
                if role != 'from_core':
                    raise ValueError(f'constant binding is only valid for a core-driven support input: {kind}.{port}={sn}')
                continue
            if role == 'into_core':
                ins.add(sn)
            elif role == 'from_core':
                outs.add(sn)
            else:
                raise ValueError(f'unsupported/missing core interface role {kind}.{port}: {role!r}')
    return ins, outs


def derive_neutral_graph_plan(root: Path)->dict: 
    """Derive component order and digital boundary from contracts + TOML.

    Only optional public-core aliases are declared by the project.  They are not a
    technology recipe and may map a stable public port name to any proven internal net.
    """
    root = Path(root); _, cfg = load_config(root); contracts = _load_semantic_contracts(root)
    if not contracts: 
        raise ValueError('semantic neutral contracts have not been generated')
    all_prod = set().union(*(_produced_nets(c) for c in contracts.values()))
    all_in = set().union(*(_contract_inputs(c) for c in contracts.values()))
    inferred_external = all_in-all_prod
    support_in, support_out = _core_boundary(root, cfg)
    static_contracts = [c for c in contracts.values() if (c.get('derivation') or {}).get('kind') == 'proof-constant-gpio']
    static_outputs = set().union(*(set(map(str, c.get('interface_outputs', []))) for c in static_contracts)) if static_contracts else set()
    if static_contracts and not support_out: 
        support_out.update(static_outputs)
    # A stateless constant core does not consume POR merely because the package
    # configuration offers a POR recipe.  No unconnected reset is invented.
    has_state = any(c.get('dffs') or c.get('latches') for c in contracts.values())
    if not has_state: 
        support_in.discard('reset')
    missing_support_inputs = support_in-inferred_external
    unbound_semantic_inputs = inferred_external-support_in
    if missing_support_inputs: 
        raise ValueError(f'core interface requests inputs not consumed by semantic contracts: {sorted(missing_support_inputs)}')
    if unbound_semantic_inputs: 
        raise ValueError(f'semantic contracts expose external inputs without core-interface binding: {sorted(unbound_semantic_inputs)}')
    ps = resolve_core_interface(root, write_report = not isinstance(cfg.get('physical_support'), dict))
    aliases = {str(k): str(v) for k, v in (ps.get('core_aliases') or {}).items()}
    assignments = []
    # Preserve literal semantic outputs as top-level structural assignments;
    # Boolean mapping correctly needs zero cells for these expressions.
    literal_outputs = {}
    for c in static_contracts: 
        for n, e in (c.get('combinational_outputs') or {}).items(): 
            if isinstance(e, (list, tuple)) and len(e)>=2 and e[0] == 'CONST': 
                literal_outputs[str(n)] = "1'b%d" % int(e[1])
    for public in sorted(support_out): 
        if public in literal_outputs: 
            assignments.append({'lhs': public, 'rhs': literal_outputs[public]}); continue
        if public in all_prod: 
            continue
        rhs = aliases.get(public)
        if rhs is None: 
            raise ValueError(f'core output {public!r} is not produced by contracts and has no core-interface alias')
        if rhs not in all_prod: 
            raise ValueError(f'core alias {public!r}->{rhs!r} points to an undriven semantic net')
        assignments.append({'lhs': public, 'rhs': rhs})
    unused_aliases = set(aliases)-support_out
    if unused_aliases: 
        raise ValueError(f'core interface contains unused public aliases: {sorted(unused_aliases)}')
    # Every direct support-facing net is a core output, while aliases expose stable names.
    ports = [{'name': n, 'direction': 'input'} for n in sorted(support_in)]
    ports += [{'name': n, 'direction': 'output'} for n in sorted(support_out)]
    order = _scc_component_order(contracts)
    return {
      'component_order': order, 'ports': ports, 'assignments': assignments, 
      'selector': neutral_graph_selector(root), 'resolved_from': 'semantic_contracts+core_interface', 
      'audit': {
        'contract_components': sorted(contracts), 
        'semantic_external_inputs': sorted(inferred_external), 
        'core_boundary_inputs': sorted(support_in), 'core_boundary_outputs': sorted(support_out), 
        'produced_nets': len(all_prod), 'public_aliases': aliases, 
      }
    }


def resolve_neutral_graph_plan(root: Path)->dict|None: 
    """Compatibility wrapper: graph plans are now derived, not fixture-selected."""
    return derive_neutral_graph_plan(root)


def emit_neutral_component_graph(root: Path)->dict: 
    root = Path(root); build = root/'build'; _, cfg = load_config(root)
    plan = derive_neutral_graph_plan(root)
    keys = component_recipe_keys(root)
    order = [str(x) for x in plan['component_order']]
    missing = [x for x in order if x not in keys]
    if missing: 
        raise ValueError(f'neutral graph references components without semantic selectors: {missing}')
    module = re.sub(r'[^A-Za-z0-9_]', '_', str(cfg.get('name', 'bio2rtl')))
    graph = {
      'version': 'bio2rtl-neutral-component-graph-v2', 
      'status': 'PASS', 'module': module, 'selector': plan['selector'], 
      'component_order': order, 
      'component_selectors': {x: keys[x] for x in order}, 
      'ports': plan['ports'], 'assignments': plan['assignments'], 
      'resolved_from': plan['resolved_from'], 'derivation_audit': plan['audit'], 
      'technology_specific_cell_choices': False, 
      'benchmark_graph_cache_read': False, 
    }
    build.mkdir(parents = True, exist_ok = True)
    (build/'neutral_component_graph.json').write_text(json.dumps(graph, indent = 2, sort_keys = True)+'\n')
    return graph
