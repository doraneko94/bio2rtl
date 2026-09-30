from __future__ import annotations
from pathlib import Path
import json


def _load(p: Path): 
    return json.loads(Path(p).read_text())
def _pass(p: Path)->bool: 
    try: 
        d = _load(p)
        return d.get('status') == 'PASS' and not d.get('counterexamples')
    except FileNotFoundError: 
        return False


def build_semantic_component_manifest(root: Path)->dict: 
    """Discover which proof-backed generic lowering components are applicable.

    The manifest is optional-pass based: absence of one optimization does not require
    the other component kinds to exist.  It also audits that every optimized physical
    state selected by architecture recovery is claimed exactly once by a lowering
    component.  Component generator names describe generic transform classes, not a
    protocol.
    """
    root = Path(root)
    b = root/'build'
    g = b/'generated_certificates'
    arch = _load(b/'architecture_ir_v16_stage3_seedless.json')
    phase = _load(b/'semantic/phase40.ir.json')
    physical = list(arch.get('physical_state', []))
    nondff = list(arch.get('non_dff_state', []))
    components = []
    def add(name, generator, roles = (), proofs = (), non_dff_roles = (), reason = None): 
        components.append({'component_class': name, 'generator': generator, 
                           'claims_physical_roles': sorted(set(map(str, roles))), 
                           'claims_proofs': sorted(set(map(str, proofs))), 
                           'claims_non_dff_roles': sorted(set(map(str, non_dff_roles))), 
                           'discovery_reason': reason or generator})

    # Scheduler event support is present only if semantic authority recovered detector
    # descriptors; no protocol/event ID is required by this discovery decision.
    if phase.get('scheduler_detectors'): 
        add('event_detectors', 'scheduler_event_detector', reason = 'phase40.scheduler_detectors non-empty')

    try: 
        legal = _load(b/'semantic/legal_product.json')
    except FileNotFoundError: 
        legal = {}
    const_gpio = [x for x in phase.get('storage_optimization', {}).get('register_storage', [])
                if x.get('kind') == 'GPIO' and x.get('storage_kind') == 'CONST']
    if const_gpio: 
        add('static_quiescent_gpio', 'static_quiescent_gpio', 
            reason = 'phase40 storage proof establishes constant GPIO register value')

    natural_event_roles = [str(x['role']) for x in physical
                         if x.get('proof') == 'phase_ir_natural_storage' and x.get('clock_domain') == 'EVENT_ONLY']
    if natural_event_roles: 
        add('natural_event_state', 'natural_event_state', roles = natural_event_roles, 
            reason = 'unoptimized phase_ir natural storage with canonical EVENT_ONLY update domain')

    natural_async_proofs = {'phase_ir_detector_free_async_recurrence', 'phase_ir_event_free_hidden_state_exact_quotient'}
    natural_async_roles = [str(x['role']) for x in nondff if x.get('proof') in natural_async_proofs]
    if natural_async_roles: 
        add('natural_async_latch', 'natural_async_latch', non_dff_roles = natural_async_roles, 
            reason = 'detector-free input-reactive one-bit recurrence proved SET/RESET/HOLD with no TOGGLE')

    if _pass(g/'control_factorization.json'): 
        roles = [x['role'] for x in physical if x.get('proof') == 'control_factorization']
        if roles: 
            add('phase_factorized_control', 'factorized_control', roles = roles, proofs = ['control_factorization'])

    # Predicate and observation Boolean DAGs are architecture-interface components.
    # Their content is still discovered through the current content-addressed binding
    # resolver; the manifest merely makes their presence optional rather than fixed.
    try: 
        from .neutral_bindings import resolve_comb_output_binding
        if resolve_comb_output_binding(root, 'recovered_predicate_network'): 
            add('recovered_predicate_network', 'bound_boolean_dag')
        if resolve_comb_output_binding(root, 'observation_tx_selector'): 
            add('observation_tx_selector', 'bound_boolean_dag')
    except (FileNotFoundError, ValueError, KeyError): 
        pass

    if _pass(g/'shared_counter.json'): 
        roles = [x['role'] for x in physical if x.get('proof') == 'shared_counter']
        nr = [x['role'] for x in nondff if x.get('implementation_class') == 'cross_coupled_latch_candidate']
        if roles: 
            add('shared_counter_and_event_latch', 'shared_counter_latch', roles = roles, proofs = ['shared_counter'], non_dff_roles = nr)

    if _pass(g/'shift_quotient.json'): 
        roles = [x['role'] for x in physical if x.get('proof') == 'shift_quotient']
        if roles: 
            add('shift_observation_quotient', 'quotient_state_bank', roles = roles, proofs = ['shift_quotient'])

    # Direct-state recurrence certificate names the semantic source, so discovery is
    # independent of a benchmark role string.
    dp = g/'direct_state_recurrence.json'
    if _pass(dp): 
        cert = _load(dp)
        src = str(cert.get('semantic_source'))
        roles = [x['role'] for x in physical if str(x.get('semantic_source')) == src]
        if roles: 
            add('direct_protocol_state', 'modulo_state_recurrence', roles = roles, proofs = ['direct_state_recurrence'])

    try: 
        from .neutral_bindings import discover_load_hold_bank_shapes
        shape = discover_load_hold_bank_shapes(root)
    except (FileNotFoundError, ValueError, KeyError): 
        shape = None
    if shape and shape.get('banks'): 
        # EVENT_ONLY natural storage has its own canonical-event lowering and must
        # never be reinterpreted as a normal load/hold bank.
        banks = [x for x in shape['banks'] if str(x.get('clock_domain'))!='EVENT_ONLY'
               and str(x.get('architecture_role')) not in set(natural_event_roles)]
        roles = [str(x['architecture_role']) for x in banks]
        if roles: 
            proofs = sorted({str(x.get('proof')) for x in physical if x.get('role') in roles and x.get('proof')})
            add('selector_snapshot_gpio_payload', 'load_hold_banks', roles = roles, proofs = proofs, reason = 'architecture/proof-discovered load-hold bank shapes')

    if _pass(g/'oe_recurrence.json'): 
        roles = [x['role'] for x in physical if x.get('proof') == 'oe_recurrence']
        if roles: 
            add('open_drain_oe_recurrence', 'single_state_recurrence', roles = roles, proofs = ['oe_recurrence'])

    # Audit coverage. A physical/non-DFF state may not be silently lost merely because
    # an optimization-specific generator is N/A.
    claims = {}
    ndclaims = {}
    for c in components: 
        for r in c['claims_physical_roles']: 
            claims.setdefault(r, []).append(c['component_class'])
        for r in c['claims_non_dff_roles']: 
            ndclaims.setdefault(r, []).append(c['component_class'])
    proles = [str(x.get('role')) for x in physical]
    nroles = [str(x.get('role')) for x in nondff]
    uncovered = [r for r in proles if r not in claims]
    duplicated = {r: v for r, v in claims.items() if len(v)!=1}
    nd_uncovered = [r for r in nroles if r not in ndclaims]
    nd_duplicated = {r: v for r, v in ndclaims.items() if len(v)!=1}
    status = 'PASS' if not (uncovered or duplicated or nd_uncovered or nd_duplicated) else 'NEEDS_GENERIC_FALLBACK'
    return {
      'version': 'bio2rtl-semantic-component-manifest-v1', 'status': status, 
      'components': components, 
      'coverage': {'physical_roles': proles, 'claimed': claims, 'uncovered': uncovered, 'duplicated': duplicated, 
                  'non_dff_roles': nroles, 'non_dff_claimed': ndclaims, 'non_dff_uncovered': nd_uncovered, 'non_dff_duplicated': nd_duplicated}, 
      'fixed_component_count_required': False, 
      'protocol_names_used_for_discovery': False, 
    }


def emit_semantic_component_manifest(root: Path)->dict: 
    root = Path(root)
    d = build_semantic_component_manifest(root)
    p = root/'build/semantic_component_manifest.json'
    p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    return d
