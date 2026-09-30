"""Routing-aware Xschem exporter for generic TR-1um standard-cell designs.

The router keeps cells in horizontal rows, uses horizontal local tracks, sends
multi-row vertical trunks outside the cell array, snaps all geometry to grid10,
and audits connectivity/clearance before writing the canonical core schematic.
"""

from __future__ import annotations

import json
import math
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

import networkx as nx

from .xschem_export import OUTPUT_PINS, _net
VERSION = 'bio2rtl-routing-aware-core-xschem-export-v3'
GRID = 10
WIRE_SPACING = 10
CELL_PITCH = 700
BASE_ROW_PITCH = 1400
X0 = 520
Y0 = 400
SYMBOL_PREFIX = 'TR-1um_5_stdcell'

def _load(path: Path): 
    return json.loads(path.read_text())

def _snap(v: float) -> int: 
    return int(round(float(v) / GRID)) * GRID

def _outpins(typ: str, pins: dict) -> set[str]: 
    out = {p for p, a in pins.items() if a.get('dir') == 'out' or p in OUTPUT_PINS}
    if not out: 
        raise RuntimeError(f'no output pin known for {typ}')
    return out

def _placement(dhir: dict, geom: dict) -> tuple[dict[int, tuple[int, int, int, int]], dict]: 
    cells = dhir['cells']
    n = len(cells)
    producers = {}
    pin_out = {t: _outpins(t, geom[t]['pins']) for t in geom}
    for i, c in enumerate(cells): 
        for p, net in c.get('ports', {}).items(): 
            if p in pin_out[c['type']]: 
                producers[_net(str(net))] = i
    g = nx.Graph()
    g.add_nodes_from(range(n))
    for j, c in enumerate(cells): 
        for p, net in c.get('ports', {}).items(): 
            if p in pin_out[c['type']] or p.lower() in ('vdd', 'gnd', 'vss'): 
                continue
            i = producers.get(_net(str(net)))
            if i is not None and i != j: 
                if g.has_edge(i, j): 
                    g[i][j]['weight'] += 1
                else: 
                    g.add_edge(i, j, weight = 1)
    try: 
        order = list(nx.utils.reverse_cuthill_mckee_ordering(g))
    except Exception: 
        order = sorted(g.nodes(), key = lambda i: (-g.degree(i), str(cells[i]['name'])))
    seen = set(order)
    order += [i for i in range(n) if i not in seen]
    target_per_row = max(4, int(math.ceil(1.2 * math.sqrt(max(1, n)))))
    rows = max(1, int(math.ceil(n / target_per_row)))
    counts = [n // rows + (1 if r < n % rows else 0) for r in range(rows)]
    row_members = []
    k = 0
    for cnt in counts: 
        row_members.append(order[k:k + cnt])
        k += cnt
    row_of = {i: r for r, rr in enumerate(row_members) for i in rr}
    rank = {i: j for r, rr in enumerate(row_members) for j, i in enumerate(rr)}
    for r, rr in enumerate(row_members): 

        def key(i): 
            neigh = [rank[j] for j in g.neighbors(i) if row_of.get(j) in (r - 1, r, r + 1)]
            b = sum(neigh) / len(neigh) if neigh else rank[i]
            return (b, str(cells[i]['name']))
        row_members[r] = sorted(rr, key = key)
    pos = {}
    for r, rr in enumerate(row_members): 
        for o, i in enumerate(rr): 
            pos[i] = (X0 + o * CELL_PITCH + r * 230, Y0 + r * BASE_ROW_PITCH, r, o)
    audit = {
        'placement_strategy': 'connectivity RCM + balanced horizontal rows + deterministic barycentric order', 
        'rows': rows, 
        'row_counts': [len(row) for row in row_members], 
        'target_cells_per_row': target_per_row, 
        'row_members': [[cells[i]['name'] for i in row] for row in row_members], 
    }
    return (pos, audit)

def _terminal_inventory(dhir: dict, geom: dict, pos: dict, aliases: dict): 
    cells = dhir['cells']
    nets = defaultdict(list)
    allpins = []
    pin_out = {t: _outpins(t, geom[t]['pins']) for t in geom}
    for i, c in enumerate(cells): 
        x, y, row, order = pos[i]
        typ = c['type']
        for pin, a in geom[typ]['pins'].items(): 
            ax = _snap(x + a['x'])
            ay = _snap(y + a['y'])
            if pin.lower() == 'vdd': 
                net = 'VDD'
            elif pin.lower() in ('gnd', 'vss'): 
                net = 'VSS'
            else: 
                if pin not in c.get('ports', {}): 
                    raise RuntimeError(f"{c['name']}:{pin} missing in DHIR")
                net = _net(str(c['ports'][pin]), aliases)
            ep = {
                'kind': 'cell', 'cell': i, 'instance': c['name'], 'pin': pin, 
                'net': net, 'x': ax, 'y': ay, 'row': row, 'type': typ, 
                'dir': a.get('dir', ''), 'is_output': pin in pin_out[typ], 
            }
            nets[net].append(ep)
            allpins.append(ep)
    for p in dhir.get('ports', []): 
        name = str(p['name'])
        eps = nets.get(name, [])
        if eps: 
            r = int(round(median([e['row'] for e in eps])))
        else: 
            r = 0
        ep = {'kind': 'port', 'name': name, 'net': name, 'row': r, 'direction': p['direction']}
        nets[name].append(ep)
    return (nets, allpins)

def _classify_and_tracks(nets: dict, row_count: int, left: int, right: int): 
    """Allocate one unique horizontal track per logical net per touched row.

    Every multi-row signal uses a unique outer vertical trunk.  This is more
    conservative than routing only >=2-row nets outside, but it makes the
    rule generic and guarantees that no long vertical signal traverses the
    cell array.
    """
    rows_for = {}
    touched = defaultdict(list)
    multi = []
    for net, eps in sorted(nets.items()): 
        if net in ('VDD', 'VSS'): 
            continue
        rows = sorted({e['row'] for e in eps})
        if not rows: 
            continue
        rows_for[net] = rows
        for r in rows: 
            touched[r].append(net)
        if len(rows) > 1: 
            multi.append(net)
    max_tracks = max([len(set(v)) for v in touched.values()] or [1])
    pitch = max(BASE_ROW_PITCH, _snap(700 + max_tracks * 20))
    row_y = [Y0 + r * pitch for r in range(row_count)]
    track = {}
    for r in range(row_count): 
        for k, net in enumerate(sorted(set(touched.get(r, [])))): 
            track[r, net] = _snap(row_y[r] - 220 - k * 20)
    trunk = {}
    lc = rc = 0
    for net in sorted(multi): 
        if lc <= rc: 
            trunk[net] = _snap(left - 320 - lc * 20)
            lc += 1
        else: 
            trunk[net] = _snap(right + 320 + rc * 20)
            rc += 1
    return (row_y, pitch, rows_for, track, trunk)

def _escape(ep: dict, cells: list[dict], geom: dict, pos: dict, lane_counts: dict) -> tuple[list[tuple[int, int, int, int]], tuple[int, int]]: 
    """Escape a signal pin completely outside its symbol before changing Y.

    Side pins first move horizontally.  Top/bottom pins first move vertically
    beyond the symbol, then horizontally.  Lanes are unique per cell side and
    20 units apart, so a Q escape cannot pass through QB (and vice versa).
    """
    if ep['kind'] == 'port': 
        return ([], (ep['x'], ep['y']))
    i = ep['cell']
    c = cells[i]
    x, y, _, _ = pos[i]
    g = geom[c['type']]
    a = g['pins'][ep['pin']]
    b = g['bbox']
    px, py = (ep['x'], ep['y'])
    lx = float(a['x'])
    ly = float(a['y'])
    side = 'right' if ep.get('is_output') else 'left'
    key = (i, side)
    k = lane_counts[key]
    lane_counts[key] += 1
    seg = []
    if side == 'left': 
        ex = _snap(x + float(b['xmin']) - 50 - k * 20)
        ey = py
        seg.append((px, py, ex, ey))
    elif side == 'right': 
        ex = _snap(x + float(b['xmax']) + 50 + k * 20)
        ey = py
        seg.append((px, py, ex, ey))
    elif side == 'top': 
        oy = _snap(y + float(b['ymin']) - 50 - k * 20)
        ex = _snap(x + float(b['xmin']) - 50 - k * 20)
        seg.extend([(px, py, px, oy), (px, oy, ex, oy)])
        ey = oy
    else: 
        oy = _snap(y + float(b['ymax']) + 50 + k * 20)
        ex = _snap(x + float(b['xmax']) + 50 + k * 20)
        seg.extend([(px, py, px, oy), (px, oy, ex, oy)])
        ey = oy
    return (seg, (ex, ey))

def _normseg(s): 
    x1, y1, x2, y2 = s
    if (x2, y2) < (x1, y1): 
        return (x2, y2, x1, y1)
    return s

def _add(seglist, net, s): 
    s = tuple(map(_snap, s))
    if s[0] == s[2] and s[1] == s[3]: 
        return
    z = _normseg(s)
    if not any((q['net'] == net and q['seg'] == z for q in seglist)): 
        seglist.append({'net': net, 'seg': z})

def _split_same_net_junctions(seglist): 
    """Split same-net orthogonal crossings into explicit Xschem junctions.

    Xschem must not be asked to infer connectivity from two wire interiors that
    merely cross.  For each geometrical H/V crossing of the *same* net, split
    any segment whose interior contains the crossing so at least one endpoint
    exists at the junction (in practice both are split when needed).  Foreign
    net interior crossings are deliberately left untouched.
    """
    bynet = defaultdict(list)
    for q in seglist: 
        bynet[q['net']].append(q['seg'])
    out = []
    for net, rs in sorted(bynet.items()): 
        cuts = [set() for _ in rs]
        for i, a in enumerate(rs): 
            ah = a[1] == a[3]
            for j in range(i + 1, len(rs)): 
                b = rs[j]
                bh = b[1] == b[3]
                if ah == bh: 
                    continue
                h, v = (a, b) if ah else (b, a)
                hx1, hx2 = sorted((h[0], h[2]))
                vy1, vy2 = sorted((v[1], v[3]))
                x = v[0]
                y = h[1]
                if hx1 <= x <= hx2 and vy1 <= y <= vy2: 
                    if _on_seg((x, y), a, strict = True): 
                        cuts[i].add((x, y))
                    if _on_seg((x, y), b, strict = True): 
                        cuts[j].add((x, y))
        for s0, cs in zip(rs, cuts): 
            if not cs: 
                _add(out, net, s0)
                continue
            x1, y1, x2, y2 = s0
            if y1 == y2: 
                pts = [(x1, y1), *sorted(cs), (x2, y2)]
                pts = sorted(set(pts), key = lambda q: q[0])
            else: 
                pts = [(x1, y1), *sorted(cs, key = lambda q: q[1]), (x2, y2)]
                pts = sorted(set(pts), key = lambda q: q[1])
            for a, b in zip(pts, pts[1:]): 
                _add(out, net, (a[0], a[1], b[0], b[1]))
    return out

def _xschem_segments_connect(a, b): 
    """Connectivity rule used for schematic audit.

    Collinear overlap connects.  Otherwise an endpoint must lie on the other
    segment; a pure interior H/V crossing is intentionally non-connecting.
    """
    ah = a[1] == a[3]
    bh = b[1] == b[3]
    if ah and bh: 
        return a[1] == b[1] and max(min(a[0], a[2]), min(b[0], b[2])) <= min(max(a[0], a[2]), max(b[0], b[2]))
    if not ah and (not bh): 
        return a[0] == b[0] and max(min(a[1], a[3]), min(b[1], b[3])) <= min(max(a[1], a[3]), max(b[1], b[3]))
    return _on_seg((a[0], a[1]), b) or _on_seg((a[2], a[3]), b) or _on_seg((b[0], b[1]), a) or _on_seg((b[2], b[3]), a)

def _route(dhir: dict, geom: dict, pos: dict, nets: dict, placement_audit: dict): 
    cells = dhir['cells']
    rows = placement_audit['rows']
    left0 = min((pos[i][0] + geom[c['type']]['bbox']['xmin'] for i, c in enumerate(cells)))
    right0 = max((pos[i][0] + geom[c['type']]['bbox']['xmax'] for i, c in enumerate(cells)))
    row_y, pitch, rows_for, track, trunk = _classify_and_tracks(nets, rows, left0, right0)
    for i, (x, _y, r, o) in list(pos.items()): 
        pos[i] = (x, row_y[r], r, o)
    aliases = {str(a['rhs']): str(a['lhs']) for a in dhir.get('assignments', []) if isinstance(a, dict) and 'rhs' in a and ('lhs' in a)}
    nets, allpins = _terminal_inventory(dhir, geom, pos, aliases)
    left = min((pos[i][0] + geom[c['type']]['bbox']['xmin'] for i, c in enumerate(cells)))
    right = max((pos[i][0] + geom[c['type']]['bbox']['xmax'] for i, c in enumerate(cells)))
    row_y, pitch, rows_for, track, trunk = _classify_and_tracks(nets, rows, left, right)
    min_outer = min([left - 320, *[x for x in trunk.values() if x < left]], default = left - 320)
    max_outer = max([right + 320, *[x for x in trunk.values() if x > right]], default = right + 320)
    for p in dhir.get('ports', []): 
        net = str(p['name'])
        ep = next((e for e in nets[net] if e['kind'] == 'port'))
        ep['x'] = _snap(min_outer - 140 if p['direction'] == 'input' else max_outer + 140)
        ep['y'] = track.get((ep['row'], net), _snap(row_y[ep['row']] - 220))
    segs = []
    lane_counts = defaultdict(int)
    noconn = []
    for net, eps in sorted(nets.items()): 
        if net in ('VDD', 'VSS'): 
            continue
        cell_eps = [e for e in eps if e['kind'] == 'cell']
        if len(eps) == 1 and cell_eps and cell_eps[0]['is_output']: 
            noconn.append(cell_eps[0])
            continue
        byrow = defaultdict(list)
        for ep in eps: 
            stubs, pt = _escape(ep, cells, geom, pos, lane_counts)
            for ss in stubs: 
                _add(segs, net, ss)
            byrow[ep['row']].append((ep, pt))
        hys = []
        for r, vals in sorted(byrow.items()): 
            hy = track[r, net]
            hys.append(hy)
            xs = []
            for ep, (ex, ey) in vals: 
                _add(segs, net, (ex, ey, ex, hy))
                xs.append(ex)
            if len(rows_for.get(net, [])) > 1: 
                xs.append(trunk[net])
            xs += [ep['x'] for ep, _ in vals if ep['kind'] == 'port']
            if len(xs) >= 2: 
                _add(segs, net, (min(xs), hy, max(xs), hy))
        if len(hys) > 1: 
            tx = trunk[net]
            _add(segs, net, (tx, min(hys), tx, max(hys)))
    power_ports = []
    for net in ('VDD', 'VSS'): 
        eps = [e for e in nets.get(net, []) if e['kind'] == 'cell']
        byrow = defaultdict(list)
        for e in eps: 
            byrow[e['row']].append(e)
        tx = _snap(left - 160 if net == 'VDD' else right + 160)
        hys = []
        for r, vals in sorted(byrow.items()): 
            hy = _snap(row_y[r] - 130 if net == 'VDD' else row_y[r] + 130)
            hys.append(hy)
            xs = [tx]
            for ep in vals: 
                _add(segs, net, (ep['x'], ep['y'], ep['x'], hy))
                xs.append(ep['x'])
            _add(segs, net, (min(xs), hy, max(xs), hy))
        if len(hys) > 1: 
            _add(segs, net, (tx, min(hys), tx, max(hys)))
        py = min(hys) if net == 'VDD' else max(hys)
        px = _snap(tx - 120 if net == 'VDD' else tx + 120)
        _add(segs, net, (px, py, tx, py))
        power_ports.append({'name': net, 'direction': 'input', 'x': px, 'y': py})
    segs = _split_same_net_junctions(segs)
    route_meta = {
        'row_pitch': pitch, 'array_left': left, 'array_right': right, 
        'long_trunks': trunk, 'classes': dict(rows_for), 
    }
    return nets, allpins, segs, noconn, power_ports, route_meta

def _point_seg_dist(px, py, s): 
    x1, y1, x2, y2 = s
    if y1 == y2: 
        cx = min(max(px, min(x1, x2)), max(x1, x2))
        return math.hypot(px - cx, py - y1)
    cy = min(max(py, min(y1, y2)), max(y1, y2))
    return math.hypot(px - x1, py - cy)

def _on_seg(p, s, strict = False): 
    x, y = p
    x1, y1, x2, y2 = s
    if x1 == x2: 
        ok = x == x1 and min(y1, y2) <= y <= max(y1, y2)
        if strict: 
            ok = ok and y not in (y1, y2)
        return ok
    ok = y == y1 and min(x1, x2) <= x <= max(x1, x2)
    if strict: 
        ok = ok and x not in (x1, x2)
    return ok

def _audit(dhir, geom, pos, nets, allpins, segs, noconn, power_ports, route_meta): 
    violations = []
    off = []
    zero = []
    for i, (x, y, r, o) in pos.items(): 
        if x % GRID or y % GRID: 
            off.append(('cell', dhir['cells'][i]['name'], x, y))
    for q in segs: 
        s = q['seg']
        if any((v % GRID for v in s)): 
            off.append(('wire', q['net'], s))
        if s[0] == s[2] and s[1] == s[3]: 
            zero.append((q['net'], s))
    nc = {(e['instance'], e['pin']) for e in noconn}
    floating = []
    for e in allpins: 
        if (e['instance'], e['pin']) in nc: 
            continue
        if not any((q['net'] == e['net'] and _on_seg((e['x'], e['y']), q['seg']) for q in segs)): 
            floating.append((e['instance'], e['pin'], e['net']))
    for net, eps in nets.items(): 
        for e in eps: 
            if e['kind'] == 'port' and (not any((q['net'] == net and _on_seg((e['x'], e['y']), q['seg']) for q in segs))): 
                floating.append(('PORT', e['name'], net))
    foreign = []
    mind = 1000000000.0
    for e in allpins: 
        for q in segs: 
            if q['net'] == e['net']: 
                continue
            d = _point_seg_dist(e['x'], e['y'], q['seg'])
            mind = min(mind, d)
            if d < WIRE_SPACING - 1e-09: 
                foreign.append((e['instance'], e['pin'], e['net'], q['net'], q['seg'], d))
    parallel = []
    endpoint_contacts = []
    for i, a in enumerate(segs): 
        sa = a['seg']
        ah = sa[1] == sa[3]
        for b in segs[i + 1:]: 
            if a['net'] == b['net']: 
                continue
            sb = b['seg']
            bh = sb[1] == sb[3]
            if ah == bh: 
                if ah: 
                    ov = max(min(sa[2], sb[2]) - max(sa[0], sb[0]), 0)
                    if ov > 0 and abs(sa[1] - sb[1]) < WIRE_SPACING: 
                        parallel.append((a['net'], b['net'], sa, sb))
                else: 
                    ov = max(min(sa[3], sb[3]) - max(sa[1], sb[1]), 0)
                    if ov > 0 and abs(sa[0] - sb[0]) < WIRE_SPACING: 
                        parallel.append((a['net'], b['net'], sa, sb))
            for p in ((sa[0], sa[1]), (sa[2], sa[3])): 
                if _on_seg(p, sb): 
                    endpoint_contacts.append((a['net'], b['net'], p, sb))
            for p in ((sb[0], sb[1]), (sb[2], sb[3])): 
                if _on_seg(p, sa): 
                    endpoint_contacts.append((b['net'], a['net'], p, sa))
    splits = []
    for net, eps in nets.items(): 
        rs = [q['seg'] for q in segs if q['net'] == net]
        terminals = [(e['x'], e['y']) for e in eps if not (e.get('kind') == 'cell' and (e['instance'], e['pin']) in nc)]
        if net in ('VDD', 'VSS'): 
            terminals += [(p['x'], p['y']) for p in power_ports if p['name'] == net]
        if not terminals: 
            continue
        gg = nx.Graph()
        gg.add_nodes_from(range(len(rs)))
        for i in range(len(rs)): 
            for j in range(i + 1, len(rs)): 
                if _xschem_segments_connect(rs[i], rs[j]): 
                    gg.add_edge(i, j)
        touched = []
        for t in terminals: 
            ids = [i for i, s in enumerate(rs) if _on_seg(t, s)]
            if not ids: 
                touched.append(None)
            else: 
                touched.append(next(iter(nx.node_connected_component(gg, ids[0]))) if len(rs) else None)
        vals = {x for x in touched if x is not None}
        if any((x is None for x in touched)) or len(vals) > 1: 
            splits.append((net, len(terminals), len(vals), touched.count(None)))
    overlaps = []
    for i, c in enumerate(dhir['cells']): 
        xi, yi, _, _ = pos[i]
        bi = geom[c['type']]['bbox']
        ai = (xi + bi['xmin'], yi + bi['ymin'], xi + bi['xmax'], yi + bi['ymax'])
        for j in range(i + 1, len(dhir['cells'])): 
            cj = dhir['cells'][j]
            xj, yj, _, _ = pos[j]
            bj = geom[cj['type']]['bbox']
            aj = (xj + bj['xmin'], yj + bj['ymin'], xj + bj['xmax'], yj + bj['ymax'])
            if max(ai[0], aj[0]) < min(ai[2], aj[2]) and max(ai[1], aj[1]) < min(ai[3], aj[3]): 
                overlaps.append((c['name'], cj['name']))
    long_internal = []
    pitch = route_meta['row_pitch']
    left, right = (route_meta['array_left'], route_meta['array_right'])
    for q in segs: 
        x1, y1, x2, y2 = q['seg']
        if x1 == x2 and left < x1 < right and (abs(y2 - y1) >= 2 * pitch): 
            long_internal.append((q['net'], q['seg']))
    status = 'PASS' if not (off or zero or floating or foreign or parallel or endpoint_contacts or splits or overlaps or long_internal) else 'FAIL'
    return {
        'version': 'bio2rtl-routed-core-audit-v1', 'status': status, 'grid': GRID, 
        'rows': len({r for _, _, r, _ in pos.values()}), 
        'row_counts': dict(Counter(r for _, _, r, _ in pos.values())), 
        'wire_segments': len(segs), 'lab_pin_count': 0, 'wire_lab_count': 0, 
        'off_grid_count': len(off), 'zero_length_wire_count': len(zero), 
        'floating_or_ambiguous_terminals': len(floating), 
        'foreign_net_wire_to_unrelated_pin_violation_count': len(foreign), 
        'foreign_net_wire_to_unrelated_pin_distance_min': None if mind == 1e9 else mind, 
        'different_net_parallel_wire_spacing_violation_count': len(parallel), 
        'endpoint_on_foreign_wire_contact_count': len(endpoint_contacts), 
        'split_net_count': len(splits), 'cell_overlap_count': len(overlaps), 
        'internal_vertical_segments_spanning_2plus_rows': len(long_internal), 
        'noconn_count': len(noconn),
        'noconn_pins': [
            {
                'instance': str(endpoint['instance']),
                'cell_type': str(endpoint['type']),
                'pin': str(endpoint['pin']),
                'net': str(endpoint['net']),
            }
            for endpoint in noconn
        ],
        'details': {
            'off_grid': off[:20], 'floating': floating[:20], 'foreign_pin': foreign[:20],
            'parallel': parallel[:20], 'endpoint_contacts': endpoint_contacts[:20],
            'splits': splits[:20], 'overlaps': overlaps[:20],
            'long_internal': long_internal[:20],
        },
    }

def _tb_symbol(ports: list[dict], project: str) -> str: 
    ins = [p for p in ports if p['direction'] == 'input']
    outs = [p for p in ports if p['direction'] == 'output']
    h = max(180, 20 * (max(len(ins) + 2, len(outs), 1) + 2))
    top = -h // 2
    bot = h // 2
    lines = [
        'v {xschem version=3.4.8RC file_version=1.3}', 'G {}', 
        f'K {{type=subcircuit\nformat="@name @pinlist {project}_core"\ntemplate="name=x1"\n}}', 
        'V {}', 'S {}', 'F {}', 'E {}', 
        f'P 4 5 -130 {top} 130 {top} 130 {bot} -130 {bot} -130 {top} {{}}', 
    ]
    left = [('VDD', 'in')] + [(p['name'], 'in') for p in ins] + [('VSS', 'in')]
    right = [(p['name'], 'out') for p in outs]
    pin_order = [p['name'] for p in ports] + ['VDD', 'VSS']
    loc = {}
    for i, (n, d) in enumerate(left): 
        loc[n] = (-150, top + 20 + i * 20, d)
    for i, (n, d) in enumerate(right): 
        loc[n] = (150, top + 20 + i * 20, d)
    dirs = {p['name']: p['direction'] for p in ports}
    dirs.update(VDD = 'in', VSS = 'in')
    for num, n in enumerate(pin_order, 1): 
        x, y, d = loc[n]
        direction = 'in' if dirs[n] == 'input' else 'out' if dirs[n] == 'output' else dirs[n]
        lines.append(
            f'B 5 {x - 2.5:g} {y - 2.5:g} {x + 2.5:g} {y + 2.5:g} '
            f'{{name={n} dir={direction} sim_pinnumber={num}}}'
        )
        lines.append(f'L 4 {(-150 if x < 0 else 130)} {y} {(-130 if x < 0 else 150)} {y} {{}}')
        lines.append(f'T {{{n}}} {(-125 if x < 0 else 125)} {y - 4} 0 {(0 if x < 0 else 1)} 0.2 0.2 {{}}')
    return '\n'.join(lines) + '\n'

def export(root: Path) -> dict: 
    root = Path(root).resolve()
    build = root / 'build'
    xdir = build / 'xschem'
    xdir.mkdir(parents = True, exist_ok = True)
    dhir = _load(build / 'physical_dhir_v18_stage7.json')
    project = str(dhir.get('module', 'bio2rtl_core'))
    geofile = root / 'technology/tr1um_xschem_symbol_geometry.json'
    allgeom = _load(geofile)['cells']
    used = {c['type'] for c in dhir['cells']}
    missing = sorted(used - set(allgeom))
    if missing: 
        raise RuntimeError(f'missing real TR-1um Xschem symbol geometry: {missing}')
    geom = {t: allgeom[t] for t in used}
    aliases = {str(a['rhs']): str(a['lhs']) for a in dhir.get('assignments', []) if isinstance(a, dict) and 'rhs' in a and ('lhs' in a)}
    pos, pa = _placement(dhir, geom)
    nets, allpins = _terminal_inventory(dhir, geom, pos, aliases)
    nets, allpins, segs, noconn, power_ports, rm = _route(dhir, geom, pos, nets, pa)
    audit = _audit(dhir, geom, pos, nets, allpins, segs, noconn, power_ports, rm)
    audit.update({'version': VERSION, 'cell_count': len(dhir['cells']), 'row_pitch': rm['row_pitch'], 'placement': pa})
    if audit['status'] != 'PASS': 
        (build / 'routed_xschem_export_audit.json').write_text(json.dumps(audit, indent = 2, sort_keys = True) + '\n')
        raise RuntimeError(json.dumps(audit, sort_keys = True))
    lines = [
        'v {xschem version=3.4.8RC file_version=1.3}', 'G {}', 'K {}', 'V {}', 
        'S {}', 'F {}', 'E {}', 
        'T {TR-1um routed core: generic row placement, M1-horizontal / M2-vertical intent} 300 120 0 0 0.3 0.3 {}', 
        'T {Topology explicit; grid10; wire/pin clearance >=10; no internal lab_pin remote connections} 300 160 0 0 0.2 0.2 {}', 
    ]
    for r in range(pa['rows']): 
        y = next((v[1] for v in pos.values() if v[2] == r))
        lines.append(f'T {{ROW {r}}} 220 {y - 90} 0 0 0.14 0.14 {{}}')
    for q in segs: 
        x1, y1, x2, y2 = q['seg']
        lines.append(f'N {x1} {y1} {x2} {y2} {{}}')
    for i, c in enumerate(dhir['cells']): 
        x, y, r, o = pos[i]
        lines.append(f"C {{{SYMBOL_PREFIX}/{c['type']}.sym}} {x} {y} 0 0 {{name=x{c['name']}}}")
    pn = 1
    for p in dhir.get('ports', []): 
        e = next((e for e in nets[str(p['name'])] if e['kind'] == 'port'))
        sym = 'devices/ipin.sym' if p['direction'] == 'input' else 'devices/opin.sym'
        lines.append(f"C {{{sym}}} {e['x']} {e['y']} 0 0 {{name=p{pn} lab={p['name']} sim_pinnumber={pn}}}")
        pn += 1
    for p in power_ports: 
        lines.append(f"C {{devices/ipin.sym}} {p['x']} {p['y']} 0 0 {{name=p{pn} lab={p['name']} sim_pinnumber={pn}}}")
        pn += 1
    for i, e in enumerate(noconn, 1): 
        lines.append(f"C {{devices/noconn.sym}} {e['x']} {e['y']} 0 0 {{name=NC{i}}}")
    text = '\n'.join(lines) + '\n'
    (xdir / f'{project}_core.sch').write_text(text)
    (xdir / f'{project}_core.sym').write_text(_tb_symbol(dhir['ports'], project))
    (xdir / f'{project}_tb.sym').write_text(_tb_symbol(dhir['ports'], project))
    # Earlier export stages may create simplified local cell symbols for their
    # own intermediate checks.  The canonical routed schematic uses the real
    # TR-1um_5_stdcell library, so do not leave an unused build/xschem/cells
    # directory in the final user-facing output.
    shutil.rmtree(xdir / 'cells', ignore_errors = True)
    guide = {
        'version': 'bio2rtl-route-guide-v1', 'grid': GRID, 
        'layer_intent': {'horizontal': 'M1', 'vertical': 'M2'}, 
        'cells': [
            {
                'instance': cell['name'], 'cell_type': cell['type'], 
                'x': pos[i][0], 'y': pos[i][1], 'row': pos[i][2], 'order': pos[i][3], 
            }
            for i, cell in enumerate(dhir['cells'])
        ], 
        'segments': [
            {
                'net': item['net'], 
                'x1': item['seg'][0], 'y1': item['seg'][1], 
                'x2': item['seg'][2], 'y2': item['seg'][3], 
                'orientation': 'H' if item['seg'][1] == item['seg'][3] else 'V', 
                'corridor': (
                    'outer'
                    if item['seg'][0] == item['seg'][2]
                    and (item['seg'][0] <= rm['array_left'] or item['seg'][0] >= rm['array_right'])
                    else 'local'
                ), 
            }
            for item in segs
        ], 
        'long_net_outer_trunks': rm['long_trunks'], 
        'audit_status': audit['status'], 
    }
    (build / f'{project}.route_guide.json').write_text(json.dumps(guide, indent = 2, sort_keys = True) + '\n')
    audit.update({
        'canonical_core_schematic': str((xdir / f'{project}_core.sch').relative_to(root)), 
        'core_symbol': str((xdir / f'{project}_core.sym').relative_to(root)), 
        'route_guide': str((build / f'{project}.route_guide.json').relative_to(root)), 
        'cell_histogram': dict(sorted(Counter(cell['type'] for cell in dhir['cells']).items())), 
    })
    (build / 'routed_xschem_export_audit.json').write_text(json.dumps(audit, indent = 2, sort_keys = True) + '\n')
    return audit
if __name__ == '__main__': 
    import sys
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    print(json.dumps(export(root), indent = 2, sort_keys = True))
