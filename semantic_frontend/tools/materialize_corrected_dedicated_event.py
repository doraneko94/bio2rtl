#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_materializer import materialize_dedicated_event_ir


def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Rebuild Dedicated Event architectural relation from corrected FSE transitions using a scheduler template.')
    ap.add_argument('--fse', type = Path, required = True)
    ap.add_argument('--scheduler-template', type = Path, required = True)
    ap.add_argument('-o', '--output', type = Path, required = True)
    ns = ap.parse_args()
    fse = json.loads(ns.fse.read_text())
    template = json.loads(ns.scheduler_template.read_text())
    out = materialize_dedicated_event_ir(fse, template)
    ns.output.parent.mkdir(parents = True, exist_ok = True)
    ns.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
    print(f"output={ns.output}")
    print(f"source_transitions={out['verification']['source_transitions']}")
    print(f"architectural_registers={len(out['architectural_registers'])}")
    print(f"predicate_basis={len(out['predicate_basis'])}")
    print(f"update_rules={len(out['update_rules'])}")
    print(f"materialized_rules={sum(bool(r['materialize']) for r in out['update_rules'])}")
    print(f"relation_checks={out['verification']['relation_checks']}")
    print('structural_pass='+('PASS' if out['verification']['structural_pass'] else 'FAIL'))
    return 0

if __name__ == '__main__': 
    raise SystemExit(main())
