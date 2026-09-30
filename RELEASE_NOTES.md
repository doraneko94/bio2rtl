# Release notes — v1.2.1

v1.2.1 cleans up the Xschem output model and fixes core-only builds while preserving the v1.2.0 TR-1um mapping baseline.

## Canonical core output

The compiler now emits one canonical routing-aware core view: `*_core.sch` / `*_core.sym`. It no longer generates the redundant `*_manual.sch` view or the fallback `*_core_phy.sch`, `*_core_phy.sym`, and `core_phy_placement.json` artifacts.

Full-chip generation uses the canonical core directly. If the canonical core pin contract does not satisfy the resolved physical-support contract, the build fails instead of generating another core variant.

The routing audit file is now named `routed_xschem_export_audit.json`.

## Core-only build

`[physical_support]` now controls only POR, I/O support, and full-chip generation. A project without that section still runs semantic analysis, TR-1um standard-cell mapping, canonical core generation, and mapping proofs.

Core-only compilation no longer reads the physical-support recipe library. For the same program and I/O configuration, support-enabled and core-only builds produce the same structural Verilog, Physical DHIR, and canonical `core.sch`.

## Regression baseline

The bundled I2C 2-bit GPIO expander retains the established mapping:

- 122 standard cells
- 21 DFFR
- 422,625.12 µm² mapped standard-cell area
- global semantic proof: 98,056 checks PASS
- selected physical mapping proof: 98,056 checks PASS

The v1.2.0 documentation stated 163,592 checks. The mapper/proof source used for this 122-cell snapshot is unchanged; a fresh regression reports `truth_checks = 98,056` in each final proof JSON. v1.2.1 corrects that documentation value.

The four non-I2C examples map to 16, 11, 29, and 12 cells. Release regression checks their output sequences from the generated standard-cell netlists. The I2C regression checks directed transactions and the final mapping proofs.

## Compatibility and license

Python 3.10+ is supported. Python 3.10 uses the `tomli` fallback. `networkx>=2.8`, required by the Xschem placement/router path, is declared as a runtime dependency.

bio2rtl is licensed under Apache License 2.0. Using bio2rtl does not by itself apply Apache-2.0 to user-generated circuits. Third-party PDK/IP remains subject to its own license terms.
