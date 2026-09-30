#!/usr/bin/env python3
from __future__ import annotations

"""Sequential signal regression for the bundled bio2rtl examples.

The four simple examples are checked directly from the generated standard-cell
``*.structural.v`` netlists.  This intentionally does not trust the source C or
semantic model for the output sequence: DFF state is advanced from the mapped
D-input logic and the generated top-level output is sampled after each declared
external rising-edge event.

The I2C example is checked with the existing directed Dedicated Event fixture,
and the caller can additionally require the generated physical mapping proof and
I/O support report from a completed build.  This matches the Ver.1 freeze
verification structure: directed protocol behavior + exact mapped-core proof.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

CELL_RE = re.compile(r"^\s*([A-Za-z0-9_]+)\s+([A-Za-z0-9_]+)\s*\((.*)\);\s*$")
PORT_RE = re.compile(r"\.([A-Za-z0-9_]+)\(([^)]+)\)")
ASSIGN_RE = re.compile(r"^\s*assign\s+([A-Za-z0-9_]+)\s*=\s*([^;]+)\s*;\s*$")

SIMPLE_EXPECTED = {
    "set_after_3_rises": [0, 0, 1, 1, 1, 1, 1, 1],
    # Counter is incremented before the comparison, so post-edge sampling starts
    # in the HIGH half at state 1, then LOW for states 2/3, HIGH for 0/1.
    "clock_divider_by4": [1, 0, 0, 1, 1, 0, 0, 1],
    "clock_divider_by4_75pct": [0, 1, 1, 1, 0, 1, 1, 1],
    "serial_pattern_1101": [1, 1, 0, 1, 1, 1, 1, 1],
}


def _parse_structural(path: Path):
    cells = []
    assigns = []
    for line in path.read_text(encoding="utf-8").splitlines():
        m = CELL_RE.match(line)
        if m:
            typ, name, rest = m.groups()
            cells.append((typ, name, dict(PORT_RE.findall(rest))))
            continue
        m = ASSIGN_RE.match(line)
        if m:
            assigns.append(m.groups())
    if not cells:
        raise AssertionError(f"no standard cells found in {path}")
    return cells, assigns


def _net_value(net: str, env: dict[str, int]):
    net = net.strip()
    if net == "1'b0":
        return 0
    if net == "1'b1":
        return 1
    return env.get(net)


def _comb_value(typ: str, ports: dict[str, str], env: dict[str, int]):
    def p(name: str):
        return _net_value(ports[name], env)

    if typ.startswith("INV"):
        a = p("A")
        return None if a is None else 1 - a

    m = re.fullmatch(r"AND([234])(?:_X1)?", typ)
    if m:
        xs = [p(chr(65 + i)) for i in range(int(m.group(1)))]
        if 0 in xs:
            return 0
        return None if None in xs else 1

    m = re.fullmatch(r"OR([234])", typ)
    if m:
        xs = [p(chr(65 + i)) for i in range(int(m.group(1)))]
        if 1 in xs:
            return 1
        return None if None in xs else 0

    m = re.fullmatch(r"NAND([234])", typ)
    if m:
        xs = [p(chr(65 + i)) for i in range(int(m.group(1)))]
        if 0 in xs:
            return 1
        return None if None in xs else 0

    m = re.fullmatch(r"NOR([234])", typ)
    if m:
        xs = [p(chr(65 + i)) for i in range(int(m.group(1)))]
        if 1 in xs:
            return 0
        return None if None in xs else 1

    if typ == "XOR2":
        a, b = p("A"), p("B")
        return None if a is None or b is None else a ^ b
    if typ == "XNOR2":
        a, b = p("A"), p("B")
        return None if a is None or b is None else 1 - (a ^ b)
    if typ == "MUX2":
        s, a, b = p("S"), p("A"), p("B")
        if s is None:
            return a if a == b else None
        return b if s else a

    # DEL4/clock buffers are part of the event detector clock path.  The simple
    # example regression advances one declared external rise at a time, so these
    # temporal primitives are not evaluated as zero-delay Boolean gates.
    if typ in {"DEL4", "CLKBUF_X4", "INV_X4"}:
        return None
    raise AssertionError(f"unsupported combinational cell in signal regression: {typ}")


def _simulate_simple_netlist(path: Path, steps: int) -> list[int]:
    cells, assigns = _parse_structural(path)
    dffs = [c for c in cells if c[0] == "DFFR"]
    combs = [c for c in cells if c[0] not in {"DFFR", "DEL4", "CLKBUF_X4", "INV_X4"}]
    if not dffs:
        raise AssertionError(f"no DFFR found in {path}")

    # DFFR in the TR-1um mapping is active-high reset, Q=0/QB=1 on reset.
    env: dict[str, int] = {"reset": 0, "tick": 1}
    for _, _, ports in dffs:
        env[ports["Q"]] = 0
        env[ports["QB"]] = 1

    def settle() -> None:
        for _ in range(256):
            changed = False
            for typ, _, ports in combs:
                out = ports.get("Y")
                if out is None:
                    continue
                value = _comb_value(typ, ports, env)
                if value is not None and env.get(out) != value:
                    env[out] = value
                    changed = True
            for lhs, rhs in assigns:
                value = _net_value(rhs, env)
                if value is not None and env.get(lhs) != value:
                    env[lhs] = value
                    changed = True
            if not changed:
                return
        raise AssertionError(f"combinational logic did not settle in {path}")

    settle()
    observed = []
    for edge in range(steps):
        settle()
        next_state = []
        for _, inst, ports in dffs:
            d = _net_value(ports["D"], env)
            if d is None:
                raise AssertionError(f"unresolved D input at edge {edge + 1}: {inst}.{ports['D']}")
            next_state.append((ports["Q"], ports["QB"], d))
        # All state flops in these examples share the generated external-rise
        # event clock; update simultaneously on one declared TICK rising edge.
        for q, qb, d in next_state:
            env[q] = d
            env[qb] = 1 - d
        settle()
        out = env.get("out_out")
        if out not in (0, 1):
            raise AssertionError(f"out_out unresolved after edge {edge + 1}: {out}")
        observed.append(int(out))
    return observed


def _check_gpio_o_build(build_dir: Path, project: str) -> None:
    io = json.loads((build_dir / "io_report.json").read_text(encoding="utf-8"))
    rows = {str(x["name"]): x for x in io.get("io", [])}
    assert rows["OUT"]["support"] == "gpio_o", rows["OUT"]
    assert rows["OUT"]["electrical_kind"] in {"push_pull_output", "bidirectional_gpio_no_readback"}
    full = json.loads((build_dir / "generic_fullchip_export_audit.json").read_text(encoding="utf-8"))
    inst = {x.get("pad"): x for x in full.get("support_instances", []) if x.get("pad")}
    assert inst["OUT"]["recipe"] == "tr1um_gpio_o_v1", inst
    # The support regression separately proves OE=1 => PAD=OUT and OE=0 => Hi-Z.
    # For these four examples OUT is inferred as a fixed push-pull output.


def _load_directed_module():
    path = ROOT / "semantic_frontend" / "tools" / "run_dedicated_event_directed_model.py"
    spec = importlib.util.spec_from_file_location("bio2rtl_directed_i2c", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _check_i2c_directed(ir_path: Path, trace_dir: Path) -> dict:
    mod = _load_directed_module()
    ir = json.loads(ir_path.read_text(encoding="utf-8"))
    trace_dir.mkdir(parents=True, exist_ok=True)
    results = {}

    # Keep the established directed model checks.
    for name, fn in [
        ("address_ack", mod.test_address_ack),
        ("gpio_write", mod.test_gpio_write),
        ("gpio_read", mod.test_gpio_read),
    ]:
        ok, detail = fn(ir, trace_dir)
        results[name] = {"status": "PASS" if ok else "FAIL", "detail": detail}
        if not ok:
            raise AssertionError(f"I2C {name} failed: {detail}")

    # Also reproduce the complete frozen electrical-benchmark transaction at the
    # digital semantic boundary: output write, switch to inputs, repeated START,
    # 0x01 then 0xFF read, master NACK, and mismatched-address NACK.
    h = mod.new_harness(ir)
    write_acks = []
    write_acks += h.write_register(0x00, 0x00)
    write_acks += h.write_register(0x01, 0x01)
    output_state = (
        (h.sim.gpio_oe >> mod.GPIO0) & 1,
        (h.sim.gpio_oe >> mod.GPIO1) & 1,
        (h.sim.gpio_out >> mod.GPIO0) & 1,
        (h.sim.gpio_out >> mod.GPIO1) & 1,
    )
    if not all(write_acks) or output_state != (1, 1, 1, 0):
        raise AssertionError(f"I2C output write mismatch: acks={write_acks}, state={output_state}")

    dir_acks = h.write_register(0x00, 0x03)
    h.set_bit(mod.GPIO0, 1)
    h.set_bit(mod.GPIO1, 0)
    h.hold("external_gpio")
    h.start()
    h.send_byte(0x84)
    a0 = h.slave_ack()
    h.send_byte(0x02)
    a1 = h.slave_ack()
    h.start()  # repeated START
    h.send_byte(0x85)
    a2 = h.slave_ack()
    read_acks = [a0, a1, a2]
    if not all(dir_acks + read_acks):
        raise AssertionError(f"I2C read setup ACK failure: {dir_acks + read_acks}")

    first = h.receive_byte()
    # Master ACK after the first byte: actively pull SDA low for the ninth clock.
    h.set_bit(mod.SCL, 0)
    h.set_bit(mod.SDA, 0)
    h.hold("master_ack_low")
    h.set_bit(mod.SCL, 1)
    h.hold("master_ack_high")
    h.set_bit(mod.SCL, 0)
    h.hold("master_ack_fall")
    h.set_bit(mod.SDA, 1)
    second = h.receive_byte()
    h.master_nack()
    h.stop()
    if (first, second) != (0x01, 0xFF):
        raise AssertionError(f"I2C read mismatch: first=0x{first:02x}, second=0x{second:02x}")

    h.start()
    h.send_byte(0x86)
    mismatch_ack = h.slave_ack()
    h.stop()
    if mismatch_ack:
        raise AssertionError("I2C mismatch address 0x86 was ACKed")
    h.save_trace(trace_dir / "full_freeze_sequence.csv")
    results["full_freeze_sequence"] = {
        "status": "PASS",
        "write_acks": write_acks,
        "output_state_oe0_oe1_out0_out1": list(output_state),
        "direction_acks": dir_acks,
        "read_setup_acks": read_acks,
        "first_read": f"0x{first:02X}",
        "second_read": f"0x{second:02X}",
        "address_0x86": "NACK",
    }
    results["status"] = "PASS"
    return results

def _check_i2c_build_proof(build_dir: Path) -> None:
    io = json.loads((build_dir / "io_report.json").read_text(encoding="utf-8"))
    support = {str(x["name"]): str(x.get("support")) for x in io.get("io", [])}
    assert support == {"SCL": "direct", "SDA": "sda_io", "GPIO0": "gpio_io", "GPIO1": "gpio_io"}, support
    for name in ("GLOBAL_SEMANTIC_MAP_PROOF.json", "SELECTED_PHYSICAL_MAP_PROOF.json"):
        proof = json.loads((build_dir / name).read_text(encoding="utf-8"))
        assert proof.get("status") == "PASS", (name, proof.get("status"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Check bundled example signal sequences from generated outputs")
    ap.add_argument("--build-root", type=Path, default=ROOT / "build" / "bio_sim_demo",
                    help="directory containing one completed build directory per example "
                         "(default: build/bio_sim_demo)")
    ap.add_argument("--i2c-ir", type=Path, default=None,
                    help="directed_reference_ir.json for i2c_gpio_2bit; enables directed I2C behavior regression")
    ap.add_argument("--i2c-proof-build", type=Path, default=None,
                    help="completed i2c_gpio_2bit build; require final mapping proofs and support selection")
    ap.add_argument("--trace-dir", type=Path, default=ROOT / "build" / "example_signal_traces")
    ap.add_argument("--skip-i2c", action="store_true",
                    help="check only the four simple examples; default requires the I2C build too")
    ap.add_argument("--json-out", type=Path, default=None)
    args = ap.parse_args()

    report = {"version": "bio2rtl-example-signal-regression-v1", "simple": {}, "i2c": {}}
    for project, expected in SIMPLE_EXPECTED.items():
        build = args.build_root / project
        structural = build / f"{project}.structural.v"
        if not structural.is_file():
            raise FileNotFoundError(structural)
        observed = _simulate_simple_netlist(structural, len(expected))
        if observed != expected:
            raise AssertionError(f"{project}: observed={observed}, expected={expected}")
        _check_gpio_o_build(build, project)
        report["simple"][project] = {"status": "PASS", "expected": expected, "observed": observed,
                                      "sample_point": "after each TICK rising edge", "pad_support": "gpio_o"}
        print(f"{project}: PASS: {observed}")

    if args.skip_i2c:
        report["i2c"]["directed"] = {"status": "SKIPPED"}
        report["i2c"]["mapped_core_proof"] = "SKIPPED"
    else:
        i2c_build = args.build_root / "i2c_gpio_2bit"
        i2c_ir = args.i2c_ir or (i2c_build / "semantic" / "directed_reference_ir.json")
        i2c_proof_build = args.i2c_proof_build or i2c_build
        if not i2c_ir.is_file():
            raise FileNotFoundError(f"I2C directed reference IR not found: {i2c_ir}")
        report["i2c"]["directed"] = _check_i2c_directed(i2c_ir, args.trace_dir)
        print("i2c_gpio_2bit directed behavior: PASS")
        _check_i2c_build_proof(i2c_proof_build)
        report["i2c"]["mapped_core_proof"] = "PASS"
        print("i2c_gpio_2bit mapped-core proof/support: PASS")

    i2c_ok = args.skip_i2c or (
        report["i2c"]["directed"].get("status") == "PASS"
        and report["i2c"]["mapped_core_proof"] == "PASS"
    )
    report["status"] = "PASS" if (
        all(x["status"] == "PASS" for x in report["simple"].values()) and i2c_ok
    ) else "FAIL"

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"EXAMPLE SIGNAL REGRESSION: {report['status']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
