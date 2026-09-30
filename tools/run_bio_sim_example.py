#!/usr/bin/env python3
"""Build a bundled BIO source with bio-sim, then compile its .dis with bio2rtl."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = {
    "set_after_3_rises": "set_after_3_rises", 
    "clock_divider_by4_75pct": "clock_divider_by4_75pct", 
    "serial_pattern_1101": "serial_pattern_1101", 
    "clock_divider_by4": "clock_divider_by4", 
    "i2c_gpio_2bit": "i2c-gpio-hw", 
}


def run(cmd: list[str], cwd: Path) -> None: 
    print("+", " ".join(cmd), flush = True)
    subprocess.run(cmd, cwd = cwd, check = True)


def check_environment() -> None: 
    if os.name == "nt": 
        print(
            "NOTE: the standard demo environment is the ISHI-kai OpenSUSI-TR10 "
            "WSL image. Start it with `wsl -d ubuntu2204_ishi-kai_EDA` and run "
            "the demo there.", 
            file = sys.stderr, 
        )
    distro = os.environ.get("WSL_DISTRO_NAME")
    if distro and distro != "ubuntu2204_ishi-kai_EDA": 
        print(
            f"NOTE: WSL distribution is {distro!r}; the documented standard is "
            "'ubuntu2204_ishi-kai_EDA'.", 
            file = sys.stderr, 
        )
    if sys.version_info < (3, 10): 
        raise SystemExit("Python 3.10 or newer is required")
    if not (shutil.which("riscv64-linux-gnu-objdump") or shutil.which("riscv64-unknown-elf-objdump")): 
        raise SystemExit(
            "RISC-V objdump was not found. In the standard ISHI-kai WSL image, run:\n"
            "  sudo apt update\n"
            "  sudo apt install -y binutils-riscv64-linux-gnu"
        )


def prepare_source(bio_sim: Path, example: str) -> tuple[str, Path]: 
    module = EXAMPLES[example]
    source = ROOT / "examples" / example / "bio_sim" / "main.c"
    target = bio_sim / "sw" / module / "main.c"
    if not (bio_sim / "sw" / "build.zig").is_file(): 
        raise SystemExit(f"not a bio-sim checkout: {bio_sim}")
    target.parent.mkdir(parents = True, exist_ok = True)
    shutil.copy2(source, target)
    print(f"source: {source} -> {target}")
    return module, target


def build_one(bio_sim: Path, example: str, prepare_only: bool) -> None: 
    module, _ = prepare_source(bio_sim, example)
    if prepare_only: 
        return
    try: 
        import ziglang  # noqa: F401
    except ImportError as exc: 
        raise SystemExit(
            "ziglang is required. In the standard ISHI-kai WSL image, run "
            "`bash tools/setup_bio2rtl_env.sh`."
        ) from exc

    run([sys.executable, "-m", "ziglang", "build", f"-Dmodule={module}"], bio_sim / "sw")
    dis = bio_sim / "sw" / module / f"{module}.dis"
    if not dis.is_file(): 
        candidates = sorted((bio_sim / "sw" / module).glob("*.dis"))
        if len(candidates) != 1: 
            raise SystemExit(f"bio-sim build completed but .dis was not found under {dis.parent}")
        dis = candidates[0]

    config = ROOT / "examples" / example / "bio2rtl.toml"
    build_dir = ROOT / "build" / "bio_sim_demo" / example
    bio2rtl = [sys.executable, "-m", "bio2rtl"]
    run([*bio2rtl, "check", str(dis), "-c", str(config)], ROOT)
    run([*bio2rtl, "build", str(dis), "-c", str(config), "--build-dir", str(build_dir)], ROOT)
    print(f"PASS: {example}\n  dis:   {dis}\n  build: {build_dir}")


def main() -> int: 
    parser = argparse.ArgumentParser(description = __doc__)
    parser.add_argument("bio_sim", type = Path, help = "path to a baochip/bio-sim checkout")
    parser.add_argument("example", nargs = "?", choices = sorted(EXAMPLES), default = "set_after_3_rises")
    parser.add_argument("--all", action = "store_true", help = "build all bundled examples")
    parser.add_argument("--prepare-only", action = "store_true", help = "only copy main.c into bio-sim")
    args = parser.parse_args()
    if not args.prepare_only: 
        check_environment()
    bio_sim = args.bio_sim.expanduser().resolve()
    names = list(EXAMPLES) if args.all else [args.example]
    for name in names: 
        print(f"\n== {name} ==")
        build_one(bio_sim, name, args.prepare_only)
    return 0


if __name__ == "__main__": 
    raise SystemExit(main())
