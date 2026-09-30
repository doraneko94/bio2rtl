from __future__ import annotations

from dataclasses import dataclass, field
from collections import defaultdict

from .hardware_pattern import HardwarePattern, HardwarePatternResult
from .next_state_expr import Expr
from .rtl_ir import RTLIR


@dataclass(frozen = True)
class HardwareGuardEdge: 
    source_block: int
    target_block: int


@dataclass
class HardwareUpdateRule: 
    register: str
    operation_kind: str
    expression: Expr
    guard_edges: tuple[HardwareGuardEdge, ...]
    occurrence_count: int


@dataclass
class HardwareBehaviorObject: 
    name: str
    width: int
    hardware_kind: str
    rules: list[HardwareUpdateRule] = field(default_factory = list)


@dataclass
class HardwareBehaviorIR: 
    objects: dict[str, HardwareBehaviorObject]
    rules: list[HardwareUpdateRule]
    original_edge_writes: int
    collapsed_rules: int


def expr_key(expr: Expr) -> tuple: 
    if expr.kind == "CONST": 
        return ("CONST", expr.value)
    if expr.kind == "STATE": 
        return ("STATE", expr.state_family)
    if expr.kind == "GPIO": 
        return ("GPIO", expr.gpio_value)
    if expr.kind == "SEMANTIC": 
        return ("SEMANTIC", expr.semantic_value)
    if expr.kind == "OP": 
        return ("OP", expr.operation, tuple(expr_key(a) for a in expr.args))
    return (expr.kind, repr(expr))


def pattern_key(pattern: HardwarePattern) -> tuple: 
    # Expression identity is sufficient for semantic grouping.  Keep the
    # classified operation kind as an explicit dimension so future lowering
    # can distinguish a recovered counter/shift recurrence from a generic op.
    return (pattern.kind, expr_key(pattern.expression))


def build_hardware_behavior_ir(
    rtl_ir: RTLIR, 
    hardware_patterns: HardwarePatternResult, 
) -> HardwareBehaviorIR: 
    objects = {
        name: HardwareBehaviorObject(
            name = name, 
            width = reg.width, 
            hardware_kind = reg.hardware_kind, 
        )
        for name, reg in rtl_ir.registers.items()
    }

    grouped: dict[tuple[str, tuple], list[HardwarePattern]] = defaultdict(list)
    for pattern in hardware_patterns.patterns: 
        grouped[(pattern.family, pattern_key(pattern))].append(pattern)

    rules: list[HardwareUpdateRule] = []
    for (family, _), patterns in sorted(grouped.items(), key = lambda kv: str(kv[0])): 
        exemplar = patterns[0]
        edges = tuple(
            HardwareGuardEdge(p.source_block, p.target_block)
            for p in sorted(patterns, key = lambda p: (p.source_block, p.target_block))
        )
        rule = HardwareUpdateRule(
            register = family, 
            operation_kind = exemplar.kind, 
            expression = exemplar.expression, 
            guard_edges = edges, 
            occurrence_count = len(patterns), 
        )
        rules.append(rule)
        objects[family].rules.append(rule)

    return HardwareBehaviorIR(
        objects = objects, 
        rules = rules, 
        original_edge_writes = len(hardware_patterns.patterns), 
        collapsed_rules = len(rules), 
    )
