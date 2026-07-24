# append_by_prefix_bin

**Given how large the cached prefix already is, how many *new* (uncached) tokens does a step
append?**

Populates `tab:append_by_prefix` (`src/05_LLMGeneration.tex`) — the quantitative companion to the
prefix-vs-append scatter (`fig:prefill_append_relationship`). For Claude and Codex, every agent
step is binned by its `prefix_tokens`, and within each bin we report the distribution of
`newly_append_tokens`: count, avg, p50, p90, p99.

Prefix bins are doubling, in 1024-token units: `<1k, 1-2k, 2-4k, 4-8k, 8-16k, 16-32k, 32-64k,
64-128k, 128-256k, >256k`. The `prefix_tokens` / `newly_append_tokens` accounting is the same one
used by `prefix_append_distribution` and `token_length_distribution`, so the numbers reconcile.

## Running it

```bash
uv run python artifacts/llm_generation/append_by_prefix_bin/analyze.py --db trace/syfi_coding_trace.duckdb
uv run python artifacts/llm_generation/append_by_prefix_bin/analyze.py        # default merged trace
```

## Outputs

- `append_by_prefix_bin.tex` — the Claude/Codex table for the paper (empty bins render as `--`).
- `append_by_prefix_bin.md` — GFM Markdown mirror of the table, rendered on the web detail page.
- `headline.json` — the few headline numbers for the Overview gallery card.
- stdout — the same per-provider breakdown in plain text.

## Headline numbers (public trace)

- Append and prefix are **inversely** related. At `<1k` prefix (cold start — a cache miss or the
  first request) the median append is **78k** tokens for Claude and **124k** for Codex.
- Once the prefix exceeds ~32k (incremental tool-loop / user steps) the median append collapses
  to **well under 1k** for both providers, with only a modest p99 tail.
- Bins reveal provider structure: Claude's prefix jumps almost straight to large values (the
  `1-2k` bin is empty, `2-4k` has 2 steps) because its system prompt is large; Codex effectively
  caps near its 256k context (only 6 steps exceed it).

No figures.

## SyFI result analysis

### append_by_prefix_bin.md

The table (`tab:append_by_prefix`) quantifies the inverse relationship behind the prefix-vs-append
scatter: the more a step has already cached, the less it appends. In the smallest prefix bin (`<1k`
— a cache miss or the very first request, where almost nothing is cached) the median append is huge,
78k tokens for Claude and 124k for Codex, because nearly the whole prompt has to be sent as new. Once
the prefix grows past 32k the median append collapses to well under 1k (Claude 951→762, Codex
954→771 across the `32-64k`..`>256k` bins), as those steps only stack an incremental tool result or
user turn onto an already-cached context. The bins also expose provider structure: Claude's prefix
jumps almost straight to large values — its `1-2k` bin is empty and `2-4k` holds just 2 steps,
reflecting a large system prompt — while Codex effectively caps near its 256k context window, with
only 6 steps exceeding it.
