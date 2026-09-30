#!/usr/bin/env python3
from pathlib import Path
import json, re, sys
from bio2rtl.project_inputs import load_config

root = Path(__file__).resolve().parent
build = root/'build'
x = build/'xschem'
dhir = json.loads((build/'physical_dhir_v18_stage7.json').read_text())
_, cfg = load_config(root)
project = str(cfg.get('name', 'bio2rtl_core'))
fail = []

core_path = x/f'{project}_core.sch'
core = core_path.read_text() if core_path.exists() else ''
instances = re.findall(r'^C \{TR-1um_5_stdcell/([^}]+)\.sym\} .*?\{name=([^ }]+)', core, re.M)
if len(instances) != len(dhir['cells']):
    fail.append(f'core instances={len(instances)} != DHIR {len(dhir["cells"])}')
if re.search(r'^C \{cells/', core, re.M):
    fail.append('canonical core references generated local cell symbols')
if re.search(r'^C \{devices/lab_pin\.sym\}', core, re.M):
    fail.append('canonical routed core contains internal remote lab_pin connectivity')

route_audit_path = build/'routed_xschem_export_audit.json'
route_audit = {}
if not route_audit_path.exists():
    fail.append('missing routed_xschem_export_audit.json')
else:
    route_audit = json.loads(route_audit_path.read_text())
    if route_audit.get('status') != 'PASS':
        fail.append('routed Xschem audit not PASS')
    if int(route_audit.get('cell_count', -1)) != len(dhir['cells']):
        fail.append('routed Xschem audit cell count mismatch')
    if route_audit.get('canonical_core_schematic') != f'build/xschem/{project}_core.sch':
        fail.append('routed Xschem audit does not identify canonical core')

required = (f'{project}_core.sch', f'{project}_core.sym', f'{project}_tb.sym')
for name in required:
    if not (x/name).exists():
        fail.append('missing '+name)


# Core-only mode must not silently regenerate package/physical-support artifacts.
if (build/'physical_support_inferred.json').exists():
    fail.append('physical_support_inferred.json present in core-only build')
if (x/f'{project}_fullchip.sch').exists() or (x/f'{project}_fullchip.sym').exists():
    fail.append('full-chip artifact present in core-only build')
if (x/'support').exists():
    fail.append('physical support directory present in core-only build')
core_if_path = build/'core_interface_inferred.json'
if not core_if_path.exists():
    fail.append('missing core_interface_inferred.json')
else:
    core_if = json.loads(core_if_path.read_text())
    if core_if.get('status') != 'PASS':
        fail.append('core interface inference not PASS')
    if core_if.get('physical_support_enabled') is not False:
        fail.append('core interface report does not identify core-only mode')

# Redundant historical views must not be regenerated.
for name in (f'{project}_manual.sch', f'{project}_core_phy.sch', f'{project}_core_phy.sym'):
    if (x/name).exists():
        fail.append('obsolete generated artifact present: '+name)
if (build/'core_phy_placement.json').exists():
    fail.append('obsolete generated artifact present: core_phy_placement.json')
if (build/'manual_xschem_export_audit.json').exists():
    fail.append('obsolete generated artifact present: manual_xschem_export_audit.json')

res = {
    'version': 'bio2rtl-generic-core-ready-audit-v3',
    'status': 'PASS' if not fail else 'FAIL',
    'failures': fail,
    'project': project,
    'core_instances': len(instances),
    'routed_core_wire_segments': route_audit.get('wire_segments'),
    'routed_core_lab_pins': route_audit.get('lab_pin_count'),
    'physical_support': 'N_A',
    'fullchip': 'N_A_NO_PHYSICAL_SUPPORT',
    'obsolete_manual_or_phy_views_generated': False if not any('obsolete generated artifact' in z for z in fail) else True,
}
(build/'generic_core_ready_audit.json').write_text(json.dumps(res, indent=2, sort_keys=True)+'\n')
print(json.dumps(res, indent=2, sort_keys=True))
raise SystemExit(0 if not fail else 1)
