# e2e_formula_check

**Question.** Can a user turn's end-to-end response time be reconstructed from a simple
formula using *average* per-round generation and tool cost? I.e. does
`e2e ≈ rounds × (avg_generation + avg_tool)` hold?

## Input

`trace/syfi_coding_trace.duckdb` by default; use `--db` for another materialized trace.

## Method / key assumptions

- Compares observed user-turn response time against a formula built from average
  per-round generation time and average tool time, to test whether a coarse analytic
  model is good enough to stand in for the full trace.
- Same turn / generation / tool definitions as `user_turn_decomposition/`
  (generation = latest input → last model output; tool = effective tool latency).
- Reports where the formula over- or under-predicts.

## How to run

```bash
uv run python validators/human_in_the_loop/e2e_formula_check/analyze.py
```

(For a fast check, pass `--db` with a smaller materialized database.)

## Outputs (written here)

- `result_analysis.md` — formula-vs-observed comparison.

## Notes

Markdown only (no figures).
