# clock_divider_by4

Produces a divide-by-4 periodic output with 50% duty: two high ticks and two low ticks per cycle.

- `TICK`: BIO GPIO16 input / event source
- `OUT`: BIO GPIO18 push-pull output (`gpio_o`)
- Generated core: 12 cells / 3 DFFR

Run from the bio2rtl repository root:

```bash
python tools/run_bio_sim_example.py ../bio-sim clock_divider_by4
```
