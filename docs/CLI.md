# CLI reference

## Recommended TR-1um environment

For the TR-1um demo and physical follow-up, the documented standard environment is the ISHI-kai OpenSUSI-TR10 WSL image. See [`SETUP.md`](SETUP.md). `bio2rtl check/build` itself does not require a local PDK checkout.

## `bio2rtl init`

Minimal project setup from user-known pins only:

```bash
bio2rtl init PROGRAM.dis \
  --io PIN_A=16 \
  --io PIN_B=GPIO17 \
  --clock PIN_A \
  --name project_name
```

`--io` accepts both `NAME=16` and `NAME=GPIO16`.

Use `--core-only` to omit TR-1um POR/I/O/full-chip support. The generated core is still mapped to TR-1um standard cells.

## `bio2rtl check`

```bash
bio2rtl check PROGRAM.dis
```

Validates the TOML without compiling. Unknown/obsolete options are rejected rather than ignored.

## `bio2rtl build`

```bash
bio2rtl build PROGRAM.dis
```

Equivalent shorthand:

```bash
bio2rtl PROGRAM.dis
```

Useful reproducibility options:

```bash
bio2rtl build PROGRAM.dis \
  --build-dir build \
  --work-dir .bio2rtl-work \
  --reuse-work
```

`--reuse-work` is useful after an external timeout because completed semantic checkpoints can be reused.

## `bio2rtl benchmark`

After a full-chip I2C build:

```bash
bio2rtl benchmark PROGRAM.dis
```

Ver.1 currently provides the directed benchmark fixture for the bundled `i2c_gpio_2bit` project. It generates an Xschem schematic with PWL-driven SCL and PWL-driven master SDA open-drain control.
