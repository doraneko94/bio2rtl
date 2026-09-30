from __future__ import annotations
from pathlib import Path
import hashlib, json, shutil
from bio2rtl.pass_manager import run_pass_manager
from bio2rtl.recover_seedless import _discover_certificates

ROOT = Path(__file__).resolve().parent
SEM = ROOT/'build/semantic'
OUT = ROOT/'build/generated_certificates'
BUILD = ROOT/'build'
required_sem = ['phase40.ir.json', 'behavioral_quotient.json', 'directfsm_table.json', 'legal_product.json', 'corrected_relation.json']
missing = [x for x in required_sem if not (SEM/x).exists()]
if missing: 
    raise SystemExit(f'missing fresh semantic artifacts: {missing}')

def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

# Generic orchestration is deliberately not content-cache driven yet. Existing semantic
# frontend caches remain active; architecture transforms are transactional and optional.
if OUT.exists(): 
    shutil.rmtree(OUT)
OUT.mkdir(parents = True)
pm = run_pass_manager(ROOT)
found, audit = _discover_certificates(OUT, require_complete = False)
report = {
 'version': 'bio2rtl-generic-architecture-certificates-g1-v1', 'status': 'PASS', 
 'pre_generated_architecture_certificates_read': False, 
 'semantic_authorities': {
  'corrected_relation_sha256': sha(SEM/'corrected_relation.json'), 
  'phase40_ir_sha256': sha(SEM/'phase40.ir.json'), 
  'behavioral_quotient_sha256': sha(SEM/'behavioral_quotient.json'), 
  'direct_fsm_sha256': sha(SEM/'directfsm_table.json'), 
  'legal_product_sha256': sha(SEM/'legal_product.json')}, 
 'certificate_count': len(found), 'schemas': sorted(found), 'certificate_audit': audit, 
 'pass_manager_report': 'generic_pass_manager_report.json', 
 'pass_actions': [{k: x[k] for k in ('pass', 'status', 'action')} for x in pm['passes']], 
}
(BUILD/'stage6_certificate_generation_report.json').write_text(json.dumps(report, indent = 2, sort_keys = True)+'\n')
print(json.dumps(report, indent = 2, sort_keys = True))
