from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .hardware_pattern import HardwarePatternResult, HardwarePattern
from .next_state_expr import Expr, NextStateExprIR
from .rtl_ir import RTLIR
from .semantic_reach import SemanticReachResult


@dataclass
class HardwareObjectFamily: 
    family: str
    width: int
    hardware_kind: str
    effective_writes: int
    cfg_write_edges: int
    distinct_update_forms: int
    update_form_uses: dict[str, int]
    pattern_kinds: dict[str, int]
    recurrence_uses: int
    recurrence_forms: int
    cfg_to_recurrence_ratio: float | None
    likely_object: str


@dataclass
class HardwareObjectAnalysis: 
    physical_families: int
    physical_bits: int
    semantic_reach_blocks: int
    semantic_reach_edges: int
    effective_writes: int
    effective_write_edges: int
    distinct_update_forms: int
    recurrence_uses: int
    recurrence_forms: int
    families: list[HardwareObjectFamily]


def _expr_key(expr: Expr) -> tuple: 
    if expr.kind == "CONST": 
        return ("CONST", expr.value)
    if expr.kind == "STATE": 
        return ("STATE", expr.state_family)
    if expr.kind == "GPIO": 
        # GPIO SSA names are intentionally not collapsed here.  The report is
        # diagnostic: exact equality is preferable to optimistic equivalence.
        return ("GPIO", expr.gpio_value)
    if expr.kind == "SEMANTIC": 
        return ("SEMANTIC", expr.semantic_value)
    if expr.kind == "OP": 
        return ("OP", expr.operation, tuple(_expr_key(a) for a in expr.args))
    return (expr.kind, repr(expr))


def _pattern_key(pattern: HardwarePattern) -> tuple: 
    kind = pattern.kind
    if kind == "LOAD_CONST": 
        return (kind, pattern.constant)
    if kind == "STATE_COPY": 
        return (kind, pattern.source_family)
    if kind in ("UP_COUNTER", "DOWN_COUNTER"): 
        # All instances for one family are the same hardware recurrence.
        return (kind,)
    if kind == "SHIFT_LEFT_INSERT": 
        return (
            kind, 
            pattern.shift_amount, 
            _expr_key(pattern.insert_expression) if pattern.insert_expression else None, 
        )
    return (kind, _expr_key(pattern.expression))


def _format_key(key: tuple) -> str: 
    kind = key[0]
    if kind == "LOAD_CONST": 
        return f"LOAD_CONST({key[1]})"
    if kind == "STATE_COPY": 
        return f"STATE_COPY({key[1]})"
    if kind in ("UP_COUNTER", "DOWN_COUNTER"): 
        return kind
    if kind == "SHIFT_LEFT_INSERT": 
        return f"SHIFT_LEFT_INSERT(k={key[1]},insert={key[2]!r})"
    return f"{kind}:{key[1]!r}" if len(key) > 1 else kind


def _likely_object(kind: str, pattern_kinds: Counter[str]) -> str: 
    if kind == "SHIFT_REGISTER": 
        return "SHIFT_REGISTER"
    if kind == "UP_COUNTER": 
        return "COUNTER_UP"
    if kind == "DOWN_COUNTER": 
        return "COUNTER_DOWN"
    if pattern_kinds.get("STATE_COPY", 0) and pattern_kinds.get("LOAD_CONST", 0): 
        return "CONTROL_OR_DATA_REGISTER"
    if pattern_kinds.get("GENERIC_EXPR", 0) and sum(pattern_kinds.values()) <= 4: 
        return "SMALL_DATAPATH_REGISTER"
    if set(pattern_kinds) <= {"LOAD_CONST"}: 
        return "FLAG_OR_ENUM_REGISTER"
    return "GENERAL_REGISTER"


def analyze_hardware_objects(
    rtl_ir: RTLIR, 
    next_state: NextStateExprIR, 
    hardware_patterns: HardwarePatternResult, 
    semantic_reach: SemanticReachResult, 
) -> HardwareObjectAnalysis: 
    by_family_patterns: dict[str, list[HardwarePattern]] = defaultdict(list)
    by_family_edges: dict[str, set[tuple[int, int]]] = defaultdict(set)

    for pattern in hardware_patterns.patterns: 
        by_family_patterns[pattern.family].append(pattern)
        by_family_edges[pattern.family].add((pattern.source_block, pattern.target_block))

    recurrence_kinds = {"UP_COUNTER", "DOWN_COUNTER", "SHIFT_LEFT_INSERT"}
    family_rows: list[HardwareObjectFamily] = []
    all_form_keys: set[tuple[str, tuple]] = set()
    total_recurrence_uses = 0
    all_recurrence_forms: set[tuple[str, tuple]] = set()

    for family, reg in sorted(rtl_ir.registers.items()): 
        pats = by_family_patterns.get(family, [])
        form_counter: Counter[tuple] = Counter(_pattern_key(p) for p in pats)
        pkinds = Counter(p.kind for p in pats)
        recurrence_counter = Counter(
            _pattern_key(p) for p in pats if p.kind in recurrence_kinds
        )

        for key in form_counter: 
            all_form_keys.add((family, key))
        for key, count in recurrence_counter.items(): 
            all_recurrence_forms.add((family, key))
            total_recurrence_uses += count

        rec_uses = sum(recurrence_counter.values())
        rec_forms = len(recurrence_counter)
        ratio = (rec_uses / rec_forms) if rec_forms else None

        family_rows.append(HardwareObjectFamily(
            family = family, 
            width = reg.width, 
            hardware_kind = reg.hardware_kind, 
            effective_writes = len(pats), 
            cfg_write_edges = len(by_family_edges.get(family, set())), 
            distinct_update_forms = len(form_counter), 
            update_form_uses = {
                _format_key(k): v for k, v in sorted(form_counter.items(), key = lambda kv: (-kv[1], str(kv[0])))
            }, 
            pattern_kinds = dict(sorted(pkinds.items())), 
            recurrence_uses = rec_uses, 
            recurrence_forms = rec_forms, 
            cfg_to_recurrence_ratio = ratio, 
            likely_object = _likely_object(reg.hardware_kind, pkinds), 
        ))

    effective_edges = {
        (w.source_block, w.target_block)
        for w in next_state.writes
    }

    return HardwareObjectAnalysis(
        physical_families = len(rtl_ir.registers), 
        physical_bits = rtl_ir.total_register_bits, 
        semantic_reach_blocks = semantic_reach.total_blocks, 
        semantic_reach_edges = semantic_reach.total_edges, 
        effective_writes = len(next_state.writes), 
        effective_write_edges = len(effective_edges), 
        distinct_update_forms = len(all_form_keys), 
        recurrence_uses = total_recurrence_uses, 
        recurrence_forms = len(all_recurrence_forms), 
        families = family_rows, 
    )


def write_hardware_object_report(result: HardwareObjectAnalysis, path: Path) -> None: 
    lines: list[str] = []
    lines.append("CPU-PROGRAM -> HARDWARE OBJECT DIAGNOSTIC")
    lines.append("=" * 72)
    lines.append(f"physical state families       : {result.physical_families}")
    lines.append(f"physical state bits           : {result.physical_bits}")
    lines.append(f"semantic CFG reach blocks     : {result.semantic_reach_blocks}")
    lines.append(f"semantic CFG reach edges      : {result.semantic_reach_edges}")
    lines.append(f"effective state writes        : {result.effective_writes}")
    lines.append(f"effective CFG write edges     : {result.effective_write_edges}")
    lines.append(f"distinct state update forms   : {result.distinct_update_forms}")
    lines.append(f"recurrence occurrences        : {result.recurrence_uses}")
    lines.append(f"distinct recurrence forms     : {result.recurrence_forms}")
    if result.recurrence_forms: 
        lines.append(
            "recurrence occurrence/form   : "
            f"{result.recurrence_uses / result.recurrence_forms:.2f}x"
        )
    lines.append("")
    lines.append("Per-family inferred hardware objects")
    lines.append("-" * 72)

    for row in result.families: 
        lines.append(
            f"{row.family} [{row.width} bit] -> {row.likely_object}"
        )
        lines.append(f"  effective writes      : {row.effective_writes}")
        lines.append(f"  CFG write edges       : {row.cfg_write_edges}")
        lines.append(f"  distinct update forms : {row.distinct_update_forms}")
        lines.append(
            "  recurrence            : "
            f"{row.recurrence_uses} occurrence(s) / {row.recurrence_forms} form(s)"
        )
        if row.cfg_to_recurrence_ratio is not None: 
            lines.append(
                "  recurrence duplication: "
                f"{row.cfg_to_recurrence_ratio:.2f}x"
            )
        lines.append(
            "  pattern kinds         : "
            + (", ".join(f"{k}={v}" for k, v in row.pattern_kinds.items()) or "-")
        )
        lines.append("  update forms:")
        for form, count in row.update_form_uses.items(): 
            lines.append(f"    {count:3d}x {form}")
        lines.append("")

    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n", encoding = "utf-8")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), 
        encoding = "utf-8", 
    )
