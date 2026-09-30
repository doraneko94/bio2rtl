from __future__ import annotations

"""Attach a by-construction conservative storage plan to Dedicated Event IR.

This pass performs no event-boundary reachability minimization.  Non-GPIO
architectural state keeps its declared width.  GPIO state keeps every bit in the
specialized steady-state GPIO mask.  Scheduler bits and active_run are retained.
It exists as a functional-validation baseline before any state minimization.
"""

from copy import deepcopy
from typing import Any


def annotate_conservative_storage(ir: dict[str, Any]) -> dict[str, Any]: 
    out = deepcopy(ir)
    mask = int(out['startup']['gpio_mask_constant']) & 0xffffffff
    mask_bits = [i for i in range(32) if (mask>>i)&1]
    register_storage = []
    for reg in out['architectural_registers']: 
        rid = str(reg['id'])
        kind = str(reg['kind'])
        width = int(reg['width'])
        row = {'register': rid, 'kind': kind, 'provenance': reg['provenance'], 'semantic_width': width, 
             'proof': 'DECLARED_WIDTH_CONSERVATIVE_BASELINE'}
        if kind == 'GPIO': 
            row.update({'storage_kind': 'PACKED_MASK_BITS', 'storage_bits': len(mask_bits), 
                        'stored_bits': list(mask_bits), 'mask': mask, 
                        'constant_zero_bits': [], 'constant_one_bits': [], 
                        'equal_bit_groups_candidate': []})
        else: 
            row.update({'storage_kind': 'DIRECT', 'storage_bits': width})
        register_storage.append(row)
    scheduler_storage = []
    for s in out.get('scheduler_owned_sources', []): 
        scheduler_storage.append({'scheduler': s['id'], 'source': s['source'], 'role': s['role'], 
                                  'storage_kind': 'DIRECT', 'storage_bits': 1, 
                                  'proof': 'SCHEDULER_DECLARED_BIT_CONSERVATIVE_BASELINE'})
    active = 1
    total = sum(r['storage_bits'] for r in register_storage)+sum(s['storage_bits'] for s in scheduler_storage)+active
    out['storage_optimization'] = {
      'version': 3, 'semantic_relation_preserved': True, 'safe_storage_proof': True, 
      'proof_mode': 'CONSERVATIVE_DECLARED_WIDTH_BASELINE', 
      'source_transitions': int(out.get('verification', {}).get('source_transitions', 0)), 
      'preoptimization_storage_upper_bound_bits': total, 'natural_storage_bits': total, 
      'active_run_storage_bits': active, 'register_storage': register_storage, 'scheduler_storage': scheduler_storage, 
      'unreachable_transition_ids': [], 
      'policy': {'drop_constant_state': False, 'natural_width_narrowing': False, 'gpio_rule_overapproximation': False, 
                'dedicated_reachability_used_for_elimination': False, 'sparse_value_encoding': False, 
                'equal_bit_coalescing': False, 'derived_state_elimination': False, 'joint_fsm_reencoding': False}, 
      'notes': ['No architectural or scheduler state is eliminated or narrowed.', 
               'GPIO storage packs only the bits selected by the proven steady-state GPIO mask specialization.', 
               'Use this baseline for directed RTL validation before applying event-boundary state minimization.']}
    return out
