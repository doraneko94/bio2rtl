# bio-sim source examples

Every public conversion example includes a Baochip bio-sim source at `examples/<name>/bio_sim/main.c`.

The standard demo environment is the ISHI-kai **OpenSUSI-TR10 WSL image** distributed from [`ishi-kai/OpenEDA-PDK_SetupScript`](https://github.com/ishi-kai/OpenEDA-PDK_SetupScript). From the bio2rtl repository root inside that image:

```bash
bash tools/setup_bio2rtl_env.sh
source .venv/bin/activate
python tools/run_bio_sim_example.py ../bio-sim set_after_3_rises
```

Run all bundled source examples:

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
```

The setup script prepares the reusable bio2rtl source-to-hardware environment, including `binutils-riscv64-linux-gnu`, `ziglang`, and `baochip/bio-sim` when they are not already available.

All five bundled sources have been verified end-to-end in the ISHI-kai OpenSUSI-TR10 WSL image: `main.c -> bio-sim -> .dis -> bio2rtl check/build -> Xschem output`.

- `i2c_gpio_2bit` contains the readable C implementation used for the I2C benchmark.
- The four small regression examples use compact C + explicit RISC-V inline assembly to keep BIO special-register operations and control flow stable across compiler optimization changes.
- Checked-in `.dis` files are frozen release-regression inputs. Source-generated `.dis` files are kept separate because toolchain updates may emit a different instruction sequence.

Use `--prepare-only` to copy the bundled sources into an existing bio-sim checkout without building them.
