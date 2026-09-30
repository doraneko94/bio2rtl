# Project TOML — Generic Ver.1

A minimal full-chip project:

```toml
schema = "bio2rtl-project-v1.1"
program = "i2c-gpio-hw.dis"
name = "i2c_gpio_2bit"

[[io]]
name = "SCL"
gpio = 16
support = "direct"

[[io]]
name = "SDA"
gpio = 17
support = "sda_io"

[[io]]
name = "GPIO0"
gpio = 18
support = "gpio_io"

[[io]]
name = "GPIO1"
gpio = 19
support = "gpio_io"

[clock]
mode = "io_edges"
source = "SCL"

[physical_support]
technology = "TR-1um"
```

## Supported fields

- `schema`: must currently be `bio2rtl-project-v1.1`
- `program`: `.dis` filename
- `name`: generated project/module basename
- `[[io]].name`: user-visible external pin name
- `[[io]].gpio`: Baochip BIO GPIO number
- `[[io]].support`: optional physical support constraint: `auto`, `direct`, `sda_io`, `gpio_io`, or `gpio_o`; omitted means `auto`
- `[clock].mode`: currently only `io_edges`
- `[clock].source`: one of the declared `[[io]].name` values
- `[physical_support].technology`: currently only `TR-1um`

`[physical_support]` is optional. When omitted, bio2rtl still targets TR-1um standard cells for the core, but does not resolve or instantiate POR/I/O support recipes and does not emit a full-chip wrapper.

Power pins are fixed to `VDD` and `VSS` for the current TR-1um backend.

The user TOML does not contain compiler-internal nets or recipe bindings. `gpio_o` is accepted only when the recovered GPIO needs no readback; incompatible support selections fail closed.
