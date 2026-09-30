#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bio2rtl.dedicated_event_simulator import DedicatedEventSimulator

SCL = 16
SDA = 17
GPIO0 = 18
GPIO1 = 19


class I2CHarness: 
    def __init__(self, sim: DedicatedEventSimulator, *, hold_cycles: int = 4): 
        self.sim = sim
        self.hold_cycles = hold_cycles
        self.external = 0xFFFFFFFF
        self.trace = []

    def _record(self, rec, label: str) -> None: 
        row = asdict(rec)
        row["label"] = label
        row["gpio_in_16"] = (rec.gpio_in >> SCL) & 1
        row["gpio_in_17"] = (rec.gpio_in >> SDA) & 1
        row["gpio_out_17"] = (rec.gpio_out_after >> SDA) & 1
        row["gpio_oe_17"] = (rec.gpio_oe_after >> SDA) & 1
        row["gpio_out_18"] = (rec.gpio_out_after >> GPIO0) & 1
        row["gpio_oe_18"] = (rec.gpio_oe_after >> GPIO0) & 1
        row["gpio_out_19"] = (rec.gpio_out_after >> GPIO1) & 1
        row["gpio_oe_19"] = (rec.gpio_oe_after >> GPIO1) & 1
        row["changed_registers"] = repr(row["changed_registers"])
        row["changed_scheduler"] = repr(row["changed_scheduler"])
        row["fired_rules"] = repr(row["fired_rules"])
        row["detector_values"] = repr(row["detector_values"])
        self.trace.append(row)

    def set_bit(self, bit: int, value: int) -> None: 
        if value: 
            self.external |= 1 << bit
        else: 
            self.external &= ~(1 << bit)

    def cycle(self, label: str, *, reset: bool = False): 
        rec = self.sim.step(self.external, reset = reset)
        self._record(rec, label)
        return rec

    def hold(self, label: str, cycles: int | None = None): 
        for _ in range(self.hold_cycles if cycles is None else cycles): 
            self.cycle(label)

    def initialize(self): 
        for _ in range(5): 
            self.cycle("reset", reset = True)
        for _ in range(12): 
            self.cycle("startup")

    def start(self): 
        self.set_bit(SCL, 1)
        self.set_bit(SDA, 1)
        self.hold("start_idle")
        self.set_bit(SDA, 0)
        self.hold("start_sda_fall")
        self.set_bit(SCL, 0)
        self.hold("start_scl_low")

    def stop(self): 
        self.set_bit(SCL, 0)
        self.set_bit(SDA, 0)
        self.hold("stop_low")
        self.set_bit(SCL, 1)
        self.hold("stop_scl_high")
        self.set_bit(SDA, 1)
        self.hold("stop_sda_rise")

    def send_bit(self, value: int, bit_index: int | None = None): 
        suffix = "" if bit_index is None else f"_b{bit_index}"
        self.set_bit(SCL, 0)
        self.set_bit(SDA, value)
        self.hold("tx_low" + suffix)
        self.set_bit(SCL, 1)
        self.hold("tx_high" + suffix)
        self.set_bit(SCL, 0)
        self.hold("tx_fall" + suffix)

    def send_byte(self, value: int): 
        for i in range(7, -1, -1): 
            self.send_bit((value >> i) & 1, i)

    def slave_ack(self) -> bool: 
        self.set_bit(SCL, 0)
        self.set_bit(SDA, 1)
        self.hold("ack_low")
        low_ack = ((self.sim.resolve_gpio(self.external) >> SDA) & 1) == 0
        self.set_bit(SCL, 1)
        self.hold("ack_high")
        high_ack = ((self.sim.resolve_gpio(self.external) >> SDA) & 1) == 0
        self.set_bit(SCL, 0)
        self.hold("ack_fall")
        return low_ack or high_ack

    def write_register(self, selector: int, value: int) -> list[bool]: 
        self.start()
        self.send_byte(0x84)
        a0 = self.slave_ack()
        self.send_byte(selector)
        a1 = self.slave_ack()
        self.send_byte(value)
        a2 = self.slave_ack()
        self.stop()
        self.hold("post_write")
        return [a0, a1, a2]

    def receive_byte(self) -> int: 
        value = 0
        self.set_bit(SDA, 1)
        for i in range(7, -1, -1): 
            self.set_bit(SCL, 0)
            self.hold(f"rx_low_b{i}")
            self.set_bit(SCL, 1)
            self.hold(f"rx_high_b{i}")
            value |= ((self.sim.resolve_gpio(self.external) >> SDA) & 1) << i
            self.set_bit(SCL, 0)
            self.hold(f"rx_fall_b{i}")
        return value

    def master_nack(self): 
        self.set_bit(SCL, 0)
        self.set_bit(SDA, 1)
        self.hold("nack_low")
        self.set_bit(SCL, 1)
        self.hold("nack_high")
        self.set_bit(SCL, 0)
        self.hold("nack_fall")

    def save_trace(self, path: Path) -> None: 
        path.parent.mkdir(parents = True, exist_ok = True)
        if not self.trace: 
            path.write_text("")
            return
        keys = list(self.trace[0])
        with path.open("w", newline = "") as f: 
            w = csv.DictWriter(f, fieldnames = keys, extrasaction = "ignore")
            w.writeheader()
            w.writerows(self.trace)


def new_harness(ir, *, apply_storage_plan: bool = False): 
    h = I2CHarness(DedicatedEventSimulator(ir, apply_storage_plan = apply_storage_plan))
    h.initialize()
    return h


def test_address_ack(ir, trace_dir: Path, *, apply_storage_plan: bool = False): 
    h = new_harness(ir, apply_storage_plan = apply_storage_plan)
    h.start()
    h.send_byte(0x84)
    ack = h.slave_ack()
    h.save_trace(trace_dir / "address_ack.csv")
    return ack, f"ACK observed={int(ack)}"


def test_gpio_write(ir, trace_dir: Path, *, apply_storage_plan: bool = False): 
    h = new_harness(ir, apply_storage_plan = apply_storage_plan)
    ack0 = h.write_register(0x00, 0x00)
    ack1 = h.write_register(0x01, 0x01)
    oe18 = (h.sim.gpio_oe >> GPIO0) & 1
    oe19 = (h.sim.gpio_oe >> GPIO1) & 1
    out18 = (h.sim.gpio_out >> GPIO0) & 1
    out19 = (h.sim.gpio_out >> GPIO1) & 1
    ok = all(ack0 + ack1) and (oe18, oe19, out18, out19) == (1, 1, 1, 0)
    h.save_trace(trace_dir / "gpio_write.csv")
    return ok, (
        f"acks={ack0 + ack1} final(OE18,OE19,OUT18,OUT19)="
        f"{(oe18, oe19, out18, out19)} expected=(1, 1, 1, 0)"
    )


def test_gpio_read(ir, trace_dir: Path, *, apply_storage_plan: bool = False): 
    h = new_harness(ir, apply_storage_plan = apply_storage_plan)
    ack_cfg = h.write_register(0x00, 0x03)
    h.set_bit(GPIO0, 1)
    h.set_bit(GPIO1, 0)
    h.hold("external_gpio")
    h.start()
    h.send_byte(0x84)
    a0 = h.slave_ack()
    h.send_byte(0x02)
    a1 = h.slave_ack()
    h.start()
    h.send_byte(0x85)
    a2 = h.slave_ack()
    value = h.receive_byte()
    h.master_nack()
    h.stop()
    oe18 = (h.sim.gpio_oe >> GPIO0) & 1
    oe19 = (h.sim.gpio_oe >> GPIO1) & 1
    # Existing SV test defines input mode as OE=0 and expects input register 0x01.
    ok = all(ack_cfg + [a0, a1, a2]) and oe18 == 0 and oe19 == 0 and value == 0x01
    h.save_trace(trace_dir / "gpio_read.csv")
    return ok, (
        f"acks={ack_cfg + [a0,a1,a2]} OE18={oe18} OE19={oe19} "
        f"read=0x{value:02x} expected=0x01"
    )


def main() -> int: 
    ap = argparse.ArgumentParser(description = "Directed I2C regression against the Dedicated Event IR Python model")
    ap.add_argument("--ir", type = Path, required = True)
    ap.add_argument("--trace-dir", type = Path, required = True)
    ap.add_argument("--apply-storage-plan", action = "store_true")
    ap.add_argument("--expect-current-failure", action = "store_true", 
                    help = "diagnostic mode: return success only when at least one directed test fails")
    args = ap.parse_args()
    ir = json.loads(args.ir.read_text())
    tests = [
        ("address_ack", test_address_ack), 
        ("gpio_write", test_gpio_write), 
        ("gpio_read", test_gpio_read), 
    ]
    all_ok = True
    for name, fn in tests: 
        ok, detail = fn(ir, args.trace_dir, apply_storage_plan = args.apply_storage_plan)
        print(f"{name}: {'PASS' if ok else 'FAIL'}: {detail}")
        all_ok &= ok
    print(f"DIRECTED MODEL REGRESSION: {'PASS' if all_ok else 'FAIL'}")
    if args.expect_current_failure: 
        return 0 if not all_ok else 2
    return 0 if all_ok else 1


if __name__ == "__main__": 
    raise SystemExit(main())
