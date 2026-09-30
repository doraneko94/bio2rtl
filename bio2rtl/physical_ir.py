from __future__ import annotations
from pathlib import Path
import collections, json, re

_SIMPLE_NET = re.compile(r"[A-Za-z_]\w*")
_SIMPLE_RHS = re.compile(r"[A-Za-z_]\w*|1'b[01]")


def histogram(ir: dict) -> collections.Counter: 
    return collections.Counter(c['type'] for c in ir['cells'])


def validate(ir: dict) -> None: 
    if ir.get('version') != 'bio2rtl-physical-dhir-v1': 
        raise ValueError(f"unsupported Physical DHIR: {ir.get('version')}")
    names = set()
    for c in ir['cells']: 
        if c['name'] in names: 
            raise ValueError(f"duplicate cell name {c['name']}")
        names.add(c['name'])
        if not c.get('type') or not isinstance(c.get('ports'), dict): 
            raise ValueError(f"bad cell: {c}")
    for a in ir.get('assignments', []): 
        if not _SIMPLE_NET.fullmatch(a['lhs']): 
            raise ValueError(f"non-net assignment lhs: {a}")
        if not _SIMPLE_RHS.fullmatch(a['rhs']): 
            raise ValueError(f"non-structural assignment rhs: {a}")


def emit_structural_sv(ir: dict, path: Path | str) -> None: 
    validate(ir)
    path = Path(path)
    module = ir['module']
    ports = ir['ports']
    portnets = {p['name'] for p in ports}
    allnets = set()
    for c in ir['cells']: 
        for net in c['ports'].values(): 
            if _SIMPLE_NET.fullmatch(net) and net not in portnets: 
                allnets.add(net)
    lines = [ir.get('timescale', '`timescale 1ns/1ps'), '', f'module {module} (']
    portdecl = [f"{p['direction']} wire {p['name']}" for p in ports]
    lines += ['  '+',\n  '.join(portdecl), ');', '']
    for w in sorted(allnets): 
        lines.append(f'  wire {w};')
    lines.append('')
    for c in ir['cells']: 
        ps = ', '.join(f'.{p}({v})' for p, v in c['ports'].items())
        lines.append(f"  {c['type']} {c['name']} ({ps});")
    lines.append('')
    for a in ir.get('assignments', []): 
        lines.append(f"  assign {a['lhs']} = {a['rhs']};")
    lines += ['endmodule', '']
    path.write_text('\n'.join(lines))


def dump(ir: dict, path: Path | str) -> None: 
    validate(ir)
    Path(path).write_text(json.dumps(ir, indent = 2, sort_keys = True)+'\n')


def component_summary(ir: dict, cell_area: dict[str, float] | None = None) -> dict: 
    validate(ir)
    output_pins = {'Y', 'Q', 'QB'}
    driven = {}
    users = collections.defaultdict(set)
    groups = collections.defaultdict(list)
    for c in ir['cells']: 
        comp = c.get('component', 'unclassified')
        groups[comp].append(c)
        for pin, net in c['ports'].items(): 
            if not _SIMPLE_NET.fullmatch(net): 
                continue
            if pin in output_pins: 
                driven[net] = comp
            else: 
                users[net].add(comp)
    for a in ir.get('assignments', []): 
        users[a['rhs']].add('__top__')
    out = {}
    for comp, cells in sorted(groups.items()): 
        h = collections.Counter(c['type'] for c in cells)
        produced = {net for net, owner in driven.items() if owner == comp}
        inputs = sorted({net for c in cells for pin, net in c['ports'].items()
                       if pin not in output_pins and _SIMPLE_NET.fullmatch(net) and driven.get(net)!=comp})
        outputs = sorted(net for net in produced if any(u!=comp for u in users.get(net, set())))
        row = {'cell_count': len(cells), 'cell_histogram': dict(sorted(h.items())), 
             'interface_inputs': inputs, 'interface_outputs': outputs}
        if cell_area is not None: 
            row['area_um2'] = sum(float(cell_area[t])*n for t, n in h.items())
        out[comp] = row
    return out
