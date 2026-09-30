# `.dis` → Xschem `.sch` / `.sym`

## 1. Prepare the TOML

For a new program, run `bio2rtl init`. For the bundled examples the TOML is already present beside each `.dis`.

## 2. Compile

```bash
bio2rtl build examples/i2c_gpio_2bit/i2c-gpio-hw.dis
```

The relevant outputs are:

- `<project>_core.sch`: canonical routing-aware mapped core
- `<project>_core.sym`: hierarchy symbol generated from the actual inferred core ports
- `<project>_tb.sym`: debug/test symbol exposing the mapped core interface
- `<project>_fullchip.sch`: routed core + TOML-selected physical support
- `<project>_fullchip.sym`: user-facing physical-pin symbol
- `build/<project>.route_guide.json`: machine-readable row/segment guide for the physical backend
- `build/routed_xschem_export_audit.json`: routing acceptance report

The routing-aware exporter is generic. It uses real TR-1um symbol pin geometry and enforces:

- all component origins, external ports, wire endpoints and bends on a 10-unit grid
- horizontal row placement
- M1-horizontal / M2-vertical routing intent
- explicit internal wiring; no remote internal `lab_pin` connectivity
- minimum separation from unrelated pins and parallel foreign-net tracks
- long vertical trunks through outer routing lanes instead of crossing multiple rows inside the cell array
- no floating/ambiguous terminals, zero-length wires, unintended split nets, or cell overlap

The generated canonical standard-cell schematic (`*_core.sch`) references the OpenSUSI library directly as `TR-1um_5_stdcell/<cell>.sym`. No duplicate `manual` or physical-core fallback view is generated. They do not depend on a hard-coded `/root/...` path. `xschemrc.bio2rtl` resolves that library through the installed TR-1um PDK. Handwritten POR/I/O support uses the same standard-cell library plus `TR-1umLIB` device symbols where required.

For the I2C example, `i2c_gpio_2bit_fullchip.sym` has exactly:

- VDD
- VSS
- SCL
- SDA
- GPIO0
- GPIO1

Use the full-chip symbol for the electrical I2C benchmark. Use the TB symbol only when you intentionally want the internal core interface.


## Full-chip support

With `[physical_support] technology = "TR-1um"`, `bio2rtl build` automatically resolves the electrical role of each declared I/O and emits the connected full-chip hierarchy. The user does not manually instantiate POR or pad-support blocks.

- reset: `support/por.sch`, with `R` connected to the core reset input
- direct input: external pin connected directly to the inferred core input
- open-drain I/O: `support/sda_io.sch`
- bidirectional / readback GPIO: `support/gpio_io.sch`
- push-pull GPIO without readback: `support/gpio_o.sch`

Only support recipes actually used by the design are copied into `build/xschem/support/`. The selected bindings are recorded in `build/physical_support_inferred.json` and `build/generic_fullchip_export_audit.json`.
For a core-only project, no support recipes or full-chip wrapper are emitted; the technology-independent digital boundary is recorded in `build/core_interface_inferred.json`.

## 3. Open Xschem

With OpenSUSI TR-1um installed, no root-specific path is required. By default bio2rtl looks for the PDK at `$HOME/pdk/TR-1um`, so a normal non-root account works without modification when the PDK is installed under that account.

If the PDK is elsewhere, set the environment explicitly:

```bash
export PDK_ROOT="$HOME/pdk"
export PDK="TR-1um"
export BIO2RTL_XSCHEM_DIR="$PWD/build/xschem"
```

`PDK_ROOT` normally names the directory containing `TR-1um`. For compatibility, pointing `PDK_ROOT` directly at the `TR-1um` directory is also accepted.

Then:

```bash
xschem --rcfile build/xschem/xschemrc.bio2rtl \
  build/xschem/i2c_gpio_2bit_fullchip.sch
```
