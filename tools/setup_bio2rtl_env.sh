#!/usr/bin/env bash
set -euo pipefail

# Prepare the standard bio2rtl environment inside the ISHI-kai
# OpenSUSI-TR10 WSL image (ubuntu2204_ishi-kai_EDA).
# This environment is for normal bio2rtl development/use, not only demos.

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BIO_SIM=${BIO_SIM:-"$ROOT/../bio-sim"}

run_root() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    else
        sudo "$@"
    fi
}

if [ -r /proc/sys/kernel/osrelease ] && ! grep -qi microsoft /proc/sys/kernel/osrelease; then
    echo "NOTE: the standard bio2rtl environment is the ISHI-kai OpenSUSI-TR10 WSL image."
fi

run_root apt-get update
run_root apt-get install -y git python3-venv python3-pip binutils-riscv64-linux-gnu

cd "$ROOT"

# A .venv copied from Windows has Scripts/python.exe instead of bin/python.
# Recreate it when it is not a usable Linux virtual environment.
if [ ! -x .venv/bin/python ]; then
    if [ -e .venv ]; then
        echo "Recreating incompatible or incomplete .venv for Linux/WSL."
        rm -rf .venv
    fi
    python3 -m venv .venv
fi

VENV_PY="$ROOT/.venv/bin/python"
"$VENV_PY" -m pip install -U pip
"$VENV_PY" -m pip install -e . ziglang

if [ ! -f "$BIO_SIM/sw/build.zig" ]; then
    if [ -e "$BIO_SIM" ]; then
        echo "ERROR: $BIO_SIM exists but is not a baochip/bio-sim checkout." >&2
        exit 1
    fi
    git clone https://github.com/baochip/bio-sim.git "$BIO_SIM"
fi

command -v riscv64-linux-gnu-objdump >/dev/null
"$VENV_PY" - <<'PY'
import ziglang
print("ziglang: PASS")
PY

echo ""
echo "bio2rtl environment: PASS"
echo "Activate later with: source $ROOT/.venv/bin/activate"
echo "bio-sim: $BIO_SIM"
"$VENV_PY" -m bio2rtl --version
echo "Quick demo: $VENV_PY tools/run_bio_sim_example.py $BIO_SIM set_after_3_rises"
