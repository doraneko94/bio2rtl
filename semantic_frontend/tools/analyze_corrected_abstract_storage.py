#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.event_boundary_state_analysis import PersistentStateSpec, analyze_packed_gpio_state
from bio2rtl.event_boundary_abstract_storage_analysis import analyze_event_boundary_abstract_storage, result_to_dict

def c(raw): 
    if not isinstance(raw, list) or len(raw)!=2 or raw[0] != 'CONST': 
        raise ValueError(raw)
    return int(raw[1])

def specs(ir): 
    out = []
    for r in ir['architectural_registers']: 
        if r['kind'] not in ('PHYSICAL', 'SEMANTIC'): 
            continue
        out.append(PersistentStateSpec(str(r['provenance']), int(r['width']), c(r['reset']), 'ARCHITECTURAL' if r['kind'] == 'PHYSICAL' else 'SEMANTIC'))
    for s in ir.get('scheduler_owned_sources', []): 
        out.append(PersistentStateSpec(str(s['source']), int(s.get('width', 1)), c(s['initial_value']), 'SCHEDULER'))
    return out

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--transitions', type = Path, required = True)
    ap.add_argument('--dedicated-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    fse = json.loads(a.transitions.read_text())
    ir = json.loads(a.dedicated_ir.read_text())
    r = analyze_event_boundary_abstract_storage(fse['transition_rows'], specs(ir))
    gp = analyze_packed_gpio_state(ir)
    gpio_bits = sum(x.storage_bits for x in gp)
    total = r.natural_storage_bits+gpio_bits+1
    lines = [
      'CORRECTED EVENT-BOUNDARY ABSTRACT STORAGE ANALYSIS', '='*94, 
      f'proof model                         : {r.proof_model}', 
      f'source transitions                  : {r.source_transitions}', 
      f'iterations                          : {r.iterations}', 
      f'final potential transition rows     : {r.final_potential_transition_rows}', 
      f'original non-GPIO+scheduler storage : {r.original_storage_bits} bit', 
      f'natural non-GPIO+scheduler storage  : {r.natural_storage_bits} bit', 
      f'packed GPIO storage                 : {gpio_bits} bit', 
      f'active_run                          : 1 bit', 
      f'total natural storage               : {total} bit', 
      f'full-domain fallbacks               : {r.expression_full_domain_fallbacks}', 
      '', 'Persistent state families', '-'*94]
    for x in r.state_rows: 
        vals = x.reachable_values_upper_bound
        vs = str(vals) if len(vals)<=20 else f'{vals[:8]} ... {vals[-3:]} ({len(vals)} values)'
        lines.append(f'{x.state:24} role={x.role:13} width={x.original_width:2} natural={x.natural_width:2} values={vs}')
        if x.sparse_encoding_candidate_bits is not None: 
            lines.append(f'  sparse candidate={x.sparse_encoding_candidate_bits} bit (NOT APPLIED)')
    lines += ['', 'Packed GPIO', '-'*94]
    for x in gp: 
        lines.append(f'{x.register:8} storage={x.storage_bits} variable_bits={x.variable_bits} values={[hex(v) for v in x.reachable_masked_values]}')
    lines += ['', 'Notes', '-'*94] + [f'- {n}' for n in r.notes]
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text('\n'.join(lines)+'\n')
    d = result_to_dict(r)
    d['packed_gpio_state'] = [x.__dict__ for x in gp]
    d['storage_summary'] = {'non_gpio_scheduler_natural_bits': r.natural_storage_bits, 'packed_gpio_bits': gpio_bits, 'active_run_bits': 1, 'total_natural_bits': total}
    a.output.with_suffix(a.output.suffix+'.json').write_text(json.dumps(d, indent = 2, sort_keys = True)+'\n')
    print('\n'.join(lines[:10]))
    print(a.output)
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
