# I²C 2-bit GPIO expander

BIO source: `bio_sim/main.c`  
Pins: SCL=GPIO16 (`direct`), SDA=GPIO17 (`sda_io`), GPIO0=GPIO18 (`gpio_io`), GPIO1=GPIO19 (`gpio_io`)  
Frozen v1.2.1 regression: 122 standard cells / 21 DFFR / 422,625.12 µm², global/selected proofs PASS.

Run from the bio2rtl repository root inside the ISHI-kai OpenSUSI-TR10 WSL image:

```bash
python tools/run_bio_sim_example.py ../bio-sim i2c_gpio_2bit
```

The frozen electrical benchmark is documented in `../../docs/I2C_ELECTRICAL_TEST.md`.
