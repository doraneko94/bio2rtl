#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_abstract_storage import annotate_abstract_storage

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--dedicated-ir', type = Path, required = True)
    ap.add_argument('--analysis', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.dedicated_ir.read_text())
    an = json.loads(a.analysis.read_text())
    out = annotate_abstract_storage(ir, an)
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    p = out['storage_optimization']
    print(f"storage={p['preoptimization_storage_upper_bound_bits']}->{p['natural_storage_bits']} bit")
    print(f"proof_model={p['proof_model']}")
    print(a.output)
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
