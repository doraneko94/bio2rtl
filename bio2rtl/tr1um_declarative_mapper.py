#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import json, os, re
try: 
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib
from .physical_ir import dump as dump_ir, emit_structural_sv, histogram, component_summary
from .recipe_keys import component_recipe_keys
from .boolean_contract import map_boolean_sequential_contract, map_neutral_component_contract
from .semantic_contracts import generate_semantic_neutral_contracts, resolve_control_encoding_plan
from .project_inputs import load_config
from .neutral_graph import emit_neutral_component_graph
from .global_semantic_mapper import map_global_semantic_core, collect_semantic_contracts, connectivity_audit
from .semantic_liveness import analyze_semantic_liveness, prune_contract_for_liveness, prune_contracts_for_liveness
from .global_semantic_proof import verify_global_semantic_map

ROOT = Path(__file__).resolve().parents[1]

def map_project(root: Path = ROOT, recipe_cache_path: Path|None = None, neutral_cache_path: Path|None = None, dhir_path: Path|None = None): 
    root = Path(root)
    build = root/'build'
    tech = root/'technology'
    dhir = json.load(open(Path(dhir_path) if dhir_path is not None else build/'architecture_ir_v16_stage3_seedless.json'))
    _, project = load_config(root)
    module = re.sub(r'[^A-Za-z0-9_]', '_', project['name'])
    # Phase-split natural state means an optional architecture proof was unavailable.
    # Branch *before* semantic component-contract generation, because those optimized
    # projections are intentionally absent in this case.  The fallback reconstructs a
    # conservative architecture directly from verified Phase40 using only real events.
    if any(bool(x.get('requires_phase_split_realization')) for x in dhir.get('physical_state', [])): 
        from .no_cert_phase40_mapper import map_no_cert_phase40_fallback
        source = Path(dhir_path) if dhir_path is not None else build/'architecture_ir_v16_stage3_seedless.json'
        return map_no_cert_phase40_fallback(root, source, module, f'{module}.structural.v')

    recipe_cache_path = Path(recipe_cache_path or os.environ.get('BIO2RTL_RECIPE_CACHE', tech/'tr1um_component_recipe_cache_v1.json'))
    neutral_cache_path = Path(neutral_cache_path or os.environ.get('BIO2RTL_NEUTRAL_CONTRACT_CACHE', tech/'neutral_component_contract_cache_v1.json'))
    cache = json.load(open(recipe_cache_path))
    neutral = json.load(open(neutral_cache_path)) if neutral_cache_path.exists() else {'contracts': {}}
    # Fresh semantic/proof-derived contracts are generated before connectivity: the
    # neutral component graph is derived from their producer/consumer interface plus
    # declarative physical-support TOML, not from a benchmark graph cache.
    keys = component_recipe_keys(root)
    control_encoding_plan = resolve_control_encoding_plan(root, cache, neutral, keys)
    generate_semantic_neutral_contracts(root, control_encoding_plan)
    graph = emit_neutral_component_graph(root)
    sem_neutral = {}
    semdir = build / 'semantic_neutral_contracts'
    if semdir.exists():
        for path in semdir.glob('*.json'):
            contract = json.load(open(path))
            sem_neutral[contract['component_class']] = contract

    raw_contracts = collect_semantic_contracts(root)
    semantic_liveness = analyze_semantic_liveness(raw_contracts, graph)
    (build / 'SEMANTIC_LIVENESS.json').write_text(
        json.dumps(semantic_liveness, indent = 2, sort_keys = True) + '\n'
    )
    live_contracts = prune_contracts_for_liveness(raw_contracts, semantic_liveness)
    live_by_component = {str(contract['component_class']): contract for contract in live_contracts}

    area_table = json.load(open(tech/'tr1um_cell_area.json'))['cell_area_um2']
    primitive_library = json.load(open(tech/'neutral_primitive_recipes_v1.json')) if (tech/'neutral_primitive_recipes_v1.json').exists() else {'primitives': {}}
    misses = []
    fallback = []
    selections = []
    cells = []
    recipe_interface_rejections = []
    for ci, comp in enumerate(graph['component_order']): 
        rec = cache.get('recipes', {}).get(comp)
        key = keys.get(comp)
        raw_contract = sem_neutral.get(comp) or neutral.get('contracts', {}).get(comp)
        contract = live_by_component.get(comp)
        if contract is None and raw_contract is not None:
            contract = prune_contract_for_liveness(raw_contract, semantic_liveness)
        candidates = []
        if rec is not None and key == rec.get('selector_sha256'): 
            # A cached physical implementation is admissible only when it does not depend on
            # historical helper nets that the current semantic component contract no longer
            # exposes.  This prevents a dead legacy predicate component from being implicitly
            # resurrected through hidden recipe dependencies.
            contract_inputs = set(map(str, (contract or {}).get('interface_inputs', [])))
            recipe_inputs = set(map(str, rec.get('interface_inputs', [])))
            hidden_recipe_inputs = sorted(recipe_inputs-contract_inputs) if contract is not None else []
            if hidden_recipe_inputs: 
                recipe_interface_rejections.append({'component': comp, 'hidden_recipe_inputs': hidden_recipe_inputs})
            if not hidden_recipe_inputs: 
                rcells = rec['cells']
                candidates.append(('physical_recipe', rcells, sum(area_table[c['type']] for c in rcells), None))
        if contract is not None and key == contract.get('selector_sha256'): 
            if contract.get('primitives') or contract.get('latches'): 
                fc = map_neutral_component_contract(contract, area_table, primitive_library, component = comp, name_prefix = f'fb{ci:02d}')
            else: 
                fc = map_boolean_sequential_contract(contract, area_table, component = comp, name_prefix = f'fb{ci:02d}')
            candidates.append(('semantic_generated' if comp in sem_neutral else 'bootstrap_cache', fc, 
                               sum(area_table[c['type']] for c in fc), contract))
        if not candidates: 
            misses.append({'component': comp, 'wanted': key, 'cached': rec.get('selector_sha256') if rec else None, 
                           'neutral': contract.get('selector_sha256') if contract else None})
            continue
        # Technology-aware choice: among semantically matching candidates, select the
        # smallest exact TR-1um cell-bbox-sum implementation.  Tie-break toward the
        # physical recipe to preserve stable connectivity when there is no area gain.
        rank = {'physical_recipe': 0, 'semantic_generated': 1, 'bootstrap_cache': 2}
        source, chosen, chosen_area, _ = min(candidates, key = lambda x: (x[2], rank[x[0]]))
        cells.extend(chosen)
        selections.append({'component': comp, 'selector_sha256': key, 'source': source, 'cells': len(chosen), 
                           'area_um2': chosen_area, 'candidate_areas_um2': {x[0]: x[2] for x in candidates}})
        if source!='physical_recipe': 
            fallback.append({'component': comp, 'selector_sha256': key, 'cells': len(chosen), 
                             'area_um2': chosen_area, 
                             'reason': 'semantic/neutral contract maps to a smaller or uniquely available exact implementation', 
                             'contract_source': source})
    if misses: 
        raise SystemExit('TR-1um component mapping unavailable after generic fallback: '+json.dumps(misses, sort_keys = True))
    # Keep component-local contracts as the proof/audit decomposition, but allow technology
    # mapping to share Boolean DAG nodes globally across component boundaries.  A global
    # candidate is admissible only after it is independently re-extracted and proven equal
    # to the merged neutral authority; otherwise the local mapping remains the safe fallback.
    local_cells = list(cells)
    local_area = sum(float(area_table[c['type']]) for c in local_cells)
    global_candidate = map_global_semantic_core(
        root,
        graph,
        area_table,
        primitive_library,
        contracts = raw_contracts,
        liveness = semantic_liveness,
    )
    global_candidate_path = build/'global_semantic_map_candidate.json'
    global_candidate_path.write_text(json.dumps(global_candidate, indent = 2, sort_keys = True)+'\n')
    global_proof = verify_global_semantic_map(root, global_candidate_path)
    global_admissible = (global_candidate.get('status') == 'PASS' and global_proof.get('status') == 'PASS')
    use_global = bool(global_admissible and float(global_candidate['area_um2']) < local_area)
    if use_global:
        cells = list(global_candidate['cells'])
        mapping_mode = 'global_semantic_neutral_dag'
    else:
        cells = local_cells
        mapping_mode = 'declarative_component_recipe_cache+neutral_boolean_fallback'

    expected_inputs = sorted(str(port['name']) for port in graph.get('ports', []) if port.get('direction') == 'input')
    required_outputs = sorted(str(port['name']) for port in graph.get('ports', []) if port.get('direction') == 'output')
    selected_connectivity_audit = connectivity_audit(
        cells,
        expected_inputs,
        required_outputs,
        graph.get('assignments', []),
    )
    if selected_connectivity_audit.get('status') != 'PASS':
        raise SystemExit(
            'selected physical mapping connectivity/dead-cone audit failed: ' +
            json.dumps(selected_connectivity_audit, sort_keys = True)
        )

    ir = {
     'version': 'bio2rtl-physical-dhir-v1', 'module': module, 'timescale': cache.get('timescale', '`timescale 1ns/1ps'), 
     'source_architecture_dhir_version': dhir['version'], 'technology': 'TR-1um', 'ports': graph['ports'], 
     'cells': cells, 'assignments': graph['assignments'], 
     'mapping_mode': mapping_mode, 'component_recipe_keys': keys, 
     'control_encoding_plan': control_encoding_plan, 
     'trusted_sv_read_by_mapper': False, 
     'global_semantic_connectivity_audit': global_candidate.get('connectivity_audit') if use_global else None,
     'selected_connectivity_audit': selected_connectivity_audit,
     'semantic_liveness': semantic_liveness,
     'global_semantic_mapping': {
       'admissible': global_admissible, 'selected': use_global, 
       'candidate_cells': int(global_candidate['cell_count']), 'candidate_area_um2': float(global_candidate['area_um2']), 
       'local_cells': len(local_cells), 'local_area_um2': local_area, 
       'proof_status': global_proof.get('status'), 'truth_checks': int(global_proof.get('truth_checks', 0)), 
       'proof_file': 'GLOBAL_SEMANTIC_MAP_PROOF.json', 'candidate_file': global_candidate_path.name, 
     }, 
    }
    out = build/'physical_dhir_v18_stage7.json'
    dump_ir(ir, out)
    # Independently prove the actually selected physical cell list, not only the
    # optional global optimization candidate.  This covers both local fallback and
    # global selection paths and is a hard compiler gate.
    selected_candidate = build/'selected_physical_map_candidate.json'
    selected_candidate.write_text(json.dumps({'version': 'bio2rtl-selected-physical-proof-candidate-v1', 'status': 'PASS', 'cells': cells, 'assignments': graph.get('assignments', [])}, indent = 2, sort_keys = True)+'\n')
    selected_proof = verify_global_semantic_map(root, selected_candidate, output_name = 'SELECTED_PHYSICAL_MAP_PROOF.json')
    if selected_proof.get('status')!='PASS': 
        raise SystemExit('selected physical mapping proof failed: '+json.dumps(selected_proof.get('failures', []), sort_keys = True))
    sv = build/f'{module}.structural.v'
    emit_structural_sv(ir, sv)
    h = histogram(ir)
    area = sum(area_table[k]*v for k, v in h.items())
    expected = sum(int(x['bits']) for x in dhir['physical_state'])
    if h.get('DFFR', 0)!=expected: 
        raise SystemExit(f'DFFR mismatch {h.get("DFFR",0)} != {expected}')
    rep = {'version': 'bio2rtl-stage9-declarative-mapper-v2', 'status': 'PASS', 'recipe_cache_misses': len(fallback), 
         'generic_fallback_components': fallback, 'component_selections': selections, 'components': len(graph['component_order']), 'neutral_component_graph_selector': graph['selector'], 
         'total_cells': sum(h.values()), 'dffr': h.get('DFFR', 0), 'area_um2': area, 
         'physical_dhir': out.name, 'structural_sv': sv.name, 'trusted_sv_read_by_mapper': False, 
         'control_encoding_plan': control_encoding_plan, 
         'mapping_mode': mapping_mode, 'global_semantic_mapping': ir['global_semantic_mapping'],
         'semantic_liveness': semantic_liveness,
         'selected_connectivity_audit': selected_connectivity_audit,
         'selected_physical_mapping_proof': {'status': selected_proof.get('status'), 'truth_checks': int(selected_proof.get('truth_checks', 0)),
                                            'proof_file': 'SELECTED_PHYSICAL_MAP_PROOF.json'}, 
         'component_summary': component_summary(ir, area_table), 'recipe_interface_rejections': recipe_interface_rejections}
    (build/'stage7_declarative_mapper_report.json').write_text(json.dumps(rep, indent = 2, sort_keys = True)+'\n')
    return rep

def main(): 
    rep = map_project()
    print(json.dumps({k: v for k, v in rep.items() if k!='component_summary'}, indent = 2, sort_keys = True))

if __name__ == '__main__': 
    main()
