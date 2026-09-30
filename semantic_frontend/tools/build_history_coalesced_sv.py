#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_sv_emitter import _expr_bit_sv, _build_prefix_nodes, _build_action_groups

NONPHYSICAL = {"CONST", "DERIVED_EXPR", "PHASE_LOCAL_ELIDED"}

def semantic_action_map(ir: dict)->tuple[dict[str, str], dict[str, list[dict]]]: 
    storage = {str(x["register"]): x for x in ir["storage_optimization"]["register_storage"]}
    retained = [
        r for r in ir["update_rules"]
        if r.get("materialize") and str(storage[str(r["target"])]["storage_kind"]) not in NONPHYSICAL
    ]
    _nodes, _order, rule_conditions = _build_prefix_nodes(retained)
    _actions, rule_to_action = _build_action_groups(retained, rule_conditions)
    by_target = {}
    for r in retained: 
        by_target.setdefault(str(r["target"]), []).append(r)
    return rule_to_action, by_target

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--source-sv', type = Path, required = True)
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--analysis', type = Path, required = True)
    ap.add_argument('--output-sv', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    text = a.source_sv.read_text()
    ir = json.loads(a.ir.read_text())
    ana = json.loads(a.analysis.read_text())
    c = ana.get('recommended')
    if not c: 
        a.output_sv.parent.mkdir(parents = True, exist_ok = True)
        a.output_sv.write_text(text)
        meta = {'version': 'history-coalesced-sv-v2-semantic-actions', 'n_a': True, 'registers': [], 
              'source_storage_bits': int(ir.get('storage_optimization', {}).get('natural_storage_bits', 0)), 
              'candidate_storage_bits': int(ir.get('storage_optimization', {}).get('natural_storage_bits', 0)), 
              'analysis_proof_model': ana.get('proof_model'), 'reason': ana.get('n_a_reason', 'no candidate')}
        a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
        rpt = ['BIO2RTL HISTORY-COALESCED SV CANDIDATE', '='*96, 'OPTIONAL RESULT        : N/A', 'identity transform       : PASS', 'RESULT                  : CANDIDATE GENERATED']
        a.report.write_text('\n'.join(rpt)+'\n')
        print('\n'.join(rpt))
        return 0
    ra, rb = map(str, c['registers'])
    w = int(c['width'])
    hist = f'HIST_{ra}_{rb}'
    arch = {str(x['id']): x for x in ir['architectural_registers']}
    widths = {k: int(v['width']) for k, v in arch.items()}
    if ra not in arch or rb not in arch: 
        raise SystemExit('FAIL recommended registers not architectural')
    if int(arch[ra]['width'])!=w or int(arch[rb]['width'])!=w: 
        raise SystemExit('FAIL history width mismatch')

    # Recover physical action names from the IR rule relation itself.  This is
    # independent of the source-SV bit equations and remains valid after
    # phase-local clean emission and correlated-group encoding.
    rule_to_action, by_target = semantic_action_map(ir)
    rules = {}
    acts = {}
    for rid in (ra, rb): 
        rr = by_target.get(rid, [])
        if len(rr)!=1: 
            raise SystemExit(f'FAIL expected exactly one materialized history write rule for {rid}, got {len(rr)}')
        rules[rid] = rr[0]
        acts[rid] = [rule_to_action[str(rr[0]['rule_id'])]]
    xa, xb = rules[ra], rules[rb]
    if xa['outcome']!=xb['outcome'] or xa['outcome']!=c['outcome']: 
        raise SystemExit('FAIL history outcome differs from proof recommendation')
    if str(xa['event_class'])!=str(c['event_class']) or str(xb['event_class'])!=str(c['event_class']): 
        raise SystemExit('FAIL history event class differs from proof recommendation')
    aa, ab = acts[ra], acts[rb]
    merged = '('+' | '.join(aa+ab)+')'

    # Ensure source SV was emitted from the same semantic rule/action numbering.
    for act in aa+ab: 
        if not re.search(rf'^logic\s+{re.escape(act)}\s*;', text, re.M): 
            raise SystemExit(f'FAIL semantic action {act} absent from source SV')

    # Replace physical declarations with one history register and semantic aliases.
    old = f'logic [{w-1}:0] r_{ra};\nlogic [{w-1}:0] r_{rb};'
    new = (f'// Proof-backed coalesced ping-pong/history storage: {ra}+{rb} -> {hist}.\n'
         f'logic [{w-1}:0] r_{hist};\n'
         f'wire [{w-1}:0] r_{ra} = r_{hist};\n'
         f'wire [{w-1}:0] r_{rb} = r_{hist};')
    if old not in text: 
        raise SystemExit(f'FAIL declaration pair missing: {ra},{rb}')
    text = text.replace(old, new, 1)

    # Replace the two source d-vectors by one direct equation.  Action selection
    # comes from the IR and the data expression comes from the proof-identified
    # common outcome, so no source-SV expression parsing is required.
    block_pat = (
        rf'wire \[{w-1}:0\] d_{re.escape(ra)};\n'
        rf'(?:assign d_{re.escape(ra)}\[\d+\] = .*?;\n)+\n'
        rf'wire \[{w-1}:0\] d_{re.escape(rb)};\n'
        rf'(?:assign d_{re.escape(rb)}\[\d+\] = .*?;\n)+'
    )
    m = re.search(block_pat, text, re.M)
    if not m: 
        raise SystemExit('FAIL cannot delimit history d-vector blocks')
    lines = [f'wire [{w-1}:0] d_{hist};']
    for bit in range(w): 
        data = _expr_bit_sv(xa['outcome'], bit, widths)
        lines.append(f'assign d_{hist}[{bit}] = (r_{hist}[{bit}] & ~{merged}) | ({merged} & {data});')
    text = text[:m.start()]+'\n'.join(lines)+'\n'+text[m.end():]

    # Reset/update storage replacement.
    startup = {str(x['register']): int(x['value'][1]) for x in ir['startup']['register_values']}
    if startup[ra]!=startup[rb]: 
        raise SystemExit('FAIL reset mismatch after analysis')
    reset_a = f"        r_{ra} <= {w}'d{startup[ra] & ((1<<w)-1)};"
    reset_b = f"        r_{rb} <= {w}'d{startup[rb] & ((1<<w)-1)};"
    if reset_a not in text or reset_b not in text: 
        raise SystemExit('FAIL reset lines missing')
    text = text.replace(reset_a, f"        r_{hist} <= {w}'d{startup[ra] & ((1<<w)-1)};", 1).replace(reset_b, '', 1)
    upd_a = f'            r_{ra} <= d_{ra};'
    upd_b = f'            r_{rb} <= d_{rb};'
    if upd_a not in text or upd_b not in text: 
        raise SystemExit('FAIL update lines missing')
    text = text.replace(upd_a, f'            r_{hist} <= d_{hist};', 1).replace(upd_b, '', 1)

    # Update storage-plan comment only; semantic IR remains the proof authority.
    m = re.search(r'// Physical storage plan: 56 -> (\d+) bits[^\n]*', text)
    if not m: 
        raise SystemExit('FAIL physical storage comment absent')
    srcbits = int(m.group(1))
    dstbits = srcbits-w
    text = text[:m.start()]+f'// Physical storage plan: 56 -> {dstbits} bits via proof-backed correlated encoding + semantic-rule history coalescing.'+text[m.end():]
    a.output_sv.write_text(text)
    meta = {
        'version': 'history-coalesced-sv-v2-semantic-actions', 
        'registers': [ra, rb], 
        'history_register': hist, 
        'width': w, 
        'source_storage_bits': srcbits, 
        'candidate_storage_bits': dstbits, 
        'rules': {ra: str(xa['rule_id']), rb: str(xb['rule_id'])}, 
        'actions': acts, 
        'event_class': c['event_class'], 
        'outcome': c['outcome'], 
        'analysis_proof_model': ana.get('proof_model'), 
        'action_recovery': 'IR update_rules + generic emitter action grouping; no source-SV action-mask parsing', 
    }
    a.metadata.write_text(json.dumps(meta, indent = 2, sort_keys = True)+'\n')
    rpt = [
        'BIO2RTL HISTORY-COALESCED SV CANDIDATE', 
        '='*96, 
        f'pair                  : {ra},{rb}', 
        f'history               : {hist}[{w-1}:0]', 
        f'storage               : {srcbits} -> {dstbits} bits', 
        f"semantic rules        : {xa['rule_id']} / {xb['rule_id']}", 
        f"write actions         : {','.join(aa)} / {','.join(ab)}", 
        f'event class           : {c["event_class"]}', 
        'source outcome         : identical by legal-product IR proof', 
        'action recovery        : semantic IR, not source-SV equation parsing', 
        'RESULT                : CANDIDATE GENERATED', 
    ]
    a.report.write_text('\n'.join(rpt)+'\n')
    print('\n'.join(rpt))
if __name__ == '__main__': 
    main()
