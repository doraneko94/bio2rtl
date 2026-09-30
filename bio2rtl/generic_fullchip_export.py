from __future__ import annotations

from pathlib import Path
from collections import Counter
import hashlib, json, re, shutil

from .project_inputs import load_config
from .physical_interface import resolve_physical_support
from .xschem_export import _emit_named_stub, OUTPUT_PINS

VERSION = 'bio2rtl-generic-fullchip-export-v1'


def _sha(p: Path)->str: 
    return hashlib.sha256(p.read_bytes()).hexdigest()

def _load_json(p: Path): 
    return json.loads(p.read_text())

def _aliases(dhir: dict)->dict[str, str]: 
    return {str(a['rhs']): str(a['lhs']) for a in dhir.get('assignments', [])
            if isinstance(a, dict) and 'rhs' in a and 'lhs' in a}

def _net(n: str, aliases: dict[str, str])->str: 
    n = str(n).strip()
    if n in ("1'b0", "1'h0", "1'd0"): 
        return 'VSS'
    if n in ("1'b1", "1'h1", "1'd1"): 
        return 'VDD'
    return aliases.get(n, n)

def _is_const(n: str)->bool: 
    return str(n).strip() in ("1'b0", "1'h0", "1'd0", "1'b1", "1'h1", "1'd1")

def _support_symbol_pins(path: Path)->dict[str, tuple[float, float, str]]: 
    out = {}
    pat = re.compile(r'^B 5 ([-+0-9.eE]+) ([-+0-9.eE]+) ([-+0-9.eE]+) ([-+0-9.eE]+) \{name=([^ }]+) dir=([^ }]+)', re.M)
    for m in pat.finditer(path.read_text()): 
        x1, y1, x2, y2 = map(float, m.group(1, 2, 3, 4))
        out[m.group(5)] = ((x1+x2)/2, (y1+y2)/2, m.group(6))
    if not out: 
        raise RuntimeError(f'no pins parsed from support symbol {path}')
    return out

def _copy_recipes(root: Path, xdir: Path, lib: dict, recipe_ids: set[str])->dict[str, dict]: 
    dst = xdir/'support'
    dst.mkdir(parents = True, exist_ok = True)
    used = {}
    for rid in sorted(recipe_ids): 
        if rid not in lib['recipes']: 
            raise RuntimeError(f'unknown physical support recipe {rid}')
        r = dict(lib['recipes'][rid])
        sp = root/'technology'/r['sch']
        yp = root/'technology'/r['sym']
        if not sp.exists() or not yp.exists(): 
            raise RuntimeError(f'missing support recipe files for {rid}')
        if _sha(sp)!=r['sha256']['sch'] or _sha(yp)!=r['sha256']['sym']: 
            raise RuntimeError(f'support recipe SHA mismatch {rid}')
        # Keep original basenames because Xschem resolves symbol -> same-basename schematic.
        shutil.copy2(sp, dst/sp.name)
        shutil.copy2(yp, dst/yp.name)
        r['copied_sch'] = sp.name
        r['copied_sym'] = yp.name
        r['symbol_pins'] = _support_symbol_pins(dst/yp.name)
        used[rid] = r
    return used

def _resolve_physical(root: Path, cfg: dict, dhir: dict)->dict: 
    ps = resolve_physical_support(root, write_report = True)
    lib = _load_json(root/'technology/support_recipes_v1.json')
    if ps.get('technology')!=lib.get('technology'): 
        raise RuntimeError(f"physical support technology {ps.get('technology')} != recipe library {lib.get('technology')}")
    pads = ps.get('pads', [])
    if not isinstance(pads, list) or not pads: 
        raise RuntimeError('physical_support.pads must be nonempty')
    names = [str(p.get('name')) for p in pads]
    if len(names)!=len(set(names)): 
        raise RuntimeError('duplicate physical pad names')
    recipe_ids = set()
    reset = ps.get('reset')
    if reset: 
        recipe_ids.add(str(reset['recipe']))
    for p in pads: 
        if p.get('recipe'): 
            recipe_ids.add(str(p['recipe']))
    return {'cfg': ps, 'pads': pads, 'reset': reset, 'library': lib, 'recipe_ids': recipe_ids}

def _core_net_inventory(dhir: dict, aliases: dict)->tuple[set[str], set[str]]: 
    produced = set()
    consumed = set()
    for c in dhir['cells']: 
        for pin, n in c.get('ports', {}).items(): 
            nn = _net(n, aliases)
            if pin in OUTPUT_PINS: 
                produced.add(nn)
            else: 
                consumed.add(nn)
    return produced, consumed

def _core_ports(dhir: dict, physical: dict, recipes: dict, aliases: dict)->list[dict]: 
    # Start with actual structural module ports.
    dirs = {str(p['name']): str(p['direction']) for p in dhir.get('ports', [])}
    produced, consumed = _core_net_inventory(dhir, aliases)
    required = set(dirs)
    ps = physical['cfg']
    reset = physical['reset']
    if reset: 
        r = recipes[str(reset['recipe'])]
        if r['kind']!='por': 
            raise RuntimeError('physical_support.reset recipe must be kind=por')
        core_net = str(reset['core_net'])
        required.add(core_net)
        # POR output drives a core input.
        dirs.setdefault(core_net, 'input')
    for p in physical['pads']: 
        kind = str(p['kind'])
        if kind.startswith('direct_'): 
            cn = str(p['core_net'])
            required.add(cn)
            want = {'direct_input': 'input', 'direct_output': 'output', 'direct_inout': 'inout'}.get(kind)
            if want is None: 
                raise RuntimeError(f'unsupported direct pad kind {kind}')
            if cn in dirs and dirs[cn]!=want: 
                raise RuntimeError(f'pad {p["name"]} expects core {cn} {want}, got {dirs[cn]}')
            dirs.setdefault(cn, want)
            continue
        rid = str(p.get('recipe', ''))
        if not rid: 
            raise RuntimeError(f'pad {p["name"]} kind={kind} requires recipe')
        r = recipes[rid]
        if r['kind']!=kind: 
            raise RuntimeError(f'pad {p["name"]} kind={kind} does not match recipe {rid} kind={r["kind"]}')
        bindings = p.get('bindings', {})
        ext_dir = str(p.get('external_direction', 'inout'))
        for rp, role in r['ports'].items(): 
            if role.startswith('power_') or role == 'pad': 
                continue
            if rp not in bindings: 
                # An output-only use of a bidirectional physical recipe does not
                # consume the recipe's pad-readback output.  Keep that terminal
                # electrically isolated inside the support instance; it is not a
                # digital-core port.
                if role == 'core_output' and ext_dir == 'out': 
                    continue
                raise RuntimeError(f'pad {p["name"]}: missing binding for recipe port {rp}')
            cn = str(bindings[rp])
            # Constants are physical ties, not digital-core ports.
            if _is_const(cn): 
                if role!='core_input': 
                    raise RuntimeError(f'pad {p["name"]}:{rp} cannot bind {role} to constant {cn}')
                continue
            required.add(cn)
            # Recipe core_output means recipe drives the core -> core input.
            want = 'input' if role == 'core_output' else 'output' if role == 'core_input' else None
            if want is None: 
                raise RuntimeError(f'unsupported recipe role {role} in {rid}:{rp}')
            if cn in dirs and dirs[cn]!=want: 
                raise RuntimeError(f'pad {p["name"]}:{rp} expects core {cn} {want}, got {dirs[cn]}')
            dirs.setdefault(cn, want)
    # Extra output taps must be real produced physical nets; extra inputs must be consumed nets.
    for n in required: 
        if n in {p['name'] for p in dhir.get('ports', [])}: 
            continue
        if dirs[n] == 'output' and n not in produced: 
            raise RuntimeError(f'physical support requests nonexistent core output/tap {n}')
        if dirs[n] == 'input' and n not in consumed: 
            raise RuntimeError(f'physical support requests nonexistent core input {n}')
    # Stable order: structural ports first, then requested internal taps alphabetically.
    base = [str(p['name']) for p in dhir.get('ports', [])]
    extra = sorted(required-set(base))
    return [{'name': n, 'direction': dirs[n]} for n in base+extra]

def _top_net_map(physical: dict)->dict[str, str]: 
    # Normally top net == core pin. Direct pads rename the top net to the external pad.
    m = {}
    for p in physical['pads']: 
        if str(p['kind']).startswith('direct_'): 
            m[str(p['core_net'])] = str(p['name'])
    return m

def _emit_instance_stubs(lines: list[str], sympos: dict[str, tuple[float, float, str]], x: float, y: float, netmap: dict[str, str], prefix: str, start: int)->int: 
    coord = {}
    lid = start
    for pin, (px, py, _d) in sympos.items(): 
        if pin not in netmap: 
            raise RuntimeError(f'missing net binding for symbol pin {pin}')
        lid = _emit_named_stub(lines, int(x+px), int(y+py), int(px), int(py), str(netmap[pin]), lid, coord)
    return lid

def _fullchip_symbol(project: str, pads: list[dict])->tuple[str, dict[str, tuple[float, float, str]]]: 
    pinrows = []
    for p in pads: 
        kind = str(p['kind'])
        d = str(p.get('external_direction') or ('in' if kind == 'direct_input' else 'out' if kind == 'direct_output' else 'inout'))
        if d not in ('in', 'out', 'inout'): 
            raise RuntimeError(f'bad external pin direction {d!r} for {p.get("name")}')
        pinrows.append((str(p['name']), d))
    ins = [n for n, d in pinrows if d == 'in']
    outs = [n for n, d in pinrows if d == 'out']
    ios = [n for n, d in pinrows if d == 'inout']
    left = ins+ios
    right = outs
    h = max(160, 40*max(len(left), len(right), 1)+80)
    top = -h//2
    bottom = h//2
    pos = {}
    for i, n in enumerate(left): 
        pos[n] = (-120, -40*(len(left)-1)/2+i*40, 'in' if n in ins else 'inout')
    for i, n in enumerate(right): 
        pos[n] = (120, -40*(len(right)-1)/2+i*40, 'out')
    pos['VDD'] = (0, top-30, 'inout')
    pos['VSS'] = (0, bottom+30, 'inout')
    pins = ['VDD', 'VSS']+[n for n, _ in pinrows]
    lines = ['v {xschem version=3.4.8RC file_version=1.3}', 'G {}', f'K {{type=subcircuit\nformat="@name @pinlist {project}_fullchip"\ntemplate="name=x1"\n}}', 'V {}', 'S {}', 'F {}', 'E {}', 
           f'P 4 5 -100 {top} 100 {top} 100 {bottom} -100 {bottom} -100 {top} {{}}', f'T {{{project}}} -85 {top+15} 0 0 0.18 0.18 {{}}']
    for i, n in enumerate(pins, 1): 
        x, y, d = pos[n]
        if x<0: 
            lines.append(f'L 4 -120 {y:g} -100 {y:g} {{}}')
        elif x>0: 
            lines.append(f'L 4 100 {y:g} 120 {y:g} {{}}')
        elif y<top: 
            lines.append(f'L 7 0 {y:g} 0 {top} {{}}')
        else: 
            lines.append(f'L 7 0 {bottom} 0 {y:g} {{}}')
        lines.append(f'B 5 {x-2.5:g} {y-2.5:g} {x+2.5:g} {y+2.5:g} {{name={n} dir={d} pinnumber={i} sim_pinnumber={i}}}')
    return '\n'.join(lines)+'\n', pos

def export(root: Path)->dict: 
    root = Path(root)
    _, cfg = load_config(root)
    build = root/'build'
    xdir = build/'xschem'
    xdir.mkdir(parents = True, exist_ok = True)
    dhir = _load_json(build/'physical_dhir_v18_stage7.json')
    aliases = _aliases(dhir)
    project = str(dhir.get('module', cfg.get('name', 'bio2rtl')))
    physical = _resolve_physical(root, cfg, dhir)
    recipes = _copy_recipes(root, xdir, physical['library'], physical['recipe_ids'])
    ports = _core_ports(dhir, physical, recipes, aliases)
    # The routing-aware canonical core is the single physical core view.
    # Do not generate a second physical-core fallback.  A full-chip build is valid
    # only when the canonical core symbol exposes exactly the support contract
    # required by the resolved physical interface; otherwise fail closed.
    canonical = xdir/f'{project}_core.sym'
    canonical_names = {p['name'] for p in ports} | {'VDD', 'VSS'}
    parsed = _support_symbol_pins(canonical) if canonical.exists() else {}
    if set(parsed) != canonical_names:
        missing = sorted(canonical_names - set(parsed))
        extra = sorted(set(parsed) - canonical_names)
        raise RuntimeError(
            f'canonical core pin contract does not match full-chip support: '
            f'missing={missing}, extra={extra}; refusing to generate a duplicate physical-core view'
        )
    core_instance_sym = f'{project}_core.sym'
    core_pos = parsed
    topmap = _top_net_map(physical)
    def tn(n): 
        return topmap.get(str(n), str(n))
    lines = ['v {xschem version=3.4.8RC file_version=1.3}', 'G {}', 'K {}', 'V {}', 'S {}', 'F {}', 'E {}', 
           'T {bio2rtl generic full-chip — TOML-driven physical support} -560 -360 0 0 0.42 0.42 {}']
    # External ports.
    ext = [('VDD', 'inout'), ('VSS', 'inout')]
    for p in physical['pads']: 
        kind = str(p['kind'])
        d = str(p.get('external_direction') or ('in' if kind == 'direct_input' else 'out' if kind == 'direct_output' else 'inout'))
        if d not in ('in', 'out', 'inout'): 
            raise RuntimeError(f'bad external pin direction {d!r} for {p.get("name")}')
        ext.append((str(p['name']), d))
    for i, (n, d) in enumerate(ext): 
        sym = 'devices/ipin.sym' if d == 'in' else 'devices/opin.sym' if d == 'out' else 'devices/iopin.sym'
        lines.append(f'C {{{sym}}} {-680 if d!="out" else 760} {-260+i*45} 0 0 {{name=tp{i+1} lab={n}}}')
    # Core instance and named stubs.
    cx, cy = 0, 0
    lines.append(f'C {{{core_instance_sym}}} {cx} {cy} 0 0 {{name=xcore}}')
    core_bind = {p['name']: tn(p['name']) for p in ports}
    core_bind.update(VDD = 'VDD', VSS = 'VSS')
    lid = _emit_instance_stubs(lines, core_pos, cx, cy, core_bind, 'tc', 1)
    # POR/reset recipe.
    instances = []
    if physical['reset']: 
        rr = physical['reset']
        rid = str(rr['recipe'])
        r = recipes[rid]
        sx, sy = -360, -180
        lines.append(f'C {{support/{r["copied_sym"]}}} {sx} {sy} 0 0 {{name=xreset}}')
        nm = {}
        for rp, role in r['ports'].items(): 
            if role == 'power_vdd': 
                nm[rp] = 'VDD'
            elif role == 'power_vss': 
                nm[rp] = 'VSS'
            elif role == 'core_output': 
                nm[rp] = tn(str(rr['core_net']))
            else: 
                raise RuntimeError(f'unsupported reset recipe role {role}')
        lid = _emit_instance_stubs(lines, r['symbol_pins'], sx, sy, nm, 'tr', lid)
        instances.append({'name': 'xreset', 'recipe': rid})
    # Pad recipes. Direct pads have no instance.
    sy = -180
    for idx, p in enumerate(physical['pads']): 
        if str(p['kind']).startswith('direct_'): 
            continue
        rid = str(p['recipe'])
        r = recipes[rid]
        sx = 420
        py = sy+idx*230
        lines.append(f'C {{support/{r["copied_sym"]}}} {sx} {py} 0 0 {{name=xpad_{idx}}}')
        nm = {}
        bindings = p.get('bindings', {})
        for rp, role in r['ports'].items(): 
            if role == 'power_vdd': 
                nm[rp] = 'VDD'
            elif role == 'power_vss': 
                nm[rp] = 'VSS'
            elif role == 'pad': 
                nm[rp] = str(p['name'])
            elif role in ('core_input', 'core_output'): 
                if rp in bindings: 
                    nm[rp] = tn(_net(str(bindings[rp]), {}))
                elif role == 'core_output' and str(p.get('external_direction', 'inout')) == 'out': 
                    # Unused readback from an output-only pad recipe.  Give the
                    # physical terminal a unique local net; never expose it as a
                    # core/user signal.
                    nm[rp] = f'NC_{p["name"]}_{rp}'
                else: 
                    raise RuntimeError(f'pad {p["name"]}: missing binding for recipe port {rp}')
            else: 
                raise RuntimeError(f'unsupported recipe role {role}')
        lid = _emit_instance_stubs(lines, r['symbol_pins'], sx, py, nm, 'ts', lid)
        instances.append({'name': f'xpad_{idx}', 'pad': p['name'], 'recipe': rid})
    (xdir/f'{project}_fullchip.sch').write_text('\n'.join(lines)+'\n')
    fsym, fpos = _fullchip_symbol(project, physical['pads'])
    (xdir/f'{project}_fullchip.sym').write_text(fsym)
    # Benchmark canvas with DUT only and labels on every exposed pin.
    bl = ['v {xschem version=3.4.8RC file_version=1.3}', 'G {}', 'K {}', 'V {}', 'S {}', 'F {}', 'E {}', 'T {bio2rtl generic benchmark canvas} -400 -260 0 0 0.42 0.42 {}', f'C {{{project}_fullchip.sym}} 0 0 0 0 {{name=xdut}}']
    for i, (n, (x, y, _d)) in enumerate(fpos.items(), 1): 
        bl.append(f'C {{devices/lab_pin.sym}} {x:g} {y:g} 0 0 {{name=bp{i} sig_type=std_logic lab={n}}}')
    (xdir/f'{project}_benchmark_template.sch').write_text('\n'.join(bl)+'\n')
    (xdir/'xschemrc.bio2rtl').write_text('''# bio2rtl generic Xschem project rc
# PDK_ROOT normally points to the directory containing TR-1um (default: $HOME/pdk).
# For backward compatibility, PDK_ROOT may also point directly to the TR-1um directory.
if {![info exists env(PDK_ROOT)] || $env(PDK_ROOT) eq {}} { set env(PDK_ROOT) "$env(HOME)/pdk" }
if {![info exists env(PDK)] || $env(PDK) eq {}} { set env(PDK) "TR-1um" }
set tr1base "$env(PDK_ROOT)/$env(PDK)"
if {[file exists "$env(PDK_ROOT)/libs.tech/xschem/xschemrc"]} { set tr1base "$env(PDK_ROOT)" }
set tr1rc "$tr1base/libs.tech/xschem/xschemrc"
if {![file exists $tr1rc]} { puts stderr "BIO2RTL_FATAL: TR-1um xschemrc not found: $tr1rc"; exit 91 }
source $tr1rc
if {![info exists env(BIO2RTL_XSCHEM_DIR)]} { set env(BIO2RTL_XSCHEM_DIR) [pwd] }
append XSCHEM_LIBRARY_PATH ":$env(BIO2RTL_XSCHEM_DIR)"
append XSCHEM_LIBRARY_PATH ":$env(BIO2RTL_XSCHEM_DIR)/support"
set netlist_type spice
''')
    area_table = _load_json(root/'technology/tr1um_cell_area.json')['cell_area_um2']
    area = sum(float(area_table[c['type']]) for c in dhir['cells'])
    audit = {'version': VERSION, 'status': 'PASS', 'project': project, 'core_cells': len(dhir['cells']), 'core_dffr': Counter(c['type'] for c in dhir['cells']).get('DFFR', 0), 'core_area_um2': area, 
           'core_ports': ports, 'external_ports': [n for n, _ in ext], 'support_instances': instances, 'support_recipes': {k: {'kind': v['kind'], 'sch': v['copied_sch'], 'sym': v['copied_sym'], 'sha256': v['sha256']} for k, v in recipes.items()}, 
           'fullchip_lab_pins': lid-1, 'toml_driven': True, 'canonical_routed_core_used': True, 'core_instance_symbol': core_instance_sym, 'hardcoded_protocol_names_in_exporter': False}
    (build/'generic_fullchip_export_audit.json').write_text(json.dumps(audit, indent = 2, sort_keys = True)+'\n')
    binding = {
      'version': 'bio2rtl-generic-support-binding-v1', 'status': 'PASS', 'technology': physical['cfg']['technology'], 
      'semantic_core': {'status': 'GENERATED', 'stdcell_count': len(dhir['cells']), 'physical_dffr': Counter(c['type'] for c in dhir['cells']).get('DFFR', 0), 'cell_body_area_um2': area}, 
      'reset': physical['reset'], 'pads': physical['pads'], 'support_recipes': audit['support_recipes'], 
      'fullchip_xschem_status': 'GENERATED_TOML_DRIVEN', 'fullchip_validation_status': 'NOT_YET_ELECTRICALLY_VALIDATED'
    }
    (root/'support_binding.json').write_text(json.dumps(binding, indent = 2, sort_keys = True)+'\n')
    return audit

if __name__ == '__main__': 
    import sys
    r = Path(sys.argv[1] if len(sys.argv)>1 else Path(__file__).resolve().parents[1])
    print(json.dumps(export(r), indent = 2, sort_keys = True))
