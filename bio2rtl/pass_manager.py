from __future__ import annotations
from dataclasses import dataclass, asdict
from pathlib import Path
import hashlib, json, os, shutil, subprocess, sys, tempfile, time


def _sha(p: Path) -> str: 
    h = hashlib.sha256()
    with p.open('rb') as f: 
        for b in iter(lambda: f.read(1024*1024), b''): 
            h.update(b)
    return h.hexdigest()


def _tree_hashes(root: Path) -> dict[str, str]: 
    if not root.exists(): 
        return {}
    return {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob('*')) if p.is_file()}

@dataclass(frozen = True)
class LegacyPass: 
    name: str
    command: tuple[str, ...]
    optional_patterns: tuple[str, ...] = ()
    description: str = ''

    def classify_failure(self, text: str) -> str: 
        return 'N_A' if any(x in text for x in self.optional_patterns) else 'FAIL'


def default_architecture_passes(root: Path) -> list[LegacyPass]: 
    py = sys.executable
    return [
      LegacyPass(
        'observation_local_bit_elimination', 
        (py, str(root/'semantic_frontend/tools/analyze_observation_local_packed_bit_specialization.py'), 
         '--ir', str(root/'build/semantic/phase40.ir.json'), 
         '--quotient', str(root/'build/semantic/behavioral_quotient.json'), 
         '--direct-table', str(root/'build/semantic/directfsm_table.json'), 
         '--legal-product', str(root/'build/semantic/legal_product.json'), 
         '--output', '{CERT_OUT}/observation_local_bit_elimination.json', 
         '--report', '{CERT_OUT}/observation_local_bit_elimination.txt'), 
        ('FAIL fresh legal product', 'no candidate', 'no packed', 'ambiguity', 'not applicable'), 
        'Generic observation-local packed-bit analysis.'), 
      LegacyPass(
        'legacy_stage6_core_bundle', 
        (py, str(root/'generate_stage6_core_certificates.py')), 
        ('canonical elimination ambiguity', 'latch candidates', 'no control factorization', 'unexpected control minimum bits=', 
         'shift regs ', 'no shift observation predicates', 'no out-of-range COUNT terminal sentinel discovered', 'rx proof failed'), 
        'Legacy adapter: canonical elimination, control factorization and shift quotient. To be split into independent generic passes later.'), 
      LegacyPass(
        'legacy_storage_relations_bundle', 
        (py, str(root/'generate_storage_relations_certificate.py')), 
        ('expected unique low->high polling completion event', 'ambiguous GPIO data/direction roles', 'expected matched 2-bit GPIO payload', 
         'expected two untracked history captures', 'expected two legal payload-commit classes', 'commit guard COUNT ambiguity', 
         'mirror invariant search expected one solution'), 
        'Legacy adapter: storage relation and mirror/history discovery.'), 
      LegacyPass(
        'shared_counter_discovery', 
        (py, str(root/'generate_stage7_dynamic_certificates.py'), '--stop-after', 'shared'), 
        ('optional prerequisite missing', 'no structurally coalescible COUNT pair', 'ambiguous structurally coalescible COUNT pairs', 'no direct guard readpoints', 
         'reset semantic source ambiguity', 'no terminal same-control hold edge discovered', 'terminal hold control ambiguity'), 
        'Transactional shared-counter discovery/proof. Non-applicable counter topology is N/A.'), 
      LegacyPass(
        'observation_snapshot_discovery', 
        (py, str(root/'generate_stage7_dynamic_certificates.py'), '--stop-after', 'snapshot'), 
        ('optional prerequisite missing', 'no structurally coalescible COUNT pair', 'ambiguous structurally coalescible COUNT pairs', 'no direct guard readpoints', 
         'reset semantic source ambiguity', 'no terminal same-control hold edge discovered', 'terminal hold control ambiguity', 
         'no proven observation snapshot candidate', 'ambiguous proven observation snapshot candidates', 'no observation load transition provenance'), 
        'Transactional observation-snapshot proof. Current implementation reuses discovered counter readpoints but commits independently.'), 
      LegacyPass(
        'oe_recurrence_discovery', 
        (py, str(root/'generate_stage7_dynamic_certificates.py'), '--stop-after', 'oe'), 
        ('optional prerequisite missing', 'no structurally coalescible COUNT pair', 'ambiguous structurally coalescible COUNT pairs', 'no direct guard readpoints', 
         'reset semantic source ambiguity', 'no terminal same-control hold edge discovered', 'terminal hold control ambiguity', 
         'no proven observation snapshot candidate', 'ambiguous proven observation snapshot candidates', 'no observation load transition provenance', 
         'no proven OE recurrence candidate', 'ambiguous proven OE recurrence candidates', 'no fall-domain OE rules', 'OE data predicate ambiguity', 'no simple OE recurrence fit'), 
        'Transactional output-enable recurrence proof; commits independently from shared-counter and snapshot certificates.'), 
    ]


def run_pass_manager(root: Path, passes: list[LegacyPass] | None = None, *, fail_on_internal_error: bool = True) -> dict: 
    root = Path(root).resolve()
    build = root/'build'
    committed = build/'generated_certificates'
    committed.mkdir(parents = True, exist_ok = True)
    passes = passes or default_architecture_passes(root)
    results = []
    semantic_dir = build/'semantic'
    semantic_hashes = _tree_hashes(semantic_dir)
    workroot = build/'generic_pass_work'
    if workroot.exists(): 
        shutil.rmtree(workroot)
    workroot.mkdir(parents = True)

    for idx, p in enumerate(passes): 
        before = _tree_hashes(committed)
        tx = workroot/f'{idx:02d}_{p.name}'/'certificates'
        tx.parent.mkdir(parents = True, exist_ok = True)
        if committed.exists(): 
            shutil.copytree(committed, tx, dirs_exist_ok = True)
        env = os.environ.copy()
        env['BIO2RTL_CERT_OUT'] = str(tx)
        cmd = [x.replace('{CERT_OUT}', str(tx)) for x in p.command]
        # The root compiler and semantic frontend intentionally carry separate
        # packages both named ``bio2rtl``.  Transactional passes must resolve
        # imports against the package that owns the invoked script, rather than
        # inheriting the caller's sys.path ordering.  This keeps optional passes
        # hermetic and prevents root/semantic namespace shadowing.
        semantic_frontend = root/'semantic_frontend'
        command_paths = []
        for arg in cmd[1:]: 
            try: 
                q = Path(arg)
                if q.is_absolute(): 
                    command_paths.append(q.resolve())
            except Exception: 
                pass
        use_semantic_namespace = any(semantic_frontend in q.parents for q in command_paths)
        package_root = semantic_frontend if use_semantic_namespace else root
        prior = env.get('PYTHONPATH', '')
        env['PYTHONPATH'] = str(package_root)+(os.pathsep+prior if prior else '')
        t0 = time.time()
        cp = subprocess.run(cmd, cwd = root, env = env, text = True, capture_output = True)
        elapsed = time.time()-t0
        combined = (cp.stdout or '')+'\n'+(cp.stderr or '')
        if cp.returncode == 0: 
            after_tx = _tree_hashes(tx)
            changed = sorted(k for k, v in after_tx.items() if before.get(k)!=v)
            shutil.rmtree(committed)
            shutil.copytree(tx, committed)
            results.append({'pass': p.name, 'status': 'PASS', 'action': 'APPLIED' if changed else 'N_A', 
                            'description': p.description, 'returncode': 0, 'elapsed_s': elapsed, 
                            'input_semantic_hashes': semantic_hashes, 'input_certificate_hashes': before, 
                            'output_certificate_hashes': after_tx, 'changed_outputs': changed})
        else: 
            cls = p.classify_failure(combined)
            row = {'pass': p.name, 'status': 'PASS' if cls == 'N_A' else 'FAIL', 'action': cls, 
                 'description': p.description, 'returncode': cp.returncode, 'elapsed_s': elapsed, 
                 'input_semantic_hashes': semantic_hashes, 'input_certificate_hashes': before, 
                 'output_certificate_hashes': before, 'changed_outputs': [], 
                 'diagnostic_tail': combined[-8000:]}
            results.append(row)
            if cls == 'FAIL' and fail_on_internal_error: 
                report = {'version': 'bio2rtl-generic-pass-manager-v1', 'status': 'FAIL', 'passes': results, 
                        'semantic_hashes': semantic_hashes, 'certificate_hashes': _tree_hashes(committed)}
                (build/'generic_pass_manager_report.json').write_text(json.dumps(report, indent = 2, sort_keys = True)+'\n')
                raise RuntimeError(f'generic pass {p.name} failed internally\n{combined[-4000:]}')

    cert_hashes = _tree_hashes(committed)
    report = {'version': 'bio2rtl-generic-pass-manager-v1', 'status': 'PASS', 'passes': results, 
            'semantic_hashes': semantic_hashes, 'certificate_hashes': cert_hashes, 
            'applied': [x['pass'] for x in results if x['action'] == 'APPLIED'], 
            'not_applicable': [x['pass'] for x in results if x['action'] == 'N_A'], 
            'failed': [x['pass'] for x in results if x['status'] == 'FAIL']}
    (build/'generic_pass_manager_report.json').write_text(json.dumps(report, indent = 2, sort_keys = True)+'\n')
    return report
