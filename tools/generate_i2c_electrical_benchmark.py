#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

VDD = 3.3
PERIOD_US = 10.0          # 100 kHz benchmark; below the 400 kHz target ceiling.
HALF_US = PERIOD_US / 2.0
EDGE_US = 0.02            # finite source transition for SPICE convergence
PAUSE_US = 20.0


class PWL: 
    def __init__(self, initial: float): 
        self.events = [(0.0, float(initial))]
        self.value = float(initial)

    def set(self, t: float, value: float): 
        value = float(value)
        if value == self.value: 
            return
        # finite ramp: hold old value immediately before the requested edge
        t0 = max(0.0, t - EDGE_US)
        if self.events[-1][0] < t0: 
            self.events.append((t0, self.value))
        self.events.append((t, value))
        self.value = value

    def spice(self) -> str: 
        return "PWL(" + " ".join(f"{t:.3f}u {v:.6g}" for t, v in self.events) + ")"


class I2CMaster: 
    def __init__(self): 
        self.t = 10.0
        self.scl = PWL(VDD)       # actual SCL voltage source
        self.gate = PWL(0.0)      # gate of master open-drain NMOS: high=pull SDA low
        self.annotations = []

    def mark(self, text: str): 
        self.annotations.append({"time_us": round(self.t, 3), "event": text})

    def wait(self, dt: float): 
        self.t += dt

    def _low(self): 
        self.scl.set(self.t, 0.0)
        self.wait(HALF_US)

    def _high(self): 
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)

    def start(self, repeated: bool = False): 
        # SDA must be released before START; make SDA H->L while SCL is high.
        if self.scl.value != VDD: 
            self.scl.set(self.t, VDD)
            self.wait(HALF_US / 2)
        self.gate.set(self.t, 0.0)
        self.wait(HALF_US / 2)
        self.mark("REPEATED_START" if repeated else "START")
        self.gate.set(self.t, VDD)
        self.wait(HALF_US / 2)
        self._low()

    def stop(self): 
        # Hold SDA low, raise SCL, then release SDA while SCL is high.
        self.gate.set(self.t, VDD)
        if self.scl.value != 0.0: 
            self.scl.set(self.t, 0.0)
        self.wait(HALF_US)
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)
        self.mark("STOP")
        self.gate.set(self.t, 0.0)
        self.wait(PAUSE_US)

    def write_bit(self, bit: int): 
        # SCL is low here.  Master NMOS pulls low for zero, releases for one.
        self.gate.set(self.t, 0.0 if bit else VDD)
        self.wait(HALF_US / 2)
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)
        self.scl.set(self.t, 0.0)
        self.wait(HALF_US / 2)

    def write_byte(self, value: int, label: str): 
        self.mark(f"WRITE_{label}_0x{value:02X}")
        for bit in range(7, -1, -1): 
            self.write_bit((value >> bit) & 1)

    def ack_slot(self, label: str): 
        # Release SDA; slave is expected to drive it low during the ninth clock.
        self.gate.set(self.t, 0.0)
        self.wait(HALF_US / 2)
        self.mark(f"SLAVE_ACK_SLOT_{label}")
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)
        self.scl.set(self.t, 0.0)
        self.wait(HALF_US / 2)

    def read_byte(self, label: str): 
        # Master releases SDA for all eight data bits; slave drives it.
        self.gate.set(self.t, 0.0)
        self.mark(f"READ_{label}")
        for _ in range(8): 
            self.wait(HALF_US / 2)
            self.scl.set(self.t, VDD)
            self.wait(HALF_US)
            self.scl.set(self.t, 0.0)
            self.wait(HALF_US / 2)

    def master_ack(self): 
        self.mark("MASTER_ACK")
        self.gate.set(self.t, VDD)
        self.wait(HALF_US / 2)
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)
        self.scl.set(self.t, 0.0)
        self.wait(HALF_US / 2)
        self.gate.set(self.t, 0.0)

    def master_nack(self): 
        self.mark("MASTER_NACK")
        self.gate.set(self.t, 0.0)
        self.wait(HALF_US / 2)
        self.scl.set(self.t, VDD)
        self.wait(HALF_US)
        self.scl.set(self.t, 0.0)
        self.wait(HALF_US / 2)

    def transaction_write_reg(self, reg: int, data: int, tag: str): 
        self.start()
        self.write_byte(0x84, f"{tag}_ADDR_W")
        self.ack_slot(f"{tag}_ADDR")
        self.write_byte(reg, f"{tag}_REG")
        self.ack_slot(f"{tag}_REG")
        self.write_byte(data, f"{tag}_DATA")
        self.ack_slot(f"{tag}_DATA")
        self.stop()

    def transaction_read_reg2(self): 
        self.start()
        self.write_byte(0x84, "READSET_ADDR_W")
        self.ack_slot("READSET_ADDR")
        self.write_byte(0x02, "READSET_REG2")
        self.ack_slot("READSET_REG2")
        self.start(repeated = True)
        self.write_byte(0x85, "READ_ADDR_R")
        self.ack_slot("READ_ADDR_R")
        self.read_byte("BYTE1_EXPECT_0x01")
        self.master_ack()
        self.read_byte("BYTE2_EXPECT_0xFF")
        self.master_nack()
        self.stop()

    def mismatch(self): 
        self.start()
        self.write_byte(0x86, "MISMATCH_ADDR")
        self.ack_slot("MISMATCH_EXPECT_NACK")
        self.stop()


def build_waveforms(): 
    m = I2CMaster()
    m.transaction_write_reg(0x00, 0x00, "DIR_OUTPUT")
    m.transaction_write_reg(0x01, 0x01, "DATA_01")
    m.transaction_write_reg(0x00, 0x03, "DIR_INPUT")
    m.transaction_read_reg2()
    m.mismatch()
    # Leave bus idle high after the final STOP.
    end = m.t + 30.0
    if m.scl.value != VDD: 
        m.scl.set(m.t, VDD)
    m.gate.set(m.t, 0.0)
    return m, end


def sch_text(project: str, scl_pwl: str, gate_pwl: str, end_us: float) -> str: 
    # Xschem named-net wiring keeps the benchmark readable.  The master NMOS
    # uses the same TR-1um MN symbol/model family as the generated SDA support.
    lines = [
        'v {xschem version=3.4.8RC file_version=1.3}', 'G {}', 'K {}', 'V {}', 'S {}', 'F {}', 'E {}', 
        'T {bio2rtl I2C electrical benchmark: full-chip DUT + open-drain master} -760 -520 0 0 0.42 0.42 {}', 
        'T {Observe V(SCL), V(SDA), V(GPIO0), V(GPIO1)} -760 -485 0 0 0.28 0.28 {}', 
        f'C {{{project}_fullchip.sym}} 0 0 0 0 {{name=xdut}}', 
        'C {devices/lab_pin.sym} -120 -60 0 0 {name=d1 sig_type=std_logic lab=SCL}', 
        'C {devices/lab_pin.sym} -120 -20 0 0 {name=d2 sig_type=std_logic lab=SDA}', 
        'C {devices/lab_pin.sym} -120 20 0 0 {name=d3 sig_type=std_logic lab=GPIO0}', 
        'C {devices/lab_pin.sym} -120 60 0 0 {name=d4 sig_type=std_logic lab=GPIO1}', 
        'C {devices/lab_pin.sym} 0 -150 0 0 {name=d5 sig_type=std_logic lab=VDD}', 
        'C {devices/lab_pin.sym} 0 150 0 0 {name=d6 sig_type=std_logic lab=VSS}', 
        # Supply and SCL sources.  vsource pin convention is top=positive, bottom=negative.
        'C {devices/vsource.sym} -620 -300 0 0 {name=VDD_SRC value=3.3 savecurrent=false}', 
        'C {devices/lab_pin.sym} -620 -330 1 0 {name=vddp sig_type=std_logic lab=VDD}', 
        'C {devices/lab_pin.sym} -620 -270 1 0 {name=vddn sig_type=std_logic lab=VSS}', 
        f'C {{devices/vsource.sym}} -620 -150 0 0 {{name=VSCL value="{scl_pwl}" savecurrent=false}}', 
        'C {devices/lab_pin.sym} -620 -180 1 0 {name=sclp sig_type=std_logic lab=SCL}', 
        'C {devices/lab_pin.sym} -620 -120 1 0 {name=scln sig_type=std_logic lab=VSS}', 
        f'C {{devices/vsource.sym}} -620 0 0 0 {{name=VSDA_MASTER_LOW value="{gate_pwl}" savecurrent=false}}', 
        'C {devices/lab_pin.sym} -620 -30 1 0 {name=sgp sig_type=std_logic lab=SDA_MASTER_LOW}', 
        'C {devices/lab_pin.sym} -620 30 1 0 {name=sgn sig_type=std_logic lab=VSS}', 
        # SDA pull-up 4.7k.
        'C {devices/res.sym} 300 -150 0 0 {name=R_SDA_PULLUP value=4.7k footprint=1206 device=resistor m=1}', 
        'C {devices/lab_pin.sym} 300 -180 1 0 {name=rp1 sig_type=std_logic lab=VDD}', 
        'C {devices/lab_pin.sym} 300 -120 1 0 {name=rp2 sig_type=std_logic lab=SDA}', 
        # Master open-drain NMOS: drain=SDA, source/body=VSS, gate=SDA_MASTER_LOW.
        'C {TR-1umLIB/MN.sym} 420 -30 0 0 {name=XM_MASTER model=NMOS w=30u l=1u m=9 spiceprefix=X as=0 ad=0 ps=0 pd=0 nrd=0 nrs=0}', 
        'C {devices/lab_pin.sym} 460 -60 1 0 {name=md sig_type=std_logic lab=SDA}', 
        'C {devices/lab_pin.sym} 420 -30 0 0 {name=mg sig_type=std_logic lab=SDA_MASTER_LOW}', 
        'C {devices/lab_pin.sym} 460 0 1 0 {name=mb sig_type=std_logic lab=VSS}', 
        'C {devices/lab_pin.sym} 460 30 1 0 {name=ms sig_type=std_logic lab=VSS}', 
        # GPIO input fixtures.  They also act as benign loads while GPIOs are outputs.
        'C {devices/res.sym} 580 -80 0 0 {name=R_GPIO0_PULLUP value=1k footprint=1206 device=resistor m=1}', 
        'C {devices/lab_pin.sym} 580 -110 1 0 {name=g0a sig_type=std_logic lab=VDD}', 
        'C {devices/lab_pin.sym} 580 -50 1 0 {name=g0b sig_type=std_logic lab=GPIO0}', 
        'C {devices/res.sym} 700 80 0 0 {name=R_GPIO1_PULLDOWN value=1k footprint=1206 device=resistor m=1}', 
        'C {devices/lab_pin.sym} 700 50 1 0 {name=g1a sig_type=std_logic lab=GPIO1}', 
        'C {devices/lab_pin.sym} 700 110 1 0 {name=g1b sig_type=std_logic lab=VSS}', 
        # Tie VSS to SPICE node 0.
        'C {devices/gnd.sym} -300 260 0 0 {name=GND1 lab=VSS}', 
        'C {devices/lab_pin.sym} -300 230 1 0 {name=gndvss sig_type=std_logic lab=VSS}', 
        # Simulation and saved traces.  Real TR-1um model includes are supplied by xschemrc.bio2rtl / PDK.
        f'C {{devices/code_shown.sym}} -730 180 0 0 {{name=SIM only_toplevel=false value=".tran 0.05u {end_us:.3f}u\n.save v(SCL) v(SDA) v(GPIO0) v(GPIO1) v(SDA_MASTER_LOW)"}}', 
    ]
    return '\n'.join(lines) + '\n'


def main() -> int: 
    ap = argparse.ArgumentParser(description = 'Generate the explicit I2C full-chip electrical benchmark schematic.')
    ap.add_argument('--root', type = Path, default = Path(__file__).resolve().parents[1], help = 'bio2rtl source root (used only as the default build location)')
    ap.add_argument('--build-dir', type = Path, default = None, help = 'compiled build directory (default: <root>/build)')
    ap.add_argument('--xschem-dir', type = Path, default = None, help = 'Xschem directory (default: <build-dir>/xschem)')
    args = ap.parse_args()
    root = args.root.resolve()
    build = args.build_dir.expanduser().resolve() if args.build_dir is not None else root / 'build'
    xdir = args.xschem_dir.expanduser().resolve() if args.xschem_dir is not None else build / 'xschem'
    dhir_path = build / 'physical_dhir_v18_stage7.json'
    io_report_path = build / 'io_report.json'
    if not dhir_path.is_file() or not io_report_path.is_file(): 
        raise SystemExit('compile the project first; missing build/physical_dhir_v18_stage7.json or build/io_report.json')
    dhir = json.loads(dhir_path.read_text())
    project = str(dhir.get('module', ''))
    io = json.loads(io_report_path.read_text())
    required = {'SCL': 'direct_input', 'SDA': 'open_drain', 'GPIO0': 'bidirectional_gpio', 'GPIO1': 'bidirectional_gpio'}
    actual = {r['name']: r['electrical_kind'] for r in io.get('io', [])}
    if project != 'i2c_gpio_2bit' or any(actual.get(k) != v for k, v in required.items()): 
        raise SystemExit('this directed electrical benchmark fixture is only for the frozen i2c_gpio_2bit project')
    fullsym = xdir / f'{project}_fullchip.sym'
    if not fullsym.is_file(): 
        raise SystemExit(f'missing generated full-chip symbol: {fullsym}')

    m, end_us = build_waveforms()
    sch = xdir / f'{project}_i2c_benchmark.sch'
    sch.write_text(sch_text(project, m.scl.spice(), m.gate.spice(), end_us))
    expected = {
        'version': 'bio2rtl-i2c-electrical-benchmark-v1', 
        'status': 'GENERATED_NOT_TRANSIENT_VALIDATED_IN_THIS_RUNTIME', 
        'clock_hz': int(1e6 / PERIOD_US), 
        'dut_symbol': fullsym.name, 
        'schematic': sch.name, 
        'parts': {
            'dut': f'{project}_fullchip.sym', 
            'supply': '3.3 V source', 
            'sda_pullup': '4.7 kohm VDD-to-SDA', 
            'master_open_drain': 'TR-1um NMOS drain=SDA source/body=VSS gate=SDA_MASTER_LOW', 
            'gpio0_fixture': '1 kohm VDD-to-GPIO0', 
            'gpio1_fixture': '1 kohm GPIO1-to-VSS', 
        }, 
        'transactions': [
            'START 0x84 ACK 0x00 ACK 0x00 ACK STOP', 
            'START 0x84 ACK 0x01 ACK 0x01 ACK STOP', 
            'START 0x84 ACK 0x00 ACK 0x03 ACK STOP', 
            'START 0x84 ACK 0x02 ACK repeated-START 0x85 ACK read 0x01 master-ACK read 0xFF master-NACK STOP', 
            'START 0x86 NACK STOP', 
        ], 
        'expected': {
            'write_acks': True, 
            'after_data_0x01_gpio0': 'HIGH', 
            'after_data_0x01_gpio1': 'LOW', 
            'first_read': '0x01', 
            'second_read': '0xFF', 
            'address_0x86': 'NACK', 
        }, 
        'observe': ['V(SCL)', 'V(SDA)', 'V(GPIO0)', 'V(GPIO1)'], 
        'stimulus_annotations': m.annotations, 
        'stop_time_us': round(end_us, 3), 
    }
    (build / 'i2c_electrical_benchmark_expected.json').write_text(json.dumps(expected, indent = 2) + '\n')
    # Static self-audit: all required parts and nets must be visible in the .sch.
    text = sch.read_text()
    required_tokens = [
        f'{project}_fullchip.sym', 'R_SDA_PULLUP', 'value=4.7k', 'XM_MASTER', 
        'lab=SDA_MASTER_LOW', 'R_GPIO0_PULLUP', 'value=1k', 'R_GPIO1_PULLDOWN', 
        'lab=SCL', 'lab=SDA', 'lab=GPIO0', 'lab=GPIO1', '.tran', 'v(SCL)', 'v(SDA)', 'v(GPIO0)', 'v(GPIO1)'
    ]
    missing = [t for t in required_tokens if t not in text]
    audit = {
        'version': 'bio2rtl-i2c-electrical-benchmark-static-audit-v1', 
        'status': 'PASS' if not missing else 'FAIL', 
        'missing_tokens': missing, 
        'schematic': str(sch), 
        'fullchip_symbol': str(fullsym), 
        'transient_executed': False, 
        'reason_transient_not_executed': 'xschem/ngspice/TR-1um PDK runtime not available in this environment', 
    }
    (build / 'i2c_electrical_benchmark_static_audit.json').write_text(json.dumps(audit, indent = 2) + '\n')
    print(json.dumps(audit, indent = 2))
    return 0 if audit['status'] == 'PASS' else 1


if __name__ == '__main__': 
    raise SystemExit(main())
