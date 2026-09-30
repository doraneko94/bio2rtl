from __future__ import annotations
from pathlib import Path
import os, re
try: 
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib


def config_path(root: Path) -> Path: 
    root = Path(root).resolve()
    env = os.environ.get('BIO2RTL_WORKSPACE_CONFIG')
    if env: 
        p = Path(env).expanduser()
        if not p.is_absolute(): 
            p = root/p
        p = p.resolve()
        if not p.is_file(): 
            raise FileNotFoundError(f'BIO2RTL_WORKSPACE_CONFIG not found: {p}')
        return p
    p = root/'input/bio2rtl.toml'
    if not p.is_file(): 
        raise FileNotFoundError(f'project config not found: {p}')
    return p


SUPPORTED_SCHEMA = "bio2rtl-project-v1.1"
SUPPORTED_CLOCK_MODE = "io_edges"
SUPPORTED_TECHNOLOGY = "TR-1um"
SUPPORTED_IO_SUPPORTS = {"auto", "direct", "sda_io", "gpio_io", "gpio_o"}


def _validate_config(cfg: dict, path: Path) -> dict: 
    if cfg.get('schema') != SUPPORTED_SCHEMA: 
        raise ValueError(f'{path}: schema must be {SUPPORTED_SCHEMA!r}')

    # Generic Ver.1 deliberately exposes only configuration fields that affect
    # production behavior.  Removed/deprecated tables are rejected rather than
    # silently accepted.
    allowed_top = {'schema', 'program', 'name', 'io', 'clock', 'physical_support'}
    extra_top = sorted(set(cfg) - allowed_top)
    if extra_top: 
        raise ValueError(f'{path}: unsupported top-level config keys: {extra_top}')

    name = cfg.get('name')
    if not isinstance(name, str) or not name.strip(): 
        raise ValueError(f'{path}: name must be a non-empty string')
    program = cfg.get('program')
    if program is not None and (not isinstance(program, str) or not program.strip()): 
        raise ValueError(f'{path}: program must be a non-empty string when present')

    ios = cfg.get('io', [])
    if not isinstance(ios, list) or not ios: 
        raise ValueError(f'{path}: at least one [[io]] entry is required')
    io_names = []
    io_gpios = []
    for i, row in enumerate(ios): 
        if not isinstance(row, dict) or not {'name', 'gpio'} <= set(row) or (set(row) - {'name', 'gpio', 'support'}): 
            raise ValueError(f'{path}: [[io]] entry {i} must contain name/gpio and may contain support')
        name = str(row['name'])
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name): 
            raise ValueError(f'{path}: [[io]] name {name!r} must match [A-Za-z_][A-Za-z0-9_]*')
        if name in io_names: 
            raise ValueError(f'{path}: duplicate [[io]] name {name!r}')
        io_names.append(name)
        try: 
            gpio = int(row['gpio'])
        except Exception as e: 
            raise ValueError(f'{path}: [[io]] {name!r} gpio must be an integer') from e
        if gpio < 0 or gpio > 31: 
            raise ValueError(f'{path}: [[io]] {name!r} GPIO{gpio} is outside the supported BIO GPIO range 0..31')
        if gpio in io_gpios: 
            raise ValueError(f'{path}: BIO GPIO{gpio} is assigned to more than one [[io]]')
        io_gpios.append(gpio)
        support = str(row.get('support', 'auto'))
        if support not in SUPPORTED_IO_SUPPORTS: 
            raise ValueError(f'{path}: [[io]] {name!r} support must be one of {sorted(SUPPORTED_IO_SUPPORTS)}, got {support!r}')

    clock = cfg.get('clock')
    if not isinstance(clock, dict): 
        raise ValueError(f'{path}: [clock] is required')
    extra_clock = sorted(set(clock) - {'mode', 'source'})
    if extra_clock: 
        raise ValueError(f'{path}: unsupported [clock] keys: {extra_clock}')
    if clock.get('mode') != SUPPORTED_CLOCK_MODE: 
        raise ValueError(f'{path}: [clock].mode currently supports only {SUPPORTED_CLOCK_MODE!r}')
    source = str(clock.get('source', ''))
    if not source: 
        raise ValueError(f'{path}: [clock].source is required')
    if source not in io_names: 
        raise ValueError(f'{path}: [clock].source {source!r} is not declared by [[io]]')

    ps = cfg.get('physical_support')
    if ps is not None: 
        if not isinstance(ps, dict): 
            raise ValueError(f'{path}: [physical_support] must be a table')
        extra_ps = sorted(set(ps)-{'technology'})
        if extra_ps: 
            raise ValueError(f'{path}: unsupported [physical_support] keys: {extra_ps}')
        if ps.get('technology') != SUPPORTED_TECHNOLOGY: 
            raise ValueError(f'{path}: [physical_support].technology currently supports only {SUPPORTED_TECHNOLOGY!r}')
    return cfg


def load_config_file(path: Path) -> dict: 
    p = Path(path).expanduser().resolve()
    if not p.is_file(): 
        raise FileNotFoundError(f'project config not found: {p}')
    cfg = tomllib.loads(p.read_text(encoding = 'utf-8'))
    return _validate_config(cfg, p)


def load_config(root: Path) -> tuple[Path, dict]: 
    p = config_path(root)
    return p, load_config_file(p)


def dis_path(root: Path) -> Path: 
    root = Path(root).resolve()
    env = os.environ.get('BIO2RTL_WORKSPACE_DIS')
    if env: 
        p = Path(env).expanduser()
        if not p.is_absolute(): 
            p = root/p
        p = p.resolve()
        if not p.is_file(): 
            raise FileNotFoundError(f'BIO2RTL_WORKSPACE_DIS not found: {p}')
        return p
    cfgp, cfg = load_config(root)
    program = cfg.get('program')
    if program: 
        p = (cfgp.parent/str(program)).resolve()
        if p.is_file(): 
            return p
    candidates = sorted((root/'input').glob('*.dis'))
    if len(candidates) == 1: 
        return candidates[0].resolve()
    if program: 
        raise FileNotFoundError(f'config program={program!r} not found beside {cfgp}; .dis candidates={candidates}')
    raise FileNotFoundError(f'cannot resolve BIO .dis; expected config program or exactly one input/*.dis, got {candidates}')
