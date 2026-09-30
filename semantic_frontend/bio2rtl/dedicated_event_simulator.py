from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .dedicated_event_reachability import (
    _detector_value, 
    _eval_expr, 
    _event_class, 
    _scheduler_next, 
)


@dataclass
class DedicatedEventCycle: 
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


class DedicatedEventSimulator: 
    """Cycle-accurate Python model of the CFG-free Dedicated Event IR.

    This class deliberately contains no protocol-specific assumptions.  It is
    useful both as a regression oracle for generated SystemVerilog and as a
    diagnostic tool while the Dedicated Event scheduler is being developed.
    GPIO resolution follows the generated RTL testbench convention:

        gpio_in = (gpio_out & gpio_oe) | (gpio_external & ~gpio_oe)

    Only bits selected by startup.gpio_mask_constant are retained as physical
    GPIO state, matching the Dedicated Event backend.
    """

    def __init__(self, ir: dict[str, Any], *, apply_storage_plan: bool = False): 
        self.ir = ir
        self.storage_plan = ir.get("storage_optimization") if apply_storage_plan else None
        self.storage_rows = {str(x["register"]): x for x in self.storage_plan.get("register_storage", [])} if self.storage_plan else {}
        self.derived_rows = {rid: row for rid, row in self.storage_rows.items() if str(row.get("storage_kind")) == "DERIVED_EXPR"}
        self.reg_specs = {r["id"]: r for r in ir["architectural_registers"]}
        self.reg_ids = [r["id"] for r in ir["architectural_registers"]]
        self.sched_rows = {r["id"]: r for r in ir.get("scheduler_owned_sources", [])}
        self.sched_ids = list(self.sched_rows)
        self.predicates = {p["id"]: p["expression"] for p in ir["predicate_basis"]}
        self.rules: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for rule in ir["update_rules"]: 
            if rule.get("materialize"): 
                self.rules[(str(rule["event_class"]), str(rule["target"]))].append(rule)
        self.gpio_mask = int(ir["startup"]["gpio_mask_constant"])
        self.startup_values = {
            str(x["register"]): int(x["value"][1]) for x in ir["startup"]["register_values"]
        }
        self.cycle_count = 0
        self.regs: dict[str, int] = {}
        self.sched: dict[str, int] = {}
        self.active_run = False
        self._reset_state()

    def _canonical_register(self, rid: str, value: int) -> int: 
        spec = self.reg_specs[rid]
        semw = int(spec["width"])
        if spec["kind"] == "GPIO": 
            value = int(value) & self.gpio_mask
        else: 
            value = int(value) & ((1 << semw) - 1)
        row = self.storage_rows.get(rid)
        if row is None: 
            return value
        kind = str(row["storage_kind"])
        bits = int(row["storage_bits"])
        if kind == "CONST": 
            return int(row["constant_value"]) & ((1 << semw) - 1)
        if kind == "DIRECT": 
            return value
        if kind == "DERIVED_EXPR": 
            # The semantic value is recomputed from its proven source state by
            # _refresh_derived_registers(); this path only width-clamps an
            # initialization/intermediate placeholder.
            return value
        if kind == "NARROW_ZERO_EXTEND": 
            return value & ((1 << bits) - 1)
        if kind == "PACKED_MASK_BITS": 
            result = 0
            for bit in row.get("stored_bits", []): 
                result |= value & (1 << int(bit))
            for bit in row.get("constant_one_bits", []): 
                result |= 1 << int(bit)
            return result & ((1 << semw) - 1)
        raise ValueError(f"unsupported storage kind {kind!r} for {rid}")

    def _refresh_derived_registers(self, gpio_in: int = 0) -> None: 
        if not self.derived_rows: 
            return
        pending = set(self.derived_rows)
        while pending: 
            progress = False
            for rid in sorted(list(pending)): 
                row = self.derived_rows[rid]
                deps = set(str(x) for x in row.get("derived_from", []))
                if deps & pending: 
                    continue
                semw = int(self.reg_specs[rid]["width"])
                value = int(_eval_expr(row["derived_expression"], self.regs, self.sched, gpio_in))
                self.regs[rid] = value & ((1 << semw) - 1)
                pending.remove(rid)
                progress = True
            if not progress: 
                raise ValueError(f"cyclic derived storage dependency: {sorted(pending)}")

    def _reset_state(self) -> None: 
        self.regs = {
            rid: self._canonical_register(rid, self.startup_values[rid]) for rid in self.reg_ids
        }
        self.sched = {}
        for sid, row in self.sched_rows.items(): 
            raw = row["initial_value"]
            if not isinstance(raw, list) or len(raw) != 2 or raw[0] != "CONST": 
                raise ValueError(f"non-constant scheduler reset: {row!r}")
            self.sched[sid] = int(raw[1])
        self._refresh_derived_registers(0)
        self.active_run = False

    @property
    def gpio_out(self) -> int: 
        return int(self.regs.get("G_DATA", 0)) & 0xFFFFFFFF

    @property
    def gpio_oe(self) -> int: 
        return int(self.regs.get("G_DIR", 0)) & 0xFFFFFFFF

    def resolve_gpio(self, gpio_external: int) -> int: 
        gpio_external &= 0xFFFFFFFF
        return ((self.gpio_out & self.gpio_oe) | (gpio_external & (~self.gpio_oe & 0xFFFFFFFF))) & 0xFFFFFFFF

    def _startup_condition(self, gpio_in: int) -> bool: 
        groups = self.ir["startup"].get("startup_run_predicates", [])
        if not groups: 
            return True
        return any(
            all(
                bool(_eval_expr(item["expression"], self.regs, self.sched, gpio_in))
                == bool(item["polarity"])
                for item in group
            )
            for group in groups
        )

    def step(self, gpio_external: int = 0xFFFFFFFF, *, reset: bool = False) -> DedicatedEventCycle: 
        gpio_external &= 0xFFFFFFFF
        self.cycle_count += 1
        out_before = self.gpio_out
        oe_before = self.gpio_oe
        gpio_in = self.resolve_gpio(gpio_external)
        active_before = self.active_run

        if reset: 
            old_regs = dict(self.regs)
            old_sched = dict(self.sched)
            self._reset_state()
            return DedicatedEventCycle(
                cycle = self.cycle_count, 
                reset = True, 
                active_run_before = active_before, 
                active_run_after = self.active_run, 
                gpio_external = gpio_external, 
                gpio_in = gpio_in, 
                gpio_out_before = out_before, 
                gpio_oe_before = oe_before, 
                gpio_out_after = self.gpio_out, 
                gpio_oe_after = self.gpio_oe, 
                event_class = None, 
                detector_values = {}, 
                changed_registers = {k: (old_regs[k], self.regs[k]) for k in self.reg_ids if old_regs[k] != self.regs[k]}, 
                changed_scheduler = {k: (old_sched[k], self.sched[k]) for k in self.sched_ids if old_sched[k] != self.sched[k]}, 
                fired_rules = {}, 
            )

        detector_values = {
            d["event_id"]: bool(_detector_value(d, self.sched_rows, self.sched, gpio_in))
            for d in self.ir["scheduler_detectors"]
        }
        event = _event_class(self.ir, detector_values)

        old_regs = dict(self.regs)
        old_sched = dict(self.sched)
        next_regs = dict(self.regs)
        fired_rules: dict[str, list[str]] = {}
        if self.active_run: 
            for rid in self.reg_ids: 
                if rid in self.derived_rows: 
                    continue
                matches: list[tuple[int, str]] = []
                for rule in self.rules.get((event, rid), []): 
                    enabled = all(
                        bool(_eval_expr(self.predicates[item["basis"]], self.regs, self.sched, gpio_in))
                        == bool(item["polarity"])
                        for item in rule.get("enable", [])
                    )
                    if not enabled: 
                        continue
                    value = self._canonical_register(
                        rid, _eval_expr(rule["outcome"], self.regs, self.sched, gpio_in)
                    )
                    matches.append((value, str(rule["rule_id"])))
                unique = {value for value, _ in matches}
                if len(unique) > 1: 
                    raise RuntimeError(
                        f"conflicting Dedicated Event updates at cycle {self.cycle_count}: "
                        f"event={event} target={rid} matches={matches}"
                    )
                if matches: 
                    next_regs[rid] = matches[0][0]
                    fired_rules[rid] = [rule_id for _, rule_id in matches]

        next_sched = dict(self.sched)
        for sid in self.sched_ids: 
            next_sched[sid] = _scheduler_next(
                self.sched_rows[sid], 
                self.sched[sid], 
                gpio_in, 
                detector_values, 
                self.ir["scheduler_detectors"], 
            )

        # Match generated RTL: scheduler maintenance always runs outside reset.
        self.sched = next_sched
        if not self.active_run: 
            if self._startup_condition(gpio_in): 
                self.active_run = True
        else: 
            self.regs = next_regs
            self._refresh_derived_registers(gpio_in)

        changed_regs = {
            rid: (old_regs[rid], self.regs[rid]) for rid in self.reg_ids if old_regs[rid] != self.regs[rid]
        }
        changed_sched = {
            sid: (old_sched[sid], self.sched[sid]) for sid in self.sched_ids if old_sched[sid] != self.sched[sid]
        }
        return DedicatedEventCycle(
            cycle = self.cycle_count, 
            reset = False, 
            active_run_before = active_before, 
            active_run_after = self.active_run, 
            gpio_external = gpio_external, 
            gpio_in = gpio_in, 
            gpio_out_before = out_before, 
            gpio_oe_before = oe_before, 
            gpio_out_after = self.gpio_out, 
            gpio_oe_after = self.gpio_oe, 
            event_class = event, 
            detector_values = detector_values, 
            changed_registers = changed_regs, 
            changed_scheduler = changed_sched, 
            fired_rules = fired_rules, 
        )
