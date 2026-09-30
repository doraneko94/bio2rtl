from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from .project_inputs import load_config_file, SUPPORTED_TECHNOLOGY

__version__ = "1.2.1"


def _sha256(path: Path) -> str: 
    h = hashlib.sha256()
    with path.open("rb") as f: 
        for chunk in iter(lambda: f.read(1024 * 1024), b""): 
            h.update(chunk)
    return h.hexdigest()


def _engine_root() -> Path: 
    root = Path(__file__).resolve().parents[1]
    required = [
        root / "run_compiler.py", 
        root / "run_semantic_frontend.py", 
        root / "semantic_frontend", 
        root / "technology", 
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing: 
        raise RuntimeError(
            "bio2rtl engine assets are missing. Use the complete bio2rtl Ver.1 source tree. "
            "Missing: " + ", ".join(missing)
        )
    return root


def _copy_engine(src: Path, dst: Path) -> None: 
    def ignore(directory: str, names: list[str]): 
        ignored = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "build", "input"}
        ignored.update(n for n in names if n.startswith(".bio2rtl-work-"))
        return ignored
    shutil.copytree(src, dst, ignore = ignore)


def _overlay_engine_for_reuse(src: Path, dst: Path) -> None: 
    def ignore(directory: str, names: list[str]): 
        ignored = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", "build", "input"}
        ignored.update(n for n in names if n.startswith(".bio2rtl-work-"))
        return ignored
    shutil.copytree(src, dst, dirs_exist_ok = True, ignore = ignore)


def _copy_tree_replace(src: Path, dst: Path) -> None: 
    if dst.exists(): 
        shutil.rmtree(dst)
    dst.parent.mkdir(parents = True, exist_ok = True)
    shutil.copytree(src, dst)


def _resolve_config(dis: Path, explicit: Path | None) -> Path: 
    if explicit is not None: 
        return explicit.expanduser().resolve()
    beside = dis.parent / "bio2rtl.toml"
    if beside.is_file(): 
        return beside.resolve()
    return Path("bio2rtl.toml").resolve()


def _validate_pair(dis: Path, config: Path) -> dict: 
    if not dis.is_file(): 
        raise ValueError(f"input .dis not found: {dis}")
    if dis.suffix.lower() != ".dis": 
        raise ValueError(f"input must be a .dis file: {dis}")
    cfg = load_config_file(config)
    declared = cfg.get("program")
    if declared and Path(str(declared)).name != dis.name: 
        raise ValueError(
            f"config program={declared!r} does not match input {dis.name!r}; "
            "update bio2rtl.toml or pass the matching .dis file"
        )
    return cfg


def _safe_project_name(stem: str) -> str: 
    s = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_")
    if not s: 
        s = "bio2rtl_project"
    if s[0].isdigit(): 
        s = "p_" + s
    return s


def _parse_io(spec: str) -> tuple[str, int]: 
    if "=" not in spec: 
        raise argparse.ArgumentTypeError("--io must be NAME=GPIO, e.g. SDA=17 or SDA=GPIO17")
    name, raw = spec.split("=", 1)
    name = name.strip()
    raw = raw.strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name): 
        raise argparse.ArgumentTypeError(f"invalid pin name {name!r}")
    if raw.upper().startswith("GPIO"): 
        raw = raw[4:]
    try: 
        gpio = int(raw, 10)
    except ValueError as e: 
        raise argparse.ArgumentTypeError(f"invalid BIO GPIO in {spec!r}") from e
    if gpio < 0 or gpio > 31: 
        raise argparse.ArgumentTypeError(f"BIO GPIO{gpio} is outside supported range 0..31")
    return name, gpio


def _build_parser() -> argparse.ArgumentParser: 
    p = argparse.ArgumentParser(
        prog = "bio2rtl", 
        description = "Compile a BIO .dis program into proof-backed TR-1um RTL/Xschem outputs.", 
        epilog = (
            "First project setup: bio2rtl init PROGRAM.dis --io SCL=16 --io SDA=17 --clock SCL\n"
            "Then compile:        bio2rtl PROGRAM.dis\n"
            "Check configuration: bio2rtl check PROGRAM.dis\n"
            "Generate benchmark:   bio2rtl benchmark PROGRAM.dis"
        ), 
        formatter_class = argparse.RawDescriptionHelpFormatter, 
    )
    p.add_argument("dis", type = Path, help = "input BIO disassembly (.dis)")
    p.add_argument("-c", "--config", type = Path, default = None, 
                   help = "project TOML (default: bio2rtl.toml beside the .dis, then ./bio2rtl.toml)")
    p.add_argument("--build-dir", type = Path, default = Path("build"), 
                   help = "complete output/build directory (default: ./build)")
    p.add_argument("-o", "--output-dir", "--xschem-dir", dest = "output_dir", type = Path, default = None, 
                   help = "Xschem output directory (default: <build-dir>/xschem)")
    advanced = p.add_argument_group("advanced/reproducibility options")
    advanced.add_argument("--work-dir", type = Path, default = None, 
                          help = "persistent isolated compiler workspace")
    advanced.add_argument("--keep-work", action = "store_true", 
                          help = "keep an automatically-created temporary workspace")
    advanced.add_argument("--reuse-work", action = "store_true", 
                          help = "reuse an existing --work-dir build cache while refreshing compiler sources")
    advanced.add_argument("--no-verify", action = "store_true", 
                          help = "skip final structural/full-chip verification")
    advanced.add_argument("-v", "--verbose", action = "store_true", 
                          help = "stream compiler subprocess output")
    p.add_argument("--version", action = "version", version = f"bio2rtl {__version__}")
    return p


def _init_parser() -> argparse.ArgumentParser: 
    p = argparse.ArgumentParser(
        prog = "bio2rtl init", 
        description = "Create a minimal bio2rtl.toml from only user-known BIO pin assignments.", 
    )
    p.add_argument("dis", type = Path, help = "BIO .dis program")
    p.add_argument("--io", action = "append", type = _parse_io, required = True, metavar = "NAME=GPIO", 
                   help = "external pin mapping; repeat for each pin (e.g. SDA=17 or SDA=GPIO17)")
    p.add_argument("--clock", required = True, metavar = "NAME", 
                   help = "declared --io pin whose physical edges drive the BIO state transitions")
    p.add_argument("-n", "--name", default = None, help = "project name (default: derived from .dis filename)")
    p.add_argument("-c", "--config", type = Path, default = None, 
                   help = "TOML path to create (default: bio2rtl.toml beside the .dis)")
    p.add_argument("--core-only", action = "store_true", 
                   help = "omit TR-1um full-chip support; generate core/TB artifacts only")
    p.add_argument("--force", action = "store_true", help = "overwrite an existing config")
    return p


def _check_parser() -> argparse.ArgumentParser: 
    p = argparse.ArgumentParser(prog = "bio2rtl check", description = "Validate the user-facing project configuration without compiling.")
    p.add_argument("dis", type = Path, help = "BIO .dis program")
    p.add_argument("-c", "--config", type = Path, default = None, help = "project TOML")
    return p




def _benchmark_parser() -> argparse.ArgumentParser: 
    p = argparse.ArgumentParser(
        prog = "bio2rtl benchmark", 
        description = (
            "Generate the directed Xschem electrical benchmark for a compiled project. "
            "Ver.1 currently provides the I2C GPIO-expander benchmark fixture."
        ), 
    )
    p.add_argument("dis", type = Path, help = "BIO .dis program used for the build")
    p.add_argument("-c", "--config", type = Path, default = None, help = "project TOML")
    p.add_argument("--build-dir", type = Path, default = Path("build"), help = "compiled build directory (default: ./build)")
    return p


def _benchmark(argv: list[str]) -> int: 
    args = _benchmark_parser().parse_args(argv)
    dis = args.dis.expanduser().resolve()
    config = _resolve_config(dis, args.config)
    cfg = _validate_pair(dis, config)
    build_dir = args.build_dir.expanduser().resolve()
    xschem_dir = build_dir / "xschem"
    required = [
        build_dir / "physical_dhir_v18_stage7.json", 
        build_dir / "io_report.json", 
        xschem_dir / f"{cfg['name']}_fullchip.sym", 
    ]
    missing = [str(x) for x in required if not x.is_file()]
    if missing: 
        raise ValueError(
            "benchmark requires a completed full-chip build. Run `bio2rtl build PROGRAM.dis` first. "
            "Missing: " + ", ".join(missing)
        )
    engine = _engine_root()
    generator = engine / "tools" / "generate_i2c_electrical_benchmark.py"
    cp = subprocess.run(
        [
            sys.executable, str(generator), 
            "--root", str(engine), 
            "--build-dir", str(build_dir), 
            "--xschem-dir", str(xschem_dir), 
        ], 
        text = True, capture_output = True, 
    )
    if cp.returncode != 0: 
        raise RuntimeError((cp.stdout + "\n" + cp.stderr).strip())
    sch = xschem_dir / f"{cfg['name']}_i2c_benchmark.sch"
    expected = build_dir / "i2c_electrical_benchmark_expected.json"
    print(f"BENCHMARK PASS: {cfg['name']}")
    print(f"  schematic: {sch}")
    print(f"  expected:  {expected}")
    print("  stimulus: SCL PWL + SDA master open-drain NMOS gate PWL")
    print("  observe: V(SCL), V(SDA), V(GPIO0), V(GPIO1)")
    return 0


def _write_init(argv: list[str]) -> int: 
    args = _init_parser().parse_args(argv)
    dis = args.dis.expanduser().resolve()
    if not dis.is_file(): 
        raise ValueError(f"input .dis not found: {dis}")
    if dis.suffix.lower() != ".dis": 
        raise ValueError(f"input must be a .dis file: {dis}")
    rows = list(args.io)
    names = [n for n, _ in rows]
    gpios = [g for _, g in rows]
    if len(names) != len(set(names)): 
        raise ValueError("duplicate --io names are not allowed")
    if len(gpios) != len(set(gpios)): 
        raise ValueError("the same BIO GPIO cannot be assigned to more than one external pin")
    if args.clock not in names: 
        raise ValueError(f"--clock {args.clock!r} is not among declared --io names: {names}")
    project = args.name or _safe_project_name(dis.stem)
    config = args.config.expanduser().resolve() if args.config else dis.parent / "bio2rtl.toml"
    if config.exists() and not args.force: 
        raise ValueError(f"config already exists: {config} (use --force to overwrite)")
    q = json.dumps
    lines = [
        'schema = "bio2rtl-project-v1.1"', 
        f"program = {q(dis.name)}", 
        f"name = {q(project)}", 
        "", 
    ]
    for name, gpio in rows: 
        lines += ["[[io]]", f"name = {q(name)}", f"gpio = {gpio}", ""]
    lines += ["[clock]", 'mode = "io_edges"', f"source = {q(args.clock)}", ""]
    if not args.core_only: 
        lines += ["[physical_support]", f"technology = {q(SUPPORTED_TECHNOLOGY)}", ""]
    config.parent.mkdir(parents = True, exist_ok = True)
    config.write_text("\n".join(lines), encoding = "utf-8")
    # Validate exactly what we wrote.
    load_config_file(config)
    print(f"CREATED: {config}")
    for name, gpio in rows: 
        suffix = " [clock source]" if name == args.clock else ""
        print(f"  {name}: BIO GPIO{gpio}{suffix}")
    if args.core_only: 
        print("  physical support: core-only")
    else: 
        print(f"  physical support: {SUPPORTED_TECHNOLOGY}; power pins VDD/VSS are implicit")
    print(f"NEXT: bio2rtl {dis}")
    return 0


def _check(argv: list[str]) -> int: 
    args = _check_parser().parse_args(argv)
    dis = args.dis.expanduser().resolve()
    config = _resolve_config(dis, args.config)
    cfg = _validate_pair(dis, config)
    print(f"CONFIG PASS: {config}")
    print(f"  project: {cfg['name']}")
    for row in cfg["io"]: 
        suffix = " [clock source]" if row["name"] == cfg["clock"]["source"] else ""
        support = str(row.get('support', 'auto'))
        print(f"  {row['name']}: BIO GPIO{int(row['gpio'])}, support={support}{suffix}")
    ps = cfg.get("physical_support")
    if ps: 
        print(f"  technology: {ps['technology']} (current Ver.1 supported value; VDD/VSS implicit)")
    else: 
        print("  physical support: none (core-only)")
    return 0


def _read_json(path: Path) -> dict: 
    try: 
        return json.loads(path.read_text(encoding = "utf-8"))
    except Exception: 
        return {}


def _print_result_summary(build_dir: Path, output_dir: Path, cfg: dict) -> None: 
    project = str(cfg["name"])
    mapper = _read_json(build_dir / "stage7_declarative_mapper_report.json")
    cells = mapper.get("total_cells")
    dffr = mapper.get("dffr")
    area = mapper.get("area_um2")
    parts = []
    if cells is not None: 
        parts.append(f"{cells} cells")
    if dffr is not None: 
        parts.append(f"{dffr} DFFR")
    if area is not None: 
        parts.append(f"{float(area):,.2f} um^2")
    print(f"PASS: {project}" + (" — " + ", ".join(parts) if parts else ""))

    io_report = _read_json(build_dir / "io_report.json")
    pads = io_report.get("io", []) if isinstance(io_report, dict) else []
    if pads: 
        labels = {
            "direct_input": "input", 
            "open_drain": "open-drain", 
            "bidirectional_gpio": "bidirectional", 
            "bidirectional_gpio_no_readback": "bidirectional output/no-readback", 
            "push_pull_output": "push-pull output", 
            "push_pull_output_readback": "push-pull output/readback", 
            "push_pull_constant_output": "constant push-pull output", 
        }
        print("Inferred external I/O:")
        for p in pads: 
            support = p.get('support')
            suffix = f"; support={support}" if support else ""
            print(f"  {p['name']}: BIO GPIO{p['bio_gpio']} -> {labels.get(p['electrical_kind'], p['electrical_kind'])}{suffix}")
        if io_report.get('power_pins'): 
            print("  VDD/VSS: fixed TR-1um power pins")

    print(f"Build:   {build_dir}")
    print(f"Xschem:  {output_dir}")
    for suffix in ("_core.sch", "_tb.sym", "_fullchip.sch"): 
        fp = output_dir / f"{project}{suffix}"
        if fp.exists(): 
            print(f"READY:   {fp}")


def _build(argv: list[str]) -> int: 
    args = _build_parser().parse_args(argv)
    dis = args.dis.expanduser().resolve()
    config = _resolve_config(dis, args.config)
    cfg = _validate_pair(dis, config)
    build_dir = args.build_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve() if args.output_dir else build_dir / "xschem"

    engine = _engine_root()
    if args.reuse_work and args.work_dir is None: 
        raise ValueError("--reuse-work requires --work-dir")
    auto_tmp = args.work_dir is None
    tmp_parent: Path | None = None
    if auto_tmp: 
        tmp_parent = Path(tempfile.mkdtemp(prefix = "bio2rtl-v1-"))
        work = tmp_parent / "project"
    else: 
        work = args.work_dir.expanduser().resolve()
        if work.exists() and not args.reuse_work: 
            shutil.rmtree(work)

    try: 
        if args.reuse_work and work.exists(): 
            _overlay_engine_for_reuse(engine, work)
        else: 
            _copy_engine(engine, work)
        inp = work / "input"
        inp.mkdir(parents = True, exist_ok = True)
        workspace_dis = inp / dis.name
        workspace_cfg = inp / "bio2rtl.toml"
        shutil.copy2(dis, workspace_dis)
        shutil.copy2(config, workspace_cfg)

        env = os.environ.copy()
        env["BIO2RTL_CLI_INPUT_DIS"] = str(dis)
        env["BIO2RTL_CLI_CONFIG"] = str(config)
        env["BIO2RTL_WORKSPACE_DIS"] = str(workspace_dis)
        env["BIO2RTL_WORKSPACE_CONFIG"] = str(workspace_cfg)

        log_path = work / "build" / "bio2rtl_cli.log"
        log_path.parent.mkdir(parents = True, exist_ok = True)

        def run_stage(label: str, command: list[str]) -> None: 
            if not args.verbose: 
                print(label, flush = True)
            if args.verbose: 
                subprocess.run(command, cwd = work, env = env, check = True)
                return
            cp = subprocess.run(command, cwd = work, env = env, text = True, capture_output = True)
            with log_path.open("a", encoding = "utf-8") as f: 
                f.write("$ " + " ".join(command) + "\n")
                f.write(cp.stdout)
                f.write(cp.stderr)
                f.write("\n")
            if cp.returncode != 0: 
                tail = (cp.stdout + "\n" + cp.stderr)[-12000:]
                raise RuntimeError(f"compiler stage failed\n{tail}\nFull log: {log_path}")

        run_stage("[1/2] Compiling semantic model, architecture and physical mapping...", [sys.executable, "run_compiler.py"])
        if not args.no_verify: 
            verifier = "verify_generic_fullchip.py" if isinstance(cfg.get("physical_support"), dict) else "verify_generic_core.py"
            run_stage("[2/2] Verifying generated artifacts...", [sys.executable, verifier])
        else: 
            print("[2/2] Verification skipped by --no-verify")

        generated_build = work / "build"
        generated_xschem = generated_build / "xschem"
        if not generated_xschem.is_dir(): 
            raise RuntimeError("compiler completed without build/xschem")

        _copy_tree_replace(generated_build, build_dir)
        canonical_xschem = build_dir / "xschem"
        if output_dir != canonical_xschem: 
            _copy_tree_replace(canonical_xschem, output_dir)

        manifest = {
            "bio2rtl_version": __version__, 
            "project": cfg["name"], 
            "status": "PASS", 
            "input_dis": str(dis), 
            "input_dis_sha256": _sha256(dis), 
            "config": str(config), 
            "config_sha256": _sha256(config), 
            "build_dir": str(build_dir), 
            "xschem_dir": str(output_dir), 
            "verified": not args.no_verify, 
            "user_toml_contains_internal_nets": False, 
        }
        for dst in {canonical_xschem, output_dir}: 
            dst.mkdir(parents = True, exist_ok = True)
            (dst / "BIO2RTL_CLI_MANIFEST.json").write_text(
                json.dumps(manifest, indent = 2, sort_keys = True) + "\n", encoding = "utf-8"
            )
        _print_result_summary(build_dir, output_dir, cfg)
        return 0
    finally: 
        if auto_tmp and tmp_parent is not None: 
            if args.keep_work: 
                print(f"WORK: {work}")
            else: 
                shutil.rmtree(tmp_parent, ignore_errors = True)


def main(argv: list[str] | None = None) -> int: 
    args = list(sys.argv[1:] if argv is None else argv)
    try: 
        if args and args[0] == "init": 
            return _write_init(args[1:])
        if args and args[0] == "check": 
            return _check(args[1:])
        if args and args[0] == "benchmark": 
            return _benchmark(args[1:])
        if args and args[0] == "build": 
            args = args[1:]
        return _build(args)
    except (ValueError, FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as e: 
        print(f"bio2rtl: ERROR: {e}", file = sys.stderr)
        return 2


if __name__ == "__main__": 
    raise SystemExit(main())
