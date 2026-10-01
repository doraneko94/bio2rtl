# Changelog

## Unreleased

## 1.2.1

- Emit one canonical routing-aware `*_core.sch` only; remove generated `*_manual.sch`, `*_core_phy.sch`, `*_core_phy.sym`, and `core_phy_placement.json`.
- Full-chip generation now requires the canonical core pin contract to match the resolved physical-support contract and fails closed instead of generating a fallback core view.
- Rename the routing audit to `routed_xschem_export_audit.json`.
- Decouple TR-1um core mapping from optional `[physical_support]`; core-only builds now complete without reading physical-support recipes and produce the same canonical core as the support-enabled build.
- Remove the stale `*_tb_template.sch` core-only verifier expectation; the documented `*_tb.sym` is the generated testbench interface artifact.
- Add an explicit release-regression target for the five bundled examples and mapped-signal/I2C checks.
- Correct the I2C proof-count documentation: the unchanged mapper/proof code reports 98,056 truth checks in each final proof JSON for the 122-cell snapshot.
- Restore the documented Python 3.10/3.11 compatibility by removing a Python 3.12-only f-string expression from an optional semantic-frontend transform; refresh GitHub Actions to Node 24-compatible action releases.

## 1.2.0

- Generic multi-output minimum-cover selection: evaluates equally minimal per-output covers jointly and adopts the smallest mapped shared-logic solution.
- Bundled I2C regression maps to 122 cells / 21 DFFR / 422,625.12 µm² with global and selected mapping proofs PASS.
- Routing-aware Xschem exporter now emits the canonical `*_core.sch`: grid10, explicit wires, no internal remote `lab_pin`, pin/wire clearance checks, M1-horizontal/M2-vertical intent, and outer-lane long vertical trunks.
- Added non-I2C release examples covering event-triggered output, divide-by-4 waveforms, and a fixed serial pattern.
- Python 3.10+ compatibility retained.
- Adopt Apache License 2.0 for the public repository; generated user designs are not forced under Apache-2.0 merely by using bio2rtl, while third-party IP terms remain separate.
- Document J-IMPACT name/logo use separately from the software license.
- Declare `networkx>=2.8` as a runtime dependency and clean the v1.2.0 implementation without changing regression outputs.
- Make every final generated TR-1um standard-cell schematic, including `*_core_phy.sch`, reference the real `TR-1um_5_stdcell` Xschem library; remove the last generated-symbol dependency from that fallback view.
- Remove root-user assumptions from generated PDK lookup: default to `$HOME/pdk/TR-1um`, accept both parent-style and direct `PDK_ROOT`, and use the same convention for Xschem and SPICE helpers.

## 1.1.2

- Support Python 3.10.
- Use stdlib `tomllib` on Python 3.11+ and `tomli` on Python 3.10.
- Declare conditional `tomli` dependency in `pyproject.toml`.
- Run GitHub Actions smoke tests on Python 3.10, 3.11, and 3.13.
- Improve direct-checkout error message when Python 3.10 is missing `tomli`.

## 1.1.1

Public/GitHub packaging release based on the frozen Generic Ver.1 B20 G15R2 core.

- user TOML contains only user-known BIO pin assignments
- automatic external-I/O type/binding inference
- CLI `init`, `check`, `build`, and `benchmark`
- `bio2rtl benchmark` generates the directed I2C Xschem electrical fixture
- GitHub-ready examples, docs, `.gitignore`, and smoke workflow
- compiler/mapping core remains the G15R2 frozen core
