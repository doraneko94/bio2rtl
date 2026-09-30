from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .hardware_behavior_ir import HardwareBehaviorIR, expr_key


def _expr_short(expr) -> str: 
    key = expr_key(expr)
    text = repr(key)
    return text if len(text) <= 180 else text[:177] + "..."


def write_hardware_behavior_report(ir: HardwareBehaviorIR, path: Path) -> None: 
    lines = []
    lines.append("BEHAVIORAL HARDWARE IR")
    lines.append("=" * 72)
    lines.append(f"hardware objects             : {len(ir.objects)}")
    lines.append(f"original CFG-selected writes : {ir.original_edge_writes}")
    lines.append(f"collapsed update rules       : {ir.collapsed_rules}")
    if ir.original_edge_writes: 
        reduction = 1.0 - ir.collapsed_rules / ir.original_edge_writes
        lines.append(f"write->rule reduction        : {100.0*reduction:.2f}%")
        lines.append(f"writes per rule              : {ir.original_edge_writes/ir.collapsed_rules:.2f}x")
    lines.append("")

    for name, obj in sorted(ir.objects.items()): 
        lines.append(f"{name} [{obj.width} bit] {obj.hardware_kind}")
        lines.append(f"  rules : {len(obj.rules)}")
        for idx, rule in enumerate(sorted(obj.rules, key = lambda r: (-r.occurrence_count, r.operation_kind))): 
            edges = ", ".join(f"BB{e.source_block}->BB{e.target_block}" for e in rule.guard_edges)
            lines.append(
                f"  R{idx:02d} {rule.operation_kind:<18} occurrences={rule.occurrence_count}"
            )
            lines.append(f"      expr  : {_expr_short(rule.expression)}")
            lines.append(f"      edges : {edges}")
        lines.append("")

    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines)+"\n", encoding = "utf-8")

    payload = {
        "original_edge_writes": ir.original_edge_writes, 
        "collapsed_rules": ir.collapsed_rules, 
        "objects": {
            name: {
                "width": obj.width, 
                "hardware_kind": obj.hardware_kind, 
                "rules": [
                    {
                        "operation_kind": r.operation_kind, 
                        "occurrence_count": r.occurrence_count, 
                        "guard_edges": [asdict(e) for e in r.guard_edges], 
                        "expression_key": repr(expr_key(r.expression)), 
                    }
                    for r in obj.rules
                ], 
            }
            for name, obj in sorted(ir.objects.items())
        }, 
    }
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(payload, indent = 2, sort_keys = True), encoding = "utf-8"
    )
