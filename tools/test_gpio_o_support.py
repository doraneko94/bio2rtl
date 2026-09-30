#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import hashlib, json, re

from bio2rtl.project_inputs import load_config_file
from bio2rtl.physical_interface import _resolve_io_support

ROOT = Path(__file__).resolve().parents[1]
LIB = json.loads((ROOT/'technology/support_recipes_v1.json').read_text())
REC = LIB['recipes']['tr1um_gpio_o_v1']
SCH = ROOT/'technology'/REC['sch']
SYM = ROOT/'technology'/REC['sym']

sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert sha(SCH) == REC['sha256']['sch']
assert sha(SYM) == REC['sha256']['sym']
assert REC['kind'] == 'gpio_output'
assert set(REC['ports']) == {'VDD','VSS','PAD','OUT','OUT_B','DIR','DIR_B'}
assert 'INPUT_VALUE' not in REC['ports']

sch = SCH.read_text()
sym = SYM.read_text()
assert 'BUF_X1' not in sch and 'BUF_X2' not in sch and 'INPUT_VALUE' not in sch
assert sch.count('TR-1um_5_stdcell/NAND2.sym') == 1
assert sch.count('TR-1um_5_stdcell/AND2_X1.sym') == 1

# gpio_o must preserve the output-driver cone of the established gpio_io support.
gpio_io = (ROOT/'technology/tr1um_hand_support_v1/gpio_io.sch').read_text()
for token in (
    'C {TR-1um_5_stdcell/NAND2.sym} 70 60 0 0 {name=x1}',
    'C {TR-1um_5_stdcell/AND2_X1.sym} 70 200 0 0 {name=x2}',
    'C {TR-1umLIB/MP.sym} 200 60 0 0 {name=XM1',
    'C {TR-1umLIB/MN.sym} 200 200 0 0 {name=XM2',
    'N 0 40 50 40 {lab=DIR_B}',
    'N 50 80 50 100 {lab=OUT}',
    'N 40 180 50 180 {lab=DIR_B}',
    'N 20 220 50 220 {lab=OUT_B}',
):
    assert token in sch and token in gpio_io, token

# Confirm the standard-cell pin geometry used by those wires: NAND(A,B)=
# (DIR_B,OUT), AND(A,B)=(DIR_B,OUT_B).
geom = json.loads((ROOT/'technology/tr1um_xschem_symbol_geometry.json').read_text())['cells']
assert geom['NAND2']['pins']['A']['x'] == -20 and geom['NAND2']['pins']['A']['y'] == -20
assert geom['NAND2']['pins']['B']['x'] == -20 and geom['NAND2']['pins']['B']['y'] == 20
assert geom['AND2_X1']['pins']['A']['x'] == -20 and geom['AND2_X1']['pins']['A']['y'] == -20
assert geom['AND2_X1']['pins']['B']['x'] == -20 and geom['AND2_X1']['pins']['B']['y'] == 20

pins = set(re.findall(r'\{name=([^ }]+) dir=', sym))
assert pins == {'VDD','VSS','PAD','OUT','OUT_B','DIR','DIR_B'}, pins

# Structural logic represented by gpio_o.sch:
# PMOS gate = NAND(DIR_B, OUT), NMOS gate = AND(DIR_B, OUT_B).
# Compiler bindings guarantee OUT_B=!OUT and DIR_B=output-enable.
# Verify all externally meaningful combinations exhaustively.
for oe in (0, 1):
    for out in (0, 1):
        out_b = 1-out
        p_gate = 1 - (oe & out)
        n_gate = oe & out_b
        p_on = (p_gate == 0)
        n_on = (n_gate == 1)
        assert not (p_on and n_on), (oe, out, p_gate, n_gate)
        if not oe:
            assert not p_on and not n_on
        elif out:
            assert p_on and not n_on
        else:
            assert n_on and not p_on

# Selection policy: gpio_o is valid only when readback is unnecessary.
assert _resolve_io_support('push_pull_output', 'auto', 'OUT', 18) == 'gpio_o'
assert _resolve_io_support('bidirectional_gpio_no_readback', 'auto', 'OUT', 18) == 'gpio_o'
assert _resolve_io_support('bidirectional_gpio', 'auto', 'GPIO0', 18) == 'gpio_io'
assert _resolve_io_support('push_pull_output_readback', 'auto', 'OUT', 18) == 'gpio_io'
for kind in ('bidirectional_gpio', 'push_pull_output_readback'):
    try:
        _resolve_io_support(kind, 'gpio_o', 'X', 1)
    except ValueError:
        pass
    else:
        raise AssertionError(f'gpio_o incorrectly accepted for {kind}')

expected = {
    'clock_divider_by4': {'TICK':'direct','OUT':'gpio_o'},
    'clock_divider_by4_75pct': {'TICK':'direct','OUT':'gpio_o'},
    'serial_pattern_1101': {'TICK':'direct','OUT':'gpio_o'},
    'set_after_3_rises': {'TICK':'direct','OUT':'gpio_o'},
    'i2c_gpio_2bit': {'SCL':'direct','SDA':'sda_io','GPIO0':'gpio_io','GPIO1':'gpio_io'},
}
for ex, want in expected.items():
    cfg = load_config_file(ROOT/'examples'/ex/'bio2rtl.toml')
    got = {str(row['name']): str(row.get('support','auto')) for row in cfg['io']}
    assert got == want, (ex, got, want)

print('GPIO_O_SUPPORT_REGRESSION: PASS')
