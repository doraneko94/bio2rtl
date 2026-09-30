from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from .dedicated_hardware_ir import DedicatedHardwareIR


def write_dedicated_hardware_report(result: DedicatedHardwareIR, path: Path) -> None: 
    lines = [
        "DEDICATED HARDWARE IR / EVENT SCHEDULING DIAGNOSTIC", 
        "=" * 78, 
        f"behavioral hardware rules          : {result.behavioral_rules}", 
        f"event-scheduled rules              : {result.event_scheduled_rules}", 
        f"event-scheduled CFG occurrences    : {result.event_scheduled_occurrences}", 
        f"definition sites under event rules : {result.event_scheduled_definition_sites}", 
        f"CFG occurrences eliminable         : {result.cfg_occurrences_eliminable_by_schedule}", 
        f"mixed-event rules                  : {result.mixed_event_rules}", 
        f"no-event rules                     : {result.no_event_rules}", 
        "", 
        "Recovered event-scheduled operations", 
        "-" * 78, 
    ]
    for op in sorted(result.operations, key = lambda x: (x.schedule_kind != "EVENT_SCHEDULED", x.register, x.operation_kind, x.expression_key)): 
        if op.schedule_kind != "EVENT_SCHEDULED": 
            continue
        lines += [
            f"{op.register}: {op.operation_kind}", 
            f"  CFG occurrences : {op.occurrence_count}", 
            f"  definition BBs  : {', '.join(f'BB{x:03d}' for x in op.definition_blocks) or '-'}", 
            f"  schedule        : {op.canonical_event} GPIO[{op.event_gpio_bit}] {op.event_edge}", 
            f"  qualifiers      : {', '.join(op.event_qualifiers) or '-'}", 
            f"  residual forms  : {len(op.residual_guard_signatures)}", 
            f"  expression      : {op.expression_key}", 
        ]
        for sig in op.residual_guard_signatures[:8]: 
            lines.append(f"    guard: {' && '.join(sig) or '<event only>'}")
        if len(op.residual_guard_signatures) > 8: 
            lines.append(f"    ... {len(op.residual_guard_signatures)-8} more")
        lines.append("")
    lines += [
        "Interpretation", 
        "-" * 78, 
        "- EVENT_SCHEDULED means every CFG spelling of the same hardware update is dominated by the same recovered physical event.", 
        "- Residual guards are retained. This pass never assumes that event recovery alone is enough to fire the operation.", 
        "- A large CFG-occurrence/definition-site ratio is direct evidence that CPU path structure can be removed from scheduling.", 
        "- No I2C protocol names, stack offsets, or reference-RTL signal names are used by the recovery rule.", 
    ]
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n")
    path.with_suffix(path.suffix + ".json").write_text(json.dumps(asdict(result), indent = 2, sort_keys = True))
