# bio2rtl

日本語: [README_ja.md](README_ja.md)

Repository file guide: [FILE_GUIDE.md](FILE_GUIDE.md) / [日本語](FILE_GUIDE_ja.md)

bio2rtl is a compiler that **analyzes a Baochip BIO `.dis` program and generates a TR-1um standard-cell circuit with the same external I/O behavior**. The generated circuit does not contain the BIO CPU.

The inputs are a `.dis` file produced by Baochip's official `bio-sim` and a `bio2rtl.toml` project file. A normal build produces a TR-1um structural netlist and Xschem core. When `[physical_support]` is enabled, bio2rtl also connects POR and the required I/O support circuits and emits a full-chip schematic.

## Quick demo

The standard TR-1um development environment is the **OpenSUSI-TR10 WSL image** distributed through the ISHI-kai [`OpenEDA-PDK_SetupScript`](https://github.com/ishi-kai/OpenEDA-PDK_SetupScript) project.

### 1. Register the WSL image

Download the image described as **“Image for WSL version of OpenSUSI-TR10 (Tokai Rika)”** in the `OpenEDA-PDK_SetupScript` README, then register it from Windows PowerShell.

```powershell
wsl --import-in-place ubuntu2204_ishi-kai_EDA .\ubuntu2204_ishi-kai_EDA\ext4.vhdx
wsl -d ubuntu2204_ishi-kai_EDA
```

### 2. Clone bio2rtl

Run inside WSL:

```bash
git clone https://github.com/J-IMPACT/bio2rtl.git
cd bio2rtl
```

### 3. Prepare the environment

```bash
bash tools/setup_bio2rtl_env.sh
source .venv/bin/activate
```

`setup_bio2rtl_env.sh` creates a Python virtual environment and installs bio2rtl, RISC-V `objdump`, and `ziglang`. If Baochip's official `bio-sim` is not present at `../bio-sim`, the script clones it there.

### 4. Compile an example

```bash
python tools/run_bio_sim_example.py ../bio-sim set_after_3_rises
```

The command runs this flow:

```text
main.c → bio-sim → .dis → bio2rtl → TR-1um circuit
```

To run all five bundled examples from C source:

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
python tools/test_example_signals.py
```

## Bundled examples

| Example | Behavior | v1.2.1 result |
|---|---|---:|
| `set_after_3_rises` | Hold `OUT=1` after the third `TICK` rising edge | 16 cells |
| `clock_divider_by4_75pct` | Periodic output that is high for three of four states | 11 cells |
| `serial_pattern_1101` | Emit the serial pattern `1101` | 29 cells |
| `clock_divider_by4` | Divide-by-4 waveform with 50% duty | 12 cells |
| `i2c_gpio_2bit` | I2C-controlled 2-bit GPIO expander | 122 cells |

The BIO C source is under `examples/<name>/bio_sim/main.c`. The bundled `.dis` files are under `examples/<name>/`.

## Compile your own BIO program

### 1. Generate a `.dis` file with `bio-sim`

Place the BIO C source at `bio-sim/sw/<module>/main.c`.

```bash
cd ../bio-sim/sw
python -m ziglang build "-Dmodule=<module>"
```

For `my_module`, the output is normally:

```text
../bio-sim/sw/my_module/my_module.dis
```

Return to the bio2rtl repository:

```bash
cd ../../bio2rtl
```

### 2. Describe the external I/O in `bio2rtl.toml`

bio2rtl derives the internal behavior from the `.dis` file. The project TOML specifies the external pin-to-BIO-GPIO mapping, the external event source that advances the state machine, and whether physical support circuits are required.

This example uses BIO GPIO16 as `TICK` and GPIO18 as `OUT`:

```toml
schema = "bio2rtl-project-v1.1"
program = "my_module.dis"
name = "my_project"

[[io]]
name = "TICK"
gpio = 16
support = "direct"

[[io]]
name = "OUT"
gpio = 18
support = "gpio_o"

[clock]
mode = "io_edges"
source = "TICK"

[physical_support]
technology = "TR-1um"
```

| TOML field | Meaning |
|---|---|
| `program` | `.dis` file to compile |
| `name` | Project name |
| `[[io]].name` | External pin name |
| `[[io]].gpio` | BIO GPIO number |
| `[[io]].support` | `auto`, `direct`, `sda_io`, `gpio_io`, or `gpio_o`; default is `auto` |
| `[clock].mode` | Currently `io_edges` |
| `[clock].source` | External I/O event source that advances state |
| `[physical_support]` | Enables POR/I/O support and full-chip generation |

I/O direction is inferred from the `.dis` behavior. With `support=auto`, bio2rtl selects a support circuit that matches the inferred I/O role. An explicit support setting that conflicts with the inferred role causes the build to fail.

`[clock].source` must name the external I/O event that actually advances the BIO behavior. bio2rtl does not create a free-running hardware clock from CPU polling alone.

### 3. Generate the TOML with `bio2rtl init`

```bash
bio2rtl init <path-to-program.dis> --io TICK=16 --io OUT=18 --clock TICK -n my_project
```

Use `--core-only` when you only need the standard-cell core:

```bash
bio2rtl init <path-to-program.dis> --io TICK=16 --io OUT=18 --clock TICK -n my_project --core-only
```

See all options with:

```bash
bio2rtl init --help
```

### 4. Check and build

```bash
bio2rtl check <path-to-program.dis>
bio2rtl build <path-to-program.dis>
```

`check` validates the `.dis`, TOML, GPIO assignments, clock source, and support settings. `build` runs semantic analysis, circuit optimization, TR-1um mapping, verification, and netlist/Xschem generation.

If the TOML is not beside the `.dis` file, pass it explicitly:

```bash
bio2rtl build <path-to-program.dis> -c path/to/bio2rtl.toml
```

## Generated artifacts

Main build outputs:

| File | Content |
|---|---|
| `build/<project>.structural.v` | Structural Verilog mapped to TR-1um standard cells |
| `build/xschem/<project>_core.sch` | Routed canonical core schematic |
| `build/xschem/<project>_core.sym` | Core symbol |
| `build/xschem/<project>_tb.sym` | Testbench symbol |
| `build/GLOBAL_SEMANTIC_MAP_PROOF.json` | Semantic mapping proof |
| `build/SELECTED_PHYSICAL_MAP_PROOF.json` | Proof for the selected physical mapping |

When `[physical_support]` is enabled, the build also emits:

| File | Content |
|---|---|
| `build/xschem/<project>_fullchip.sch` | Core + POR + I/O support schematic |
| `build/xschem/<project>_fullchip.sym` | Full-chip symbol |
| `build/xschem/support/` | Support circuits used by the project |

v1.2.1 does not generate `*_manual.sch` or `*_core_phy.*`. The TR-1um standard-cell core has one canonical Xschem representation: `*_core.sch`.

## Core-only builds

Remove `[physical_support]` from the TOML to generate only the TR-1um standard-cell core.

Semantic analysis, TR-1um mapping, core schematic generation, and mapping proofs still run. POR, `sda_io`, `gpio_io`, `gpio_o`, and the full-chip schematic are not generated.

In v1.2.1, core mapping no longer depends on the physical-support recipe library. Given the same `.dis` and I/O configuration, enabling or disabling physical support does not change the canonical core logic.

## TR-1um physical support

Add the following section to connect POR and the required I/O support circuits:

```toml
[physical_support]
technology = "TR-1um"
```

The current support circuits are stored under `technology/tr1um_hand_support_v1/`.

| Circuit | Used for | Main implementation |
|---|---|---|
| `por.sch` | Power-on reset | RR: W=2.8 µm / L=60 µm ×8, CSIO: 110 × 110 µm ×4, INV_X2, BUF_X4 |
| `sda_io.sch` | Open-drain output with pin input | BUF_X1, BUF_X2, BUF_X4, NMOS: W=30 µm / L=1 µm / m=9 |
| `gpio_io.sch` | Push-pull output with readback | NAND2, AND2_X1, BUF_X1, BUF_X2, PMOS: W=30 µm / L=1 µm / m=27, NMOS: W=30 µm / L=1 µm / m=9 |
| `gpio_o.sch` | Push-pull output without readback | NAND2, AND2_X1, PMOS: W=30 µm / L=1 µm / m=27, NMOS: W=30 µm / L=1 µm / m=9 |

The I/O connections are:

| I/O role | Connection |
|---|---|
| Ordinary input | External pin directly to the core input |
| Open-drain | `PAD` to the external pin, `IN` to the core input, `LOW` to drive-low control |
| Push-pull / bidirectional with readback | `gpio_io`; `INPUT_VALUE` returns the pad state to the core |
| Push-pull without readback | `gpio_o`; `OUT/OUT_B` carry output data and `DIR/DIR_B` control output-enable |

`i2c_gpio_2bit` uses `direct` for SCL, `sda_io` for SDA, and `gpio_io` for GPIO0/1.

The current `fullchip` output covers the digital core, POR, and I/O support. ESD and pad-ring generation are outside the current scope.

## Compiler flow

bio2rtl analyzes the relation between external I/O events and internal state in the `.dis` program, then constructs dedicated state and combinational logic. The main stages are:

- **Feasible symbolic execution / event semantics**: extract executable branches and I/O events
- **Reachable-state reduction**: remove unreachable states
- **State quotienting / storage reduction**: merge externally equivalent states and determine required storage
- **Architecture recovery**: recover counters, shift registers, FSMs, latches, and GPIO data/direction state
- **Boolean minimization**: minimize logic over care/don't-care conditions
- **Global logic sharing**: share logic across outputs
- **Dead-cone elimination**: remove combinational logic that affects neither retained state nor external outputs
- **TR-1um mapping**: map the resulting logic to TR-1um standard cells
- **Semantic/physical proof**: check the correspondence between the semantic model and the selected mapped circuit

The compiler flow is not tied to I2C. The bundled examples cover edge counting, periodic waveforms, a serial pattern, and an I2C GPIO expander through the same compiler path.

## v1.2.1 scope

v1.2.1 targets Baochip BIO `.dis` programs and the TR-1um backend. The main target is a small reactive circuit whose state advances on external I/O edges.

The generic whole-equivalence proof targets the B20 range, where the reachable semantic state space is approximately `2^20 = 1,048,576` states or less. This is not a 20-flip-flop physical limit. Source register width or the generated storage count may exceed 20 bits when the reachable semantic state space remains within B20.

The build stops when it encounters an unsupported instruction, an I/O role that cannot be resolved uniquely, or a proof contradiction.

## Verification

The v1.2.1 release regression checks all five bundled examples through configuration validation, full build, mapping proofs, and Xschem audits. The four simple examples are simulated directly from the generated standard-cell netlists and compared with their expected output sequences. The I2C example runs directed transactions for ACK, GPIO write/read, consecutive reads, and wrong-address NACK, then checks the final mapping proofs.

Run the regression from the bundled `.dis` files with:

```bash
make release-regression
```

To regenerate the `.dis` files from the BIO C sources in the standard WSL environment:

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
python tools/test_example_signals.py
```

See [`docs/VERIFICATION.md`](docs/VERIFICATION.md) for the detailed checks.

## Intended use

A BIO I/O function can be developed and changed as a C program while its behavior is still evolving. Once the external behavior is fixed, bio2rtl can use the BIO program as the input specification for a dedicated TR-1um circuit, reducing the amount of separate RTL that must be written for that function.

## License

bio2rtl is licensed under the Apache License 2.0.

Using bio2rtl does not by itself apply Apache-2.0 to user-generated circuits. Third-party PDK/IP remains subject to its own license terms.
