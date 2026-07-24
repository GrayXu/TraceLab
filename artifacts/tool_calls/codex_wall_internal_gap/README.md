# codex_wall_internal_gap

**For Codex tool calls, how much wall-clock time sits outside the work the runner
actually did — the end-to-end vs. internal latency gap?**

## Experiment overview

Codex traces carry two latency notions per tool call:

- **End-to-end (wall)** — `tool_wall_latency_ms`, the timestamp span from the model
  emitting the function call to its output being recorded.
- **Internal** — `tool_internal_latency_ms`, the runner-reported `Wall time: … seconds`
  parsed out of the tool output, i.e. how long the command itself ran.

The experiment quantifies the **positive residual**
`gap = max(tool_wall_latency_ms − tool_internal_latency_ms, 0)`: the slice of
end-to-end time the runner did *not* attribute to executing the command. In the
normalized trace this residual is the only signal available for approval / user-wait
overhead, because tool inputs, outputs, and explicit approval events are not retained,
so it is best read as an **upper bound** on client-side waiting around the call rather
than a direct approval measurement.

Only `(provider = 'codex')` calls with **both** timings present, positive wall time, and
non-negative internal time enter the residual statistics. The paper float
`tab:codex_tool_e2e_internal` (emitted as `codex_tool_e2e_internal.tex` / `.md`)
aggregates these into an `All timed` row plus one row per major execution-like tool
(`exec_command`, `write_stdin`, `shell_command`, `apply_patch`), reporting calls,
summed E2E / internal / residual hours, the average residual, and the P50/90/99 residual
seconds. The script also writes several CSV breakdowns (per-tool, per-category, residual
buckets, direct-human wall time, top-gap examples) and a `result_analysis.md` narrative.

## Running it

```bash
# released DuckDB, outputs written next to this README
uv run python artifacts/tool_calls/codex_wall_internal_gap/analyze.py --db trace/syfi_coding_trace.duckdb

# default merged trace
uv run python artifacts/tool_calls/codex_wall_internal_gap/analyze.py
```

Standard `trace_db` CLI (`--db` | `-i/--input` | `-o/--output-dir`). Useful flag:
`--top-gap-examples` (rows in the top-gap-examples CSV, default 50).

## Outputs

- `codex_tool_e2e_internal.tex` — the paper float `tab:codex_tool_e2e_internal`:
  per-tool E2E / internal / positive-residual latency with avg and P50/90/99.
- `codex_tool_e2e_internal.md` — GFM mirror of that table (same numbers, no caption)
  for the web detail page.
- `headline.json` — the few headline numbers for the Overview gallery card.
- `result_analysis.md` — narrative of the main numbers, coverage, and interpretability
  limits.
- CSVs: `codex_tool_timing_coverage.csv`, `codex_wall_internal_gap_by_tool.csv`,
  `codex_wall_internal_gap_by_category.csv`, `codex_wall_internal_gap_buckets.csv`,
  `codex_direct_human_wall_time.csv`, `codex_top_wall_internal_gap_examples.csv`.

## Headline numbers (public trace)

- Across **338k** both-timed Codex calls, end-to-end time is **598.6h** and internal time is
  **470.1h**, leaving a **129.7h** residual gap (21.7% of E2E).
- `exec_command` carries most of it: **248k** calls and **108.7h** residual.
- Residuals are usually tiny but heavy-tailed: median **0.16s**, P90 **0.31s**, P99
  **10.2s** over all timed calls.

## SyFI result analysis

### codex_tool_e2e_internal.md

Codex's observed end-to-end tool latency substantially exceeds the runner's internal execution
time, so a non-trivial slice of tool wall-time is not actual command work (the paper's
`tab:codex_tool_e2e_internal`). The 338k both-timed calls sum to 598.6h E2E and 470.1h internal,
leaving a 129.7h residual (~21.7% of E2E). `exec_command`
dominates that gap with 108.7h of residual (internal 53.6h out of 162.2h E2E), consistent with shell
commands being the ones most likely to stall on permission/auto-approval. The residual is mostly
made of many tiny gaps plus a long tail: the average is 1.38s and P50/P90 stay small
(0.16s/0.31s), while P99 reaches 10.2s. `write_stdin` is the opposite shape — huge E2E (429.3h)
that is almost all internal, leaving only 18.7h residual — confirming the overhead concentrates
in command launches, not in long-running interactive sessions. Read this residual as an upper
bound on client-side waiting around the call (approval, shell startup, scheduling), not a direct
approval measurement.
