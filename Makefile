.PHONY: check-i2c build-i2c benchmark-i2c check-set-after-3-rises build-set-after-3-rises check-examples build-examples check-gpio-o check-core-only release-regression

check-i2c:
	python -m bio2rtl check examples/i2c_gpio_2bit/i2c-gpio-hw.dis

build-i2c:
	python -m bio2rtl build examples/i2c_gpio_2bit/i2c-gpio-hw.dis

benchmark-i2c:
	python -m bio2rtl benchmark examples/i2c_gpio_2bit/i2c-gpio-hw.dis

check-set-after-3-rises:
	python -m bio2rtl check examples/set_after_3_rises/set_after_3_rises.dis

build-set-after-3-rises:
	python -m bio2rtl build examples/set_after_3_rises/set_after_3_rises.dis

check-gpio-o:
	PYTHONPATH=. python tools/test_gpio_o_support.py

check-examples:
	python -m bio2rtl check examples/set_after_3_rises/set_after_3_rises.dis
	python -m bio2rtl check examples/clock_divider_by4_75pct/clock_divider_by4_75pct.dis
	python -m bio2rtl check examples/serial_pattern_1101/serial_pattern_1101.dis
	python -m bio2rtl check examples/clock_divider_by4/clock_divider_by4.dis
	python -m bio2rtl check examples/i2c_gpio_2bit/i2c-gpio-hw.dis
	PYTHONPATH=. python tools/test_gpio_o_support.py

build-examples:
	python -m bio2rtl build examples/set_after_3_rises/set_after_3_rises.dis --build-dir build/release_regression/set_after_3_rises
	python -m bio2rtl build examples/clock_divider_by4_75pct/clock_divider_by4_75pct.dis --build-dir build/release_regression/clock_divider_by4_75pct
	python -m bio2rtl build examples/serial_pattern_1101/serial_pattern_1101.dis --build-dir build/release_regression/serial_pattern_1101
	python -m bio2rtl build examples/clock_divider_by4/clock_divider_by4.dis --build-dir build/release_regression/clock_divider_by4
	python -m bio2rtl build examples/i2c_gpio_2bit/i2c-gpio-hw.dis --build-dir build/release_regression/i2c_gpio_2bit

check-core-only:
	mkdir -p build
	test -e build/release_regression/set_after_3_rises/set_after_3_rises.structural.v || python -m bio2rtl build examples/set_after_3_rises/set_after_3_rises.dis --build-dir build/release_regression/set_after_3_rises
	python -c "from pathlib import Path; s=Path('examples/set_after_3_rises/bio2rtl.toml').read_text(); Path('build/release_core_only.toml').write_text(s.split('\n[physical_support]')[0] + '\n')"
	python -m bio2rtl build examples/set_after_3_rises/set_after_3_rises.dis -c build/release_core_only.toml --build-dir build/release_core_only
	test ! -e build/release_core_only/xschem/set_after_3_rises_fullchip.sch
	test ! -e build/release_core_only/physical_support_inferred.json
	cmp build/release_regression/set_after_3_rises/set_after_3_rises.structural.v build/release_core_only/set_after_3_rises.structural.v
	cmp build/release_regression/set_after_3_rises/physical_dhir_v18_stage7.json build/release_core_only/physical_dhir_v18_stage7.json
	cmp build/release_regression/set_after_3_rises/xschem/set_after_3_rises_core.sch build/release_core_only/xschem/set_after_3_rises_core.sch

release-regression: check-examples
	python -m compileall -q bio2rtl semantic_frontend tools
	PYTHONPATH=. python tools/test_generic_pass_manager.py
	$(MAKE) build-examples
	$(MAKE) check-core-only
	PYTHONPATH=. python tools/test_example_signals.py --build-root build/release_regression --json-out build/release_regression/example_signal_regression.json
