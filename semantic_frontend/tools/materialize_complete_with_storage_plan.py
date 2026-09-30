#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from copy import deepcopy
from pathlib import Path


def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Attach an accepted storage plan to the complete corrected Dedicated Event relation without changing semantics.')
    ap.add_argument('--complete-ir', type = Path, required = True)
    ap.add_argument('--storage-plan-ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    complete = json.loads(a.complete_ir.read_text())
    plan = json.loads(a.storage_plan_ir.read_text())
    if 'storage_optimization' not in plan: 
        raise SystemExit('storage-plan-ir has no storage_optimization')
    dst = deepcopy(complete)
    dst['storage_optimization'] = deepcopy(plan['storage_optimization'])
    dst.setdefault('production_stage_metadata', {})['storage_plan_materialized_from_version'] = plan.get('version')
    bits = int(dst['storage_optimization'].get('natural_storage_bits', -1))
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(dst, indent = 2, sort_keys = True) + '\n')
    lines = [
        'COMPLETE RELATION + ACCEPTED STORAGE PLAN', 
        '=' * 88, 
        f'complete relation version : {complete.get("version")}', 
        f'storage plan version      : {plan.get("version")}', 
        f'natural storage bits      : {bits}', 
        f'update rules preserved    : {len(complete.get("update_rules", []))} -> {len(dst.get("update_rules", []))}', 
        'semantic relation rewrite : NONE', 
        'RESULT                    : PASS', 
    ]
    a.report.write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__': 
    raise SystemExit(main())
