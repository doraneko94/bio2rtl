from __future__ import annotations
from pathlib import Path
from collections import defaultdict
import json
from .boolean_contract import map_neutral_component_contract
from .semantic_liveness import analyze_semantic_liveness, prune_contracts_for_liveness

OUTPINS = {'Y', 'Q', 'QB'}

def _load(p: Path): 
    return json.loads(Path(p).read_text())

def collect_semantic_contracts(root: Path)->list[dict]: 
    d = Path(root)/'build'/'semantic_neutral_contracts'
    out = []
    for p in sorted(d.glob('*.json')): 
        c = _load(p)
        if c.get('component_class'): 
            out.append(c)
    return out

def merge_semantic_contracts(root: Path, contracts: list[dict]|None = None)->dict: 
    contracts = contracts or collect_semantic_contracts(root)
    comb = {}
    dffs = []
    prims = []
    latches = []
    declared = set()
    outs = set()
    owner = {}
    for c in contracts: 
        name = str(c['component_class'])
        for k, v in c.get('combinational_outputs', {}).items(): 
            k = str(k)
            if k in comb and comb[k]!=v: 
                raise ValueError(f'global comb collision {k}')
            comb[k] = v
            owner.setdefault(k, set()).add(name)
        for d in c.get('dffs', []): 
            dffs.append(d)
            owner.setdefault(str(d['q']), set()).add(name)
            owner.setdefault(str(d['qb']), set()).add(name)
        for p in c.get('primitives', []): 
            prims.append(p)
            owner.setdefault(str(p['output']), set()).add(name)
        for l in c.get('latches', []): 
            latches.append(l)
            owner.setdefault(str(l['q']), set()).add(name)
            owner.setdefault(str(l['qb']), set()).add(name)
        declared.update(map(str, c.get('interface_inputs', [])))
        outs.update(map(str, c.get('interface_outputs', [])))
    produced = set(comb)
    for d in dffs: 
        produced.update((str(d['q']), str(d['qb'])))
    for p in prims: 
        produced.add(str(p['output']))
    for l in latches: 
        produced.update((str(l['q']), str(l['qb'])))
    external = sorted(declared-produced)
    # Only state primitive complementary outputs are physically free globally.  A component-local
    # complement declaration between two ordinary interface nets is not a global free resource.
    complements = {}
    for d in dffs: 
        complements[str(d['q'])] = str(d['qb'])
        complements[str(d['qb'])] = str(d['q'])
    for l in latches: 
        complements[str(l['q'])] = str(l['qb'])
        complements[str(l['qb'])] = str(l['q'])
    contract = {'component_class': 'global_semantic_core', 'interface_inputs': external, 'interface_outputs': sorted(outs), 
              'combinational_outputs': comb, 'dffs': dffs, 'primitives': prims, 'latches': latches, 'complements': complements}
    return {'contract': contract, 'contracts': contracts, 'producer_owners': {k: sorted(v) for k, v in owner.items()}}

def connectivity_audit(
    cells: list[dict],
    external_inputs: list[str],
    required_outputs: list[str],
    assignments: list[dict] | None = None,
) -> dict:
    prod = defaultdict(list)
    use = defaultdict(list)
    output_cells = defaultdict(list)
    for cell in cells:
        for pin, net in cell.get('ports', {}).items():
            net = str(net)
            row = (str(cell['name']), str(pin), str(cell['type']))
            if pin in OUTPINS:
                prod[net].append(row)
                output_cells[net].append(cell)
            else:
                use[net].append(row)

    ext = set(map(str, external_inputs))
    undriven = []
    for net, rows in sorted(use.items()):
        if net in ("1'b0", "1'b1") or net in ext:
            continue
        if net not in prod:
            undriven.append({'net': net, 'users': [list(row) for row in rows[:16]]})

    multi_driver = [
        {'net': net, 'drivers': [list(row) for row in rows]}
        for net, rows in sorted(prod.items())
        if len(rows) > 1
    ]

    assignment_map = {str(row['lhs']): str(row['rhs']) for row in assignments or []}
    resolved_required = set()
    missing_outputs = []
    for output in map(str, required_outputs):
        net = assignment_map.get(output, output)
        if net in ("1'b0", "1'b1"):
            continue
        resolved_required.add(net)
        if net not in prod and net not in ext:
            missing_outputs.append({'output': output, 'resolved_net': net})

    dangling_comb_outputs = []
    for net, rows in sorted(prod.items()):
        if net in resolved_required or net in use:
            continue
        # Q/QB outputs may legitimately be unused complements.  The dead-cone
        # invariant we enforce here is specifically for mapped combinational Y nets.
        y_drivers = [row for row in rows if row[1] == 'Y']
        if not y_drivers:
            continue
        dangling_comb_outputs.append({
            'net': net,
            'drivers': [list(row) for row in y_drivers],
        })

    failures = undriven or multi_driver or missing_outputs or dangling_comb_outputs
    return {
        'status': 'FAIL' if failures else 'PASS',
        'undriven': undriven,
        'multi_driver': multi_driver,
        'missing_outputs': missing_outputs,
        'dangling_comb_outputs': dangling_comb_outputs,
        'external_inputs': sorted(ext),
        'produced_net_count': len(prod),
        'consumed_net_count': len(use),
    }

def map_global_semantic_core(
    root: Path,
    graph: dict,
    area_table: dict[str, float],
    primitive_library: dict,
    contracts: list[dict] | None = None,
    liveness: dict | None = None,
) -> dict:
    root = Path(root)
    raw_contracts = contracts or collect_semantic_contracts(root)
    liveness = liveness or analyze_semantic_liveness(raw_contracts, graph)
    live_contracts = prune_contracts_for_liveness(raw_contracts, liveness)
    merged = merge_semantic_contracts(root, live_contracts)
    contract = merged['contract']
    expected_inputs = sorted(str(p['name']) for p in graph.get('ports', []) if p.get('direction') == 'input')
    if sorted(contract['interface_inputs'])!=expected_inputs: 
        raise ValueError(f'global semantic external input mismatch contract={contract["interface_inputs"]} graph={expected_inputs}')
    cells = map_neutral_component_contract(contract, area_table, primitive_library, component = 'global_semantic_core', name_prefix = 'gbl')
    outputs = [str(p['name']) for p in graph.get('ports', []) if p.get('direction') == 'output']
    audit = connectivity_audit(cells, expected_inputs, outputs, graph.get('assignments', []))
    area = sum(float(area_table[c['type']]) for c in cells)
    return {
        'version': 'bio2rtl-global-semantic-map-candidate-v2',
        'status': audit['status'],
        'cells': cells,
        'assignments': graph.get('assignments', []),
        'cell_count': len(cells),
        'area_um2': area,
        'contract': contract,
        'producer_owners': merged['producer_owners'],
        'connectivity_audit': audit,
        'semantic_liveness': liveness,
        'source_components': sorted(str(c['component_class']) for c in merged['contracts']),
        'component_local_helper_nets_not_assumed_free': True,
    }


def emit_global_candidate_dhir(root: Path, candidate: dict, graph: dict, *, filename: str = 'physical_dhir_global_candidate.json')->dict: 
    from .physical_ir import dump, emit_structural_sv, histogram
    root = Path(root)
    build = root/'build'
    base = _load(build/'physical_dhir_v18_stage7.json')
    ir = {
      'version': 'bio2rtl-physical-dhir-v1', 'module': base['module'], 'timescale': base.get('timescale', '`timescale 1ns/1ps'), 
      'source_architecture_dhir_version': base.get('source_architecture_dhir_version'), 
      'technology': 'TR-1um', 'ports': graph['ports'], 'cells': candidate['cells'], 'assignments': graph.get('assignments', []), 
      'mapping_mode': 'global_semantic_neutral_dag', 'trusted_sv_read_by_mapper': False, 
      'global_semantic_candidate_version': candidate.get('version'), 'global_semantic_connectivity_audit': candidate.get('connectivity_audit'), 
    }
    out = build/filename
    dump(ir, out)
    sv = build/(base['module']+'.global_candidate.structural.v')
    emit_structural_sv(ir, sv)
    return {'physical_dhir': str(out.relative_to(root)), 'structural_sv': str(sv.relative_to(root)), 'histogram': dict(histogram(ir)), 'cells': len(ir['cells'])}
