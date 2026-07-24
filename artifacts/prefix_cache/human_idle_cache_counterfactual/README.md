# Cost of Human Thinking Time

**From a consumer perspective, how much extra append-prefill and API cost does a user pay because
"thinking" time can turn prefix-cache hits into misses?**

## Experiment overview

This is an upper-bound savings estimate, not an observed cache metric. We analyze prefix caching
from a **consumer perspective**: the user-visible cost of "thinking" is the extra fresh-input
prefill billed when a user-initiated step resumes after the prefix cache has expired. For every
user-initiated step `S` with a predecessor `P` in the same session, the estimate caps observed
append at the step's net context growth:

```text
total_input(S)            = prefix_tokens(S) + newly_append_tokens(S)
context_growth(S)         = max(0, total_input(S) - total_input(P))
append_after_retained_cache(S)  = min(newly_append_tokens(S), context_growth(S))
prefix_after_retained_cache(S)  = total_input(S) - append_after_retained_cache(S)
```

All other steps keep their observed `prefix_tokens` / `newly_append_tokens` split. The total input
length and output tokens do not change. Any append-token reduction is moved into prefix tokens and
billed at the cache-read price; remaining Claude cache-creation tokens are billed at the 5-minute
cache-write rate. Because the estimate assumes all shifted tokens can be served from cache at the
cache-read rate, the resulting savings are an upper bound rather than an achievable policy
guarantee.

Method and assumptions:

- A **user-initiated step** is a round whose first timing event is `user_message`, matching the
  trigger convention used by `cache_hit_ratio` and `redundant_prefill`.
- Pairing follows `redundant_prefill` / `session/total_input_growth`: the predecessor is the last
  round seen for the same `session_id` in `round_pk` file order. Session-first user steps have no
  predecessor and remain unchanged.
- This isolates the consumer-side cost of human thinking time. Tool-result steps and session-first
  steps are left as observed.
- Costs use `artifacts/utils/pricing.json` through `artifacts/web_analytics/pricing.py`: append at
  fresh-input/cache-write rates, prefix at the cache-read rate, output unchanged. Unpriced rounds
  contribute to token counts but are excluded from dollar totals.

## Code structure

- `collect(con)` streams `rounds` joined with each round's first timing event, walks sessions in
  file order, and accumulates observed vs. retained-cache token/cost totals by scope.
- `ScopeAccum` stores token totals, user-step coverage, priced-round coverage, and observed /
  retained-cache cost buckets. It also stores per-reduced-step cost-saved samples and preceding
  human idle gaps for avg / p50 / p90 rows.
- `write_summary_csv(...)`, `render_md(...)`, and `write_latex_table(...)` emit the raw summary,
  web table, and optional paper table.

## Running it

```bash
# default merged trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/prefix_cache/human_idle_cache_counterfactual/analyze.py

# a prebuilt DB, into a chosen dir
uv run python artifacts/prefix_cache/human_idle_cache_counterfactual/analyze.py \
  --db trace/syfi_coding_trace.duckdb -o "$TMPDIR/out"
```

## Outputs

- `human_idle_cache_counterfactual_summary.csv` - raw observed vs. retained-cache token and cost
  totals per scope (`merged`, `claude`, `codex`).
- `human_idle_cache_counterfactual.md` - GFM table for the web detail page.
- `human_idle_cache_counterfactual.tex` - optional LaTeX table.
- `headline.json` - headline values for the Overview gallery card.

## SyFI result analysis

### human_idle_cache_counterfactual.md

From this consumer perspective, if user-initiated steps retained their prefix cache across human
thinking time, the user-initiated steps contain **5.01B** observed append tokens and **2.30B** after
retained-cache accounting: a **2.70B-token** reduction, or **54.0%** of their observed append.

With `pricing.json` prices as of **2026-07**, the observed cost is **$90,888** and the retained-cache
estimate is **$76,556**, a **$14,333** (**15.8%**) saving over priced rounds. The split is **2.14B**
fewer append tokens and **$12,427** saved for Claude, and **565.0M** fewer append tokens and
**$1,906** saved for Codex. Token reductions include all rounds.
