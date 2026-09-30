#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_expression_minimization import minimize_dedicated_event_expressions


def main()->int: 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    ap.add_argument('--report', type = Path, required = True)
    a = ap.parse_args()
    src = json.loads(a.ir.read_text())
    dst, stats = minimize_dedicated_event_expressions(src)
    a.output.parent.mkdir(parents = True, exist_ok = True)
    a.output.write_text(json.dumps(dst, indent = 2, sort_keys = True)+'\n')
    s = stats
    lines = ['DEDICATED EVENT EXPRESSION MINIMIZATION', '='*88, 
      f'predicate expressions       : {s.predicate_expressions}', f'predicates changed          : {s.predicates_changed}', 
      f'predicate AST nodes         : {s.predicate_nodes_before} -> {s.predicate_nodes_after}', 
      f'materialized update rules   : {s.materialized_rules}', f'outcomes changed            : {s.outcome_expressions_changed}', 
      f'distinct outcomes           : {s.distinct_outcomes_before} -> {s.distinct_outcomes_after}', 
      f'outcome AST nodes           : {s.outcome_nodes_before} -> {s.outcome_nodes_after}', 
      f'startup expressions changed : {s.startup_expressions_changed}']
    a.report.parent.mkdir(parents = True, exist_ok = True)
    a.report.write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))
    return 0
if __name__ == '__main__': 
    raise SystemExit(main())
