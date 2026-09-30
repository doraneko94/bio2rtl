# serial_pattern_1101

Emits the fixed serial pattern `1, 1, 0, 1` on successive rising edges of `TICK`, then stops.

- `TICK`: BIO GPIO16 input / event source
- `OUT`: BIO GPIO18 push-pull output (`gpio_o`)
- Generated core: 29 cells / 7 DFFR

Run from the bio2rtl repository root:

```bash
python tools/run_bio_sim_example.py ../bio-sim serial_pattern_1101
```
