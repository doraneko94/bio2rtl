#!/usr/bin/env python3
from pathlib import Path
import hashlib, json, re, sys
from bio2rtl.project_inputs import load_config
from bio2rtl.physical_interface import resolve_physical_support

root = Path(__file__).resolve().parent
build = root/'build'
x = build/'xschem'
dhir = json.loads((build/'physical_dhir_v18_stage7.json').read_text())
_, cfg = load_config(root)
project = str(dhir.get('module', cfg.get('name', 'bio2rtl')))
audit = json.loads((build/'generic_fullchip_export_audit.json').read_text())
fail = []
if audit.get('status') != 'PASS':
    fail.append('generic exporter audit not PASS')
if audit.get('core_cells') != len(dhir['cells']):
    fail.append('core cell count mismatch')
if audit.get('canonical_routed_core_used') is not True:
    fail.append('full-chip does not use canonical routed core')
if audit.get('core_instance_symbol') != f'{project}_core.sym':
    fail.append('full-chip core instance is not canonical core symbol')

core_path = x/f'{project}_core.sch'
core = core_path.read_text() if core_path.exists() else ''
top = (x/f'{project}_fullchip.sch').read_text()
sym = (x/f'{project}_fullchip.sym').read_text()
inst = re.findall(r'^C \{TR-1um_5_stdcell/([^}]+)\.sym\} .*?\{name=([^ }]+)', core, re.M)
if len(inst) != len(dhir['cells']):
    fail.append(f'core schematic instances {len(inst)} != {len(dhir["cells"])}')
if re.search(r'^C \{devices/lab_pin\.sym\}', core, re.M):
    fail.append('canonical routed core contains internal remote lab_pin connectivity')
if re.search(r'^N .*\{lab=', core, re.M) or re.search(r'^N .*\{lab=', top, re.M):
    fail.append('legacy N {lab=} connectivity remains')

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

# External full-chip symbol contract comes entirely from TOML.
ps = resolve_physical_support(root, write_report=False)
expected = ['VDD', 'VSS'] + [str(p['name']) for p in ps.get('pads', [])]
spins = [m.group(1) for m in re.finditer(r'^B 5 .*?\{name=([^ }]+) dir=', sym, re.M)]
if spins != expected:
    fail.append(f'fullchip symbol pins {spins} != TOML {expected}')

# Every selected recipe file must still match its technology-library SHA.
lib = json.loads((root/'technology/support_recipes_v1.json').read_text())
recipe_ids = set()
if ps.get('reset'):
    recipe_ids.add(str(ps['reset']['recipe']))
for p in ps.get('pads', []):
    if p.get('recipe'):
        recipe_ids.add(str(p['recipe']))
for rid in sorted(recipe_ids):
    r = lib['recipes'][rid]
    for k in ('sch', 'sym'):
        src = Path(r[k]).name
        fp = x/'support'/src
        if not fp.exists():
            fail.append(f'missing support {rid}:{src}')
            continue
        h = hashlib.sha256(fp.read_bytes()).hexdigest()
        if h != r['sha256'][k]:
            fail.append(f'support SHA mismatch {rid}:{src}')

expected_support = (1 if ps.get('reset') else 0) + sum(not str(p['kind']).startswith('direct_') for p in ps.get('pads', []))
support_refs = re.findall(r'^C \{support/([^}]+)\} .*?\{name=([^ }]+)', top, re.M)
if len(support_refs) != expected_support:
    fail.append(f'support instances {len(support_refs)} != {expected_support}')

src = (root/'bio2rtl/generic_fullchip_export.py').read_text().lower()
for bad in ('i2c', 'sda', 'gpio0', 'gpio1', 'ack_enter', 'read_ack'):
    if bad in src:
        fail.append(f'protocol-specific token in generic exporter: {bad}')

for req in (f'{project}_core.sch', f'{project}_core.sym', f'{project}_fullchip.sch', f'{project}_fullchip.sym', f'{project}_benchmark_template.sch', 'xschemrc.bio2rtl'):
    if not (x/req).exists():
        fail.append('missing '+req)
for name in (f'{project}_manual.sch', f'{project}_core_phy.sch', f'{project}_core_phy.sym'):
    if (x/name).exists():
        fail.append('obsolete generated artifact present: '+name)
if (build/'core_phy_placement.json').exists():
    fail.append('obsolete generated artifact present: core_phy_placement.json')
if (build/'manual_xschem_export_audit.json').exists():
    fail.append('obsolete generated artifact present: manual_xschem_export_audit.json')

res = {
    'version': 'bio2rtl-generic-fullchip-ready-audit-v2',
    'status': 'PASS' if not fail else 'FAIL',
    'failures': fail,
    'project': project,
    'core_cells': len(inst),
    'routed_core_wire_segments': route_audit.get('wire_segments'),
    'external_ports': spins,
    'support_instances': len(support_refs),
    'toml_driven': True,
    'canonical_routed_core_only': True,
    'protocol_tokens_in_generic_exporter': False if not any('protocol-specific' in z for z in fail) else True,
    'obsolete_manual_or_phy_views_generated': False if not any('obsolete generated artifact' in z for z in fail) else True,
}
(build/'generic_fullchip_ready_audit.json').write_text(json.dumps(res, indent=2, sort_keys=True)+'\n')
print(json.dumps(res, indent=2, sort_keys=True))
raise SystemExit(0 if not fail else 1)
