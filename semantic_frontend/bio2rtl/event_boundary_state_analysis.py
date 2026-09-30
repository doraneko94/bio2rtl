from __future__ import annotations

from dataclasses import dataclass, asdict
from math import ceil, log2
import ast
from collections import Counter, defaultdict, deque
import hashlib
from typing import Any, Iterable


Expr = tuple


def _freeze(value: Any) -> Any: 
    if isinstance(value, list): 
        return tuple(_freeze(x) for x in value)
    if isinstance(value, tuple): 
        return tuple(_freeze(x) for x in value)
    return value


def _u32(value: int) -> int: 
    return value & 0xFFFFFFFF


def _s32(value: int) -> int: 
    value = _u32(value)
    return value - (1 << 32) if value & (1 << 31) else value


@dataclass(frozen = True)
class PersistentStateSpec: 
    name: str
    width: int
    reset: int
    role: str  # ARCHITECTURAL / SEMANTIC / SCHEDULER


@dataclass
class PersistentStateRow: 
    state: str
    role: str
    original_width: int
    reachable_values: list[int]
    reachable_value_count: int
    natural_width: int
    information_width: int
    constant: bool
    startup_fixed: bool
    recurrence_classes: dict[str, int]
    derivable_from: list[str]
    encoded_width_candidate: int | None
    encoded_values: list[int] | None
    notes: list[str]


@dataclass
class EventBoundaryStateAnalysisResult: 
    source_transitions: int
    reachable_transitions: int
    unreachable_transition_ids: list[str]
    reachable_states: int
    closure_depth: int
    reachable_state_sha256: str
    state_order: list[str]
    gpio_dependency_bits: dict[str, list[int]]
    state_rows: list[PersistentStateRow]
    equality_classes: list[list[str]]
    original_storage_bits: int
    natural_storage_bits: int
    information_lower_bound_bits: int
    notes: list[str]


@dataclass(frozen = True)
class _CompiledTransition: 
    transition_id: str
    events: tuple[str, ...]
    state_constraints: tuple[tuple[Expr, bool], ...]
    gpio_constraints: tuple[tuple[Expr, bool], ...]
    changed_outcomes: tuple[tuple[str, Expr], ...]
    recurrence: tuple[tuple[str, str], ...]
    gpio_names: tuple[str, ...]


def _parse_predicate(text: str) -> Expr: 
    return _freeze(ast.literal_eval(text))


def _state_refs(expr: Any, out: set[str] | None = None) -> set[str]: 
    if out is None: 
        out = set()
    if isinstance(expr, tuple): 
        if expr and expr[0] in ("STATE", "HW_STATE"): 
            out.add(str(expr[1]))
            return out
        for x in expr: 
            _state_refs(x, out)
    return out


def _gpio_refs(expr: Any, out: set[str] | None = None) -> set[str]: 
    if out is None: 
        out = set()
    if isinstance(expr, tuple): 
        if expr and expr[0] in ("GPIO_SAMPLE", "GPIO_VALUE"): 
            out.add(str(expr[-1]))
            return out
        for x in expr: 
            _gpio_refs(x, out)
    return out


def _const_value(expr: Any) -> int | None: 
    if isinstance(expr, tuple) and len(expr) >= 2 and expr[0] == "CONST": 
        return int(expr[1])
    return None


def _backward_gpio_dependencies(
    expr: Any, 
    demanded_bits: set[int] | None = None, 
    result: dict[str, set[int]] | None = None, 
) -> dict[str, set[int]]: 
    """Conservatively recover input GPIO bits that can affect *expr*.

    The analysis is exact for the bitwise/constant-shift forms emitted by the
    current symbolic executor. Unsupported arithmetic is intentionally
    conservative: every bit of any GPIO operand is marked relevant.
    """
    if result is None: 
        result = defaultdict(set)
    if demanded_bits is None: 
        demanded_bits = set(range(32))
    if not isinstance(expr, tuple) or not expr: 
        return result

    tag = expr[0]
    if tag in ("GPIO_SAMPLE", "GPIO_VALUE"): 
        result[str(expr[-1])].update(i for i in demanded_bits if 0 <= i < 32)
        return result
    if tag in ("STATE", "HW_STATE", "CONST", "BOOL"): 
        return result

    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        # A comparison result is one bit, but generally depends on every
        # non-forced bit of both operands. Operand-local masks/shifts below
        # reduce this set again.
        _backward_gpio_dependencies(expr[1], set(range(32)), result)
        _backward_gpio_dependencies(expr[2], set(range(32)), result)
        return result

    if tag == "OP": 
        op = str(expr[1])
        args = tuple(expr[2])
        if not args: 
            return result

        if op == "SHR" and len(args) == 2: 
            shift = _const_value(args[1])
            if shift is not None: 
                shift &= 31
                src = {i + shift for i in demanded_bits if i + shift < 32}
                _backward_gpio_dependencies(args[0], src, result)
                return result
        if op == "SHL" and len(args) == 2: 
            shift = _const_value(args[1])
            if shift is not None: 
                shift &= 31
                src = {i - shift for i in demanded_bits if i - shift >= 0}
                _backward_gpio_dependencies(args[0], src, result)
                return result
        if op in ("AND", "OR", "XOR") and len(args) == 2: 
            ca, cb = _const_value(args[0]), _const_value(args[1])
            if cb is not None: 
                mask = _u32(cb)
                if op == "AND": 
                    needed = {i for i in demanded_bits if (mask >> i) & 1}
                elif op == "OR": 
                    needed = {i for i in demanded_bits if not ((mask >> i) & 1)}
                else: 
                    needed = set(demanded_bits)
                _backward_gpio_dependencies(args[0], needed, result)
                return result
            if ca is not None: 
                mask = _u32(ca)
                if op == "AND": 
                    needed = {i for i in demanded_bits if (mask >> i) & 1}
                elif op == "OR": 
                    needed = {i for i in demanded_bits if not ((mask >> i) & 1)}
                else: 
                    needed = set(demanded_bits)
                _backward_gpio_dependencies(args[1], needed, result)
                return result
            _backward_gpio_dependencies(args[0], demanded_bits, result)
            _backward_gpio_dependencies(args[1], demanded_bits, result)
            return result

        # ADD/SUB and any future operation can propagate carries. Preserve
        # soundness by marking all bits of GPIO operands as relevant.
        for arg in args: 
            if _gpio_refs(arg): 
                _backward_gpio_dependencies(arg, set(range(32)), result)
        return result

    for child in expr[1:]: 
        _backward_gpio_dependencies(child, demanded_bits, result)
    return result


def _eval_expr(expr: Expr, state: dict[str, int], gpio: dict[str, int]) -> int: 
    tag = expr[0]
    if tag == "CONST": 
        return int(expr[1])
    if tag == "BOOL": 
        return int(bool(expr[1]))
    if tag in ("STATE", "HW_STATE"): 
        return int(state[str(expr[1])])
    if tag in ("GPIO_SAMPLE", "GPIO_VALUE"): 
        return int(gpio[str(expr[-1])])
    if tag == "OP": 
        op = str(expr[1])
        args = tuple(expr[2])
        vals = [_eval_expr(x, state, gpio) for x in args]
        if op == "ADD": 
            return _u32(vals[0] + vals[1])
        if op == "SUB": 
            return _u32(vals[0] - vals[1])
        if op == "AND": 
            return _u32(vals[0]) & _u32(vals[1])
        if op == "OR": 
            return _u32(vals[0]) | _u32(vals[1])
        if op == "XOR": 
            return _u32(vals[0]) ^ _u32(vals[1])
        if op == "SHL": 
            return _u32(vals[0] << (_u32(vals[1]) & 31))
        if op == "SHR": 
            return _u32(vals[0]) >> (_u32(vals[1]) & 31)
        raise ValueError(f"unsupported operation: {op}")
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        a = _eval_expr(expr[1], state, gpio)
        b = _eval_expr(expr[2], state, gpio)
        if tag == "EQ": 
            return int(_u32(a) == _u32(b))
        if tag == "NE": 
            return int(_u32(a) != _u32(b))
        if tag == "ULT": 
            return int(_u32(a) < _u32(b))
        if tag == "UGE": 
            return int(_u32(a) >= _u32(b))
        if tag == "SLT": 
            return int(_s32(a) < _s32(b))
        return int(not (_s32(a) < _s32(b)))
    raise ValueError(f"unsupported expression: {expr!r}")


def _is_hold(expr: Expr, state: str) -> bool: 
    return expr in (("STATE", state), ("HW_STATE", state))


def _recurrence_class(expr: Expr, state: str) -> str: 
    if _is_hold(expr, state): 
        return "HOLD"
    if expr and expr[0] == "CONST": 
        return "CONST"
    if expr and expr[0] in ("STATE", "HW_STATE"): 
        return "COPY"
    if expr and expr[0] == "OP": 
        op = expr[1]
        args = tuple(expr[2])
        if op in ("ADD", "SUB") and any(a in (("STATE", state), ("HW_STATE", state)) for a in args): 
            if any(_const_value(a) is not None for a in args): 
                return "COUNT"
        if op in ("SHL", "SHR"): 
            return "SHIFT"
        # Shift-register update commonly appears as OR(input_bit, SHL(state,1)).
        if op in ("OR", "ADD") and any(
            isinstance(a, tuple) and a and a[0] == "OP" and a[1] in ("SHL", "SHR")
            for a in args
        ): 
            return "SHIFT"
    return "GENERAL"


def _compile_transitions(rows: Iterable[dict], states: set[str]) -> tuple[list[_CompiledTransition], dict[str, set[int]]]: 
    compiled: list[_CompiledTransition] = []
    gpio_deps: dict[str, set[int]] = defaultdict(set)
    for row in rows: 
        state_constraints = []
        gpio_constraints = []
        expressions = []
        for c in row.get("constraints", []): 
            pred = _parse_predicate(c["predicate"])
            item = (pred, bool(c["polarity"]))
            (gpio_constraints if _gpio_refs(pred) else state_constraints).append(item)
            expressions.append(pred)
        outcomes: dict[str, Expr] = {}
        for key in ("physical_outcome", "semantic_outcome"): 
            for item in row.get(key, []): 
                name = str(item["state"])
                if name in states: 
                    expr = _freeze(item["expr"])
                    outcomes[name] = expr
                    expressions.append(expr)
        deps: dict[str, set[int]] = defaultdict(set)
        for expr in expressions: 
            local = _backward_gpio_dependencies(expr)
            for name, bits in local.items(): 
                deps[name].update(bits)
                gpio_deps[name].update(bits)
        changed = tuple(sorted((name, expr) for name, expr in outcomes.items() if not _is_hold(expr, name)))
        recurrence = tuple(sorted((name, _recurrence_class(expr, name)) for name, expr in outcomes.items()))
        compiled.append(_CompiledTransition(
            transition_id = str(row["transition_id"]), 
            events = tuple(str(x) for x in row.get("events", [])), 
            state_constraints = tuple(state_constraints), 
            gpio_constraints = tuple(gpio_constraints), 
            changed_outcomes = changed, 
            recurrence = recurrence, 
            gpio_names = tuple(sorted(deps)), 
        ))
    return compiled, gpio_deps


def _gpio_domains(deps: dict[str, set[int]], max_gpio_bits_per_sample: int) -> dict[str, tuple[int, ...]]: 
    domains = {}
    for name, bits0 in deps.items(): 
        bits = sorted(bits0)
        if len(bits) > max_gpio_bits_per_sample: 
            raise RuntimeError(
                f"GPIO sample {name} depends on {len(bits)} bits ({bits}); "
                f"exact enumeration limit is {max_gpio_bits_per_sample}"
            )
        values = []
        for pattern in range(1 << len(bits)): 
            value = 0
            for i, bit in enumerate(bits): 
                if (pattern >> i) & 1: 
                    value |= 1 << bit
            values.append(value)
        domains[name] = tuple(values)
    return domains


def _information_width(count: int) -> int: 
    if count <= 1: 
        return 0
    return int(ceil(log2(count)))


def analyze_event_boundary_persistent_state(
    transition_rows: list[dict], 
    state_specs: list[PersistentStateSpec], 
    *, 
    max_states: int = 500_000, 
    max_gpio_bits_per_sample: int = 12, 
) -> EventBoundaryStateAnalysisResult: 
    """Compute an event-boundary persistent-state closure.

    All feasibility-proven transition rows are allowed whenever their symbolic
    guard is satisfiable. Event labels are deliberately *not* used as extra
    gating conditions, making this an over-approximation of any later scheduler
    realization. Consequently, a state/transition proven unreachable here is
    also unreachable in a scheduler that only restricts event availability.

    GPIO samples are existentially enumerated over every input bit on which the
    transition relation actually depends. Dependency bits are recovered from
    the symbolic expressions; no GPIO numbers are hard-coded.
    """
    specs = {s.name: s for s in state_specs}
    if len(specs) != len(state_specs): 
        raise ValueError("duplicate persistent state specification")
    names = tuple(sorted(specs))
    widths = {n: specs[n].width for n in names}
    masks = {n: (1 << widths[n]) - 1 for n in names}
    start = tuple(specs[n].reset & masks[n] for n in names)

    compiled, gpio_deps = _compile_transitions(transition_rows, set(names))
    gpio_domains = _gpio_domains(gpio_deps, max_gpio_bits_per_sample)
    index = {n: i for i, n in enumerate(names)}

    def state_dict(values: tuple[int, ...]) -> dict[str, int]: 
        return {n: values[index[n]] for n in names}

    def apply_transition(tr: _CompiledTransition, values: tuple[int, ...], state: dict[str, int]) -> set[tuple[int, ...]]: 
        if any(bool(_eval_expr(pred, state, {})) != polarity for pred, polarity in tr.state_constraints): 
            return set()
        gpio_names = tr.gpio_names
        if gpio_names: 
            import itertools
            assignments = itertools.product(*(gpio_domains[n] for n in gpio_names))
        else: 
            assignments = ((),)
        result = set()
        for combo in assignments: 
            gpio = dict(zip(gpio_names, combo))
            if any(bool(_eval_expr(pred, state, gpio)) != polarity for pred, polarity in tr.gpio_constraints): 
                continue
            nxt = list(values)
            for name, expr in tr.changed_outcomes: 
                nxt[index[name]] = _eval_expr(expr, state, gpio) & masks[name]
            result.add(tuple(nxt))
        return result

    seen = {start}
    frontier = {start}
    reached_transitions: set[str] = set()
    reachable_rule_counts: dict[str, Counter[str]] = {n: Counter() for n in names}
    depth = 0
    while frontier: 
        depth += 1
        new_frontier: set[tuple[int, ...]] = set()
        for values in frontier: 
            state = state_dict(values)
            for tr in compiled: 
                outs = apply_transition(tr, values, state)
                if not outs: 
                    continue
                reached_transitions.add(tr.transition_id)
                for nxt in outs: 
                    if nxt not in seen: 
                        seen.add(nxt)
                        new_frontier.add(nxt)
                        if len(seen) > max_states: 
                            raise RuntimeError(f"event-boundary state limit reached: {max_states}")
        frontier = new_frontier

    for tr in compiled: 
        if tr.transition_id in reached_transitions: 
            for name, cls in tr.recurrence: 
                reachable_rule_counts[name][cls] += 1

    values_by_state = {n: set() for n in names}
    for values in seen: 
        for name, value in zip(names, values): 
            values_by_state[name].add(value)

    # Exact equality classes over the over-approximated reachable relation.
    parent = {n: n for n in names}
    def find(x: str) -> str: 
        while parent[x] != x: 
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a: str, b: str) -> None: 
        a, b = find(a), find(b)
        if a != b: 
            parent[b] = a
    for i, a in enumerate(names): 
        ia = index[a]
        for b in names[i + 1:]: 
            ib = index[b]
            if all(v[ia] == v[ib] for v in seen): 
                union(a, b)
    groups: dict[str, list[str]] = defaultdict(list)
    for n in names: 
        groups[find(n)].append(n)
    equality_classes = [sorted(g) for g in groups.values() if len(g) > 1]
    equality_classes.sort(key = lambda g: (g[0], len(g)))

    # Single-state functional derivability. This reports only nonconstant
    # targets and excludes identity; it does not propose joint FSM recoding.
    derivable: dict[str, list[str]] = defaultdict(list)
    for target in names: 
        if len(values_by_state[target]) <= 1: 
            continue
        ti = index[target]
        for source in names: 
            if source == target: 
                continue
            si = index[source]
            mapping: dict[int, int] = {}
            ok = True
            for state in seen: 
                sv, tv = state[si], state[ti]
                old = mapping.setdefault(sv, tv)
                if old != tv: 
                    ok = False
                    break
            if ok and len(mapping) > 1: 
                derivable[target].append(source)

    rows: list[PersistentStateRow] = []
    for name in names: 
        spec = specs[name]
        vals = sorted(values_by_state[name])
        constant = len(vals) == 1
        natural = 0 if constant else max(1, max(vals).bit_length())
        info = _information_width(len(vals))
        encoded_candidate = None
        encoded_vals = None
        notes = []
        if not constant and info < natural: 
            encoded_candidate = info
            encoded_vals = vals
            notes.append("non-contiguous value encoding could reduce FFs but may add decode/MUX logic")
        if natural < spec.width and not constant: 
            notes.append("natural bit-width narrowing is recurrence/range preserving")
        if constant: 
            notes.append(f"constant after startup: {vals[0]}")
        if derivable.get(name): 
            notes.append("functionally derivable from at least one other single state over the reachable closure")
        rows.append(PersistentStateRow(
            state = name, 
            role = spec.role, 
            original_width = spec.width, 
            reachable_values = vals, 
            reachable_value_count = len(vals), 
            natural_width = natural, 
            information_width = info, 
            constant = constant, 
            startup_fixed = constant, 
            recurrence_classes = dict(sorted(reachable_rule_counts[name].items())), 
            derivable_from = sorted(derivable.get(name, [])), 
            encoded_width_candidate = encoded_candidate, 
            encoded_values = encoded_vals, 
            notes = notes, 
        ))

    original_bits = sum(s.width for s in state_specs)
    natural_bits = sum(r.natural_width for r in rows)
    info_bits = sum(r.information_width for r in rows)
    unreachable = sorted(
        (tr.transition_id for tr in compiled if tr.transition_id not in reached_transitions), 
        key = lambda x: (len(x), x), 
    )
    h = hashlib.sha256()
    h.update(("STATE_ORDER:" + ",".join(names) + "\n").encode())
    for state in sorted(seen): 
        h.update((",".join(str(x) for x in state) + "\n").encode())
    return EventBoundaryStateAnalysisResult(
        source_transitions = len(compiled), 
        reachable_transitions = len(reached_transitions), 
        unreachable_transition_ids = unreachable, 
        reachable_states = len(seen), 
        closure_depth = depth, 
        reachable_state_sha256 = h.hexdigest(), 
        state_order = list(names), 
        gpio_dependency_bits = {k: sorted(v) for k, v in sorted(gpio_deps.items())}, 
        state_rows = rows, 
        equality_classes = equality_classes, 
        original_storage_bits = original_bits, 
        natural_storage_bits = natural_bits, 
        information_lower_bound_bits = info_bits, 
        notes = [
            "Closure uses only feasibility-proven event-boundary transitions.", 
            "Event labels are ignored as gating, so reachability is an over-approximation of scheduler-constrained execution.", 
            "GPIO values enumerate exactly the dependency bits recovered from transition expressions; GPIO bit numbers are not hard-coded.", 
            "Natural-width reductions preserve the observed numeric representation.", 
            "Information-width reductions for sparse value sets require encoding/decoding and are candidates, not automatic rewrites.", 
            "Joint control-state re-encoding is intentionally not performed.", 
        ], 
    )


def result_to_dict(result: EventBoundaryStateAnalysisResult) -> dict: 
    return asdict(result)


@dataclass
class PackedGPIOStateRow: 
    register: str
    mask: int
    reset_masked: int
    reachable_masked_values: list[int]
    variable_bits: list[int]
    constant_zero_bits: list[int]
    constant_one_bits: list[int]
    storage_bits: int
    materialized_rules: int
    notes: list[str]


def _eval_dedicated_reg_expr(expr: Any, register: str, value: int) -> int: 
    expr = _freeze(expr)
    tag = expr[0]
    if tag == "CONST": 
        return int(expr[1])
    if tag == "REG": 
        if str(expr[1]) != register: 
            raise ValueError(f"cross-register GPIO expression {expr!r} while analyzing {register}")
        return value
    if tag == "OP": 
        op = str(expr[1])
        args = tuple(expr[2])
        vals = [_eval_dedicated_reg_expr(x, register, value) for x in args]
        if op == "ADD": 
            return _u32(vals[0] + vals[1])
        if op == "SUB": 
            return _u32(vals[0] - vals[1])
        if op == "AND": 
            return _u32(vals[0]) & _u32(vals[1])
        if op == "OR": 
            return _u32(vals[0]) | _u32(vals[1])
        if op == "XOR": 
            return _u32(vals[0]) ^ _u32(vals[1])
        if op == "SHL": 
            return _u32(vals[0] << (_u32(vals[1]) & 31))
        if op == "SHR": 
            return _u32(vals[0]) >> (_u32(vals[1]) & 31)
        raise ValueError(f"unsupported Dedicated IR operation: {op}")
    raise ValueError(f"unsupported Dedicated IR expression: {expr!r}")


def analyze_packed_gpio_state(dedicated_ir: dict) -> list[PackedGPIOStateRow]: 
    """Prove packed GPIO storage bits from Dedicated IR recurrence.

    The mask is taken from startup specialization metadata. All materialized
    update rules for each GPIO register are allowed regardless of enable/event,
    so the closure is an over-approximation. This is intentionally independent
    of transition-ID pruning and therefore safe as a bit-level storage bound.
    """
    mask = int(dedicated_ir["startup"]["gpio_mask_constant"])
    mask_bits = [i for i in range(32) if (mask >> i) & 1]
    rows = []
    reg_specs = {
        r["id"]: r for r in dedicated_ir["architectural_registers"] if r.get("kind") == "GPIO"
    }
    for register, spec in sorted(reg_specs.items()): 
        raw_reset = spec["reset"]
        if not isinstance(raw_reset, list) or len(raw_reset) != 2 or raw_reset[0] != "CONST": 
            raise ValueError(f"GPIO reset is not constant: {register}: {raw_reset!r}")
        reset = int(raw_reset[1]) & mask
        rules = [
            r for r in dedicated_ir["update_rules"]
            if r.get("target") == register and bool(r.get("materialize"))
        ]
        seen = {reset}
        frontier = {reset}
        while frontier: 
            nxt_frontier = set()
            for value in frontier: 
                for rule in rules: 
                    nxt = _eval_dedicated_reg_expr(rule["outcome"], register, value) & mask
                    if nxt not in seen: 
                        seen.add(nxt)
                        nxt_frontier.add(nxt)
            frontier = nxt_frontier
        variable, zeros, ones = [], [], []
        for bit in mask_bits: 
            vals = {(v >> bit) & 1 for v in seen}
            if len(vals) > 1: 
                variable.append(bit)
            elif 1 in vals: 
                ones.append(bit)
            else: 
                zeros.append(bit)
        rows.append(PackedGPIOStateRow(
            register = register, 
            mask = mask, 
            reset_masked = reset, 
            reachable_masked_values = sorted(seen), 
            variable_bits = variable, 
            constant_zero_bits = zeros, 
            constant_one_bits = ones, 
            storage_bits = len(variable), 
            materialized_rules = len(rules), 
            notes = [
                "All materialized GPIO update rules were enabled nondeterministically; closure is an over-approximation.", 
                "Only startup gpio_mask_constant bits are physical Dedicated HW state.", 
            ], 
        ))
    return rows
