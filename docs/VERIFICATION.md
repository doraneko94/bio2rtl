# Verification status

This document records the v1.2.1 release regression for the files included in this repository.

## Bundled example builds

The five bundled `.dis` examples complete configuration checking, semantic compilation, TR-1um mapping, Xschem generation, and the final project verifier.

| Example | Cells | DFFR | Mapped cell area |
|---|---:|---:|---:|
| `set_after_3_rises` | 16 | 3 | 51,870.36 µm² |
| `clock_divider_by4_75pct` | 11 | 3 | 40,351.96 µm² |
| `serial_pattern_1101` | 29 | 7 | 95,521.34 µm² |
| `clock_divider_by4` | 12 | 3 | 42,862.22 µm² |
| `i2c_gpio_2bit` | 122 | 21 | 422,625.12 µm² |

All five final CLI manifests report `bio2rtl_version = 1.2.1`.

## Mapped-signal regression

`tools/test_example_signals.py` evaluates the generated standard-cell netlists for the four simple examples. The observed post-edge output sequences match the expected sequences:

- `set_after_3_rises`: `0 0 1 1 1 1 1 1`
- `clock_divider_by4`: `1 0 0 1 1 0 0 1`
- `clock_divider_by4_75pct`: `0 1 1 1 0 1 1 1`
- `serial_pattern_1101`: `1 1 0 1 1 1 1 1`

The output pin in these four builds is inferred as a readback-free push-pull output and uses `gpio_o`.

## I2C regression

The `i2c_gpio_2bit` build has:

- 122 standard cells
- 21 DFFR plus the S00 non-DFF NOR-latch state
- 422,625.12 µm² mapped standard-cell area
- mapping mode `global_semantic_neutral_dag`
- legal-product canonical elimination: 4,968 states / 9,700 stored unique edges
- global semantic mapping proof: **98,056 truth checks, PASS**
- selected physical mapping proof: **98,056 truth checks, PASS**

The v1.2.0 documentation previously stated 163,592 checks. The mapper and proof implementation in the original v1.2.0 source snapshot is unchanged in v1.2.1; rerunning that implementation for the 122-cell snapshot produces `truth_checks = 98,056` in both final proof JSON files. v1.2.1 corrects the documentation to match the generated artifacts.

The inferred external I/O support is:

- SCL: `direct`
- SDA: `sda_io`
- GPIO0: `gpio_io`
- GPIO1: `gpio_io`

The directed I2C regression checks and passes:

- address ACK
- GPIO output write; final `(OE18, OE19, OUT18, OUT19) = (1, 1, 1, 0)`
- GPIO input/read path
- repeated START read sequence
- first read byte `0x01`
- second read byte `0xFF`
- master NACK after the second byte
- wrong address `0x86` returns NACK
- final global and selected mapping proofs

## Canonical routed Xschem core

For the 122-cell I2C core, `routed_xschem_export_audit.json` reports:

- 9 placement rows
- 10-unit coordinate grid
- 122 cells
- 2,087 wire segments
- internal `lab_pin` count = 0
- off-grid coordinates = 0
- zero-length wires = 0
- floating/ambiguous terminals = 0
- cell overlaps = 0
- foreign-net wire / unrelated-pin clearance violations = 0
- different-net parallel-wire spacing violations = 0
- internal vertical segments spanning two or more rows = 0

Six unused DFFR output pins are explicitly emitted as `noconn`; they are listed in the audit and are not floating or ambiguous routed terminals.

The release generator emits only the canonical `*_core.sch` / `*_core.sym` core view. `*_manual.sch`, `*_core_phy.sch`, and `*_core_phy.sym` are not generated.

## Core-only regression

`set_after_3_rises` is also built without `[physical_support]`.

The core-only build:

- completes with 16 cells / 3 DFFR / 51,870.36 µm²
- does not emit `*_fullchip.sch`
- does not emit `physical_support_inferred.json`
- does not require the physical-support recipe path for core mapping

The following support-enabled and core-only artifacts are byte-for-byte identical:

- `set_after_3_rises.structural.v`
- `physical_dhir_v18_stage7.json`
- `xschem/set_after_3_rises_core.sch`

This checks the v1.2.1 separation between TR-1um core mapping and optional physical support.

## Other release checks

The release snapshot also passes:

- `bio2rtl check` for all five bundled examples
- Python `compileall` for `bio2rtl`, `semantic_frontend`, and `tools`
- generic pass-manager optionality regression
- semantic dead-cone regression
- `gpio_o` recipe/hash, support-selection, no-readback, and drive/Hi-Z regression
- editable package build/install with version `1.2.1`

The same regression can be rerun with:

```bash
make release-regression
```

## External toolchain and analog checks

`make release-regression` starts from the `.dis` files included in this repository. Regenerating those files from the bundled C sources is a separate check because it depends on the external Baochip `bio-sim`, `ziglang`, and RISC-V `objdump` toolchain. In the documented OpenSUSI-TR10 WSL environment, run:

```bash
python tools/run_bio_sim_example.py ../bio-sim --all
python tools/test_example_signals.py
```

The generated electrical benchmark and physical-support schematics are digital/static release artifacts. Transistor-level timing, analog rise/fall behavior, POR startup, and bus-frequency margin require Xschem/ngspice with the installed TR-1um PDK and are not claimed by the digital release regression.
