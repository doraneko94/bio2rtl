#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_guard_minimization import minimize_dedicated_event_guards


def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    out, stats = minimize_dedicated_event_guards(ir)
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    for k, v in stats.__dict__.items(): 
        print(f'{k}={v}')
    print(a.output)
if __name__ == '__main__': 
    main()
