#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, random, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bio2rtl.dedicated_event_simulator import DedicatedEventSimulator
from tools.run_dedicated_event_directed_model import I2CHarness, GPIO0, GPIO1

class HistoryCoalescedSimulator(DedicatedEventSimulator): 
    def __init__(self, ir, pair, *, apply_storage_plan = True): 
        self.hist_pair = tuple(pair)
        self.hist_value = 0
        super().__init__(ir, apply_storage_plan = apply_storage_plan)
        a, b = self.hist_pair
        self.hist_value = self.regs[a]
        if self.regs[b]!=self.hist_value: 
            raise ValueError('reset values differ')
        self.regs[a] = self.regs[b] = self.hist_value
    def step(self, gpio_external = 0xffffffff, *, reset = False): 
        a, b = self.hist_pair
        # The coalesced implementation presents one physical history value at all semantic read sites.
        self.regs[a] = self.regs[b] = self.hist_value
        rec = super().step(gpio_external, reset = reset)
        if reset: 
            self.hist_value = self.regs[a]
        else: 
            fa = a in rec.fired_rules
            fb = b in rec.fired_rules
            if fa and fb and self.regs[a]!=self.regs[b]: 
                raise RuntimeError(f'coalesced write conflict {a}={self.regs[a]} {b}={self.regs[b]}')
            if fa: 
                self.hist_value = self.regs[a]
            elif fb: 
                self.hist_value = self.regs[b]
        self.regs[a] = self.regs[b] = self.hist_value
        return rec

def directed(ir, pair, trace_dir): 
    def newh(): 
        h = I2CHarness(HistoryCoalescedSimulator(ir, pair, apply_storage_plan = True))
        h.initialize()
        return h
    h = newh()
    h.start()
    h.send_byte(0x84)
    ack = h.slave_ack()
    h.save_trace(trace_dir/'address_ack.csv')
    if not ack: 
        raise SystemExit('FAIL directed address ACK')
    h = newh()
    a0 = h.write_register(0x00, 0x00)
    a1 = h.write_register(0x01, 0x01)
    vals = ((h.sim.gpio_oe>>GPIO0)&1, (h.sim.gpio_oe>>GPIO1)&1, (h.sim.gpio_out>>GPIO0)&1, (h.sim.gpio_out>>GPIO1)&1)
    h.save_trace(trace_dir/'gpio_write.csv')
    if not all(a0+a1) or vals!=(1, 1, 1, 0): 
        raise SystemExit(f'FAIL directed write acks={a0+a1} vals={vals}')
    h = newh()
    ac = h.write_register(0x00, 0x03)
    h.set_bit(GPIO0, 1)
    h.set_bit(GPIO1, 0)
    h.hold('external_gpio')
    h.start()
    h.send_byte(0x84)
    a0 = h.slave_ack()
    h.send_byte(0x02)
    a1 = h.slave_ack()
    h.start()
    h.send_byte(0x85)
    a2 = h.slave_ack()
    v = h.receive_byte()
    h.master_nack()
    h.stop()
    h.save_trace(trace_dir/'gpio_read.csv')
    if not all(ac+[a0, a1, a2]) or v!=1: 
        raise SystemExit(f'FAIL directed read acks={ac+[a0,a1,a2]} read={v:#x}')
    return v

def main(): 
    ap = argparse.ArgumentParser()
    ap.add_argument('--ir', type = Path, required = True)
    ap.add_argument('--metadata', type = Path, required = True)
    ap.add_argument('--cycles', type = int, default = 100000)
    ap.add_argument('--seed', type = lambda x: int(x, 0), default = 0xC011EA5E)
    ap.add_argument('--trace-dir', type = Path, required = True)
    ap.add_argument('--output', type = Path, required = True)
    a = ap.parse_args()
    ir = json.loads(a.ir.read_text())
    meta = json.loads(a.metadata.read_text())
    if meta.get('n_a'): 
        lines = ['BIO2RTL HISTORY-COALESCING MODEL EQUIVALENCE', '='*96, 'OPTIONAL RESULT             : N/A', 'identity semantic candidate : PASS', 'RESULT                      : PASS']
        text = '\n'.join(lines)+'\n'
        a.output.parent.mkdir(parents = True, exist_ok = True)
        a.output.write_text(text)
        print(text, end = '')
        return 0
    pair = list(map(str, meta['registers']))
    base = DedicatedEventSimulator(ir, apply_storage_plan = True)
    cand = HistoryCoalescedSimulator(ir, pair, apply_storage_plan = True)
    rng = random.Random(a.seed)
    checked = 0
    live_invariant = 0
    for cyc in range(a.cycles): 
        reset = cyc<2 or rng.randrange(8192) == 0
        ext = rng.getrandbits(32)
        base.step(ext, reset = reset)
        cand.step(ext, reset = reset)
        if base.active_run!=cand.active_run or base.sched!=cand.sched or base.gpio_out!=cand.gpio_out or base.gpio_oe!=cand.gpio_oe: 
            raise SystemExit(f'FAIL external/scheduler mismatch cycle={cyc}')
        for rid in base.reg_ids: 
            if rid in pair: 
                continue
            if base.regs[rid]!=cand.regs[rid]: 
                raise SystemExit(f'FAIL register mismatch cycle={cyc} {rid}: {base.regs[rid]} != {cand.regs[rid]}')
        # For the discovered two-phase pair in this benchmark, P12=0/1 selects the live bank.
        # This is diagnostic only; general correctness is checked above on all non-coalesced state and outputs.
        if pair == ['P13', 'P14']: 
            phase = base.regs.get('P12')
            if phase == 0: 
                if base.regs[pair[0]]!=cand.hist_value: 
                    raise SystemExit(f'FAIL live-bank invariant cycle={cyc} phase=0')
                live_invariant+=1
            elif phase == 1: 
                if base.regs[pair[1]]!=cand.hist_value: 
                    raise SystemExit(f'FAIL live-bank invariant cycle={cyc} phase=1')
                live_invariant+=1
        checked+=1
    a.trace_dir.mkdir(parents = True, exist_ok = True)
    val = directed(ir, pair, a.trace_dir)
    lines = ['BIO2RTL HISTORY-COALESCING MODEL EQUIVALENCE', '='*96, f'pair                       : {pair[0]},{pair[1]}', f'random cycles              : {a.cycles}', f'seed                       : {a.seed:#x}', f'cycles compared            : {checked}', f'live-bank invariant checks : {live_invariant}', 'all non-pair state         : PASS', 'scheduler / GPIO OUT/OE    : PASS', 'directed address/write/read: PASS', f'directed read value        : 0x{val:02x}', 'RESULT                     : PASS']
    text = '\n'.join(lines)+'\n'
    a.output.write_text(text)
    print(text, end = '')
if __name__ == '__main__': 
    main()
