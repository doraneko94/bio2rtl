from __future__ import annotations

from dataclasses import dataclass, asdict
from collections import Counter, defaultdict
import hashlib
import itertools
from typing import Any


def _u32(value: int) -> int: 
    return value & 0xFFFFFFFF


def _s32(value: int) -> int: 
    value = _u32(value)
    return value - (1 << 32) if value & (1 << 31) else value


def _const(expr: Any) -> int | None: 
    if isinstance(expr, list) and len(expr) >= 2 and expr[0] == "CONST": 
        return int(expr[1])
    return None


def _gpio_input_dependencies(expr: Any, demanded: set[int] | None = None) -> set[int]: 
    """Backward bit dependency for Dedicated Event IR GPIO_INPUT expressions."""
    if demanded is None: 
        demanded = set(range(32))
    if not isinstance(expr, list) or not expr: 
        return set()
    tag = expr[0]
    if tag == "GPIO_INPUT": 
        return {i for i in demanded if 0 <= i < 32}
    if tag in ("CONST", "REG", "SCHED_REG"): 
        return set()
    if tag == "BIT_VALUE": 
        bit = int(expr[2])
        return _gpio_input_dependencies(expr[1], {bit})
    if tag == "EQ_CONST": 
        return _gpio_input_dependencies(expr[1], set(range(32)))
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        return _gpio_input_dependencies(expr[1], set(range(32))) | _gpio_input_dependencies(expr[2], set(range(32)))
    if tag == "OP": 
        op = str(expr[1])
        args = expr[2]
        if op == "SHR" and len(args) == 2: 
            shift = _const(args[1])
            if shift is not None: 
                shift &= 31
                return _gpio_input_dependencies(args[0], {i + shift for i in demanded if i + shift < 32})
        if op == "SHL" and len(args) == 2: 
            shift = _const(args[1])
            if shift is not None: 
                shift &= 31
                return _gpio_input_dependencies(args[0], {i - shift for i in demanded if i - shift >= 0})
        if op in ("AND", "OR", "XOR") and len(args) == 2: 
            ca, cb = _const(args[0]), _const(args[1])
            if cb is not None: 
                mask = _u32(cb)
                if op == "AND": 
                    need = {i for i in demanded if (mask >> i) & 1}
                elif op == "OR": 
                    need = {i for i in demanded if not ((mask >> i) & 1)}
                else: 
                    need = set(demanded)
                return _gpio_input_dependencies(args[0], need)
            if ca is not None: 
                mask = _u32(ca)
                if op == "AND": 
                    need = {i for i in demanded if (mask >> i) & 1}
                elif op == "OR": 
                    need = {i for i in demanded if not ((mask >> i) & 1)}
                else: 
                    need = set(demanded)
                return _gpio_input_dependencies(args[1], need)
        # Carries/general expressions can mix bit positions. Conservatively
        # demand every bit from every GPIO-containing operand.
        out = set()
        for arg in args: 
            if _contains_gpio_input(arg): 
                out |= _gpio_input_dependencies(arg, set(range(32)))
        return out
    out = set()
    for child in expr[1:]: 
        out |= _gpio_input_dependencies(child, demanded)
    return out


def _contains_gpio_input(expr: Any) -> bool: 
    if isinstance(expr, list): 
        if expr and expr[0] == "GPIO_INPUT": 
            return True
        return any(_contains_gpio_input(x) for x in expr[1:])
    if isinstance(expr, dict): 
        return any(_contains_gpio_input(v) for v in expr.values())
    return False


@dataclass
class DedicatedReachStateRow: 
    register: str
    kind: str
    provenance: str
    declared_width: int
    reachable_values: list[int]
    constant: bool
    natural_storage_bits: int
    variable_mask_bits: list[int]
    equal_bit_groups: list[list[int]]


@dataclass
class DedicatedEventReachabilityResult: 
    reachable_states: int
    closure_depth: int
    evaluated_steps: int
    rule_conflicts: int
    gpio_input_bits: list[int]
    event_class_counts: dict[str, int]
    state_rows: list[DedicatedReachStateRow]
    scheduler_values: dict[str, list[int]]
    projection_state_order: list[str]
    projection_sha256: str
    full_state_sha256: str
    natural_storage_bits: int
    correlation_encoding_candidate_bits: int
    notes: list[str]


def _eval_expr(expr: Any, regs: dict[str, int], sched: dict[str, int], gpio: int) -> int: 
    tag = expr[0]
    if tag == "CONST": 
        return int(expr[1])
    if tag == "REG": 
        return int(regs[str(expr[1])])
    if tag == "SCHED_REG": 
        return int(sched[str(expr[1])])
    if tag == "GPIO_INPUT": 
        return gpio
    if tag == "BIT_VALUE": 
        return (_eval_expr(expr[1], regs, sched, gpio) >> int(expr[2])) & 1
    if tag == "EQ_CONST": 
        return int(_u32(_eval_expr(expr[1], regs, sched, gpio)) == _u32(int(expr[2])))
    if tag in ("EQ", "NE", "ULT", "UGE", "SLT", "NOT_SLT"): 
        a = _eval_expr(expr[1], regs, sched, gpio)
        b = _eval_expr(expr[2], regs, sched, gpio)
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
    if tag == "OP": 
        op = str(expr[1])
        vals = [_eval_expr(x, regs, sched, gpio) for x in expr[2]]
        if op == "AND": 
            return _u32(vals[0]) & _u32(vals[1])
        if op == "OR": 
            return _u32(vals[0]) | _u32(vals[1])
        if op == "XOR": 
            return _u32(vals[0]) ^ _u32(vals[1])
        if op == "ADD": 
            return _u32(vals[0] + vals[1])
        if op == "SUB": 
            return _u32(vals[0] - vals[1])
        if op == "SHL": 
            return _u32(vals[0] << (_u32(vals[1]) & 31))
        if op == "SHR": 
            return _u32(vals[0]) >> (_u32(vals[1]) & 31)
        raise ValueError(f"unsupported Dedicated Event op: {op}")
    raise ValueError(f"unsupported Dedicated Event expression: {expr!r}")


def _detector_value(det: dict, sched_rows: dict[str, dict], sched: dict[str, int], gpio: int) -> bool: 
    bit = int(det["input_bit"])
    input_level = (gpio >> bit) & 1
    for q in det.get("qualifiers", []): 
        if q.get("source") != "GPIO_INPUT": 
            raise ValueError(f"unsupported detector qualifier: {q!r}")
        if ((gpio >> int(q["bit"])) & 1) != int(q["level"]): 
            return False

    kind = det["kind"]
    if kind == "QUALIFIED_INPUT_EDGE": 
        candidates = [
            row for row in sched_rows.values()
            if row.get("role") == "EDGE_HISTORY_AUX" and int(row.get("input_bit", -1)) == bit
        ]
        if len(candidates) != 1: 
            raise ValueError(f"expected one EDGE_HISTORY_AUX scheduler for GPIO[{bit}], got {len(candidates)}")
        sid = candidates[0]["id"]
        previous = int(sched[sid])
        if det["edge"] == "RISE": 
            return previous == 0 and input_level == 1
        if det["edge"] == "FALL": 
            return previous == 1 and input_level == 0
        raise ValueError(f"unsupported edge: {det['edge']}")
    if kind == "POLLING_PHASE_COMPLETION": 
        sid = str(det["phase_state"])
        return int(sched[sid]) == int(det["phase_before"]) and input_level == int(det["phase_after"])
    raise ValueError(f"unsupported scheduler detector kind: {kind}")


def _event_class(ir: dict, detector_values: dict[str, bool]) -> str: 
    matches = []
    for event in ir["event_classes"]: 
        if all(detector_values[x] for x in event.get("required_detectors", [])) and all(
            not detector_values[x] for x in event.get("forbidden_detectors", [])
        ): 
            matches.append(event["event_class"])
    if len(matches) != 1: 
        raise ValueError(f"event partition is not one-hot: detectors={detector_values} matches={matches}")
    return matches[0]


def _scheduler_next(row: dict, current: int, gpio: int, detector_values: dict[str, bool], detectors: list[dict]) -> int: 
    maintenance = row.get("maintenance")
    if maintenance == "CAPTURE_GPIO_INPUT": 
        return (gpio >> int(row["input_bit"])) & 1
    if maintenance == "EVENT_PHASE_AUTOMATON": 
        value = current
        relevant = [d for d in detectors if d.get("phase_state") == row["id"]]
        fired = [d for d in relevant if detector_values[d["event_id"]]]
        if len(fired) > 1: 
            raise ValueError(f"multiple phase transitions fired for {row['id']}: {fired}")
        if fired: 
            value = int(fired[0]["phase_after"])
        return value
    raise ValueError(f"unsupported scheduler maintenance: {maintenance}")


def _equal_bit_groups(values: set[int], bits: list[int]) -> list[list[int]]: 
    parent = {b: b for b in bits}
    def find(x: int) -> int: 
        while parent[x] != x: 
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a: int, b: int) -> None: 
        a, b = find(a), find(b)
        if a != b: 
            parent[b] = a
    for i, a in enumerate(bits): 
        for b in bits[i + 1:]: 
            if all(((v >> a) & 1) == ((v >> b) & 1) for v in values): 
                union(a, b)
    groups: dict[int, list[int]] = defaultdict(list)
    for b in bits: 
        groups[find(b)].append(b)
    return [sorted(g) for g in groups.values() if len(g) > 1]


def analyze_dedicated_event_reachability(
    ir: dict, 
    *, 
    projection_order: list[str] | None = None, 
    apply_storage_optimization: bool = False, 
    max_states: int = 500_000, 
    max_gpio_bits: int = 12, 
) -> DedicatedEventReachabilityResult: 
    regs = list(ir["architectural_registers"])
    reg_ids = [r["id"] for r in regs]
    reg_index = {r: i for i, r in enumerate(reg_ids)}
    reg_specs = {r["id"]: r for r in regs}
    gpio_mask = int(ir["startup"]["gpio_mask_constant"])
    storage_plan = None
    storage_by_reg = {}
    if apply_storage_optimization: 
        storage_plan = ir.get("storage_optimization")
        if not storage_plan: 
            raise ValueError("apply_storage_optimization requested but IR has no storage_optimization")
        storage_by_reg = {x["register"]: x for x in storage_plan["register_storage"]}

    sched_list = list(ir.get("scheduler_owned_sources", []))
    sched_ids = [r["id"] for r in sched_list]
    sched_index = {s: i for i, s in enumerate(sched_ids)}
    sched_rows = {r["id"]: r for r in sched_list}

    # Exact external-input domain from detector bits, qualifiers and predicate basis.
    input_bits = set()
    for det in ir["scheduler_detectors"]: 
        input_bits.add(int(det["input_bit"]))
        input_bits.update(int(q["bit"]) for q in det.get("qualifiers", []) if q.get("source") == "GPIO_INPUT")
    for pred in ir["predicate_basis"]: 
        input_bits |= _gpio_input_dependencies(pred["expression"])
    input_bits = sorted(input_bits)
    if len(input_bits) > max_gpio_bits: 
        raise RuntimeError(f"Dedicated IR depends on {len(input_bits)} GPIO input bits {input_bits}; exact limit={max_gpio_bits}")
    gpio_values = []
    for pattern in range(1 << len(input_bits)): 
        value = 0
        for i, bit in enumerate(input_bits): 
            if (pattern >> i) & 1: 
                value |= 1 << bit
        gpio_values.append(value)

    startup_values = {x["register"]: int(x["value"][1]) for x in ir["startup"]["register_values"]}
    start_regs = []
    for reg in regs: 
        value = startup_values[reg["id"]]
        if reg["kind"] == "GPIO": 
            value &= gpio_mask
        else: 
            value &= (1 << int(reg["width"])) - 1
        start_regs.append(value)
    start_sched = []
    for row in sched_list: 
        raw = row["initial_value"]
        if not isinstance(raw, list) or raw[0] != "CONST": 
            raise ValueError(f"non-constant scheduler reset: {row}")
        start_sched.append(int(raw[1]))
    def canonicalize_register(rid: str, value: int) -> int: 
        spec = reg_specs[rid]
        if not apply_storage_optimization: 
            return (value & gpio_mask) if spec["kind"] == "GPIO" else (value & ((1 << int(spec["width"])) - 1))
        sp = storage_by_reg[rid]
        kind = sp["storage_kind"]
        if kind == "CONST": 
            return int(sp["constant_value"])
        if kind == "DIRECT": 
            return value & ((1 << int(spec["width"])) - 1)
        if kind == "NARROW_ZERO_EXTEND": 
            return value & ((1 << int(sp["storage_bits"])) - 1)
        if kind == "PACKED_MASK_BITS": 
            mask = 0
            for bit in sp["stored_bits"]: 
                mask |= 1 << int(bit)
            const_one = 0
            for bit in sp.get("constant_one_bits", []): 
                const_one |= 1 << int(bit)
            return (value & mask) | const_one
        raise ValueError(f"unsupported storage kind: {kind}")

    start_regs = [canonicalize_register(rid, value) for rid, value in zip(reg_ids, start_regs)]
    start = (tuple(start_regs), tuple(start_sched))

    pred_map = {p["id"]: p["expression"] for p in ir["predicate_basis"]}
    rules_by: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rule in ir["update_rules"]: 
        if rule.get("materialize"): 
            rules_by[(rule["event_class"], rule["target"])].append(rule)

    def decode_state(state): 
        rv, sv = state
        return ({r: rv[reg_index[r]] for r in reg_ids}, {s: sv[sched_index[s]] for s in sched_ids})

    conflict_count = 0
    event_counts: Counter[str] = Counter()
    evaluated_steps = 0

    def step(state, gpio: int): 
        nonlocal conflict_count, evaluated_steps
        evaluated_steps += 1
        rdict, sdict = decode_state(state)
        detector_values = {
            d["event_id"]: _detector_value(d, sched_rows, sdict, gpio)
            for d in ir["scheduler_detectors"]
        }
        event = _event_class(ir, detector_values)
        event_counts[event] += 1
        next_regs = list(state[0])
        for rid in reg_ids: 
            matches = []
            for rule in rules_by.get((event, rid), []): 
                if all(bool(_eval_expr(pred_map[x["basis"]], rdict, sdict, gpio)) == bool(x["polarity"]) for x in rule["enable"]): 
                    value = _eval_expr(rule["outcome"], rdict, sdict, gpio)
                    value = canonicalize_register(rid, value)
                    matches.append((value, rule["rule_id"]))
            unique = {v for v, _ in matches}
            if len(unique) > 1: 
                conflict_count += 1
            if matches: 
                next_regs[reg_index[rid]] = matches[0][0]
        next_sched = list(state[1])
        for sid in sched_ids: 
            row = sched_rows[sid]
            next_sched[sched_index[sid]] = _scheduler_next(
                row, sdict[sid], gpio, detector_values, ir["scheduler_detectors"]
            )
        return (tuple(next_regs), tuple(next_sched))

    seen = {start}
    frontier = {start}
    depth = 0
    while frontier: 
        depth += 1
        nxt = set()
        for state in frontier: 
            for gpio in gpio_values: 
                ns = step(state, gpio)
                if ns not in seen: 
                    seen.add(ns)
                    nxt.add(ns)
                    if len(seen) > max_states: 
                        raise RuntimeError(f"Dedicated IR state limit reached: {max_states}")
        frontier = nxt

    values: dict[str, set[int]] = {r: set() for r in reg_ids}
    sched_values: dict[str, set[int]] = {s: set() for s in sched_ids}
    for rv, sv in seen: 
        for rid, val in zip(reg_ids, rv): 
            values[rid].add(val)
        for sid, val in zip(sched_ids, sv): 
            sched_values[sid].add(val)

    rows = []
    natural_storage = 0
    correlation_storage = 0
    for reg in regs: 
        rid = reg["id"]
        vals = values[rid]
        if reg["kind"] == "GPIO": 
            bits = [i for i in range(32) if (gpio_mask >> i) & 1]
            variable = [b for b in bits if len({(v >> b) & 1 for v in vals}) > 1]
            groups = _equal_bit_groups(vals, variable)
            natural = len(variable)
            # Equal bit groups can share one FF each. No giant joint re-encoding.
            correlated = natural - sum(len(g) - 1 for g in groups)
        else: 
            variable = []
            groups = []
            natural = 0 if len(vals) == 1 else max(1, max(vals).bit_length())
            correlated = natural
        natural_storage += natural
        correlation_storage += correlated
        rows.append(DedicatedReachStateRow(
            register = rid, 
            kind = reg["kind"], 
            provenance = reg["provenance"], 
            declared_width = int(reg["width"]), 
            reachable_values = sorted(vals), 
            constant = len(vals) == 1, 
            natural_storage_bits = natural, 
            variable_mask_bits = variable, 
            equal_bit_groups = groups, 
        ))
    for sid in sched_ids: 
        vals = sched_values[sid]
        natural_storage += 0 if len(vals) == 1 else max(1, max(vals).bit_length())
        correlation_storage += 0 if len(vals) == 1 else max(1, max(vals).bit_length())

    provenance_to_reg = {
        r["provenance"]: r["id"] for r in regs if r["kind"] in ("PHYSICAL", "SEMANTIC")
    }
    provenance_to_sched = {r["source"]: r["id"] for r in sched_list}
    if projection_order is None: 
        projection_order = sorted(list(provenance_to_reg) + list(provenance_to_sched))
    h = hashlib.sha256()
    h.update(("STATE_ORDER:" + ",".join(projection_order) + "\n").encode())
    projected = set()
    for rv, sv in seen: 
        vals = []
        for name in projection_order: 
            if name in provenance_to_reg: 
                vals.append(rv[reg_index[provenance_to_reg[name]]])
            elif name in provenance_to_sched: 
                vals.append(sv[sched_index[provenance_to_sched[name]]])
            else: 
                raise KeyError(f"projection state not in Dedicated IR: {name}")
        projected.add(tuple(vals))
    for row in sorted(projected): 
        h.update((",".join(str(x) for x in row) + "\n").encode())

    full_h = hashlib.sha256()
    full_h.update(("REG_ORDER:" + ",".join(reg_ids) + "|SCHED_ORDER:" + ",".join(sched_ids) + "\n").encode())
    for rv, sv in sorted(seen): 
        full_h.update((",".join(str(x) for x in rv) + "|" + ",".join(str(x) for x in sv) + "\n").encode())

    return DedicatedEventReachabilityResult(
        reachable_states = len(seen), 
        closure_depth = depth, 
        evaluated_steps = evaluated_steps, 
        rule_conflicts = conflict_count, 
        gpio_input_bits = input_bits, 
        event_class_counts = dict(sorted(event_counts.items())), 
        state_rows = rows, 
        scheduler_values = {k: sorted(v) for k, v in sorted(sched_values.items())}, 
        projection_state_order = list(projection_order), 
        projection_sha256 = h.hexdigest(), 
        full_state_sha256 = full_h.hexdigest(), 
        natural_storage_bits = natural_storage, 
        correlation_encoding_candidate_bits = correlation_storage, 
        notes = [
            "Startup active_run is treated as already asserted; analysis covers steady-state Dedicated Event execution.", 
            "GPIO input bits are inferred from scheduler detectors, qualifiers, and predicate-basis expressions.", 
            "All combinations of relevant external GPIO input bits are explored on every step.", 
            "Update-rule conflicts count distinct enabled outcomes for the same event/target on reachable states.", 
            "GPIO equal-bit sharing is reported separately from natural bit elimination and does not use joint FSM encoding.", 
            "Storage optimization canonicalization is " + ("enabled." if apply_storage_optimization else "disabled."), 
        ], 
    )


def result_to_dict(result: DedicatedEventReachabilityResult) -> dict: 
    return asdict(result)
