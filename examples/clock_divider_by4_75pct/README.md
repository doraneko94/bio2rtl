# clock_divider_by4_75pct

Produces a divide-by-4 periodic output with 75% duty: one low tick followed by three high ticks.

- `TICK`: BIO GPIO16 input / event source
- `OUT`: BIO GPIO18 push-pull output (`gpio_o`)
- Generated core: 11 cells / 3 DFFR

Run from the bio2rtl repository root:

```bash
python tools/run_bio_sim_example.py ../bio-sim clock_divider_by4_75pct
```
