---
name: coding-trace-collect
description: "Collect, count, and extract Claude Code and Codex CLI local histories into normalized coding-trace JSONL files. Use when running collect_llm_traces.py, extract_claude_rounds.py, extract_codex_rounds.py, collect_all_users_sudo.sh, scanning current-user or all-user homes, choosing output paths, using --fresh-extract or --append-dedup, or troubleshooting extraction counts."
---

# Coding Trace Collect

## Overview

Use this skill to collect local Claude Code and Codex history stores and write normalized round traces. Collection finds source files and counts sessions; extraction converts provider-specific histories into JSONL rows where each line is one LLM invocation.

## First Steps

1. Work from the coding-trace repo root, identified by `pyproject.toml`, `README.md`, and `scripts/collect_llm_traces.py`.
2. Run every Python entry point through `uv run python ...`.
3. Decide scan scope before running commands:
   - Current user: default behavior.
   - All users under `/home`: use `--all-user`, or the sudo wrapper when unreadable homes are expected. Use `--home-root PATH` for nonstandard home roots.
4. Decide write behavior:
   - Default extraction appends deduped rows.
   - The sudo wrapper's default dated output is a fresh snapshot of currently readable sessions.
   - For a corpus refresh, merge fresh dated host snapshots after
     `trace/collections/current/merged.private.jsonl`; this both refreshes normalizer fields and
     retains history whose raw sessions have been cleaned up.
   - Use an explicit cumulative `--output` without `--fresh-extract` only when intentionally
     maintaining one append-only file.
5. Do not publish private outputs until `$coding-trace-sanitize` has been applied.
6. After sanitization, use `$coding-trace-analyze` for artifact and validator dispatchers. Collection should not own plotting outputs or timing-fit CSVs.

## Main Collection Commands

Count current-user Claude and Codex history without extraction:

```bash
uv run python scripts/collect_llm_traces.py
```

Extract a combined current-user trace:

```bash
uv run python scripts/collect_llm_traces.py --extract-rounds
```

Write to a specific file and start fresh. This is the recommended current-user command
only when intentionally creating a new, non-cumulative dataset:

```bash
uv run python scripts/collect_llm_traces.py --extract-rounds trace/llm_round_trace.jsonl --fresh-extract
```

Scan all homes under `/home` without sudo:

```bash
uv run python scripts/collect_llm_traces.py --all-user --extract-rounds
```

Use the sudo wrapper for a fresh, timestamped all-user snapshot while preserving final file
ownership. Reuse one `collection_id` on every host:

```bash
collection_id="$(date -u +%Y%m%dT%H%M%SZ)"
scripts/collect_all_users_sudo.sh \
  --collection-id "$collection_id" \
  --fresh-extract
```

The wrapper writes
`trace/collections/<collection_id>/hosts/llm_round_trace_v2.<collection_id>.<host>.all_users.jsonl`
and a sibling `.collection_report.json`. With `--sanitize`, it also writes a sibling
`.public.jsonl`. It prints an overview summary by default; add `--no-summary` for quiet batch runs.
Add `--quiet-progress` only to suppress collector progress messages.

To combine older and newer private normalized traces while letting newer
normalizer fields win on overlap:

```bash
uv run python scripts/merge_round_traces.py \
  trace/collections/current/merged.private.jsonl \
  "trace/collections/${collection_id}/hosts/"*.all_users.jsonl \
  -o "trace/collections/${collection_id}/merged.private.jsonl"
```

For a private-to-public current-user flow:

```bash
collection_id="$(date -u +%Y%m%dT%H%M%SZ)"

uv run python scripts/collect_llm_traces.py \
  --extract-rounds "trace/collections/${collection_id}/merged.private.jsonl" \
  --fresh-extract

uv run python scripts/sanitize_round_trace.py \
  "trace/collections/${collection_id}/merged.private.jsonl" \
  -o "trace/collections/${collection_id}/merged.public.jsonl"
```

## Timing Fit CSV

The long-form timing-segment CSV is now owned by the timing-fit artifact, not the
collection pipeline. `artifacts/run_all.py --db <trace.duckdb>` builds it
automatically before timing analyses. Do not precompute it during normal collection.
To build it directly for a timing-only manual run:

```bash
uv run python artifacts/llm_generation/timing_fit/collect_timing_fit_trace.py \
  --db trace/collections/current/merged.public.duckdb \
  -o artifacts/llm_generation/timing_fit/timing_fit_trace.csv
```

The output has one timing segment per row. Claude segments use message-level output accounting; Codex can also emit reasoning-split segments when reasoning markers and exact reasoning-token counts are available.

## Output Paths

Important output names:

- Current-user combined trace: `trace/llm_round_trace.jsonl`.
- Current-user Claude-only trace: `trace/claude_round_trace.jsonl`.
- Current-user Codex-only trace: `trace/codex_round_trace.jsonl`.
- Sudo-wrapper all-user trace:
  `trace/collections/<timestamp>/hosts/llm_round_trace_v2.<timestamp>.<host>.all_users.jsonl`.
- Sudo-wrapper report: the sibling `.collection_report.json`.
- Sudo-wrapper sanitized output with `--sanitize`: the sibling `.public.jsonl`.
- Selected historical collection: `trace/collections/current`.
- Timing-fit artifact CSV: `artifacts/llm_generation/timing_fit/timing_fit_trace.csv`.

Use `--trace-dir DIR` to change the default extraction directory for omitted output
paths. Passing an explicit path to `--extract-rounds`, `--extract-claude-rounds`, or
`--extract-codex-rounds` overrides both the built-in default and `--trace-dir`.

## Provider-Specific Extraction

Use direct extractors when the user already has a provider source directory:

```bash
uv run python scripts/extract_claude_rounds.py ~/.claude/projects/PROJECT_DIR -o trace/claude_round_trace.jsonl --append-dedup
```

```bash
uv run python scripts/extract_codex_rounds.py ~/.codex/sessions -o trace/codex_round_trace.jsonl --append-dedup
```

Prefer `collect_llm_traces.py` for normal use because it scans both providers, handles `.claude.back`, tracks skipped paths, and combines extraction stats.

## Important Options

- `--json`: emit a machine-readable collection report.
- `--no-claude-back`: skip `.claude.back/projects`.
- `--quiet-host-progress`: collector option that suppresses progress messages during host scanning and extraction.
- `--quiet-progress`: sudo-wrapper option that passes the collector quiet mode.
- `--collection-id`: sudo-wrapper UTC timestamp used in both the collection directory and file names.
- `--no-summary`: sudo-wrapper option that skips the post-collection overview summary.
- `--no-sudo`: sudo-wrapper option that runs all-user collection without sudo.
- `--extract-project-filter TEXT`: only extract Claude projects whose directory name contains `TEXT`; repeat the option for multiple filters.
- `--fresh-extract`: remove selected extraction outputs before writing.
- `--append-dedup`: direct extractor option that appends only unseen `trace_key` rows.

## Validation After Collection

After extraction, check basic shape before analysis:

```bash
uv run python artifacts/trace_facts/overview_summary/analyze.py -i trace/collections/current/merged.private.jsonl
```

For JSON output:

```bash
uv run python artifacts/trace_facts/overview_summary/analyze.py -i trace/collections/current/merged.private.jsonl --json
```

If outputs are unexpectedly small, inspect the collection report for `skipped_paths`, source counts, `candidate_rounds`, `written_rounds`, and `skipped_duplicate_rounds`.
