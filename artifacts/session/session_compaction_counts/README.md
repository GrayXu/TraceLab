# session_compaction_counts

**How many context compactions does a coding session undergo?**

A *compaction* is the behavioral event the paper distinguishes from the plain size buckets in
[`total_input_growth`](../total_input_growth): when the running context nears its limit it is
summarized/dropped to a short history and then slowly re-accumulates. We detect it structurally
from the per-step total input length (`prefix_tokens + newly_append_tokens`), ordered by
`round_pk` within each session. A step `i` is a compaction when **all three** hold:

1. **Great reduction** — `total[i-1] - total[i] >= 64k` (`--min-drop-tokens`, defaults to
   `growth.MAJOR_REDUCTION_MIN_TOKENS`). Every compaction is therefore also a *major reduction*;
   compactions are the strict subset that also satisfy (2) and (3).
2. **Near the context limit** — the pre-drop level `total[i-1]` is at least `--near-max-ratio`
   (0.75) of the session's observed max total input. The drop happens near the session's peak,
   not at a small early dip.
3. **Recovers slowly** — the context does *not* rebound to `--rebound-ratio` (0.75) of the
   pre-drop level within the next `--rebound-steps` (3) steps, and at least one step follows.
   A drop that immediately snaps back is a branch/edit artifact, not a compaction.

Each compaction is attributed to the trigger of step `i`, using the same
**user-initiated** / **tool-initiated** split the rest of the paper uses: `user_message` →
user-initiated (an explicit `/compact` or a new request that forced summarization);
`tool_result` → tool-initiated (auto-compaction mid-loop).

## Running it

```bash
# pinned public trace
uv run python artifacts/session/session_compaction_counts/analyze.py --db trace/syfi_coding_trace.duckdb

# default merged trace
uv run python artifacts/session/session_compaction_counts/analyze.py

# loosen/tighten the definition
uv run python artifacts/session/session_compaction_counts/analyze.py \
    --near-max-ratio 0.8 --rebound-steps 5
```

## Outputs

- `session_compaction_counts.tex` — the merged summary table (`tab:session_compaction`).
- `session_compaction_counts.md` — GFM Markdown mirror of the table, rendered on the web detail page.
- `headline.json` — the few headline numbers for the Overview gallery card.
- stdout — merged + per-provider (Claude / Codex): total compactions, share of sessions with
  ≥1, the per-session distribution (avg / p25 / p50 / p90 / p99, over all sessions and over
  only those with ≥1), and the user-initiated-vs-tool-initiated trigger split.

## Headline numbers (public trace, default criteria)

- Of **2,750** major reductions (≥64k drops), **2,520** (91.6%) qualify as compactions.
- **8.8%** of the 8,058 sessions undergo at least one compaction.
- Predominantly **tool-initiated** (79.2%, mid-loop) rather than user-initiated.
- Far more common in **Codex** (17.4% of sessions, 1,984 events) than **Claude** (4.4%, 536).
- Among sessions that compact at all, the mean is 3.5 and the tail is long (Codex p99 = 37).

No figures.

## SyFI result analysis

### session_compaction_counts.md

Most large context drops are genuine compactions, and they hit Codex far harder than Claude (the
paper's `tab:session_compaction`). Of the major (≥64k) reductions, **536/669** qualify for Claude
and **1,984/2,081** for Codex — 2,520 of 2,750 combined — so the structural near-limit + slow-recovery
test rarely flags a false positive. Compactions are uncommon per session but lopsided by provider:
only **4.4%** of Claude sessions ever compact versus **17.4%** of Codex, and among sessions that do,
the mean and tail are larger for Codex (avg 4.16, p99 = 37) than Claude (avg 2.28, p99 = 12). The
trigger split mirrors the autonomy finding: Codex compactions are overwhelmingly **tool-initiated**
(82.5%, auto-compaction mid-loop), while Claude's are more evenly split (67.0% tool / 33.0% user).
