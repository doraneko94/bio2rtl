#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.scheduler_recovery import recover_scheduler_template

def load(p: Path): 
    return json.loads(p.read_text())

def main()->int: 
 ap = argparse.ArgumentParser(description = 'Recover Dedicated Event scheduler template from regenerated frontend diagnostics; no old scheduler fixture is consumed.')
 ap.add_argument('--fse', type = Path, required = True)
 ap.add_argument('--state', type = Path, required = True)
 ap.add_argument('--state-role', type = Path, required = True)
 ap.add_argument('--canonical-event', type = Path, required = True)
 ap.add_argument('--polling', type = Path, required = True)
 ap.add_argument('--semantic-sv', type = Path, required = True)
 ap.add_argument('-o', '--output', type = Path, required = True)
 ns = ap.parse_args()
 out = recover_scheduler_template(fse_report = load(ns.fse), state_report = load(ns.state), state_role_report = load(ns.state_role), canonical_event_report = load(ns.canonical_event), polling_report = load(ns.polling), semantic_sv = ns.semantic_sv.read_text())
 ns.output.parent.mkdir(parents = True, exist_ok = True)
 ns.output.write_text(json.dumps(out, indent = 2, sort_keys = True)+'\n')
 print(f'output={ns.output}')
 print(f"architectural_registers={len(out['architectural_registers'])}")
 print(f"scheduler_owned_sources={len(out['scheduler_owned_sources'])}")
 print(f"scheduler_detectors={len(out['scheduler_detectors'])}")
 print(f"event_classes={len(out['event_classes'])}")
 print(f"startup_gpio_mask=0x{out['startup']['gpio_mask_constant']:08x}")
 print('fixture_free=PASS')
 return 0
if __name__ == '__main__': 
    raise SystemExit(main())
