from __future__ import annotations
from pathlib import Path
import json, re
from .stage6_generic_certificates import _eval_expr, _refs


def _load(p: Path): 
    return json.loads(Path(p).read_text())

def _reg_width(phase: dict, reg: str)->int: 
    for x in phase.get('architectural_registers', []): 
        if str(x.get('id')) == reg: 
            return int(x['width'])
    raise KeyError(reg)

def _basis_selector_compat(phase: dict, selector: str, value: int)->dict[str, bool]: 
    out = {}
    for b in phase.get('predicate_basis', []): 
        e = b.get('expression')
        refs = _refs(e)
        if refs and refs <= {selector}: 
            try: 
                out[str(b['id'])] = bool(_eval_expr(e, {selector: value}))
            except Exception: 
                pass
    return out

def _selector_values_for_rule(phase: dict, rule: dict, selector: str)->list[int]: 
    width = _reg_width(phase, selector)
    vals = []
    for v in range(1<<width): 
        known = _basis_selector_compat(phase, selector, v)
        ok = True
        for en in rule.get('enable', []): 
            bid = str(en['basis'])
            if bid in known and known[bid] != bool(en['polarity']): 
                ok = False
                break
        if ok: 
            vals.append(v)
    return vals

def _mirror_map(storrel: dict)->dict[str, dict]: 
    out = {}
    for rel in storrel.get('gpio_mirror_fusion', {}).get('relations', []): 
        m = re.match(r'^\s*([A-Za-z_]\w*)\s*=\s*(~)?([A-Za-z_]\w*)\[(\d+):(\d+)\]\s*$', str(rel))
        if m: 
            out[m.group(1)] = {'kind': 'register_slice', 'source': m.group(3), 'invert': bool(m.group(2)), 
                             'hi': int(m.group(4)), 'lo': int(m.group(5)), 'relation': rel}
    return out

def _normalize_source(expr, mirrors: dict)->dict|None: 
    if not isinstance(expr, list) or not expr: 
        return None
    tag = expr[0]
    if tag == 'CONST': 
        return {'kind': 'constant', 'value': int(expr[1])}
    if tag == 'REG': 
        r = str(expr[1])
        return mirrors.get(r, {'kind': 'register', 'source': r, 'invert': False})
    # Detect (GPIO_INPUT >> lo) & mask, irrespective of AND argument order.
    if tag == 'OP' and expr[1] == 'AND': 
        args = expr[2]
        mask = None
        shifted = None
        for a in args: 
            if isinstance(a, list) and a and a[0] == 'CONST': 
                mask = int(a[1])
            elif isinstance(a, list): 
                shifted = a
        if mask is not None and shifted and shifted[0] == 'OP' and shifted[1] == 'SHR': 
            aa = shifted[2]
            if len(aa) == 2 and aa[0] == ['GPIO_INPUT'] and aa[1][0] == 'CONST': 
                lo = int(aa[1][1])
                width = max(1, int(mask).bit_length())
                # Require a contiguous low mask for a clean slice certificate.
                if mask == (1<<width)-1: 
                    return {'kind': 'external_input_slice', 'lo': lo, 'hi': lo+width-1, 'invert': False}
    return {'kind': 'expression', 'expression': expr}

def discover_observation_source_projection(root: Path)->dict: 
    root = Path(root)
    phase = _load(root/'build/semantic/phase40.ir.json')
    snap = _load(root/'build/generated_certificates/observation_snapshot.json')
    stor = _load(root/'build/generated_certificates/storage_relations.json')
    if snap.get('status')!='PASS' or stor.get('status')!='PASS': 
        return {'version': 'bio2rtl-observation-source-projection-v1', 'status': 'N_A', 'reason': 'required proofs not PASS'}
    obs = str(snap['observation_register'])
    sel = str(snap['selector_register'])
    mirrors = _mirror_map(stor)
    rules = [r for r in phase.get('update_rules', []) if str(r.get('target')) == obs and r.get('materialize')]
    by_value = {v: [] for v in range(1<<_reg_width(phase, sel))}
    rows = []
    for r in rules: 
        src = _normalize_source(r.get('outcome'), mirrors)
        vals = _selector_values_for_rule(phase, r, sel)
        row = {'rule_id': r.get('rule_id'), 'source_transition_ids': r.get('source_transition_ids', []), 
             'selector_values': vals, 'source': src}
        rows.append(row)
        for v in vals: 
            by_value[v].append(src)
    # Deduplicate identical source descriptions. If selector-only predicates do not
    # uniquely select a rule, retain ambiguity as a hard failure rather than guessing.
    final = {}
    amb = {}
    for v, xs in by_value.items(): 
        uniq = []
        for x in xs: 
            if x not in uniq: 
                uniq.append(x)
        if len(uniq) == 1: 
            final[str(v)] = uniq[0]
        else: 
            amb[str(v)] = uniq
    ext = [(int(v), x) for v, x in final.items() if x.get('kind') == 'external_input_slice']
    expected = sorted(map(int, snap.get('selector_values', [])))
    status = 'PASS' if sorted(map(int, final)) == expected and not amb and ext else 'FAIL'
    return {
      'version': 'bio2rtl-observation-source-projection-v1', 'status': status, 
      'observation_register': obs, 'selector_register': sel, 'selector_width': _reg_width(phase, sel), 
      'selector_values': expected, 'source_by_selector': final, 'ambiguous_or_missing': amb, 
      'external_input_selector_values': [v for v, _ in ext], 
      'snapshot_bits': snap.get('snapshot_bits', []), 'rule_evidence': rows, 
      'authority': ['phase40.update_rules', 'observation_snapshot', 'storage_relations'], 
      'physical_recipe_used': False, 'protocol_names_used': False, 
    }

def emit_observation_source_projection(root: Path)->dict: 
    root = Path(root)
    d = discover_observation_source_projection(root)
    p = root/'build/generated_certificates/observation_source_projection.json'
    p.parent.mkdir(parents = True, exist_ok = True)
    p.write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    return d
