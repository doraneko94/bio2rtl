from __future__ import annotations

"""Attach a storage plan justified by scalable FSE Cartesian abstraction."""

from copy import deepcopy
from math import ceil, log2
from typing import Any


def _info_width(values: list[int]) -> int: 
    return 0 if len(values) <= 1 else int(ceil(log2(len(values))))


def annotate_abstract_storage(ir: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]: 
    out = deepcopy(ir)
    rows = {str(x['state']): x for x in analysis['state_rows']}
    gpio_rows = {str(x['register']): x for x in analysis['packed_gpio_state']}

    register_storage: list[dict[str, Any]] = []
    for reg in out['architectural_registers']: 
        rid = str(reg['id'])
        kind = str(reg['kind'])
        semw = int(reg['width'])
        base = {
            'register': rid, 
            'kind': kind, 
            'provenance': reg['provenance'], 
            'semantic_width': semw, 
        }
        if kind == 'GPIO': 
            g = gpio_rows[rid]
            bits = int(g['storage_bits'])
            vals = [int(v) for v in g['reachable_masked_values']]
            base.update({
                'proof': 'MATERIALIZED_GPIO_RULE_NONDETERMINISTIC_OVERAPPROX', 
                'mask': int(g['mask']), 
                'reachable_values': vals, 
                'constant_zero_bits': list(g['constant_zero_bits']), 
                'constant_one_bits': list(g['constant_one_bits']), 
                'materialized_rules': int(g['materialized_rules']), 
            })
            if bits == 0: 
                base.update({'storage_kind': 'CONST', 'storage_bits': 0, 'constant_value': int(vals[0]) if vals else int(g['reset_masked'])})
            else: 
                base.update({'storage_kind': 'PACKED_MASK_BITS', 'storage_bits': bits, 'stored_bits': list(g['variable_bits']), 'equal_bit_groups_candidate': []})
            register_storage.append(base)
            continue

        prov = str(reg['provenance'])
        if prov not in rows: 
            raise KeyError(f'missing abstract storage row for {rid} provenance={prov}')
        r = rows[prov]
        vals = [int(v) for v in r['reachable_values_upper_bound']]
        nw = int(r['natural_width'])
        base.update({
            'proof': 'FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1', 
            'reachable_values_upper_bound': vals, 
            'recurrence_classes': dict(r.get('recurrence_classes', {})), 
        })
        if nw == 0: 
            base.update({'storage_kind': 'CONST', 'storage_bits': 0, 'constant_value': int(vals[0])})
        elif nw < semw: 
            base.update({'storage_kind': 'NARROW_ZERO_EXTEND', 'storage_bits': nw})
        else: 
            base.update({'storage_kind': 'DIRECT', 'storage_bits': semw})
        info = _info_width(vals)
        if nw and info < nw: 
            base['sparse_encoding_candidate'] = {
                'storage_bits': info, 
                'values': vals, 
                'automatic': False, 
                'reason': 'candidate only; encoding/decoder cost not proven area-positive', 
            }
        register_storage.append(base)

    scheduler_storage: list[dict[str, Any]] = []
    for sched in out.get('scheduler_owned_sources', []): 
        source = str(sched['source'])
        r = rows[source]
        nw = int(r['natural_width'])
        vals = [int(v) for v in r['reachable_values_upper_bound']]
        scheduler_storage.append({
            'scheduler': str(sched['id']), 'source': source, 'role': sched['role'], 
            'storage_kind': 'CONST' if nw == 0 else 'DIRECT', 'storage_bits': nw, 
            'reachable_values_upper_bound': vals, 'constant_value': int(vals[0]) if nw == 0 else None, 
            'proof': 'FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1', 
        })

    mask = int(out['startup']['gpio_mask_constant'])
    mask_bits = sum(1 for i in range(32) if (mask>>i)&1)
    gpio_regs = sum(1 for r in out['architectural_registers'] if r['kind'] == 'GPIO')
    non_gpio = sum(int(r['width']) for r in out['architectural_registers'] if r['kind']!='GPIO')
    scheduler_pre = sum(int(s.get('width', 1)) for s in out.get('scheduler_owned_sources', []))
    pre = non_gpio + mask_bits*gpio_regs + scheduler_pre + 1
    optimized = sum(int(x['storage_bits']) for x in register_storage) + sum(int(x['storage_bits']) for x in scheduler_storage) + 1

    expected = int(analysis['storage_summary']['total_natural_bits'])
    if optimized != expected: 
        raise ValueError(f'annotation storage total {optimized} != analysis total {expected}')

    out['storage_optimization'] = {
        'version': 3, 
        'proof_model': 'FSE_PER_REGISTER_CARTESIAN_OVERAPPROX_V1', 
        'semantic_relation_preserved': True, 
        'safe_storage_proof': True, 
        'source_transitions': int(analysis['source_transitions']), 
        'analysis_iterations': int(analysis['iterations']), 
        'final_potential_transition_rows': int(analysis['final_potential_transition_rows']), 
        'expression_full_domain_fallbacks': int(analysis['expression_full_domain_fallbacks']), 
        'preoptimization_storage_upper_bound_bits': pre, 
        'natural_storage_bits': optimized, 
        'active_run_storage_bits': 1, 
        'register_storage': register_storage, 
        'scheduler_storage': scheduler_storage, 
        'policy': {
            'drop_constant_state': True, 
            'natural_width_narrowing': True, 
            'gpio_rule_overapproximation': True, 
            'scheduler_reachability_used_for_elimination': False, 
            'exact_joint_reachability_required': False, 
            'cross_state_guard_correlations_used_for_elimination': False, 
            'sparse_value_encoding': False, 
            'equal_bit_coalescing': False, 
            'derived_state_elimination': False, 
            'joint_fsm_reencoding': False, 
        }, 
        'notes': [
            'The Dedicated Event architectural relation and guard-minimized rule set are not rewritten by this annotation.', 
            'Non-GPIO narrowing is justified by a monotone per-register Cartesian over-approximation of all feasibility-proven source transitions.', 
            'Unsupported state correlations are ignored and therefore can only widen abstract domains.', 
            'GPIO packed-bit storage is justified independently by nondeterministic closure over all materialized GPIO update rules.', 
            'No scheduler reachability, protocol identity, BB identity, transition ID, sparse recoding, or joint FSM recoding is used for elimination.', 
        ], 
    }
    return out
