from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
import json
from pathlib import Path

from .control_expr import ControlExpr
from .edge_state_semantics import EdgeStateSemantics
from .final_state import FinalStateResult
from .gpio_effect import GPIOEffectResult
from .hardware_pattern import HardwarePatternResult
from .ir import IRBlock, IROp
from .logical_state import LogicalStateAnalysis
from .next_state_expr import Expr, NextStateExprIR
from .control_predicate_analysis import ControlPredicateResult


@dataclass
class FamilyDiagnostic: 
    family: str
    width: int
    proof: str
    structural_role: str
    phi_headers: list[int]
    initial_values: list[str]
    feedback_values: list[str]
    edge_writes: int
    effective_writes: int
    write_kinds: dict[str, int]
    hardware_patterns: dict[str, int]
    expression_ops: dict[str, int]
    expression_depth_max: int
    expression_nodes: int
    state_dependencies: list[str]
    semantic_dependencies: list[str]
    gpio_dependencies: list[str]
    branch_predicate_count: int
    gpio_effect_dependency_count: int
    gpio_effect_kinds: dict[str, int]
    defining_blocks: list[int]
    defining_addresses: list[str]
    evidence: list[str]


@dataclass
class PhysicalStateDiagnosticResult: 
    families: list[FamilyDiagnostic]
    total_families: int
    total_bits: int
    total_edge_writes: int
    total_effective_writes: int
    families_used_by_control: int
    families_used_by_gpio_effects: int
    effective_write_edges: int
    max_effective_writes_on_edge: int
    max_write_edges: list[str]
    state_copy_relations: dict[str, int]
    repeated_rhs_groups: int


def _walk_expr(expr: Expr, out: dict) -> None: 
    out["nodes"] += 1
    if expr.kind == "STATE" and expr.state_family is not None: 
        out["states"].add(expr.state_family)
    elif expr.kind == "SEMANTIC" and expr.semantic_value is not None: 
        out["semantic"].add(expr.semantic_value)
    elif expr.kind == "GPIO" and expr.gpio_value is not None: 
        out["gpio"].add(expr.gpio_value)
    elif expr.kind == "OP" and expr.operation is not None: 
        out["ops"][expr.operation] += 1
    for arg in expr.args: 
        _walk_expr(arg, out)


def _expr_depth(expr: Expr) -> int: 
    if not expr.args: 
        return 1
    return 1 + max(_expr_depth(arg) for arg in expr.args)


def _walk_control_expr(expr: ControlExpr, states: set[str]) -> None: 
    if expr.kind == "STATE" and expr.state_family is not None: 
        states.add(expr.state_family)
    for arg in expr.args: 
        _walk_control_expr(arg, states)
    for _pred, incoming in expr.phi_inputs: 
        _walk_control_expr(incoming, states)


def _definition_map(blocks: list[IRBlock]) -> dict[str, tuple[int, IROp]]: 
    result = {}
    for block in blocks: 
        for op in block.ops: 
            if op.dst is not None: 
                result[op.dst] = (block.id, op)
    return result


def _collect_definition_sites(
    values: set[str], 
    definition_map: dict[str, tuple[int, IROp]], 
) -> tuple[set[int], set[int]]: 
    blocks: set[int] = set()
    addresses: set[int] = set()
    visiting: set[str] = set()

    def visit(value: str) -> None: 
        if value in visiting: 
            return
        visiting.add(value)
        item = definition_map.get(value)
        if item is None: 
            return
        block_id, op = item
        blocks.add(block_id)
        if op.address is not None: 
            addresses.add(op.address)
        for arg in op.args: 
            # Constants and BB annotations simply do not resolve in the map.
            visit(arg)

    for value in values: 
        visit(value)
    return blocks, addresses


def _role(patterns: Counter, width: int, write_kinds: Counter) -> tuple[str, list[str]]: 
    evidence: list[str] = []
    if patterns["SHIFT_LEFT_INSERT"]: 
        evidence.append("recurrence matches q'=(q<<k)|input")
        return "SHIFT_REGISTER", evidence
    if patterns["UP_COUNTER"] and not patterns["DOWN_COUNTER"]: 
        evidence.append("self recurrence contains increment")
        return "UP_COUNTER", evidence
    if patterns["DOWN_COUNTER"] and not patterns["UP_COUNTER"]: 
        evidence.append("self recurrence contains decrement")
        return "DOWN_COUNTER", evidence
    if width == 1: 
        if patterns["LOAD_CONST"]: 
            evidence.append("1-bit persistent state with constant loads")
        return "FLAG", evidence
    if patterns["LOAD_CONST"] and not patterns["GENERIC_EXPR"]: 
        evidence.append("multi-bit state dominated by constant/copy loads")
        return "ENUM_OR_SMALL_REGISTER", evidence
    if write_kinds["STATE_COPY"]: 
        evidence.append("receives data from another persistent state")
    if patterns["GENERIC_EXPR"]: 
        evidence.append("generic arithmetic/logic next-state expression present")
    return "DATA_OR_CONTROL_REGISTER", evidence


def analyze_physical_state_diagnostic(
    blocks: list[IRBlock], 
    logical: LogicalStateAnalysis, 
    final_state: FinalStateResult, 
    edge_semantics: EdgeStateSemantics, 
    next_state: NextStateExprIR, 
    hardware_patterns: HardwarePatternResult, 
    control_predicates: ControlPredicateResult, 
    gpio_effects: GPIOEffectResult, 
) -> PhysicalStateDiagnosticResult: 
    definition_map = _definition_map(blocks)

    all_writes = defaultdict(list)
    effective_writes = defaultdict(list)
    for write in edge_semantics.writes: 
        all_writes[write.family].append(write)
    for write in edge_semantics.effective_writes: 
        effective_writes[write.family].append(write)

    next_writes = defaultdict(list)
    for write in next_state.writes: 
        next_writes[write.family].append(write)

    gpio_family_kinds: dict[str, Counter] = defaultdict(Counter)
    for effect in gpio_effects.effects: 
        deps: set[str] = set()
        _walk_control_expr(effect.expression, deps)
        for family in deps: 
            gpio_family_kinds[family][effect.kind] += 1

    effective_by_edge: dict[tuple[int, int], list] = defaultdict(list)
    for write in edge_semantics.effective_writes: 
        effective_by_edge[(write.source_block, write.target_block)].append(write)

    max_writes = max((len(items) for items in effective_by_edge.values()), default = 0)
    max_write_edges = [
        f"BB{src:03d}->BB{dst:03d}"
        for (src, dst), items in sorted(effective_by_edge.items())
        if len(items) == max_writes
    ]

    copy_relations = Counter()
    for write in edge_semantics.effective_writes: 
        if write.kind == "STATE_COPY" and write.source_family is not None: 
            copy_relations[f"{write.source_family} -> {write.family}"] += 1

    repeated_rhs_groups = 0
    for _edge, items in effective_by_edge.items(): 
        rhs = Counter((item.kind, item.source_value) for item in items)
        repeated_rhs_groups += sum(1 for count in rhs.values() if count > 1)

    families: list[FamilyDiagnostic] = []
    for family, state in sorted(final_state.states.items()): 
        writes = all_writes.get(family, [])
        effective = effective_writes.get(family, [])
        write_kinds = Counter(write.kind for write in writes)

        pattern_objs = hardware_patterns.families.get(family)
        pattern_counts = Counter()
        if pattern_objs is not None: 
            pattern_counts.update(pattern.kind for pattern in pattern_objs.patterns)

        expr_info = {
            "nodes": 0, 
            "states": set(), 
            "semantic": set(), 
            "gpio": set(), 
            "ops": Counter(), 
        }
        depth_max = 0
        source_values: set[str] = set()
        for write in effective: 
            source_values.add(write.source_value)
        for write in next_writes.get(family, []): 
            _walk_expr(write.expression, expr_info)
            depth_max = max(depth_max, _expr_depth(write.expression))

        defining_blocks, defining_addresses = _collect_definition_sites(
            source_values, definition_map
        )

        logical_family = logical.stack_families.get(family)
        phi_headers = []
        initial_values: list[str] = []
        feedback_values: list[str] = []
        if logical_family is not None: 
            phi_headers = list(logical_family.header_blocks)
            initial_values = sorted(logical_family.initial_values)
            feedback_values = sorted(logical_family.feedback_values)

        role, evidence = _role(pattern_counts, state.width, write_kinds)
        if (
            state.width == 1
            and expr_info["gpio"]
            and not expr_info["states"]
            and not expr_info["semantic"]
            and set(expr_info["ops"]) <= {"AND", "SHR", "SHL"}
        ): 
            role = "GPIO_SAMPLE_LATCH"
            evidence.append(
                "1-bit state is loaded from a pure GPIO bit-extraction expression"
            )
        pred_count = len(control_predicates.per_family_predicates.get(family, set()))
        gpio_kinds = gpio_family_kinds.get(family, Counter())
        if pred_count: 
            evidence.append(f"used by {pred_count} unique branch predicate(s)")
        if gpio_kinds: 
            evidence.append(
                "feeds external GPIO effect(s): "
                + ", ".join(f"{kind}={count}" for kind, count in sorted(gpio_kinds.items()))
            )
        if expr_info["gpio"]: 
            evidence.append("next-state depends directly on sampled GPIO")
        if expr_info["semantic"]: 
            evidence.append("next-state depends on semantic SSA value(s)")

        families.append(
            FamilyDiagnostic(
                family = family, 
                width = state.width, 
                proof = state.proof, 
                structural_role = role, 
                phi_headers = phi_headers, 
                initial_values = initial_values, 
                feedback_values = feedback_values, 
                edge_writes = len(writes), 
                effective_writes = len(effective), 
                write_kinds = dict(sorted(write_kinds.items())), 
                hardware_patterns = dict(sorted(pattern_counts.items())), 
                expression_ops = dict(sorted(expr_info["ops"].items())), 
                expression_depth_max = depth_max, 
                expression_nodes = expr_info["nodes"], 
                state_dependencies = sorted(expr_info["states"] - {family}), 
                semantic_dependencies = sorted(expr_info["semantic"]), 
                gpio_dependencies = sorted(expr_info["gpio"]), 
                branch_predicate_count = pred_count, 
                gpio_effect_dependency_count = sum(gpio_kinds.values()), 
                gpio_effect_kinds = dict(sorted(gpio_kinds.items())), 
                defining_blocks = sorted(defining_blocks), 
                defining_addresses = [f"0x{x:04x}" for x in sorted(defining_addresses)], 
                evidence = evidence, 
            )
        )

    return PhysicalStateDiagnosticResult(
        families = families, 
        total_families = len(families), 
        total_bits = sum(item.width for item in families), 
        total_edge_writes = sum(item.edge_writes for item in families), 
        total_effective_writes = sum(item.effective_writes for item in families), 
        families_used_by_control = sum(1 for item in families if item.branch_predicate_count), 
        families_used_by_gpio_effects = sum(1 for item in families if item.gpio_effect_dependency_count), 
        effective_write_edges = len(effective_by_edge), 
        max_effective_writes_on_edge = max_writes, 
        max_write_edges = max_write_edges, 
        state_copy_relations = dict(sorted(copy_relations.items())), 
        repeated_rhs_groups = repeated_rhs_groups, 
    )


def _fmt_counter(values: dict[str, int]) -> str: 
    if not values: 
        return "-"
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items()))


def write_physical_state_report(
    result: PhysicalStateDiagnosticResult, 
    path: Path, 
) -> None: 
    lines: list[str] = []
    lines.append("PHYSICAL STATE SEMANTIC DIAGNOSTIC")
    lines.append("=" * 72)
    lines.append(f"families                 : {result.total_families}")
    lines.append(f"IR persistent bits       : {result.total_bits}")
    lines.append(f"edge writes              : {result.total_edge_writes}")
    lines.append(f"effective edge writes    : {result.total_effective_writes}")
    lines.append(f"families in control      : {result.families_used_by_control}")
    lines.append(f"families feeding GPIO    : {result.families_used_by_gpio_effects}")
    lines.append(f"effective write edges    : {result.effective_write_edges}")
    lines.append(f"max writes on one edge   : {result.max_effective_writes_on_edge}")
    lines.append(f"max-write edges          : {', '.join(result.max_write_edges) or '-'}")
    lines.append(f"repeated RHS groups/edge : {result.repeated_rhs_groups}")
    lines.append("state-copy relations     :")
    if result.state_copy_relations: 
        for relation, count in result.state_copy_relations.items(): 
            lines.append(f"  {count:3d}x {relation}")
    else: 
        lines.append("  -")
    lines.append("")
    for item in result.families: 
        lines.append(f"{item.family} [{item.width} bit] {item.structural_role}")
        lines.append(f"  proof              : {item.proof}")
        lines.append(f"  PHI headers        : {', '.join(f'BB{x:03d}' for x in item.phi_headers) or '-'}")
        lines.append(f"  initial values     : {', '.join(item.initial_values) or '-'}")
        lines.append(f"  feedback values    : {', '.join(item.feedback_values) or '-'}")
        lines.append(f"  edge writes        : {item.edge_writes} (effective {item.effective_writes})")
        lines.append(f"  write kinds        : {_fmt_counter(item.write_kinds)}")
        lines.append(f"  HW patterns        : {_fmt_counter(item.hardware_patterns)}")
        lines.append(f"  expr ops           : {_fmt_counter(item.expression_ops)}")
        lines.append(f"  expr nodes/depth   : {item.expression_nodes} / {item.expression_depth_max}")
        lines.append(f"  state deps         : {', '.join(item.state_dependencies) or '-'}")
        lines.append(f"  semantic deps      : {', '.join(item.semantic_dependencies) or '-'}")
        lines.append(f"  GPIO deps          : {', '.join(item.gpio_dependencies) or '-'}")
        lines.append(f"  branch predicates  : {item.branch_predicate_count}")
        lines.append(f"  GPIO effect deps   : {item.gpio_effect_dependency_count} ({_fmt_counter(item.gpio_effect_kinds)})")
        lines.append(f"  defining BBs       : {', '.join(f'BB{x:03d}' for x in item.defining_blocks) or '-'}")
        lines.append(f"  defining addresses : {', '.join(item.defining_addresses) or '-'}")
        for evidence in item.evidence: 
            lines.append(f"  evidence           : {evidence}")
        lines.append("")

    path.parent.mkdir(parents = True, exist_ok = True)
    path.write_text("\n".join(lines), encoding = "utf-8")

    json_path = path.with_suffix(path.suffix + ".json") if path.suffix else Path(str(path) + ".json")
    json_path.write_text(
        json.dumps(asdict(result), indent = 2, sort_keys = True), 
        encoding = "utf-8", 
    )
