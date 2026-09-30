from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .hardware_behavior_ir import HardwareBehaviorIR, expr_key
from .semantic_next_state import GuardExpr, SemanticNextStateResult


Literal = tuple[str, str | int, bool]
Cube = frozenset[Literal]


@dataclass
class HardwareEnableRuleDiagnostic: 
    register: str
    operation_kind: str
    occurrence_count: int
    exact_guard_occurrences: int
    raw_cubes: int
    raw_literal_uses: int
    simplified_cubes: int
    simplified_literal_uses: int
    cube_reduction_pct: float
    literal_reduction_pct: float
    region_atoms: list[str]
    predicate_blocks: list[int]
    simplified_terms: list[str]


@dataclass
class HardwareEnableAnalysisResult: 
    rules: list[HardwareEnableRuleDiagnostic]
    multi_path_rules: int
    multi_path_occurrences: int
    raw_cubes: int
    simplified_cubes: int
    raw_literal_uses: int
    simplified_literal_uses: int
    cube_reduction_pct: float
    literal_reduction_pct: float
    rules_with_reduction: int
    rules_collapsed_to_one_cube: int
    notes: list[str]


def _atom(expr: GuardExpr) -> Literal: 
    if expr.kind == "ACTIVE": 
        assert expr.region_name is not None
        return ("ACTIVE", expr.region_name, True)
    if expr.kind == "PRED": 
        assert expr.block_id is not None
        return ("PRED", expr.block_id, True)
    raise ValueError(f"not an atom: {expr.kind}")


def _negate_literal(lit: Literal) -> Literal: 
    return (lit[0], lit[1], not lit[2])


def _dnf(expr: GuardExpr, negated: bool = False) -> set[Cube]: 
    if expr.kind == "CONST": 
        value = bool(expr.value)
        if negated: 
            value = not value
        return {frozenset()} if value else set()

    if expr.kind in {"ACTIVE", "PRED"}: 
        lit = _atom(expr)
        if negated: 
            lit = _negate_literal(lit)
        return {frozenset((lit,))}

    if expr.kind == "NOT": 
        return _dnf(expr.args[0], not negated)

    if expr.kind not in {"AND", "OR"}: 
        raise ValueError(f"unsupported guard expression: {expr.kind}")

    # De Morgan when negated.
    effective_kind = expr.kind
    if negated: 
        effective_kind = "OR" if expr.kind == "AND" else "AND"

    parts = [_dnf(arg, negated) for arg in expr.args]

    if effective_kind == "OR": 
        out: set[Cube] = set()
        for part in parts: 
            out.update(part)
        return out

    out: set[Cube] = {frozenset()}
    for part in parts: 
        combined: set[Cube] = set()
        for left in out: 
            for right in part: 
                merged = set(left)
                contradiction = False
                for lit in right: 
                    if _negate_literal(lit) in merged: 
                        contradiction = True
                        break
                    merged.add(lit)
                if not contradiction: 
                    combined.add(frozenset(merged))
        out = combined
        if not out: 
            break
    return out


def _absorb(cubes: set[Cube]) -> set[Cube]: 
    ordered = sorted(cubes, key = lambda c: (len(c), sorted(c)))
    kept: list[Cube] = []
    for cube in ordered: 
        if any(prev <= cube for prev in kept): 
            continue
        kept.append(cube)
    return set(kept)


def _combine_once(cubes: set[Cube]) -> tuple[set[Cube], bool]: 
    cubes = _absorb(cubes)
    items = list(cubes)
    additions: set[Cube] = set()
    used: set[Cube] = set()
    for i, a in enumerate(items): 
        for b in items[i + 1:]: 
            only_a = a - b
            only_b = b - a
            if len(only_a) != 1 or len(only_b) != 1: 
                continue
            la = next(iter(only_a))
            lb = next(iter(only_b))
            if la == _negate_literal(lb): 
                additions.add(frozenset(a & b))
                used.add(a)
                used.add(b)
    if not additions: 
        return cubes, False
    result = (cubes - used) | additions
    return _absorb(result), True


def simplify_cubes(cubes: set[Cube]) -> set[Cube]: 
    current = _absorb(cubes)
    while True: 
        current, changed = _combine_once(current)
        if not changed: 
            return _absorb(current)


def _literal_text(lit: Literal) -> str: 
    kind, ident, positive = lit
    if kind == "ACTIVE": 
        text = f"ACTIVE({ident})"
    else: 
        text = f"PRED(BB{int(ident):03d})"
    return text if positive else f"!{text}"


def _cube_text(cube: Cube) -> str: 
    if not cube: 
        return "1"
    return " & ".join(_literal_text(lit) for lit in sorted(cube, key = str))


def analyze_hardware_enables(
    behavior: HardwareBehaviorIR, 
    semantic_next_state: SemanticNextStateResult, 
) -> HardwareEnableAnalysisResult: 
    guards_by_key: dict[tuple[str, tuple], list[GuardExpr]] = {}
    for folded in semantic_next_state.writes: 
        key = (folded.write.family, expr_key(folded.write.expression))
        guards_by_key.setdefault(key, []).append(folded.guard)

    rows: list[HardwareEnableRuleDiagnostic] = []
    for rule in behavior.rules: 
        key = (rule.register, expr_key(rule.expression))
        guards = guards_by_key.get(key, [])
        if not guards: 
            continue

        raw: set[Cube] = set()
        for guard in guards: 
            raw.update(_dnf(guard))
        raw = _absorb(raw)
        simplified = simplify_cubes(raw)

        raw_lits = sum(len(c) for c in raw)
        simple_lits = sum(len(c) for c in simplified)
        cube_red = 0.0 if not raw else 100.0 * (len(raw) - len(simplified)) / len(raw)
        lit_red = 0.0 if not raw_lits else 100.0 * (raw_lits - simple_lits) / raw_lits
        regions = sorted({str(l[1]) for c in simplified for l in c if l[0] == "ACTIVE"})
        blocks = sorted({int(l[1]) for c in simplified for l in c if l[0] == "PRED"})

        rows.append(HardwareEnableRuleDiagnostic(
            register = rule.register, 
            operation_kind = rule.operation_kind, 
            occurrence_count = rule.occurrence_count, 
            exact_guard_occurrences = len(guards), 
            raw_cubes = len(raw), 
            raw_literal_uses = raw_lits, 
            simplified_cubes = len(simplified), 
            simplified_literal_uses = simple_lits, 
            cube_reduction_pct = cube_red, 
            literal_reduction_pct = lit_red, 
            region_atoms = regions, 
            predicate_blocks = blocks, 
            simplified_terms = [_cube_text(c) for c in sorted(simplified, key = lambda c: (len(c), _cube_text(c)))], 
        ))

    multi = [r for r in rows if r.occurrence_count > 1]
    raw_cubes = sum(r.raw_cubes for r in multi)
    simp_cubes = sum(r.simplified_cubes for r in multi)
    raw_lits = sum(r.raw_literal_uses for r in multi)
    simp_lits = sum(r.simplified_literal_uses for r in multi)

    return HardwareEnableAnalysisResult(
        rules = rows, 
        multi_path_rules = len(multi), 
        multi_path_occurrences = sum(r.occurrence_count for r in multi), 
        raw_cubes = raw_cubes, 
        simplified_cubes = simp_cubes, 
        raw_literal_uses = raw_lits, 
        simplified_literal_uses = simp_lits, 
        cube_reduction_pct = 0.0 if not raw_cubes else 100.0 * (raw_cubes - simp_cubes) / raw_cubes, 
        literal_reduction_pct = 0.0 if not raw_lits else 100.0 * (raw_lits - simp_lits) / raw_lits, 
        rules_with_reduction = sum(1 for r in multi if r.simplified_literal_uses < r.raw_literal_uses), 
        rules_collapsed_to_one_cube = sum(1 for r in multi if r.simplified_cubes == 1), 
        notes = [
            "Guards are exact symbolic reach guards grouped by identical state update expression.", 
            "Simplification removes CFG path distinctions only by Boolean absorption and complementary-literal merging.", 
            "PRED(BBxxx) atoms are still program-control predicates; this report measures path collapse before semantic predicate lowering.", 
        ], 
    )


def write_hardware_enable_report(result: HardwareEnableAnalysisResult, path: Path) -> None: 
    lines = [
        "CPU PATH -> HARDWARE ENABLE DIAGNOSTIC", 
        "=" * 76, 
        f"multi-path hardware rules       : {result.multi_path_rules}", 
        f"CFG-selected occurrences        : {result.multi_path_occurrences}", 
        f"raw exact guard cubes           : {result.raw_cubes}", 
        f"simplified guard cubes          : {result.simplified_cubes}", 
        f"cube reduction                  : {result.cube_reduction_pct:.2f}%", 
        f"raw guard literal uses          : {result.raw_literal_uses}", 
        f"simplified guard literal uses   : {result.simplified_literal_uses}", 
        f"literal reduction               : {result.literal_reduction_pct:.2f}%", 
        f"rules with guard reduction      : {result.rules_with_reduction}", 
        f"rules collapsed to one cube     : {result.rules_collapsed_to_one_cube}", 
        "", 
        "Per hardware update rule", 
        "-" * 76, 
    ]
    for row in sorted(result.rules, key = lambda r: (-r.occurrence_count, r.register, r.operation_kind)): 
        if row.occurrence_count <= 1: 
            continue
        lines.extend([
            f"{row.register} :: {row.operation_kind}", 
            f"  CFG occurrences     : {row.occurrence_count}", 
            f"  exact guard instances: {row.exact_guard_occurrences}", 
            f"  guard cubes         : {row.raw_cubes} -> {row.simplified_cubes} ({row.cube_reduction_pct:.2f}% reduction)", 
            f"  literal uses        : {row.raw_literal_uses} -> {row.simplified_literal_uses} ({row.literal_reduction_pct:.2f}% reduction)", 
            f"  active regions      : {', '.join(row.region_atoms) or '-'}", 
            f"  remaining predicates: {', '.join(f'BB{x:03d}' for x in row.predicate_blocks) or '-'}", 
            "  simplified enable terms:", 
        ])
        for term in row.simplified_terms: 
            lines.append(f"    {term}")
        lines.append("")

    lines.append("Notes")
    lines.append("-" * 76)
    lines.extend(f"- {note}" for note in result.notes)
    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines) + "\n", encoding = "utf-8")
    path.with_suffix(path.suffix + ".json").write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), encoding = "utf-8"
    )
