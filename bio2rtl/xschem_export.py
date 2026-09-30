from __future__ import annotations
import json, os
from pathlib import Path
from collections import Counter, defaultdict

VERSION = 'bio2rtl-generic-xschem-core-export-v2'
OUTPUT_PINS = {'Y', 'Q', 'QB', 'Z', 'ZN', 'OUT'}


def _net(n: str, aliases: dict[str, str] | None = None) -> str: 
    n = n.strip()
    if n in ("1'b0", "1'h0", "1'd0"): 
        return 'VSS'
    if n in ("1'b1", "1'h1", "1'd1"): 
        return 'VDD'
    return (aliases or {}).get(n, n)


def _pin_layout(pins: list[str]) -> dict[str, tuple[int, int, str]]: 
    sig = [p for p in pins if p.lower() not in ('vdd', 'gnd', 'vss')]
    ins = [p for p in sig if p not in OUTPUT_PINS]
    outs = [p for p in sig if p in OUTPUT_PINS]
    pos: dict[str, tuple[int, int, str]] = {}

    def ys(n: int) -> list[int]: 
        if n <= 1: 
            return [0]
        return [int((i - (n - 1) / 2) * 20) for i in range(n)]

    for p, y in zip(ins, ys(len(ins))): 
        pos[p] = (-60, y, 'in')
    for p, y in zip(outs, ys(len(outs))): 
        pos[p] = (60, y, 'out')
    for p in pins: 
        if p.lower() == 'vdd': 
            pos[p] = (0, -60, 'inout')
        elif p.lower() in ('gnd', 'vss'): 
            pos[p] = (0, 60, 'inout')
    return pos


def _symbol(cell: str, pins: list[str]) -> tuple[str, dict[str, tuple[int, int, str]]]: 
    pos = _pin_layout(pins)
    lines = [
        'v {xschem version=3.4.8 file_version=1.3}', 
        'G {}', 
        f'K {{type=primitive\nformat="x@name @pinlist {cell}"\ntemplate="name=x1"}}', 
        'V {}', 'S {}', 'F {}', 'E {}', 
        'L 4 -40 -40 40 -40 {}', 
        'L 4 40 -40 40 40 {}', 
        'L 4 40 40 -40 40 {}', 
        'L 4 -40 40 -40 -40 {}', 
    ]
    for i, p in enumerate(pins, 1): 
        x, y, d = pos[p]
        if x < 0: 
            lines.append(f'L 4 -60 {y} -40 {y} {{}}')
        elif x > 0: 
            lines.append(f'L 4 40 {y} 60 {y} {{}}')
        elif y < 0: 
            lines.append('L 4 0 -60 0 -40 {}')
        else: 
            lines.append('L 4 0 40 0 60 {}')
        lines.append(
            f'B 5 {x-2.5:g} {y-2.5:g} {x+2.5:g} {y+2.5:g} '
            f'{{name={p} dir={d} pinnumber={i} sim_pinnumber={i}}}'
        )
        tx = x + 7 if x < 0 else x - 7 if x > 0 else x + 7
        ty = y - 3 if y >= 0 else y + 3
        flip = 1 if x > 0 else 0
        lines.append(f'T {{{p}}} {tx:g} {ty:g} 0 {flip} 0.12 0.12 {{}}')
    lines.append('T {@name} -35 -28 0 0 0.16 0.16 {}')
    lines.append(f'T {{{cell}}} -35 -8 0 0 0.14 0.14 {{}}')
    return '\n'.join(lines) + '\n', pos


def _core_symbol(ports: list[dict], project: str) -> str: 
    pins = [p['name'] for p in ports] + ['VDD', 'VSS']
    dirs = {p['name']: ('in' if p['direction'] == 'input' else 'out') for p in ports}
    dirs.update(VDD = 'inout', VSS = 'inout')
    ins = [p for p in pins if dirs[p] == 'in']
    outs = [p for p in pins if dirs[p] == 'out']
    pos: dict[str, tuple[int, int]] = {}
    for i, p in enumerate(ins): 
        pos[p] = (-100, -80 + i * 30)
    for i, p in enumerate(outs): 
        pos[p] = (100, -80 + i * 30)
    pos['VDD'] = (0, -120)
    pos['VSS'] = (0, 120)

    lines = [
        'v {xschem version=3.4.8 file_version=1.3}', 'G {}', 
        f'K {{type=subcircuit\nformat="x@name @pinlist {project}_core"\ntemplate="name=x1"}}', 
        'V {}', 'S {}', 'F {}', 'E {}', 
        'L 4 -80 -100 80 -100 {}', 'L 4 80 -100 80 100 {}', 
        'L 4 80 100 -80 100 {}', 'L 4 -80 100 -80 -100 {}', 
    ]
    for num, p in enumerate(pins, 1): 
        x, y = pos[p]
        d = dirs[p]
        lines.append(
            f'B 5 {x-2.5:g} {y-2.5:g} {x+2.5:g} {y+2.5:g} '
            f'{{name={p} dir={d} pinnumber={num} sim_pinnumber={num}}}'
        )
        if x < 0: 
            lines.append(f'L 4 -100 {y} -80 {y} {{}}')
        elif x > 0: 
            lines.append(f'L 4 80 {y} 100 {y} {{}}')
        elif y < 0: 
            lines.append('L 4 0 -120 0 -100 {}')
        else: 
            lines.append('L 4 0 100 0 120 {}')
        tx = -75 if x < 0 else 55 if x > 0 else 7
        lines.append(f'T {{{p}}} {tx:g} {y-4:g} 0 0 0.12 0.12 {{}}')
    lines += [
        'T {@name} -70 -88 0 0 0.2 0.2 {}', 
        'T {bio2rtl v1 trusted core} -70 -62 0 0 0.16 0.16 {}', 
    ]
    return '\n'.join(lines) + '\n'


def _emit_named_stub(lines: list[str], ax: int, ay: int, px: int, py: int, net: str, label_id: int, coord_labels: dict[tuple[int, int], str]) -> int: 
    if abs(px) >= abs(py) and px != 0: 
        ex, ey = ax + (30 if px > 0 else -30), ay
    elif py != 0: 
        ex, ey = ax, ay + (30 if py > 0 else -30)
    else: 
        ex, ey = ax + 30, ay
    key = (int(ex), int(ey))
    old = coord_labels.get(key)
    if old is not None and old != net: 
        raise RuntimeError(f'Xschem label coordinate collision at {key}: {old} vs {net}')
    coord_labels[key] = net
    lines.append(f'N {ax} {ay} {ex} {ey} {{}}')
    lines.append(f'C {{devices/lab_pin.sym}} {ex} {ey} 0 0 {{name=cl{label_id} sig_type=std_logic lab={net}}}')
    return label_id+1


def export(root: Path) -> dict: 
    root = Path(root)
    build = root / 'build'
    xdir = build / 'xschem'
    cdir = xdir / 'cells'
    sdir = build / 'spice'
    xdir.mkdir(parents = True, exist_ok = True)
    cdir.mkdir(exist_ok = True)
    sdir.mkdir(exist_ok = True)

    dhir = json.load(open(build / 'physical_dhir_v18_stage7.json'))
    pin_cfg = json.load(open(root / 'technology/tr1um_cell_spice_pins.json'))['cells']
    project = str(dhir.get('module', 'bio2rtl_core'))
    # Structural output aliases are part of the Physical DHIR, not protocol knowledge.
    # Map internal RHS nets to exported LHS port names so arbitrary projects are handled.
    output_alias = {str(a['rhs']): str(a['lhs']) for a in dhir.get('assignments', [])
                    if isinstance(a, dict) and 'rhs' in a and 'lhs' in a}

    layouts = {}
    for typ in sorted({c['type'] for c in dhir['cells']}): 
        txt, pos = _symbol(typ, pin_cfg[typ])
        (cdir / f'{typ}.sym').write_text(txt)
        layouts[typ] = pos
    (xdir / f'{project}_core.sym').write_text(_core_symbol(dhir['ports'], project))

    groups = defaultdict(list)
    order = []
    for c in dhir['cells']: 
        if c['component'] not in groups: 
            order.append(c['component'])
        groups[c['component']].append(c)

    lines = [
        'v {xschem version=3.4.8 file_version=1.3}', 
        'G {}', 'K {}', 'V {}', 'S {}', 'F {}', 'E {}', 
        f'T {{bio2rtl — generated semantic core ({len(dhir["cells"])} standard cells)}} 0 -220 0 0 0.5 0.5 {{}}', 
        'T {POR and physical I/O support are outside this core; see support_binding.json.} 0 -180 0 0 0.25 0.25 {}', 
    ]

    pn = 1
    py = -120
    for p in dhir['ports']: 
        lab = p['name']
        sym = 'devices/ipin.sym' if p['direction'] == 'input' else 'devices/opin.sym'
        x = -360 if p['direction'] == 'input' else 1200
        lines.append(f'C {{{sym}}} {x} {py} 0 0 {{name=p{pn} lab={lab} sim_pinnumber={pn}}}')
        py += 40
        pn += 1
    lines.append(f'C {{devices/ipin.sym}} -360 {py+20} 0 0 {{name=p{pn} lab=VDD sim_pinnumber={pn}}}')
    pn += 1
    lines.append(f'C {{devices/ipin.sym}} -360 {py+60} 0 0 {{name=p{pn} lab=VSS sim_pinnumber={pn}}}')

    y = 0
    inst_count = 0
    label_id = 1
    coord_labels = {}
    for g in order: 
        cells = groups[g]
        lines.append(f'T {{{g} — {len(cells)} cells}} 0 {y} 0 0 0.28 0.28 {{}}')
        y += 60
        cols, sx, sy = 4, 320, 240
        for j, c in enumerate(cells): 
            col, row = j % cols, j // cols
            x, cy = col * sx + 80, y + row * sy
            typ, name, pos = c['type'], c['name'], layouts[c['type']]
            lines.append(f'C {{cells/{typ}.sym}} {x} {cy} 0 0 {{name={name}}}')
            for pin in pin_cfg[typ]: 
                px, dy, _ = pos[pin]
                ay, ax = cy + dy, x + px
                if pin.lower() == 'vdd': 
                    net = 'VDD'
                elif pin.lower() in ('gnd', 'vss'): 
                    net = 'VSS'
                else: 
                    if pin not in c['ports']: 
                        raise RuntimeError(f'{name}:{pin} missing in DHIR')
                    net = _net(c['ports'][pin], output_alias)
                label_id = _emit_named_stub(lines, ax, ay, px, dy, net, label_id, coord_labels)
            inst_count += 1
        y += ((len(cells) + cols - 1) // cols) * sy + 100
    (xdir / f'{project}_core.sch').write_text('\n'.join(lines) + '\n')

    # Exact standard-cell SPICE reference; power pins are made explicit.
    ports = [p['name'] for p in dhir['ports']] + ['VDD', 'VSS']
    sp = [
        '* bio2rtl v1 trusted semantic core — generated from Physical DHIR', 
        '* TR-1um standard-cell core only; POR/pads are not included.', 
        f'.subckt {project}_core ' + ' '.join(ports), 
    ]
    bad = []
    for c in dhir['cells']: 
        typ, name = c['type'], c['name']
        nets = []
        expected = {p: _net(n, output_alias) for p, n in c['ports'].items()}
        for pin in pin_cfg[typ]: 
            if pin.lower() == 'vdd': 
                n = 'VDD'
            elif pin.lower() in ('gnd', 'vss'): 
                n = 'VSS'
            else: 
                n = _net(c['ports'][pin], output_alias)
            nets.append(n)
        expected.update({p: 'VDD' for p in pin_cfg[typ] if p.lower() == 'vdd'})
        expected.update({p: 'VSS' for p in pin_cfg[typ] if p.lower() in ('gnd', 'vss')})
        if set(expected) != set(pin_cfg[typ]): 
            bad.append({'instance': name, 'expected': sorted(expected), 'pin_order': pin_cfg[typ]})
        sp.append('X' + name + ' ' + ' '.join(nets) + ' ' + typ)
    sp += [f'.ends {project}_core', '']
    (sdir / f'{project}_core.spice').write_text('\n'.join(sp))

    used = sorted({c['type'] for c in dhir['cells']})
    helper = '''#!/usr/bin/env python3
from pathlib import Path
import os
pdk_root=Path(os.path.expanduser(os.environ.get("PDK_ROOT", "~/pdk")))
pdk_name=os.environ.get("PDK", "TR-1um")
root=pdk_root if (pdk_root/"libs.tech").is_dir() else pdk_root/pdk_name
base=Path(__file__).resolve().parent
used=%r
out=base/"tr1um_local_models.inc"
lines=[f'.include "{root}/libs.tech/spice/models/models_IP62_mos_v2.lib"']
for c in used:
    lines.append(f'.include "{root}/STDLIB/LogicCells/extracted/{c}.extracted"')
out.write_text("\\n".join(lines)+"\\n")
print(out)
''' % used
    (sdir / 'prepare_local_models.py').write_text(helper)
    os.chmod(sdir / 'prepare_local_models.py', 0o755)

    hist = Counter(c['type'] for c in dhir['cells'])
    audit = {
        'version': VERSION, 
        'status': 'PASS' if not bad and inst_count == len(dhir['cells']) else 'FAIL', 
        'instances': inst_count, 
        'cell_histogram': dict(sorted(hist.items())), 
        'components': {g: len(groups[g]) for g in order}, 
        'standard_cell_symbol_count': len(layouts), 
        'connectivity_failures': bad, 
        'explicit_lab_pins': label_id-1, 
        'label_coordinates': len(coord_labels), 
        'power_binding': 'VDD/VSS explicit on every standard-cell instance', 
        'output_aliases': output_alias, 
        'support_binding_status': 'see ../../support_binding.json', 
    }
    (build / 'xschem_export_audit.json').write_text(json.dumps(audit, indent = 2) + '\n')
    if audit['status'] != 'PASS': 
        raise RuntimeError(audit)
    return audit


if __name__ == '__main__': 
    import sys
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    print(json.dumps(export(root), indent = 2))
