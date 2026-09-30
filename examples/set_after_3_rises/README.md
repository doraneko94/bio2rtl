# set_after_3_rises

Sets `OUT` high after the third rising edge of `TICK`, then holds it high.

- `TICK`: BIO GPIO16 input / event source
- `OUT`: BIO GPIO18 push-pull output (`gpio_o`)
- Generated core: 16 cells / 3 DFFR

Run from the bio2rtl repository root:

```bash
python tools/run_bio_sim_example.py ../bio-sim set_after_3_rises
```
