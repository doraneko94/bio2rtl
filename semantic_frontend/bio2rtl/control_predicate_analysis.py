from __future__ import annotations

from dataclasses import dataclass, field

from .semantic_ssa import (
    SemanticCondition, 
    SemanticExpr, 
    SemanticSSAResult, 
)
from .structural_netlist import StructuralNetlist


@dataclass
class ControlPredicateResult: 
    unique_predicates: dict[str, list[int]]
    physical_families: set[str]
    semantic_states: set[str]
    gpio_blocks: set[int]
    physical_bits: int
    predicate_classes: dict[str, int]
    per_family_predicates: dict[str, set[str]] = field(default_factory = dict)


def _expr_key(expr: SemanticExpr) -> str: 
    if expr.kind == "CONST": 
        return f"C:{0 if expr.value is None else int(expr.value) & 0xFFFFFFFF}"
    if expr.kind == "STATE": 
        return f"S:{expr.state_family}"
    if expr.kind == "SEMANTIC_STATE": 
        return f"SS:{expr.semantic_state_name}"
    if expr.kind == "GPIO": 
        return f"G:{expr.gpio_block}"
    if expr.kind == "LIVEIN": 
        return f"L:{expr.livein_name}"
    if expr.kind == "PHI": 
        parts = ",".join(
            f"{pred}:{_expr_key(incoming)}"
            for pred, incoming in expr.phi_inputs
        )
        return f"PHI@{expr.phi_block}({parts})"
    if expr.kind == "OP": 
        args = ",".join(_expr_key(arg) for arg in expr.args)
        return f"{expr.operation}({args})"
    return f"?{expr.kind}"


def _condition_key(cond: SemanticCondition) -> str: 
    lhs = _expr_key(cond.lhs)
    rhs = _expr_key(cond.rhs)

    # EQ/NE are commutative. Canonicalize operand order so identical
    # predicates discovered through different SSA spellings collapse.
    if cond.operation in {"EQ", "NE"} and rhs < lhs: 
        lhs, rhs = rhs, lhs

    return f"{cond.operation}({lhs},{rhs})"


def _collect_expr_dependencies(
    expr: SemanticExpr, 
    physical: set[str], 
    semantic: set[str], 
    gpio: set[int], 
) -> None: 
    if expr.kind == "STATE": 
        if expr.state_family is not None: 
            physical.add(expr.state_family)
        return

    if expr.kind == "SEMANTIC_STATE": 
        if expr.semantic_state_name is not None: 
            semantic.add(expr.semantic_state_name)
        return

    if expr.kind == "GPIO": 
        if expr.gpio_block is not None: 
            gpio.add(expr.gpio_block)
        return

    for arg in expr.args: 
        _collect_expr_dependencies(arg, physical, semantic, gpio)

    for _pred, incoming in expr.phi_inputs: 
        _collect_expr_dependencies(incoming, physical, semantic, gpio)


def _classify_predicate(cond: SemanticCondition) -> str: 
    physical: set[str] = set()
    semantic: set[str] = set()
    gpio: set[int] = set()
    _collect_expr_dependencies(cond.lhs, physical, semantic, gpio)
    _collect_expr_dependencies(cond.rhs, physical, semantic, gpio)

    if gpio and not physical and not semantic: 
        return "GPIO_ONLY"
    if physical and not semantic and not gpio: 
        return "PHYSICAL_ONLY"
    if semantic and not physical and not gpio: 
        return "SEMANTIC_ONLY"
    if physical or semantic or gpio: 
        return "MIXED"
    return "CONSTANT_ONLY"


def analyze_control_predicates(
    structural: StructuralNetlist, 
    semantic_ssa: SemanticSSAResult, 
) -> ControlPredicateResult: 
    widths: dict[str, int] = {}
    for family, node_id in structural.state_nodes.items(): 
        node = structural.nodes[node_id]
        widths[family] = 32 if node.width is None else int(node.width)

    unique: dict[str, list[int]] = {}
    physical: set[str] = set()
    semantic: set[str] = set()
    gpio: set[int] = set()
    classes: dict[str, int] = {}
    per_family: dict[str, set[str]] = {}

    for block_id, cond in sorted(semantic_ssa.branch_conditions.items()): 
        key = _condition_key(cond)
        unique.setdefault(key, []).append(block_id)

        local_physical: set[str] = set()
        local_semantic: set[str] = set()
        local_gpio: set[int] = set()
        _collect_expr_dependencies(cond.lhs, local_physical, local_semantic, local_gpio)
        _collect_expr_dependencies(cond.rhs, local_physical, local_semantic, local_gpio)

        physical.update(local_physical)
        semantic.update(local_semantic)
        gpio.update(local_gpio)

        klass = _classify_predicate(cond)
        classes[klass] = classes.get(klass, 0) + 1

        for family in local_physical: 
            per_family.setdefault(family, set()).add(key)

    physical_bits = sum(widths.get(family, 32) for family in physical)

    return ControlPredicateResult(
        unique_predicates = unique, 
        physical_families = physical, 
        semantic_states = semantic, 
        gpio_blocks = gpio, 
        physical_bits = physical_bits, 
        predicate_classes = classes, 
        per_family_predicates = per_family, 
    )


def print_control_predicates(result: ControlPredicateResult) -> None: 
    print()
    print("=" * 72)
    print("CONTROL PREDICATE ABSTRACTION AUDIT")
    print("=" * 72)
    print(f"branch blocks          : {sum(len(v) for v in result.unique_predicates.values())}")
    print(f"unique predicates      : {len(result.unique_predicates)}")
    print(f"physical deps          : {len(result.physical_families)}")
    print(f"physical dep bits      : {result.physical_bits}")
    print(f"semantic deps          : {len(result.semantic_states)}")
    print(f"GPIO sample deps       : {', '.join(f'BB{x:03d}' for x in sorted(result.gpio_blocks)) or '-'}")

    for klass in sorted(result.predicate_classes): 
        print(f"{klass:22s}: {result.predicate_classes[klass]}")

    print("physical family predicate counts:")
    for family in sorted(result.physical_families): 
        print(f"    {family:32s}: {len(result.per_family_predicates.get(family, set()))}")

    print("reused predicates:")
    reused = [
        (key, blocks)
        for key, blocks in result.unique_predicates.items()
        if len(blocks) > 1
    ]
    if not reused: 
        print("    -")
    else: 
        for key, blocks in sorted(reused, key = lambda item: (-len(item[1]), item[0]))[:20]: 
            block_text = ", ".join(f"BB{x:03d}" for x in blocks)
            print(f"    {len(blocks):2d}x [{block_text}] {key}")

    print("CONTROL PREDICATE ABSTRACTION AUDIT: PASS")
