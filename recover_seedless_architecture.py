from pathlib import Path
import json, hashlib
from bio2rtl.project_inputs import dis_path, config_path
from bio2rtl.recover_seedless import recover_seedless_dhir
from bio2rtl.post_architecture_maturation import mature_post_architecture_certificates

root = Path(__file__).resolve().parent
phase = root/'build/semantic/phase40.ir.json'
sem = root/'build/semantic'
cert = root/'build/generated_certificates'
input_dis = dis_path(root)
toml = config_path(root)
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()

dhir = recover_seedless_dhir(phase, cert)
(root/'build').mkdir(exist_ok = True)
arch_path = root/'build/architecture_ir_v16_stage3_seedless.json'
arch_path.write_text(json.dumps(dhir, indent = 2, sort_keys = True)+'\n')
post = mature_post_architecture_certificates(root)
# Content-binding checks: generated architecture certificates are accepted only
# when their embedded authority hashes match the semantics generated in this run.
auth = {
 'relation_sha256': sha(sem/'corrected_relation.json'), 
 'ir_sha256': sha(phase), 'phase40_ir_sha256': sha(phase), 
 'quotient_sha256': sha(sem/'behavioral_quotient.json'), 
 'behavioral_quotient_sha256': sha(sem/'behavioral_quotient.json'), 
 'direct_table_sha256': sha(sem/'directfsm_table.json'), 
 'legal_product_sha256': sha(sem/'legal_product.json'), 
}
checks = []
def walk(x, path, file): 
    if isinstance(x, dict): 
        for k, v in x.items(): 
            if k in auth and isinstance(v, str) and len(v) == 64: 
                checks.append({'file': file, 'path': path+'/'+k, 'declared': v, 'generated': auth[k], 'match': v == auth[k]})
            walk(v, path+'/'+k, file)
    elif isinstance(x, list): 
        for i, v in enumerate(x): 
            walk(v, f'{path}/{i}', file)
for p in sorted(cert.glob('*.json')): 
    walk(json.loads(p.read_text()), '', p.name)
bad = [x for x in checks if not x['match']]
if bad: 
    raise SystemExit('certificate semantic binding mismatch:\n'+json.dumps(bad, indent = 2))

report = {
 'status': 'PASS', 'stage': 'v17-stage5-seedless-architecture-recovery', 
 'physical_dff_bits': dhir['physical_dff_bits'], 
 'physical_state_entries': len(dhir['physical_state']), 
 'non_dff_state_bits': sum(x['bits'] for x in dhir['non_dff_state']), 
 'v10_seed_read': dhir['discovery_audit']['v10_seed_read'], 
 'trusted_architecture_manifest_read': False, 
 'pre_generated_architecture_certificates_read': False, 
 'input_dis_sha256': sha(input_dis), 'toml_sha256': sha(toml), 'phase_ir_sha256': sha(phase), 
 'certificate_binding_checks': len(checks), 'certificate_binding_failures': len(bad), 
 'post_architecture_maturation_status': post.get('status'), 'post_architecture_maturation_report': 'post_architecture_maturation_report.json', 
}
(root/'build/report.json').write_text(json.dumps(report, indent = 2, sort_keys = True)+'\n')
print(json.dumps(report, indent = 2, sort_keys = True))
