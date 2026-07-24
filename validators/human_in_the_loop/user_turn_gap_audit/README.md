# user_turn_gap_audit

**Question.** Inside a user turn's response window, what is the *unclassified* elapsed
time actually made of? (A deep drill-down into the residual from
`user_turn_decomposition/`.)

## Input

`trace/syfi_coding_trace.duckdb` by default; use `--db` for another materialized trace.

## Method / key assumptions

- Takes the residual = response time − generation − tool time, and attributes the gap
  to specific sources: between-round handoffs, untimed tool calls, post-output
  usage-accounting events, missing timestamps, etc.
- Same turn / generation / tool definitions as `user_turn_decomposition/`.
- Goal: confirm the residual is accounting noise, not a missing real cost.

## How to run

```bash
uv run python validators/human_in_the_loop/user_turn_gap_audit/analyze.py
```

(For a fast check, pass `--db` with a smaller materialized database.)

## Outputs (written here)

- `result_analysis.md` — breakdown of the unclassified gap by source.

## Notes

Markdown only (no figures).
