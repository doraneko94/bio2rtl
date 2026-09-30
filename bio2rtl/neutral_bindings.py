from __future__ import annotations
from pathlib import Path
import json
from .recipe_keys import control_semantic_selector, event_semantic_selector, component_recipe_keys


def _load(path: Path) -> dict: 
    return json.loads(path.read_text())


def _cache(root: Path) -> dict: 
    path = Path(root) / 'technology' / 'neutral_interface_binding_cache_v1.json'
    if not path.exists(): 
        return {'version': 'missing', 'entries': []}
    return _load(path)


def resolve_control_interface_binding(root: Path, *, include_roles: bool = True) -> dict | None: 
    root = Path(root)
    wanted = control_semantic_selector(root)
    base = None
    for entry in _cache(root).get('entries', []): 
        if entry.get('kind') == 'phase_factorized_control' and entry.get('semantic_selector') == wanted: 
            base = {**entry['plan'], 'resolved_from': 'neutral_interface_binding_cache', 'semantic_selector': wanted}
            break
    if base is None: 
        return None
    if not include_roles: 
        return base
    # Semantic branch meaning must not come from the historical branch-net binding.  Keep only
    # bank Q/QB/clock/reset naming policy, then discover branch discriminator expressions from
    # the current control proof + legal domain.
    role = None
    certp = root/'build/generated_certificates/control_branch_roles.json'
    if certp.exists(): 
        try: 
            import hashlib
            cand = _load(certp)
            a = cand.get('proof_anchor', {})
            def sh(p): 
                return hashlib.sha256(Path(p).read_bytes()).hexdigest() if Path(p).exists() else None
            current = {
              'control_factorization_sha256': sh(root/'build/generated_certificates/control_factorization.json'), 
              'legal_product_sha256': sh(root/'build/semantic/legal_product.json'), 
              'semantic_predicate_projection_sha256': sh(root/'build/generated_certificates/semantic_predicate_projection.json'), 
              'shared_counter_index_projection_sha256': sh(root/'build/generated_certificates/shared_counter_index_projection.json'), 
            }
            if cand.get('status') == 'PASS' and a == current: 
                role = cand
        except Exception: 
            role = None
    if role is None: 
        try: 
            from .legal_role_synthesis import emit_control_branch_roles
            role = emit_control_branch_roles(root)
        except (FileNotFoundError, KeyError, ValueError): 
            role = None
    if role and role.get('status') == 'PASS' and not any(x.get('counterexamples') for x in role.get('proofs', [])): 
        base = {**base, 'branch_roles': role['branch_roles'], 'helpers': [], 
              'branch_role_certificate': 'build/generated_certificates/control_branch_roles.json', 
              'resolved_from': 'bank naming/clock/reset plan + legal-domain auto branch roles'}
    return base


def resolve_oe_interface_binding(root: Path) -> dict | None: 
    """Resolve OE state/clock naming while deriving count-zero semantics from proof.

    Historical bindings named a precomputed ``pc7_n`` predicate.  Generic lowering instead
    projects the semantic decrement counter through the shared-counter index proof at the
    actual source phase of the OE update.  The physical counter value is therefore derived,
    never protocol- or benchmark-hardcoded.
    """
    root = Path(root)
    wanted = component_recipe_keys(root).get('open_drain_oe_recurrence')
    base = None
    for entry in _cache(root).get('entries', []): 
        if entry.get('kind') == 'open_drain_oe_recurrence' and entry.get('component_selector') == wanted: 
            base = {**entry['plan'], 'resolved_from': 'neutral_interface_binding_cache', 'component_selector': wanted}
            break
    if base is None: 
        return None
    try: 
        from .legal_role_synthesis import legal_edge_samples
        from .semantic_projection import semantic_state_pairs
        oe = _load(root/'build/generated_certificates/oe_recurrence.json')
        idx = _load(root/'build/generated_certificates/shared_counter_index_projection.json')
        source = str(oe.get('decrement_count_source', ''))
        if idx.get('status')!='PASS' or str(idx.get('semantic_count_source', ''))!=source: 
            raise ValueError('shared-counter index projection does not cover OE decrement source')
        phases = sorted({str(r['phase']) for r in legal_edge_samples(root, str(oe['fall_event']))})
        if len(phases)!=1: 
            raise ValueError(f'OE fall source phase is not unique: {phases}')
        rel = idx.get('relations_by_phase', {}).get(phases[0])
        if not rel: 
            raise ValueError(f'no shared-counter index relation for OE source phase {phases[0]}')
        modulus = int(idx['modulus'])
        offset = int(rel['offset_mod'])
        # semantic_count = physical_count + offset (mod modulus).
        physical_zero = (-offset)%modulus
        pairs = semantic_state_pairs(root, str((idx.get('proof_anchor') or {}).get('shared_counter_semantic_sources', {}).get('increment_source', '')))
        if not pairs: 
            raise ValueError('physical shared-counter bits unavailable')
        terms = []
        for bit, q, qb in sorted(pairs): 
            terms.append(['VAR', q if ((physical_zero>>int(bit))&1) else qb])
        expr = terms[0] if len(terms) == 1 else ['AND', terms]
        base = {**base, 
              'count_zero': {'expression': expr, 'polarity': 'direct', 'semantic_source': source, 
                            'source_phase': phases[0], 'physical_value': physical_zero}, 
              'resolved_from': 'state/clock naming plan + proof-derived shared-counter count-zero projection'}
    except (FileNotFoundError, KeyError, ValueError): 
        # Migration fallback remains fail-safe while generic projection corpus is extended.
        pass
    return base


def resolve_event_interface_binding(root: Path) -> dict | None: 
    root = Path(root)
    wanted = event_semantic_selector(root)
    path = root/'technology'/'neutral_event_interface_binding_cache_v1.json'
    if path.exists(): 
        for entry in _load(path).get('entries', []): 
            if entry.get('semantic_selector') == wanted: 
                return {**entry['plan'], 'resolved_from': 'neutral_event_interface_binding_cache', 'semantic_selector': wanted}
    # Generic cache-miss derivation.  No polling phase, clock, or protocol net is
    # invented: event outputs are named from canonical semantic event IDs, and
    # qualified input edges use the technology-neutral one-delay history primitive.
    phase = _load(root/'build/semantic/phase40.ir.json')
    det = list(phase.get('scheduler_detectors', []))
    if not det: 
        return None
    supported = {'QUALIFIED_INPUT_EDGE', 'POLLING_PHASE_COMPLETION'}
    if any(str(d.get('kind')) not in supported for d in det): 
        return None
    qedges = [d for d in det if str(d.get('kind')) == 'QUALIFIED_INPUT_EDGE']
    # A falling polling completion needs the complemented physical input as a
    # true clock.  Derive its name from the project GPIO/core binding rather than
    # assuming protocol spellings such as SCL/SCL_N.
    from .semantic_projection import gpio_core_input_map
    bitnet = gpio_core_input_map(root)
    fall_clock_bits = sorted({int(d['input_bit']) for d in det
                            if str(d.get('kind')) == 'POLLING_PHASE_COMPLETION'
                            and str(d.get('edge', '')).upper() == 'FALL'})
    clock_complements = {str(b): str(bitnet[b])+'_n' for b in fall_clock_bits if b in bitnet}
    return {
      'version': 'bio2rtl-event-interface-binding-generic-v2', 
      'event_nets': {str(d['event_id']): 'evt_'+str(d['event_id']).lower() for d in det}, 
      'qualified_edge_delay': {'primitive_kind': 'delay_element', 'implementation_class': 'DEL4', 'stages': 1}, 
      'input_complements': {}, 'clock_complements': clock_complements, 
      'clock_inverter': {'primitive_kind': 'clock_inverter', 'implementation_class': 'X4'}, 
      'reset_groups': [], 
      'resolved_from': 'semantic_scheduler_descriptor_generic_fallback', 
      'semantic_selector': wanted, 
      'generic_cache_miss': True, 
    }


def resolve_shift_interface_binding(root: Path, *, include_roles: bool = True) -> dict | None: 
    root = Path(root)
    wanted = component_recipe_keys(root).get('shift_observation_quotient')
    path = root/'technology'/'neutral_shift_interface_binding_cache_v1.json'
    if not path.exists(): 
        return None
    base = None
    for entry in _load(path).get('entries', []): 
        if entry.get('component_selector') == wanted: 
            base = {**entry['plan'], 'resolved_from': 'neutral_shift_interface_binding_cache', 'component_selector': wanted}
            break
    if base is None: 
        return None
    if not include_roles: 
        return base
    # Replace the historical recovered-predicate clock-enable net with a freshly
    # synthesized legal-domain role whenever the proof is available.  Encoding,
    # state-bit names and primitive clock policy remain technology/interface choices;
    # the Boolean enable semantics come from the current semantic authority.
    try: 
        from .legal_role_synthesis import discover_shift_clock_role
        role = discover_shift_clock_role(root)
    except (FileNotFoundError, KeyError, ValueError): 
        role = None
    if role and role.get('status') == 'PASS' and not role.get('counterexamples'): 
        cg = dict(base.get('clock_gate', {}))
        cg['enable_expression'] = role['expression']
        cg.pop('enable', None)
        cg['enable_role'] = 'shift_clock_enable'
        cg['enable_role_certificate'] = 'build/generated_certificates/shift_clock_role.json'
        base = {**base, 'clock_gate': cg, 'resolved_from': 'encoding/interface plan + legal-domain shift-clock role'}
    return base


def resolve_shared_counter_interface_binding(root: Path, *, include_roles: bool = True) -> dict | None: 
    root = Path(root)
    wanted = component_recipe_keys(root).get('shared_counter_and_event_latch')
    path = root/'technology'/'neutral_shared_counter_interface_binding_cache_v1.json'
    if not path.exists(): 
        return None
    base = None
    for entry in _load(path).get('entries', []): 
        if entry.get('component_selector') == wanted: 
            base = {**entry['plan'], 'resolved_from': 'neutral_shared_counter_interface_binding_cache', 'component_selector': wanted}
            break
    if base is None: 
        return None
    if not include_roles: 
        return base
    try: 
        from .legal_role_synthesis import discover_shared_counter_increment_role
        from .proof_bitset import expr_vars
        role = discover_shared_counter_increment_role(root)
    except (FileNotFoundError, KeyError, ValueError): 
        role = None
    if role and role.get('status') == 'PASS' and not role.get('counterexamples'): 
        base = {**base, 
              'increment_enable_expression': role['physical_expression'], 
              'increment_enable_inputs': sorted(expr_vars(role['physical_expression'])), 
              'increment_role_certificate': 'build/generated_certificates/shared_counter_increment_role.json', 
              'resolved_from': 'counter/latch naming-retiming plan + legal-domain increment role'}
        base.pop('increment_enable', None)
    # Residual asynchronous latch-clear semantics are discovered from the semantic latch
    # recurrence rather than supplied as a recovered-predicate net name.
    try: 
        from .legal_role_synthesis import discover_event_latch_residual_clear_role
        clr = discover_event_latch_residual_clear_role(root)
    except (FileNotFoundError, KeyError, ValueError): 
        clr = None
    if clr and clr.get('status') == 'PASS' and not clr.get('counterexamples'): 
        base = {**base, 
              'residual_latch_clear_expression': clr['physical_expression'], 
              'residual_latch_clear_inputs': sorted(expr_vars(clr['physical_expression'])), 
              'residual_latch_clear_role_certificate': 'build/generated_certificates/event_latch_residual_clear_role.json', 
              'resolved_from': str(base.get('resolved_from', ''))+' + legal-domain residual latch-clear role'}
        base.pop('residual_latch_clear', None)
    return base


def resolve_direct_state_interface_binding(root: Path, *, include_roles: bool = True) -> dict | None: 
    root = Path(root)
    wanted = component_recipe_keys(root).get('direct_protocol_state')
    path = root/'technology'/'neutral_direct_state_interface_binding_cache_v1.json'
    if not path.exists(): 
        return None
    base = None
    for entry in _load(path).get('entries', []): 
        if entry.get('component_selector') == wanted: 
            base = {**entry['plan'], 'resolved_from': 'neutral_direct_state_interface_binding_cache', 'component_selector': wanted}
            break
    if base is None: 
        return None
    if not include_roles: 
        return base
    try: 
        from .legal_role_synthesis import discover_direct_state_advance_role
        from .proof_bitset import expr_vars
        role = discover_direct_state_advance_role(root)
    except (FileNotFoundError, KeyError, ValueError): 
        role = None
    if role and role.get('status') == 'PASS' and not role.get('counterexamples'): 
        base = {**base, 
              'advance_expression': role['physical_expression'], 
              'advance_inputs': sorted(expr_vars(role['physical_expression'])), 
              'advance_role_certificate': 'build/generated_certificates/direct_state_advance_role.json', 
              'resolved_from': 'state naming/clock plan + legal-domain direct-state advance role'}
    return base

def resolve_load_hold_bank_binding(root: Path) -> dict | None: 
    root = Path(root)
    wanted = component_recipe_keys(root).get('selector_snapshot_gpio_payload')
    # Primary path: derive recurrence predicates and load data from semantic/proof authority.
    try: 
        from .semantic_projection import discover_load_hold_binding
        auto = discover_load_hold_binding(root)
    except (FileNotFoundError, KeyError, ValueError): 
        auto = None
    if auto is not None: 
        return {**auto, 'component_selector': wanted}
    # Migration fallback only; kept until the generic semantic projection corpus is closed.
    path = root/'technology'/'neutral_load_hold_bank_binding_cache_v1.json'
    if not path.exists(): 
        return None
    for entry in _load(path).get('entries', []): 
        if entry.get('component_selector') == wanted: 
            return {**entry['plan'], 'resolved_from': 'neutral_load_hold_bank_binding_cache', 'component_selector': wanted}
    return None

def resolve_comb_output_binding(root: Path, component_class: str) -> dict | None: 
    root = Path(root)
    wanted = component_recipe_keys(root).get(component_class)
    if component_class == 'observation_tx_selector': 
        try: 
            from .observation_tx_discovery import discover_observation_tx_binding
            auto = discover_observation_tx_binding(root)
        except (FileNotFoundError, KeyError, ValueError): 
            auto = None
        if auto is not None: 
            return {**auto, 'component_selector': wanted}
    path = root/'technology'/'neutral_comb_output_binding_cache_v1.json'
    if not path.exists(): 
        return None
    for entry in _load(path).get('entries', []): 
        if entry.get('component_class') == component_class and entry.get('component_selector') == wanted: 
            return {**entry['plan'], 'resolved_from': 'neutral_comb_output_binding_cache', 'component_selector': wanted}
    return None


def discover_load_hold_bank_shapes(root: Path) -> dict: 
    """Discover load/hold state-bank *shape* from recovered architecture authority.

    This deliberately does not choose the final Boolean load/data expressions.  It removes
    the benchmark-specific requirement that the list of state banks, widths, semantic
    sources/bits, and clock domains be supplied by a binding cache.  Detailed recurrence
    binding remains a later proof/search step.
    """
    root = Path(root)
    arch = _load(root/'build/architecture_ir_v16_stage3_seedless.json')
    direct_cert = root/'build/generated_certificates/direct_state_recurrence.json'
    direct_source = None
    if direct_cert.exists(): 
        d = _load(direct_cert)
        if d.get('status') == 'PASS': 
            direct_source = str(d.get('semantic_source'))
    banks = []
    for x in arch.get('physical_state', []): 
        proof = str(x.get('proof', ''))
        src = x.get('semantic_source')
        # Direct modulo recurrence has its own lowering.  Natural storage and the explicit
        # observation snapshot are the generic load/hold candidates.
        if src is not None and str(src) == direct_source: 
            continue
        if proof not in ('phase_ir_natural_storage', 'observation_snapshot'): 
            continue
        bits = int(x.get('realization_dff_bits', x.get('bits', 0)))
        if bits<=0: 
            continue
        sb = x.get('semantic_bits')
        if sb is None: 
            sb = list(range(bits))
        sb = [int(v) for v in sb]
        if len(sb)!=bits: 
            raise ValueError(f'load/hold candidate {x.get("role")} semantic_bits width mismatch')
        banks.append({
          'architecture_role': str(x['role']), 
          'semantic_source': str(src), 
          'semantic_bits': sb, 
          'width': bits, 
          'clock_domain': str(x.get('clock_domain', '')), 
          'proof': proof, 
        })
    return {'version': 'bio2rtl-load-hold-bank-shape-discovery-v1', 'status': 'PASS', 'banks': banks, 
            'source': 'architecture recovery + proof kind; no interface binding cache'}
