# append_vs_prefix_latency

**Question.** Are append-heavy agent steps actually slower than otherwise-matched
prefix-heavy steps? Not just "append-heavy rows are slower on average", but: after
matching on provider, model, segment kind, total input length, and output length, do
append-heavy rows separate cleanly from prefix-heavy ones?

## Input

`../timing_fit/timing_fit_trace.csv` (override with `-i`) — the long-form
timing-segment CSV produced by `../timing_fit/collect_timing_fit_trace.py`. **Not** the
JSONL trace. `artifacts/run_all.py` builds it automatically from `--db` before running
this experiment.

## Method / key assumptions

- Rows are bucketed by `(provider, model, segment_kind, total-token bin,
  output-token bin)`. Within each bucket, **append-heavy** rows (append share
  `≥ --append-heavy-share`) are compared against **prefix-heavy** rows (append share
  `≤ --prefix-heavy-max-append-share`).
- Reports two things:
  - **effect size** — how often an append-heavy row is slower than a matched
    prefix-heavy row (`pair_weighted_append_slower_probability`);
  - **separation quality** — whether a duration threshold distinguishes the two
    classes after normalizing each row by its bucket's prefix-heavy median latency
    (`global_normalized_best_balanced_accuracy`).
- Durations are trimmed per group (`--trim-quantile`, default 0.99) and filtered to
  `[--min-duration-ms, --max-duration-ms]` to drop implausible spans.

## How to run

Recommended dispatcher path:

```bash
uv run python artifacts/run_all.py \
  --only llm_generation/append_vs_prefix_latency \
  --db trace/syfi_coding_trace.duckdb
```

The dispatcher builds `../timing_fit/timing_fit_trace.csv` from `--db` first. Manual
direct runs assume that CSV already exists:

```bash
uv run python artifacts/llm_generation/append_vs_prefix_latency/analyze.py
```

## Outputs

- `append_vs_prefix_latency.json` / `.md` — verdict + summary.
- `append_vs_prefix_matched_buckets.csv`, `append_vs_prefix_normalized_rows.csv`
- `append_vs_prefix_bucket_effects.png`, `append_vs_prefix_normalized_overlap.png`

## Self-contained PNGs

Each PNG embeds this README, the CSVs, and `analyze.py`. Unpack with
`uv run python artifacts/utils/png_sidecar.py extract <png>`.

## SyFI result analysis

### append_vs_prefix_bucket_effects.png

The effect-size view, one point per matched bucket of `(provider, model, segment kind, total-token
bin, output-token bin)`. The verdict is **append-heavy steps are slower, but the two classes do not
separate cleanly**: pair-weighted `P(append-heavy slower than a matched prefix-heavy row)` is 75.2%
(Cliff's delta 0.505) and append-heavy is the slower median in 643 of 752 buckets (85.5%), yet the
median bucket-level latency ratio is only **1.17x**. The largest, cleanest buckets (long context,
short output Codex `tool_result→tool_call`) push ratios to 1.5–2.5x with 85–94% dominance, so the
effect is real where append dwarfs a tiny output — but the *typical* bucket gap is modest.

### append_vs_prefix_normalized_overlap.png

The separation-quality view: each row's duration is normalized by its bucket's prefix-heavy median,
then append-heavy and prefix-heavy rows are overlaid. They **overlap heavily** — the best global
normalized-duration threshold (1.04x) reaches only 61.7% balanced accuracy, well below the 75% bar for
a clean split. So the strong "append-heavy rows form a separable slow class" hypothesis is rejected:
append share shifts the distribution but does not cleanly classify a row's latency. As an
observational trace, matching controls token lengths and model/segment identity but not queueing,
batch composition, cache residency, or transient load, all of which add to the overlap.
