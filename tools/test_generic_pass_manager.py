#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib, json, sys, tempfile
from bio2rtl.pass_manager import LegacyPass, run_pass_manager


def sha(p): 
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

with tempfile.TemporaryDirectory(prefix = 'bio2rtl-pass-manager-test-') as td: 
    root = Path(td)
    sem = root/'build/semantic'
    cert = root/'build/generated_certificates'
    sem.mkdir(parents = True)
    cert.mkdir(parents = True)
    (sem/'authority.json').write_text('{"semantic":"dummy"}\n')
    anchor = cert/'anchor.json'
    anchor.write_text('{"status":"PASS","kind":"anchor"}\n')
    h = sha(anchor)
    p = LegacyPass('intentional_na', (sys.executable, '-c', 'import sys; print("canonical elimination ambiguity: []",file=sys.stderr); sys.exit(3)'), ('canonical elimination ambiguity',))
    rep = run_pass_manager(root, [p])
    assert rep['status'] == 'PASS' and rep['passes'][0]['action'] == 'N_A', rep
    assert anchor.exists() and sha(anchor) == h
    # Unknown errors are fail-closed, not silently converted to N/A.
    q = LegacyPass('intentional_internal_failure', (sys.executable, '-c', 'import sys; print("unexpected internal bug",file=sys.stderr); sys.exit(4)'), ('known optional',))
    try: 
        run_pass_manager(root, [q])
    except RuntimeError: 
        pass
    else: 
        raise AssertionError('unexpected internal error was not fail-closed')
print('GENERIC_PASS_MANAGER_OPTIONALITY_TEST: PASS')


# Semantic liveness regression: an unobservable combinational role is pruned,
# while a top-output dependency remains live through an assignment alias.
from bio2rtl.semantic_liveness import analyze_semantic_liveness, prune_contracts_for_liveness
from bio2rtl.global_semantic_mapper import connectivity_audit

_contract = {
    'component_class': 'test_component',
    'interface_inputs': ['tick'],
    'interface_outputs': ['live_evt', 'dead_evt'],
    'combinational_outputs': {
        'live_evt': ['VAR', 'tick'],
        'dead_evt': ['NOT', ['VAR', 'tick']],
    },
    'dffs': [],
    'primitives': [],
    'latches': [],
}
_graph = {
    'ports': [
        {'name': 'tick', 'direction': 'input'},
        {'name': 'out', 'direction': 'output'},
    ],
    'assignments': [
        {'lhs': 'out', 'rhs': 'live_evt'},
    ],
}
_liveness = analyze_semantic_liveness([_contract], _graph)
assert _liveness['live_combinational_outputs'] == ['live_evt'], _liveness
assert _liveness['pruned_combinational_outputs'] == ['dead_evt'], _liveness
_pruned = prune_contracts_for_liveness([_contract], _liveness)[0]
assert sorted(_pruned['combinational_outputs']) == ['live_evt'], _pruned

# A mapped combinational Y with no consumer or required output is a hard failure.
_audit = connectivity_audit(
    [
        {
            'name': 'u_dead',
            'type': 'INV_X1',
            'ports': {'A': 'tick', 'Y': 'dead_net'},
        },
    ],
    ['tick'],
    [],
    [],
)
assert _audit['status'] == 'FAIL' and _audit['dangling_comb_outputs'], _audit
print('SEMANTIC_DEAD_CONE_REGRESSION_TEST: PASS')
