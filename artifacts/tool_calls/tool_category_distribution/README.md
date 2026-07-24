# tool_category_distribution

**When tools are folded into a handful of coarse *categories* (execute, file write/edit, file
read/search, agent/task, web/lookup, …), how are calls and latency distributed across those
categories — and how concentrated is the latency long tail in a few slow calls?**

## Experiment overview

Individual tool names are numerous and provider-specific; this experiment groups them into coarse
categories that mean the same thing across Claude Code and Codex, then reports how calls and
effective latency split across those categories.

Method and assumptions:

- **One row per call.** We count entries in `tool_calls` (the UNNESTed `tools[]`), not agent steps.
- **Two fixed tool→category maps.** A 5-category-plus-`other` map (`Execute command`, `File
  write/edit`, `File read/search`, `Agent/task`, `Web/remote/lookup`, `Other`) drives the count
  ring and latency bar; a 7-bucket presentation map (which additionally splits out `Planning`)
  drives the dashboard. Both maps are explicit name→category sets ported verbatim — the
  `tool_category_tool_map.csv` emits the realized `(category, provider, tool)` breakdown for
  auditing.
- **Effective tool latency** = `tool_internal_latency_ms` if present, else `tool_wall_latency_ms`
  (the legacy `latency_ms` fallback is not in the normalized schema). Only **positive** latencies
  contribute to summed latency and to the percentile/long-tail views; missing and non-positive
  latencies are counted separately but excluded from the sums.
- **Long-tail bins.** Positive latencies are bucketed into `<1s`, `1–10s`, `10s–1m`, `>1m` to
  contrast each bucket's *share of calls* against its *share of total latency*.

## Code structure

`analyze.py` is a query→fold→plot pipeline over the shared trace DuckDB:

- `load_tool_aggregates(con)` — one `GROUP BY (provider, tool_name)` over `tool_calls ⋈ rounds`
  that returns per-tool `calls`, `error_calls`, the valid/missing/non-positive latency-class counts,
  and summed positive latency. Provider/tool-name normalization (`<unknown-provider>` /
  `<unknown-tool>`) is done in SQL to match the old loader.
- `load_positive_latency_histogram(con)` — `(tool_name, latency_ms, count)` rows for positive
  latencies, expanded in Python into the per-category latency lists the percentiles consume.
- `scan_trace` / `scan_trace_presentation` / `scan_trace_long_tail_latency` — fold the per-tool
  aggregates into the coarse categories using the **verbatim** `category_for_tool` /
  `presentation_category_for_tool` maps (summing is order-independent over the integer-ms latencies).
- `category_rows` / `presentation_rows` / `long_tail_rows` + their `write_*_csv` — shape and emit
  the four CSVs.
- `plot_count_ring` / `plot_latency_bar` / `plot_dashboard` / `plot_long_tail_imbalance` — the
  four figures. `main()` wires the standard `trace_db` CLI and embeds the PNG sidecar.

The data layer (parsing, surrogate keys, schema) lives in `artifacts/utils/trace_db.py`; see
`artifacts/utils/DB_SCHEMA.md`.

## Running it

```bash
# default merged trace, output next to this README
uv run python artifacts/tool_calls/tool_category_distribution/analyze.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/tool_calls/tool_category_distribution/analyze.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/tool_calls/tool_category_distribution/analyze.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

## Outputs

- `tool_category_count_ring.png` — donut of call counts across the 6 coarse categories.
- `tool_category_latency_bar.png` — summed effective latency (hours) per category, with average.
- `tool_category_dashboard.png` — combined donut + category table + latency-quantile strip for the
  7-bucket presentation map.
- `tool_latency_long_tail_imbalance.png` — call-share vs latency-share across the `<1s … >1m` bins.
- `tool_category_summary.csv` — per coarse category: calls, share, error rate, latency-class counts,
  summed/avg latency.
- `tool_category_tool_map.csv` — the realized `(category, provider, tool_name)` breakdown.
- `tool_category_dashboard_summary.csv` — per presentation category: calls, share, p25/p50/p90/p99
  seconds.
- `tool_latency_long_tail_imbalance.csv` — per latency bin: calls, call share, latency, latency
  share.
- `result_analysis.md` — generated run log.

The PNGs are self-contained — each embeds this README, the CSVs, and the plotting code. Unpack with
`uv run python artifacts/utils/png_sidecar.py extract <png>`.

## SyFI result analysis

### tool_category_count_ring.png

The donut shows how the agents' tool calls split across the six coarse categories, and the ordering
is sharply heavy-headed. Execute-command alone is 73.4% of all calls, file write/edit 10.4% and file
read/search 9.3%; agent/task (1.5%) and web/remote/lookup (1.0%) are thin slivers, with everything
else folded into `Other` (4.3%). So once provider-specific tool names are normalized into shared
categories, the agents' work is overwhelmingly shell execution plus file I/O. The center label is
the total call count and each slice is annotated with its share; the legend carries exact counts so
the small slices stay legible.

### tool_category_latency_bar.png

Re-ranking the same categories by **summed effective latency** (hours) tells a different story than
the count ring, because per-call cost varies by more than two orders of magnitude (each bar is
annotated with average seconds per call). Execute-command leads at ~1,826h, while agent/task
averages ~43s per valid call (128h total) and web/remote/lookup ~16s (33.5h). The `Other` bucket
punches far above its 4.3% call share at ~968h (~109s average). File read/search, despite being
the third-most-called category, costs only ~17.8h at ~0.93s per valid call.
That is the count-vs-cost gap: cheap high-frequency primitives versus expensive, rarer calls that
block on real work or the user.

### tool_category_dashboard.png

The presentation dashboard combines three views of the 7-bucket map (which additionally splits out
`Planning`): a call-count donut (left), a ranked category table (middle), and a log-scale
**latency-quantile strip** (right, p25/p50/p90/p99 per category). The strip exposes the
within-category spread that a single average hides — shell/command sits at a p50 of ~0.85s but a
p99 of ~235s, planning jumps from a p50 of ~0.07s to a p99 of ~378s, and agent/task climbs from a
p50 of ~0.18s to a p99 of ~600s. A category can thus have a modest median yet a p99 three to four
orders of magnitude larger, which is exactly the long-tail behavior the next figure quantifies in
aggregate.

### tool_latency_long_tail_imbalance.png

This is the headline imbalance: the top bar is each latency bin's **share of calls**, the bottom
its **share of total latency**, and the two invert. The `<1s` bin holds 61.3% of calls but only
0.6% of total latency, and `1–10s` adds 25.4% of calls for 4.2% of latency. At the other
end the `>1m` bin is just 3.4% of calls yet 83.6% of all tool latency, with `10s–1m` (10.0% of calls)
contributing 11.7%. So a handful of slow calls account for the overwhelming majority
of time spent in tools — the same long-tail signature seen per-provider, now aggregated across
categories. Exact figures are in `tool_latency_long_tail_imbalance.csv`.
