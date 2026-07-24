# session_token_steps

**Inside a single coding session, how does the context window grow agent step by agent step —
where do the user's messages land, how much is cheap cached prefix vs. freshly appended input, and
where does the agent compact and start over?**

## Experiment overview

Each row in the trace is one agent step. This experiment picks a handful of illustrative
sessions and, for each, draws one bar per step: cached/prefix tokens (blue) stacked under
newly appended input tokens (orange), with the running total input as a line on top. A thin top
strip lays the same steps out on a 5-minute wall-clock timeline so you can see where the agent
paused. It is the closest thing in the toolkit to *watching a session breathe*.

Method and assumptions:

- **One step per invocation**, ordered within a session by `(round_index, first-event timestamp,
  ingestion order)`. The ingestion-order tie-break is file order, so equal-timestamp steps never
  reorder.
- **Prefix vs. append** come straight from the step's `prefix_tokens` / `newly_append_tokens`;
  their sum is the full input size for that invocation.
- **User-initiated steps** (`U1`, `U2`, …) are the steps whose timing events include a visible
  `user_message`, i.e. where the human actually typed — as opposed to tool-triggered steps.
- **Compaction** is flagged two ways: an *explicit* marker (a timing event whose type/source
  mentions "compact"), or an *inferred input drop* — the full input size falls by ≥8k tokens and
  ≥25% from a base of ≥32k, and **stays** low for the next few steps (a one-step dip that
  rebounds is ignored). A prefix-only decrease is deliberately *not* treated as compaction, because
  a cache miss can shift tokens from prefix to append without shrinking the real context.
- **Generation time** per step is measured from the last input event at-or-before the first model
  output to the last model output — the model's own "thinking + generating" span, excluding human
  wait time.
- **Session selection** is automatic and deterministic: candidates are filtered by step count and
  user-initiated/tool-triggered mix, then three ranked picks are unioned — a balanced score, a context-heavy
  score, and a compaction-heavy score — so the gallery shows variety, not six look-alikes. Pin
  specific sessions with `--session-id`.

## Code structure

This is a **hybrid** experiment: the trace DuckDB does the single-pass ingest, and Python keeps the
per-session heuristics (ordering, windowing, compaction detection, scoring) that don't belong in
SQL.

- `load_sessions_from_db(con)` — three queries (step scalars, per-step timing events, per-step
  tool counts), all in ingestion order, assembled into `SessionStats` objects. This is the only
  data-loading code; everything below is unchanged from the pre-DuckDB version.
- `RoundRow` / `TimingEvent` — one invocation and its timing rows; `first_observed_timestamp`,
  `input_to_last_output_span_seconds`, `has_explicit_compaction_marker` derive the per-step facts.
- `find_compaction_markers(rounds)` — the explicit + inferred-drop logic with the rebound guard.
- `SessionStats` — per-session rollups and the three selection scores.
- `select_sessions(...)` / `select_window(...)` — which sessions, and (if `--max-steps`) which
  contiguous window of one.
- `plot_session(...)` — the stacked-bar + timeline figure; `write_outputs(...)` — the candidate CSV
  and the selected-sessions JSON.

The data layer lives in `artifacts/utils/trace_db.py` (see `artifacts/utils/DB_SCHEMA.md`).

## Running it

```bash
# auto-select illustrative sessions from the default merged trace
uv run python artifacts/session/session_token_steps/plot.py

# a specific trace (materialized to a temp DuckDB cache on first use)
uv run python artifacts/session/session_token_steps/plot.py -i trace/sample.jsonl

# a prebuilt DB (run_all.py's build-db step passes this), into a chosen dir
uv run python artifacts/session/session_token_steps/plot.py --db "$TMPDIR/trace.duckdb" -o "$TMPDIR/out"

# pin exact sessions
uv run python artifacts/session/session_token_steps/plot.py --session-id <session_id>
```

Selection knobs: `--top-sessions`, `--context-sessions`, `--compaction-sessions`, the
`--min/--max-rounds` and `--min-user-input-rounds` / `--min-tool-result-rounds` filters,
`--max-steps` (window a long session), `--candidate-limit` (CSV depth). `--select-offset` /
`--select-stride` shard the selected set for parallel rendering.

## Outputs

- `<session>_token_steps.png` — one figure per selected session (filename is a stable hash of the
  session id).
- `session_token_steps_candidates.csv` — every ranked candidate session with its rollups and
  scores; the `selected` column flags which were plotted.
- `selected_session_token_steps.json` — the exact selection and per-window metrics.

Each PNG embeds this README, the candidate CSV, and `plot.py`. Unpack with
`uv run python artifacts/utils/png_sidecar.py extract <png>`.

## SyFI result analysis

### <session>_token_steps.png

One session, watched step by step (the paper's `fig:session_progress_example`; the filename is just
the session's hash). Every figure reads the same way:

- **The orange/blue split is the cache story.** A tall blue (prefix) base under a thin orange
  (append) cap is the efficient steady state — most of the step's input is cheap cached prefix and
  little is freshly paid for. Steps are usually initiated close together in time, so the cache holds
  and the prefix stays warm; a sudden tall orange bar is a cache miss forcing a large fresh prefill,
  as in the paper's example where ~10 minutes of human inactivity around step 28 evicts the prefix.
- **The total-input line climbs** as tool results and file contents accumulate, ramping the window
  toward the model's limit.
- **Purple `C` markers are compaction** — the running total collapses and the session restarts its
  context; the bands show how many steps it lasted and whether it compacts once or repeatedly.
- **Red `U` lines are user-initiated steps**; wide gaps between them mean long autonomous tool-driven
  stretches on a single instruction.
- **The timeline strip** separates compute from wall-clock — adjacent steps far apart in time are
  where the human was reading/thinking, dense blocks are the agent working uninterrupted.
