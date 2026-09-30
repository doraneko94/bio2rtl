from __future__ import annotations

from collections import defaultdict
from typing import Any

from .dedicated_event_simulator import DedicatedEventCycle, DedicatedEventSimulator
from .dedicated_event_reachability import _detector_value, _eval_expr, _event_class, _scheduler_next


class EventSemanticSimulator(DedicatedEventSimulator): 
    """Cycle model for a proven detector+predicate cross-event relation."""

    def __init__(self, ir: dict[str, Any], *, apply_storage_plan: bool = True): 
        super().__init__(ir, apply_storage_plan = apply_storage_plan)
        meta = ir.get("event_semantic_update_relation")
        if not meta: 
            raise ValueError("IR has no event_semantic_update_relation")
        self.eventsem_rules: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for rule in meta.get("rules", []): 
            if self.storage_rows.get(str(rule["target"]), {}).get("storage_kind") == "DERIVED_EXPR": 
                continue
            self.eventsem_rules[str(rule["target"])].append(rule)

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
                cycle = self.cycle_count, reset = True, active_run_before = active_before, active_run_after = self.active_run, 
                gpio_external = gpio_external, gpio_in = gpio_in, gpio_out_before = out_before, gpio_oe_before = oe_before, 
                gpio_out_after = self.gpio_out, gpio_oe_after = self.gpio_oe, event_class = None, detector_values = {}, 
                changed_registers = {k: (old_regs[k], self.regs[k]) for k in self.reg_ids if old_regs[k]!=self.regs[k]}, 
                changed_scheduler = {k: (old_sched[k], self.sched[k]) for k in self.sched_ids if old_sched[k]!=self.sched[k]}, 
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
                for rule in self.eventsem_rules.get(rid, []): 
                    dok = all(bool(detector_values[str(x["detector"])]) == bool(x["polarity"]) for x in rule.get("detector_enable", []))
                    if not dok: 
                        continue
                    pok = all(bool(_eval_expr(self.predicates[str(x["basis"])], self.regs, self.sched, gpio_in)) == bool(x["polarity"]) for x in rule.get("enable", []))
                    if not pok: 
                        continue
                    value = self._canonical_register(rid, _eval_expr(rule["outcome"], self.regs, self.sched, gpio_in))
                    matches.append((value, str(rule["rule_id"])))
                unique = {v for v, _ in matches}
                if len(unique) > 1: 
                    raise RuntimeError(f"conflicting cross-event updates cycle={self.cycle_count} target={rid} matches={matches}")
                if matches: 
                    next_regs[rid] = matches[0][0]
                    fired_rules[rid] = [x[1] for x in matches]
        next_sched = dict(self.sched)
        for sid in self.sched_ids: 
            next_sched[sid] = _scheduler_next(self.sched_rows[sid], self.sched[sid], gpio_in, detector_values, self.ir["scheduler_detectors"])
        self.sched = next_sched
        if not self.active_run: 
            if self._startup_condition(gpio_in): 
                self.active_run = True
        else: 
            self.regs = next_regs
            self._refresh_derived_registers(gpio_in)
        return DedicatedEventCycle(
            cycle = self.cycle_count, reset = False, active_run_before = active_before, active_run_after = self.active_run, 
            gpio_external = gpio_external, gpio_in = gpio_in, gpio_out_before = out_before, gpio_oe_before = oe_before, 
            gpio_out_after = self.gpio_out, gpio_oe_after = self.gpio_oe, event_class = event, detector_values = detector_values, 
            changed_registers = {k: (old_regs[k], self.regs[k]) for k in self.reg_ids if old_regs[k]!=self.regs[k]}, 
            changed_scheduler = {k: (old_sched[k], self.sched[k]) for k in self.sched_ids if old_sched[k]!=self.sched[k]}, 
            fired_rules = fired_rules, 
        )
