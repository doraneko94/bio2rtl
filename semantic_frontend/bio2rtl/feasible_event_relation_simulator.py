from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any
import ast

from .control_expr import ControlExpr
from .dedicated_event_reachability import _detector_value, _scheduler_next
from .event_boundary_state_analysis import _compile_transitions, _eval_expr, _gpio_refs, _freeze


def _u32(x: int) -> int: 
    return int(x) & 0xFFFFFFFF


def _eval_control_expr(expr: ControlExpr, state: dict[str, int], gpio_in: int) -> int: 
    k = expr.kind
    if k == "CONST": 
        return _u32(expr.value or 0)
    if k == "STATE": 
        return _u32(state[str(expr.state_family)])
    if k == "CONTROL_STATE": 
        return _u32(state[str(expr.control_state_name)])
    if k == "GPIO": 
        # Current FSE reports name a symbolic sampled GPIO value.  The relation
        # simulator intentionally maps it to the current resolved GPIO input,
        # matching the Dedicated predicate lowering convention.
        return _u32(gpio_in)
    if k == "LIVEIN": 
        raise ValueError(f"unresolved LIVEIN in complete FSE GPIO effect: {expr}")
    if k == "PHI_MUX": 
        raise ValueError(f"unresolved PHI_MUX in complete FSE GPIO effect: {expr}")
    if k != "OP": 
        raise ValueError(f"unsupported ControlExpr kind: {k}")
    vals = [_eval_control_expr(a, state, gpio_in) for a in expr.args]
    op = str(expr.operation)
    if op == "ADD": 
        return _u32(vals[0] + vals[1])
    if op == "SUB": 
        return _u32(vals[0] - vals[1])
    if op == "AND": 
        return _u32(vals[0] & vals[1])
    if op == "OR": 
        return _u32(vals[0] | vals[1])
    if op == "XOR": 
        return _u32(vals[0] ^ vals[1])
    if op == "SHL": 
        return _u32(vals[0] << (vals[1] & 31))
    if op == "SHR": 
        return _u32(vals[0] >> (vals[1] & 31))
    raise ValueError(f"unsupported ControlExpr operation: {op}")


def _parse_control_expr(text: str) -> ControlExpr: 
    # The report is generated locally by bio2rtl and contains only dataclass
    # constructor reprs.  Parse via AST and accept only ControlExpr/tuple/list/
    # scalar syntax rather than executing arbitrary report text.
    node = ast.parse(text, mode = "eval").body

    def conv(n): 
        if isinstance(n, ast.Constant): 
            return n.value
        if isinstance(n, ast.Tuple): 
            return tuple(conv(x) for x in n.elts)
        if isinstance(n, ast.List): 
            return [conv(x) for x in n.elts]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub) and isinstance(n.operand, ast.Constant): 
            return -n.operand.value
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "ControlExpr": 
            if n.args: 
                raise ValueError("positional ControlExpr report arguments are not supported")
            kw = {x.arg: conv(x.value) for x in n.keywords}
            return ControlExpr(**kw)
        raise ValueError(f"unsupported ControlExpr report AST: {ast.dump(n)}")

    out = conv(node)
    if not isinstance(out, ControlExpr): 
        raise ValueError("effect expression did not parse to ControlExpr")
    return out


@dataclass
class FeasibleRelationCycle: 
    cycle: int
    reset: bool
    active_run_before: bool
    active_run_after: bool
    gpio_external: int
    gpio_in: int
    gpio_out_before: int
    gpio_oe_before: int
    gpio_out_after: int
    gpio_oe_after: int
    event_class: str | None
    detector_values: dict[str, bool]
    changed_registers: dict[str, tuple[int, int]]
    changed_scheduler: dict[str, tuple[int, int]]
    fired_rules: dict[str, list[str]]


class FeasibleEventRelationSimulator: 
    """Drive the feasibility-proven event-boundary transition relation directly.

    Unlike DedicatedEventSimulator, this bypasses the handed-off predicate/update
    materialization.  The same recovered hardware detectors/scheduler are used,
    but source FSE transition rows are selected directly by event set + guard.
    This separates a source relation / boundary problem from a Dedicated IR
    materialization problem without introducing protocol-specific compiler rules.
    """

    def __init__(self, fse_report: dict[str, Any], dedicated_ir: dict[str, Any]): 
        self.fse = fse_report
        self.ir = dedicated_ir
        self.sched_rows = {r["id"]: r for r in dedicated_ir.get("scheduler_owned_sources", [])}
        self.sched_source_to_id = {str(r["source"]): str(r["id"]) for r in dedicated_ir.get("scheduler_owned_sources", [])}
        self.arch = [r for r in dedicated_ir["architectural_registers"] if r["kind"] != "GPIO"]
        self.prov_to_arch = {str(r["provenance"]): r for r in self.arch}
        self.state_names = set(self.prov_to_arch) | set(self.sched_source_to_id)
        self.compiled, _deps = _compile_transitions(fse_report["transition_rows"], self.state_names)
        self.raw_by_id = {str(r["transition_id"]): r for r in fse_report["transition_rows"]}
        self.by_events = defaultdict(list)
        for tr in self.compiled: 
            self.by_events[frozenset(tr.events)].append(tr)
        self.gpio_mask = int(dedicated_ir["startup"]["gpio_mask_constant"])
        startup = {str(x["register"]): int(x["value"][1]) for x in dedicated_ir["startup"]["register_values"]}
        self.startup_arch = {str(r["provenance"]): int(startup[str(r["id"])]) for r in self.arch}
        self.startup_gpio = {
            str(r["provenance"]): int(startup[str(r["id"])]) & self.gpio_mask
            for r in dedicated_ir["architectural_registers"] if r["kind"] == "GPIO"
        }
        self.cycle_count = 0
        self.state: dict[str, int] = {}
        self.sched: dict[str, int] = {}
        self.gpio_data = 0
        self.gpio_direction = 0
        self.active_run = False
        self._reset_state()

    def _reset_state(self): 
        self.state = dict(self.startup_arch)
        self.sched = {}
        for sid, row in self.sched_rows.items(): 
            self.sched[sid] = int(row["initial_value"][1])
        self.gpio_data = int(self.startup_gpio.get("gpio_data", 0))
        self.gpio_direction = int(self.startup_gpio.get("gpio_direction", 0))
        self.active_run = False

    @property
    def gpio_out(self): 
        return self.gpio_data & 0xFFFFFFFF
    @property
    def gpio_oe(self): 
        return self.gpio_direction & 0xFFFFFFFF

    def resolve_gpio(self, gpio_external: int) -> int: 
        ext = _u32(gpio_external)
        return _u32((self.gpio_out & self.gpio_oe) | (ext & ~self.gpio_oe))

    def _constraint_state(self) -> dict[str, int]: 
        s = dict(self.state)
        for source, sid in self.sched_source_to_id.items(): 
            s[source] = int(self.sched[sid])
        return s

    def _startup_condition(self, gpio_in: int) -> bool: 
        # Reuse the Dedicated startup predicates because the source relation is
        # defined only after the same steady-state entry point.
        groups = self.ir["startup"].get("startup_run_predicates", [])
        if not groups: 
            return True
        # Dedicated expressions use REG/HW rather than FSE tuple form; current
        # fixture startup condition is a direct GPIO expression.  Import lazily.
        from .dedicated_event_reachability import _eval_expr as eval_ded
        # Make synthetic reg view indexed by Dedicated IDs for startup evaluator.
        regs = {str(r['id']): self.state[str(r['provenance'])] for r in self.arch}
        regs['G_DATA'] = self.gpio_data
        regs['G_DIR'] = self.gpio_direction
        return any(all(bool(eval_ded(i['expression'], regs, self.sched, gpio_in)) == bool(i['polarity']) for i in g) for g in groups)

    def _apply_effects(self, raw: dict[str, Any], source_state: dict[str, int], gpio_in: int): 
        data = self.gpio_data
        direction = self.gpio_direction
        mask = self.gpio_mask
        for eff in raw.get('gpio_effects', []): 
            expr = _parse_control_expr(str(eff['expression']))
            val = _eval_control_expr(expr, source_state, gpio_in)
            kind = str(eff['kind'])
            if kind == 'GPIO_MASK': 
                mask = _u32(val)
            elif kind == 'GPIO_SET': 
                data = _u32(data | (val & mask))
            elif kind == 'GPIO_CLEAR_N': 
                data = _u32(data & (val | _u32(~mask)))
            elif kind == 'GPIO_DIR_SET': 
                direction = _u32(direction | (val & mask))
            elif kind == 'GPIO_DIR_CLEAR': 
                direction = _u32(direction & ~(val & mask))
            else: 
                raise ValueError(f'unsupported GPIO effect kind: {kind}')
        self.gpio_data = data & self.gpio_mask
        self.gpio_direction = direction & self.gpio_mask

    def step(self, gpio_external: int = 0xFFFFFFFF, *, reset: bool = False) -> FeasibleRelationCycle: 
        self.cycle_count += 1
        gpio_external = _u32(gpio_external)
        before_out, before_oe = self.gpio_out, self.gpio_oe
        gpio_in = self.resolve_gpio(gpio_external)
        active_before = self.active_run
        old_state = dict(self.state)
        old_sched = dict(self.sched)
        if reset: 
            self._reset_state()
            return FeasibleRelationCycle(self.cycle_count, True, active_before, self.active_run, gpio_external, gpio_in, before_out, before_oe, self.gpio_out, self.gpio_oe, None, {}, 
                {k: (old_state.get(k, 0), self.state.get(k, 0)) for k in set(old_state)|set(self.state) if old_state.get(k, 0)!=self.state.get(k, 0)}, 
                {k: (old_sched.get(k, 0), self.sched.get(k, 0)) for k in set(old_sched)|set(self.sched) if old_sched.get(k, 0)!=self.sched.get(k, 0)}, {})

        det = {d['event_id']: bool(_detector_value(d, self.sched_rows, self.sched, gpio_in)) for d in self.ir['scheduler_detectors']}
        active_events = frozenset(k for k, v in det.items() if v)
        event_name = '+'.join(sorted(active_events)) if active_events else 'EVENT_FREE'
        fired = {}
        if self.active_run and active_events: 
            s = self._constraint_state()
            candidates = []
            for tr in self.by_events.get(active_events, []): 
                gpio_names = set(tr.gpio_names)
                gpio = {name: gpio_in for name in gpio_names}
                if any(bool(_eval_expr(pred, s, gpio))!=pol for pred, pol in tr.state_constraints): 
                    continue
                if any(bool(_eval_expr(pred, s, gpio))!=pol for pred, pol in tr.gpio_constraints): 
                    continue
                candidates.append(tr)
            if not candidates: 
                fired = {'FSE': ['NO_MATCH']}
            else: 
                results = []
                for tr in candidates: 
                    ns = dict(self.state)
                    source = dict(s)
                    gpio = {name: gpio_in for name in tr.gpio_names}
                    for name, expr in tr.changed_outcomes: 
                        if name in self.sched_source_to_id: 
                            continue
                        if name in ns: 
                            width = int(self.prov_to_arch[name]['width'])
                            ns[name] = _eval_expr(expr, source, gpio) & ((1<<width)-1)
                    # GPIO effects are part of complete observable source outcome.
                    old_data, old_dir = self.gpio_data, self.gpio_direction
                    self._apply_effects(self.raw_by_id[tr.transition_id], source, gpio_in)
                    gd, gr = self.gpio_data, self.gpio_direction
                    self.gpio_data, self.gpio_direction = old_data, old_dir
                    results.append((tuple(sorted(ns.items())), gd, gr, tr.transition_id))
                uniq = {(a, b, c) for a, b, c, _ in results}
                if len(uniq)>1: 
                    raise RuntimeError(f'FSE source relation conflict cycle={self.cycle_count} events={sorted(active_events)} candidates={results}')
                ns_items, gd, gr = next(iter(uniq))
                self.state = dict(ns_items)
                self.gpio_data = gd
                self.gpio_direction = gr
                fired = {'FSE': [r[3] for r in results]}

        # Hardware scheduler maintenance is intentionally identical to Dedicated IR.
        next_sched = dict(self.sched)
        for sid in self.sched: 
            next_sched[sid] = _scheduler_next(self.sched_rows[sid], self.sched[sid], gpio_in, det, self.ir['scheduler_detectors'])
        self.sched = next_sched
        if not self.active_run and self._startup_condition(gpio_in): 
            self.active_run = True

        changed_state = {k: (old_state[k], self.state[k]) for k in old_state if old_state[k]!=self.state[k]}
        changed_sched = {k: (old_sched[k], self.sched[k]) for k in old_sched if old_sched[k]!=self.sched[k]}
        return FeasibleRelationCycle(self.cycle_count, False, active_before, self.active_run, gpio_external, gpio_in, before_out, before_oe, self.gpio_out, self.gpio_oe, event_name, det, changed_state, changed_sched, fired)
