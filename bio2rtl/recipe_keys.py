from __future__ import annotations
from pathlib import Path
import hashlib, json
try: 
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from .project_inputs import load_config


def _load(p: Path): 
    return json.loads(p.read_text())
def _h(obj) -> str: 
    return hashlib.sha256(json.dumps(obj, sort_keys = True, separators = (',', ':')).encode()).hexdigest()

def _sha(p: Path) -> str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()

def _normalized_storage_relation(cert: dict) -> dict: 
    g = cert['gpio_mirror_fusion']
    # Physical behavior depends on the number/width of payload mirrors and their
    # inversion polarity, not on semantic register spellings.
    rel = []
    for x in g.get('relations', []): 
        rel.append({'inverted': '= ~' in x or '=~' in x})
    return {
      'relation_polarities': sorted(rel, key = lambda x: x['inverted']), 
      'payload_width': len(g.get('payload_bits', [])), 
      'unique_solution': bool(g.get('unique_solution')), 
      'commit_event': g.get('commit_event'), 
    }

def _normalized_observation_local(cert: dict) -> list[dict]: 
    out = []
    for r in cert.get('results', []): 
        row = {'semantic_bit': r.get('semantic_bit'), 'classification': r.get('classification')}
        if 'formula' in r: 
            f = r['formula']; atom = f.get('atom', {}) if isinstance(f, dict) else {}
            row['formula_shape'] = {'kind': f.get('kind') if isinstance(f, dict) else None, 'value': atom.get('value')}
        out.append(row)
    return sorted(out, key = lambda x: (x.get('semantic_bit', -1), x.get('classification', '')))

def _normalized_canonical(cert: dict) -> dict: 
    r = cert['result']; f = r.get('formula', {})
    vals = []
    for side in ('left', 'right'): 
        a = f.get(side, {})
        if isinstance(a, dict): 
            vals.append({'kind': a.get('kind'), 'value': a.get('value')})
    return {'classification': r.get('classification'), 'formula_kind': f.get('kind'), 'atoms': sorted(vals, key = lambda x: (str(x['kind']), str(x['value'])))}


def semantic_control_shape(root: Path)->dict: 
    root = Path(root); g = root/'build/generated_certificates'; sem = root/'build/semantic'
    ctrl = _load(g/'control_factorization.json')
    return {
      'total_ff_bits': ctrl['total_ff_bits'], 'rise_bits': ctrl['rise_clocked_bits'], 'fall_bits': ctrl['fall_clocked_bits'], 
      'reset_code': ctrl['reset_code'], 
      'encoded_states': [{k: r[k] for k in ('state', 'rise_code', 'fall_code', 'reachable_phases')} for r in ctrl['state_rows']], 
      'phase_authority': _sha(sem/'phase40.ir.json'), 
    }



def event_semantic_shape(root: Path)->dict: 
    """Protocol-neutral scheduler-event shape used by event-detector lowering."""
    root = Path(root); phase = _load(root/'build/semantic/phase40.ir.json')
    det = []
    for d in phase.get('scheduler_detectors', []): 
        det.append({
          'event_id': str(d.get('event_id')), 'kind': str(d.get('kind')), 
          'edge': d.get('edge'), 'input_bit': d.get('input_bit'), 
          'qualifiers': sorted([{'source': str(q.get('source')), 'bit': q.get('bit'), 'level': int(q.get('level', 0))}
                               for q in d.get('qualifiers', [])], key = lambda x: (x['source'], x['bit'], x['level'])), 
          'phase_before': d.get('phase_before'), 'phase_after': d.get('phase_after'), 
        })
    det.sort(key = lambda x: (x['kind'], str(x['input_bit']), str(x['edge']), x['event_id']))
    return {'scheduler_detectors': det}

def event_semantic_selector(root: Path)->str: 
    return _h({'kind': 'scheduler-event-detector-semantic-shape-v1', 'shape': event_semantic_shape(root)})

def control_semantic_selector(root: Path)->str: 
    return _h({'kind': 'phase-factorized-control-semantic-shape-v1', 'shape': semantic_control_shape(root)})

def resolve_cached_control_encoding_plan(root: Path)->dict|None: 
    root = Path(root); p = root/'technology/control_encoding_plan_cache_v1.json'
    if not p.exists(): 
        return None
    d = _load(p); wanted = control_semantic_selector(root)
    for e in d.get('entries', []): 
        if e.get('semantic_selector') == wanted: 
            return e.get('plan')
    return None

def component_recipe_keys(root: Path) -> dict[str, str]: 
    """Return content-addressed selectors for every *applicable* optimized component.

    Generic Ver.1 treats optimization certificates as optional.  A missing shift,
    snapshot, OE, shared-counter, etc. proof therefore removes only the selector(s)
    that require that proof; it no longer makes the whole project un-keyable.
    When the complete historical I2C proof set is present, selectors are byte-for-byte
    identical to the pre-generic implementation.
    """
    root = Path(root); g = root/'build/generated_certificates'; sem = root/'build/semantic'; tech = root/'technology'
    _, project = load_config(root)
    def opt(name): 
        p = g/f'{name}.json'
        if not p.exists(): 
            return None
        try: 
            d = _load(p)
        except Exception: 
            return None
        if d.get('status') not in (None, 'PASS'): 
            return None
        if d.get('counterexamples'): 
            return None
        return d
    cert = {n: opt(n) for n in [
        'control_factorization', 'shift_quotient', 'shared_counter', 'shared_counter_sync_exit', 
        'shared_counter_terminal_hold', 'observation_snapshot', 'oe_recurrence', 'storage_relations', 
        'canonical_elimination', 'observation_local_bit_elimination']}
    policy = _load(tech/'physical_realization_policy.json') if (tech/'physical_realization_policy.json').exists() else {'policies': []}
    if (g/'control_factorization.json').exists(): 
        control_encoding_plan = resolve_cached_control_encoding_plan(root)
    else: 
        control_encoding_plan = None
    if control_encoding_plan is None: 
        control_encoding_plan = {'version': 'missing-control-encoding-plan'}
    if not (sem/'phase40.ir.json').exists(): 
        return {}
    phase_sha = _sha(sem/'phase40.ir.json'); common = {'phase40_sha256': phase_sha, 'clock_mode': project.get('clock', {}).get('mode')}
    selectors = {}

    # Scheduler event support is semantic-authority based and independent of the
    # optimization certificates below.
    selectors['event_detectors'] = {'event_shape': event_semantic_shape(root), 'clock_mode': common['clock_mode']}

    # Startup-only quiescent programs may collapse all GPIO state to constants.
    # This selector is semantic-authority based and introduces no clock/state.
    try: 
        lp = _load(sem/'legal_product.json')
    except Exception: 
        lp = {}
    phase_for_const = _load(sem/'phase40.ir.json')
    const_gpio = []
    for row in phase_for_const.get('storage_optimization', {}).get('register_storage', []): 
        if row.get('kind') == 'GPIO' and row.get('storage_kind') == 'CONST': 
            const_gpio.append({'register': str(row.get('register')), 'constant_value': int(row.get('constant_value', 0)), 
                               'semantic_width': int(row.get('semantic_width', 32))})
    if const_gpio: 
        selectors['static_quiescent_gpio'] = {'phase_authority': phase_sha, 'constant_gpio': sorted(const_gpio, key = lambda x: x['register'])}

    # Conservative natural EVENT_ONLY storage is itself a first-class semantic
    # component.  Its selector is derived solely from the recovered architecture
    # state/update-domain shape and current phase authority.
    ap = root/'build/architecture_ir_v16_stage3_seedless.json'
    if ap.exists(): 
        arch = _load(ap)
        natural = []
        for x in arch.get('physical_state', []): 
            if x.get('proof') == 'phase_ir_natural_storage' and x.get('clock_domain') == 'EVENT_ONLY': 
                natural.append({
                  'semantic_source': x.get('semantic_source'), 
                  'semantic_bits': list(map(int, x.get('semantic_bits', []))), 
                  'bits': int(x.get('bits', 0)), 
                  'active_event_classes': sorted(map(str, (x.get('natural_update_domain') or {}).get('active_event_classes', []))), 
                })
        if natural: 
            selectors['natural_event_state'] = {'phase_authority': phase_sha, 'states': sorted(natural, key = lambda x: (str(x['semantic_source']), x['semantic_bits']))}
        async_latches = []
        for x in arch.get('non_dff_state', []): 
            if x.get('proof') in {'phase_ir_detector_free_async_recurrence', 'phase_ir_event_free_hidden_state_exact_quotient'}: 
                async_latches.append({'semantic_source': x.get('semantic_source'), 'semantic_bits': list(map(int, x.get('semantic_bits', []))), 
                                      'async_recurrence': x.get('async_recurrence')})
        if async_latches: 
            selectors['natural_async_latch'] = {'phase_authority': phase_sha, 'latches': sorted(async_latches, key = lambda x: (str(x['semantic_source']), x['semantic_bits']))}

    ctrl = cert['control_factorization']
    control_shape = None
    if ctrl is not None: 
        control_shape = {
          'total_ff_bits': ctrl['total_ff_bits'], 'rise_bits': ctrl['rise_clocked_bits'], 'fall_bits': ctrl['fall_clocked_bits'], 
          'reset_code': ctrl['reset_code'], 
          'encoded_states': [{k: r[k] for k in ('state', 'rise_code', 'fall_code', 'reachable_phases')} for r in ctrl['state_rows']], 
        }
        selectors['phase_factorized_control'] = {'control_shape': control_shape, 'phase_authority': phase_sha, 'physical_encoding_plan': control_encoding_plan}

    sc = cert['shared_counter']; counter_shape = None
    if sc is not None and cert['shared_counter_sync_exit'] is not None and cert['shared_counter_terminal_hold'] is not None: 
        counter_shape = {
          'width': sc['counter_width'], 'max_value': sc['max_value'], 'terminal_value': sc['terminal_value'], 
          'operation_counts': sc['operation_counts'], 
          'sync_exit': {k: cert['shared_counter_sync_exit'].get(k) for k in ('tx_exit_low_class_phase', 'operation_counts')}, 
          'terminal_hold': {k: cert['shared_counter_terminal_hold'].get(k) for k in ('mismatch_semantic_edges', 'terminal_edge_groups', 'physical_consequence')}, 
        }
        selectors['shared_counter_and_event_latch'] = {'counter_shape': counter_shape, 'physical_policy': policy.get('policies', []), 'control_encoding_plan': control_encoding_plan}

    rx = cert['shift_quotient']
    if rx is not None: 
        shift_shape = {
          'semantic_width': rx['semantic_width'], 'quotient_state_count': rx['quotient_state_count'], 
          'transition': rx['shift_transition'], 'synchronizing_depth': rx['synchronizing_depth'], 
          'all_observations_terminal_qualified': rx['all_observations_terminal_qualified'], 
        }
        selectors['shift_observation_quotient'] = {'shift_shape': shift_shape}

    snap = cert['observation_snapshot']; snapshot_shape = None
    if snap is not None: 
        snapshot_shape = {
          'selector_values': snap['selector_values'], 'snapshot_bits': snap['snapshot_bits'], 
          'loads': snap['load_edges_by_selector'], 'tx_read_states': snap['tx_read_states'], 
          'claims': snap['claims'], 
        }
    storage_shape = _normalized_storage_relation(cert['storage_relations']) if cert['storage_relations'] is not None else None
    canonical_shape = _normalized_canonical(cert['canonical_elimination']) if cert['canonical_elimination'] is not None else None
    observation_local_shape = _normalized_observation_local(cert['observation_local_bit_elimination']) if cert['observation_local_bit_elimination'] is not None else None

    # Residual predicate/direct-state selectors can exist with only the phase authority;
    # include optional proof shapes exactly when available.  Historical full-proof
    # projects retain the exact old selector objects/hashes.
    if canonical_shape is not None and observation_local_shape is not None: 
        selectors['recovered_predicate_network'] = {'phase_authority': phase_sha, 'canonical': canonical_shape, 'observation_local': observation_local_shape, 'control_encoding_plan': control_encoding_plan}
    if canonical_shape is not None: 
        selectors['direct_protocol_state'] = {'phase_authority': phase_sha, 'canonical': canonical_shape}
    if snapshot_shape is not None and storage_shape is not None: 
        selectors['selector_snapshot_gpio_payload'] = {'phase_authority': phase_sha, 'snapshot': snapshot_shape, 'storage': storage_shape}
    if snapshot_shape is not None and storage_shape is not None and observation_local_shape is not None: 
        selectors['observation_tx_selector'] = {'snapshot': snapshot_shape, 'storage': storage_shape, 'observation_local': observation_local_shape}
    oe = cert['oe_recurrence']
    if oe is not None and sc is not None: 
        selectors['open_drain_oe_recurrence'] = {'natural_recurrence': oe['natural_recurrence'], 'truth_table': oe['truth_table_by_control_state'], 'counter_width': sc['counter_width'], 'control_encoding_plan': control_encoding_plan}
    return {k: _h({'component_class': k, 'selector': v}) for k, v in selectors.items()}

