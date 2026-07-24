# session_internal_counts

**How much work does one coding session, and one request, contain?**

Computes the count distributions behind `tab:session_internal_counts`
(`src/04_SessionContext.tex`): requests, user-/tool-initiated steps, and tool calls per
session; tool-initiated steps and tool calls per request; and tool calls per step — each as
avg / p25 / p50 / p90 / p99.

## Running it

```bash
# default merged trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/session/session_internal_counts/analyze.py

# the pinned public trace
uv run python artifacts/session/session_internal_counts/analyze.py --db trace/syfi_coding_trace.duckdb

# a prebuilt DB, into a chosen dir
uv run python artifacts/session/session_internal_counts/analyze.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"
```

## Outputs

- `session_internal_counts.tex` — the merged three-line (booktabs) table, ready to `\input` or
  paste into `src/04_SessionContext.tex`.
- `session_internal_counts.md` — GFM Markdown mirror of the table, rendered on the web detail page.
- `headline.json` — the few headline numbers for the Overview gallery card.
- stdout — the full merged + per-provider (Claude / Codex) breakdown.

No figures.

## SyFI result analysis

### session_internal_counts.md

Coding sessions are persistent and overwhelmingly autonomous (the paper's
`tab:session_internal_counts`). A session averages 10.1 requests but with a long tail (p99 = 161),
so the human keeps coming back to the same session over and over. Per session there are far more
tool-initiated steps (avg 69.9) than user-initiated ones (avg 9.8), so once a request lands the
loop runs itself: resolving one request takes 6.7 tool-initiated steps and 8.9 tool calls on
average. At the step level each round issues just over one tool call (avg 1.1, p50 1, p90 2), so
parallel tool calling does happen but is the exception, not the norm.
