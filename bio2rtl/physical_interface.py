from __future__ import annotations
from pathlib import Path
import json, re
from .project_inputs import load_config, SUPPORTED_TECHNOLOGY


def _slug(name: str)->str: 
    s = re.sub(r'[^A-Za-z0-9_]+', '_', str(name)).strip('_').lower()
    return s or 'io'


def io_input_net(cfg: dict, io: dict)->str: 
    """Compiler-owned core input net for one user-declared BIO GPIO.

    Users never name this net.  The edge-clock source gets the compact base name;
    other pins use <name>_in.  This preserves the established I2C spelling while
    remaining independent of protocol identity.
    """
    name = str(io['name']); base = _slug(name)
    source = str((cfg.get('clock') or {}).get('source', ''))
    return base if name == source else f'{base}_in'


def gpio_input_map_from_config(cfg: dict)->dict[int, str]: 
    return {int(io['gpio']): io_input_net(cfg, io) for io in cfg.get('io', [])}


def _load_json(p: Path): 
    return json.loads(p.read_text())


def _phase_gpio_input_bits(phase: dict)->set[int]: 
    # Reuse the semantic front-end's bit-precise dependency analysis.  This is
    # proof/IR structure, not protocol naming.
    from semantic_frontend.bio2rtl.dedicated_event_reachability import _gpio_input_dependencies
    bits = set()
    for r in phase.get('update_rules', []): 
        bits |= set(map(int, _gpio_input_dependencies(r.get('outcome'))))
    for p in phase.get('predicate_basis', []): 
        bits |= set(map(int, _gpio_input_dependencies(p.get('expression'))))
    for d in phase.get('scheduler_detectors', []): 
        if d.get('input_bit') is not None: 
            bits.add(int(d['input_bit']))
        for q in d.get('qualifiers', []): 
            if q.get('source') == 'GPIO_INPUT' and q.get('bit') is not None: 
                bits.add(int(q['bit']))
    return bits


def _gpio_bit_constant(phase: dict, reg: str, bit: int)->int|None: 
    rows = {str(x.get('register')): x for x in (phase.get('storage_optimization') or {}).get('register_storage', [])}
    row = rows.get(reg, {})
    vals = row.get('reachable_values') or row.get('reachable_values_upper_bound') or []
    if vals: 
        bs = {((int(v)>>bit)&1) for v in vals}
        if len(bs) == 1: 
            return next(iter(bs))
    if bit in set(map(int, row.get('constant_one_bits', []))): 
        return 1
    if bit in set(map(int, row.get('constant_zero_bits', []))): 
        return 0
    return None


def _resolve_io_support(kind: str, requested: str, name: str, bit: int)->str:
    """Resolve/validate the physical support circuit for one recovered I/O role.

    `auto` chooses the smallest support circuit that preserves the recovered
    semantics.  Explicit selections are treated as constraints and fail closed
    when they would discard a required readback path or use the wrong electrical
    interface.
    """
    requested = str(requested or 'auto')
    auto = {
      'direct_input': 'direct',
      'open_drain': 'sda_io',
      'bidirectional_gpio': 'gpio_io',
      'bidirectional_gpio_no_readback': 'gpio_o',
      'push_pull_output': 'gpio_o',
      'push_pull_output_readback': 'gpio_io',
      'push_pull_constant_output': 'gpio_o',
    }
    allowed = {
      'direct_input': {'direct'},
      'open_drain': {'sda_io'},
      'bidirectional_gpio': {'gpio_io'},
      'bidirectional_gpio_no_readback': {'gpio_io', 'gpio_o'},
      'push_pull_output': {'gpio_io', 'gpio_o'},
      'push_pull_output_readback': {'gpio_io'},
      'push_pull_constant_output': {'gpio_io', 'gpio_o'},
    }
    if kind not in auto:
        raise ValueError(f'{name}/GPIO{bit}: no support policy for recovered I/O kind {kind!r}')
    chosen = auto[kind] if requested == 'auto' else requested
    if chosen not in allowed[kind]:
        raise ValueError(
            f'{name}/GPIO{bit}: support={requested!r} is incompatible with recovered I/O kind {kind!r}; '
            f'allowed={sorted(allowed[kind])}'
        )
    return chosen


def infer_physical_intent(root: Path)->dict: 
    """Infer package-pin electrical intent from .dis-derived semantic authority.

    User configuration supplies [[io]] name/gpio and may constrain support selection.
    determines which declared GPIOs are inputs, open-drain, or bidirectional from
    Phase40/architecture facts.  Ambiguous/unsupported cases fail closed.
    """
    root = Path(root); _, cfg = load_config(root)
    phase = _load_json(root/'build/semantic/phase40.ir.json')
    arch_path = root/'build/architecture_ir_v16_stage3_seedless.json'
    arch = _load_json(arch_path) if arch_path.exists() else {}
    input_bits = _phase_gpio_input_bits(phase)

    data_bits = set(); dir_bits = set(); open_bits = set()
    for e in arch.get('physical_state', []): 
        role = str(e.get('role', ''))
        if role == 'gpio_data_payload': 
            data_bits.update(map(int, e.get('semantic_bits', [])))
        elif role == 'gpio_direction_payload': 
            dir_bits.update(map(int, e.get('semantic_bits', [])))
        elif role == 'open_drain_oe': 
            src = e.get('semantic_source') or {}
            if isinstance(src, dict) and str(src.get('register')) == 'G_DIR' and src.get('bit') is not None: 
                open_bits.add(int(src['bit']))

    # Natural-storage fallback can leave GPIO bits in the phase IR without the
    # optimized role labels.  Add its retained dynamic bits conservatively.
    storage = {str(x.get('register')): x for x in (phase.get('storage_optimization') or {}).get('register_storage', [])}
    for reg, dst in [('G_DATA', data_bits), ('G_DIR', dir_bits)]: 
        row = storage.get(reg, {})
        dst.update(map(int, row.get('stored_bits', [])))
        # DIRECT/NARROW GPIO storage may not have stored_bits.
        if str(row.get('kind')) == 'GPIO' and not row.get('stored_bits') and int(row.get('storage_bits', 0)): 
            mask = int(row.get('mask', 0))
            dst.update(i for i in range(32) if (mask>>i)&1)

    pads = []
    for io in cfg.get('io', []): 
        name = str(io['name']); bit = int(io['gpio']); read = bit in input_bits
        is_open = bit in open_bits
        has_data = bit in data_bits
        has_dir = bit in dir_bits
        data_const = _gpio_bit_constant(phase, 'G_DATA', bit)
        dir_const = _gpio_bit_constant(phase, 'G_DIR', bit)
        if is_open: 
            if has_data or data_const not in (0, None): 
                raise ValueError(f'{name}/GPIO{bit}: open-drain role conflicts with non-constant-low G_DATA semantics')
            if not read: 
                raise ValueError(f'{name}/GPIO{bit}: open-drain pad is not read by recovered semantics; unsupported recipe shape')
            kind = 'open_drain'
        elif has_data and has_dir: 
            kind = 'bidirectional_gpio' if read else 'bidirectional_gpio_no_readback'
        elif has_data and not has_dir and dir_const == 1: 
            kind = 'push_pull_output'
        elif (not read) and (not has_data) and (not has_dir) and data_const in (0, 1) and dir_const == 1: 
            kind = 'push_pull_constant_output'
        elif read and not has_data and not has_dir and dir_const in (0, None): 
            kind = 'direct_input'
        elif read and has_data and not has_dir and dir_const == 1: 
            kind = 'push_pull_output_readback'
        elif read and (has_data != has_dir): 
            raise ValueError(f'{name}/GPIO{bit}: GPIO data/direction semantics are incomplete; refusing ambiguous pad inference')
        else: 
            raise ValueError(f'{name}/GPIO{bit}: declared [[io]] has no supported recovered external I/O behavior (G_DATA const={data_const}, G_DIR const={dir_const})')
        support = _resolve_io_support(kind, str(io.get('support', 'auto')), name, bit)
        pads.append({'name': name, 'gpio': bit, 'kind': kind, 'support': support, 'input_net': io_input_net(cfg, io) if read else None, 
                     'data_constant': data_const, 'direction_constant': dir_const})

    return {
      'version': 'bio2rtl-physical-interface-intent-v1', 'status': 'PASS', 
      'technology': SUPPORTED_TECHNOLOGY, 
      'clock': {'mode': cfg['clock']['mode'], 'source': cfg['clock']['source']}, 
      'gpio_input_bits': sorted(input_bits), 'gpio_data_dynamic_bits': sorted(data_bits), 
      'gpio_direction_dynamic_bits': sorted(dir_bits), 'open_drain_bits': sorted(open_bits), 
      'pads': pads, 
      'user_declared_only': True, 
    }



def _write_public_io_report(root: Path, intent: dict)->None: 
    """Write a user-facing I/O report containing no compiler-internal net names."""
    root = Path(root)
    clock = intent.get('clock') or {}
    rows = []
    for p in intent.get('pads', []): 
        rows.append({
          'name': str(p['name']), 
          'bio_gpio': int(p['gpio']), 
          'electrical_kind': str(p['kind']), 
          'support': str(p.get('support', 'auto')), 
          'clock_source': str(p['name']) == str(clock.get('source', '')), 
        })
    report = {
      'version': 'bio2rtl-user-io-report-v1', 'status': 'PASS', 
      'technology': intent.get('technology'), 
      'power_pins': ['VDD', 'VSS'] if intent.get('technology') == 'TR-1um' else [], 
      'clock': clock, 'io': rows, 
      'contains_compiler_internal_net_names': False, 
    }
    out = root/'build/io_report.json'; out.parent.mkdir(parents = True, exist_ok = True)
    out.write_text(json.dumps(report, indent = 2, sort_keys = True)+'\n')

def _contracts(root: Path)->list[dict]: 
    d = Path(root)/'build/semantic_neutral_contracts'
    return [_load_json(p) for p in sorted(d.glob('*.json'))] if d.exists() else []


def _gpio_payload_bindings(contracts: list[dict])->dict[tuple[str, int], dict]: 
    out = {}
    for c in contracts: 
        nb = c.get('neutral_interface_binding') or {}
        banks = nb.get('banks', [])
        if isinstance(banks, list): 
            for bank in banks: 
                if not isinstance(bank, dict): 
                    continue
                src = str(bank.get('semantic_source', ''))
                if src not in ('G_DATA', 'G_DIR'): 
                    continue
                for b in bank.get('bits', []): 
                    bit = int(b['semantic_bit']); q = str(b['q']); qb = str(b['qb']); on = str(b.get('semantic_on', 'q'))
                    pos = q if on == 'q' else qb; neg = qb if on == 'q' else q
                    key = (src, bit); val = {'pos': pos, 'neg': neg, 'source': 'semantic_neutral_contract_bank'}
                    if key in out and out[key]!=val: 
                        raise ValueError(f'conflicting payload bindings for {key}: {out[key]} vs {val}')
                    out[key] = val
        # Generic natural-event storage exposes semantic identity directly on
        # each DFF rather than through a bank descriptor.  This is equally
        # proof-backed and is required for non-I2C output-only GPIOs.
        for d in c.get('dffs', []): 
            if not isinstance(d, dict): 
                continue
            src = str(d.get('semantic_source', ''))
            if src not in ('G_DATA', 'G_DIR') or d.get('semantic_bit') is None: 
                continue
            bit = int(d['semantic_bit']); q = str(d.get('q', '')); qb = str(d.get('qb', ''))
            if not q or not qb: 
                raise ValueError(f'incomplete DFF payload binding for {(src,bit)}')
            key = (src, bit); val = {'pos': q, 'neg': qb, 'source': 'semantic_neutral_contract_dff'}
            if key in out: 
                # Different metadata source labels are fine if the actual nets agree.
                if out[key]['pos']!=q or out[key]['neg']!=qb: 
                    raise ValueError(f'conflicting payload bindings for {key}: {out[key]} vs {val}')
            else: 
                out[key] = val
    return out


def _open_drain_bindings(root: Path, arch: dict, contracts: list[dict])->dict[int, dict]: 
    out = {}
    oe_bits = []
    for e in arch.get('physical_state', []): 
        if str(e.get('role'))!='open_drain_oe': 
            continue
        src = e.get('semantic_source') or {}
        if isinstance(src, dict) and str(src.get('register')) == 'G_DIR' and src.get('bit') is not None: 
            oe_bits.append(int(src['bit']))
    if not oe_bits: 
        return out
    candidates = []
    for c in contracts: 
        if str(c.get('component_class'))!='open_drain_oe_recurrence': 
            continue
        st = (c.get('neutral_interface_binding') or {}).get('state') or {}
        if st.get('q') and st.get('qb'): 
            candidates.append({'pos': str(st['q']), 'neg': str(st['qb'])})
    if len(oe_bits)!=len(candidates): 
        raise ValueError(f'open-drain semantic-source/binding cardinality mismatch: bits={oe_bits}, bindings={candidates}')
    for bit, b in zip(sorted(oe_bits), candidates): 
        out[bit] = {**b, 'source': 'oe_recurrence_contract'}
    return out


def _unique_recipe(lib: dict, kind: str)->str: 
    ids = sorted(k for k, v in lib.get('recipes', {}).items() if str(v.get('kind')) == kind)
    if len(ids)!=1: 
        raise ValueError(f'technology recipe kind {kind!r} must resolve uniquely, got {ids}')
    return ids[0]


def resolve_core_interface(root: Path, *, write_report: bool = False)->dict:
    """Resolve the digital core boundary without requiring physical-support recipes.

    The compiler target is TR-1um independently of whether a package/POR/I/O-support
    wrapper is requested.  This function derives only the core-side nets and aliases
    demanded by the recovered I/O semantics.  It deliberately does not read the
    technology support-recipe library.
    """
    root = Path(root); _, cfg = load_config(root); intent = infer_physical_intent(root)
    dhir_path = root/'build/physical_dhir_v18_stage7.json'
    dhir = _load_json(dhir_path) if dhir_path.exists() else {}
    no_cert = str(dhir.get('mapping_mode', '')).startswith('phase40-direct-no-architecture-certificate')

    aliases = {}; pads = []
    if no_cert:
        for p in intent['pads']:
            name = p['name']; kind = p['kind']; base = _slug(name); support = str(p['support'])
            if support == 'direct':
                pads.append({'name': name, 'kind': 'direct_input', 'core_net': p['input_net']})
            elif support == 'sda_io':
                pads.append({'name': name, 'kind': 'open_drain',
                             'bindings': {'IN': p['input_net'], 'LOW': f'{base}_drive_low'}})
            elif support in ('gpio_io', 'gpio_o'):
                if kind in ('bidirectional_gpio', 'bidirectional_gpio_no_readback'):
                    b = {'OUT': f'{base}_out', 'OUT_B': f'{base}_out_b', 'DIR': f'{base}_oe_b', 'DIR_B': f'{base}_oe'}
                elif kind in ('push_pull_output', 'push_pull_output_readback'):
                    b = {'OUT': f'{base}_out', 'OUT_B': f'{base}_out_b', 'DIR': "1'b0", 'DIR_B': "1'b1"}
                elif kind == 'push_pull_constant_output':
                    v = int(p['data_constant']); b = {'OUT': f"1'b{v}", 'OUT_B': f"1'b{1-v}", 'DIR': "1'b0", 'DIR_B': "1'b1"}
                else:
                    raise ValueError(f'unsupported {support} electrical kind {kind}')
                if support == 'gpio_io' and p.get('input_net'):
                    b['INPUT_VALUE'] = p['input_net']
                core_kind = 'bidirectional_gpio' if support == 'gpio_io' else 'gpio_output'
                external_direction = 'inout' if kind == 'bidirectional_gpio' else 'out'
                pads.append({'name': name, 'kind': core_kind, 'external_direction': external_direction, 'bindings': b})
            else:
                raise ValueError(f'unsupported resolved support circuit {support!r} for {name}')
        resolution_source = '[[io]] + clock + Phase40/architecture + conservative DHIR naming'
    else:
        contracts = _contracts(root)
        if not contracts:
            raise ValueError('semantic neutral contracts required to resolve core interface nets')
        arch = _load_json(root/'build/architecture_ir_v16_stage3_seedless.json')
        gp = _gpio_payload_bindings(contracts); od = _open_drain_bindings(root, arch, contracts)
        for p in intent['pads']:
            name = p['name']; bit = int(p['gpio']); kind = p['kind']; base = _slug(name); support = str(p['support'])
            if support == 'direct':
                pads.append({'name': name, 'kind': 'direct_input', 'core_net': p['input_net']}); continue
            if support == 'sda_io':
                st = od.get(bit)
                if not st:
                    raise ValueError(f'{name}/GPIO{bit}: open-drain semantic state binding not found')
                public = f'{base}_drive_low'; aliases[public] = st['pos']
                pads.append({'name': name, 'kind': 'open_drain', 'bindings': {'IN': p['input_net'], 'LOW': public}}); continue
            if support in ('gpio_io', 'gpio_o'):
                if kind in ('bidirectional_gpio', 'bidirectional_gpio_no_readback'):
                    data = gp.get(('G_DATA', bit)); dire = gp.get(('G_DIR', bit))
                    if not data or not dire:
                        raise ValueError(f'{name}/GPIO{bit}: payload contract binding not found')
                    pout = f'{base}_out'; poe = f'{base}_oe'; aliases[pout] = data['pos']; aliases[poe] = dire['pos']
                    b = {'OUT': pout, 'OUT_B': data['neg'], 'DIR': dire['neg'], 'DIR_B': poe}
                elif kind in ('push_pull_output', 'push_pull_output_readback'):
                    data = gp.get(('G_DATA', bit))
                    if not data:
                        raise ValueError(f'{name}/GPIO{bit}: output data contract binding not found')
                    pout = f'{base}_out'; aliases[pout] = data['pos']
                    b = {'OUT': pout, 'OUT_B': data['neg'], 'DIR': "1'b0", 'DIR_B': "1'b1"}
                elif kind == 'push_pull_constant_output':
                    v = int(p['data_constant']); b = {'OUT': f"1'b{v}", 'OUT_B': f"1'b{1-v}", 'DIR': "1'b0", 'DIR_B': "1'b1"}
                else:
                    raise ValueError(f'unsupported {support} electrical kind {kind}')
                if support == 'gpio_io' and p.get('input_net'):
                    b['INPUT_VALUE'] = p['input_net']
                core_kind = 'bidirectional_gpio' if support == 'gpio_io' else 'gpio_output'
                external_direction = 'inout' if kind == 'bidirectional_gpio' else 'out'
                pads.append({'name': name, 'kind': core_kind, 'external_direction': external_direction, 'bindings': b}); continue
            raise ValueError(f'unsupported resolved support circuit {support!r} for {name}')
        resolution_source = '[[io]] + clock + Phase40/architecture/contracts'

    core = {
        'technology': SUPPORTED_TECHNOLOGY,
        'core_aliases': aliases,
        'reset': {'core_net': 'reset'},
        'pads': pads,
    }
    if write_report:
        report = {
            'version': 'bio2rtl-inferred-core-interface-v1', 'status': 'PASS', 'intent': intent,
            'resolved_core_interface': core, 'physical_support_enabled': isinstance(cfg.get('physical_support'), dict),
            'user_toml_contains_internal_nets': False, 'resolution_source': resolution_source,
        }
        out = root/'build/core_interface_inferred.json'; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        _write_public_io_report(root, intent)
    return core


def resolve_physical_support(root: Path, *, write_report: bool = True)->dict:
    """Add technology support recipes to an already resolved digital core boundary."""
    root = Path(root); _, cfg = load_config(root)
    if not isinstance(cfg.get('physical_support'), dict):
        raise ValueError('physical support is not enabled for this core-only project')
    intent = infer_physical_intent(root)
    core = resolve_core_interface(root, write_report=False)
    lib = _load_json(root/'technology/support_recipes_v1.json')
    if cfg['physical_support'].get('technology') != lib.get('technology'):
        raise ValueError('physical technology does not match recipe library')
    if core['technology'] != lib.get('technology'):
        raise ValueError('compiler target technology does not match physical-support recipe library')

    pads = []
    for pad in core.get('pads', []):
        q = dict(pad); kind = str(q.get('kind', ''))
        if kind == 'direct_input':
            pads.append(q); continue
        recipe_kind = {'open_drain': 'open_drain', 'bidirectional_gpio': 'bidirectional_gpio', 'gpio_output': 'gpio_output'}.get(kind)
        if recipe_kind is None:
            raise ValueError(f'unsupported core interface kind for physical support: {kind!r}')
        q['recipe'] = _unique_recipe(lib, recipe_kind)
        pads.append(q)

    ps = {
        'technology': core['technology'], 'core_aliases': dict(core.get('core_aliases') or {}),
        'reset': {'recipe': _unique_recipe(lib, 'por'), 'core_net': 'reset'}, 'pads': pads,
    }
    report = {
        'version': 'bio2rtl-inferred-physical-support-v1', 'status': 'PASS', 'intent': intent,
        'resolved_support': ps, 'user_toml_contains_internal_nets': False,
        'resolution_source': 'core_interface + TR-1um support recipe library',
    }
    if write_report:
        out = root/'build/physical_support_inferred.json'; out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
        _write_public_io_report(root, intent)
    return ps

