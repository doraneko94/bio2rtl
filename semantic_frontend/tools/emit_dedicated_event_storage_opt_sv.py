#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_sv_emitter import emit_storage_optimized_dedicated_event_sv

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--precise-expressions', action = 'store_true')
    ap.add_argument('--optimize-prefix-order', action = 'store_true')
    ap.add_argument('--bitwise-update-equations', action = 'store_true')
    ap.add_argument('--event-semantic-update-equations', action = 'store_true')
    ap.add_argument('--bitwise-outcome-lowering', action = 'store_true')
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    text = emit_storage_optimized_dedicated_event_sv(ir, precise_expressions = a.precise_expressions, optimize_prefix_order = a.optimize_prefix_order, bitwise_update_equations = a.bitwise_update_equations or a.event_semantic_update_equations, event_semantic_update_equations = a.event_semantic_update_equations, bitwise_outcome_lowering = a.bitwise_outcome_lowering)
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(text)
    print(a.output)
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
