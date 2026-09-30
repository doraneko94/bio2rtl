import json
from pathlib import Path
import subprocess, sys
from bio2rtl.project_inputs import load_config
root = Path(__file__).resolve().parent
# Isolated production stages.  Dynamic architecture certificates are generated only
# after the generic/transactional core certificate pass has completed, because the core
# stage intentionally recreates build/generated_certificates from scratch.  Keeping this
# ordering explicit prevents observation-snapshot/OE proofs from being silently deleted
# before architecture recovery.
subprocess.run([sys.executable, str(root/'run_semantic_frontend.py')], cwd = root, check = True)
subprocess.run([sys.executable, str(root/'generate_architecture_certificates.py')], cwd = root, check = True)
subprocess.run([sys.executable, str(root/'generate_stage7_dynamic_certificates.py'), '--stop-after', 'oe', '--optional'], cwd = root, check = True)
subprocess.run([sys.executable, str(root/'recover_seedless_architecture.py')], cwd = root, check = True)
subprocess.run([sys.executable, '-m', 'bio2rtl.tr1um_declarative_mapper'], cwd = root, check = True)
subprocess.run([sys.executable, '-m', 'bio2rtl.xschem_export', str(root)], cwd = root, check = True)
subprocess.run([sys.executable, '-m', 'bio2rtl.routed_xschem_export', str(root)], cwd = root, check = True)
_, cfg0 = load_config(root)
has_physical_support = isinstance(cfg0.get('physical_support'), dict)
if has_physical_support: 
    subprocess.run([sys.executable, '-m', 'bio2rtl.generic_fullchip_export', str(root)], cwd = root, check = True)
# Without an explicit TOML physical_support boundary, Generic Ver.1 still emits
# the exact core/TB artifacts but deliberately emits no full-chip wrapper.
# This is fail-closed with respect to pad topology: no protocol-specific I/O is invented.
cfg = json.loads(json.dumps(cfg0))
project = str(cfg.get('name', 'bio2rtl_core'))
print(root/f'build/{project}.structural.v')
print(root/f'build/xschem/{project}_core.sch')
print(root/f'build/xschem/{project}_core.sym')
print(root/f'build/xschem/{project}_tb.sym')
if has_physical_support: 
    print(root/f'build/xschem/{project}_fullchip.sch')
    print(root/f'build/xschem/{project}_fullchip.sym')
else: 
    print('FULLCHIP_XSCHEM=N_A_NO_PHYSICAL_SUPPORT')
support_path = root/'support_binding.json'
support = json.loads(support_path.read_text()) if has_physical_support and support_path.exists() else {}
print('CORE_STATUS='+support.get('semantic_core', {}).get('status', 'GENERATED'))
print('FULLCHIP_XSCHEM='+support.get('fullchip_xschem_status', 'GENERATED') if has_physical_support else 'N_A_NO_PHYSICAL_SUPPORT')
print('FULLCHIP_VALIDATION='+support.get('fullchip_validation_status', 'NOT_YET_ELECTRICALLY_VALIDATED') if has_physical_support else 'N_A_NO_PHYSICAL_SUPPORT')
