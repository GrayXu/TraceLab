# bash_command_breakdown

**Agents do most of their real work through the shell — so which executables do those commands
actually run, how often is each one used, and how long does each take?**

## Experiment overview

Scope is the command/shell execute tools — the calls that run a shell command: `Bash` (Claude) and
`exec_command` / `shell` / `shell_command` (Codex). Every such call is classified by the
executable(s) it runs and timed by its per-call latency, then folded into one dataset the analyses
read.

Method and assumptions:

- **One label per executable occurrence**, not per line. A pipeline/chain contributes one label per
  stage: `grep x | tail` → `grep`+`tail`; `git log && echo --- && git diff` → `git`×2 (`echo` is
  shell plumbing — counted but tagged, not dropped). Commands inside `$(...)` count (they run);
  heredoc and `apply_patch` bodies are *data*, not commands.
- **Deterministic and offline.** Each command is parsed with a real bash grammar (tree-sitter-bash)
  and its executables named by a semantic layer (transparent-wrapper stripping, `python -m NAME` →
  `NAME`, path → basename, synonyms). This names **99.86%** of calls with certainty — no LLM, no
  endpoint. The ~0.14% it can't name (dynamic program names, parse errors) is left as an honest
  `unresolved` bucket, never guessed.
- **Runtime is measured on single-executable calls only** (`n_exe == 1`): a pipeline/chain can't
  attribute one wall time across its stages. Codex reports wall time in whole seconds, so its
  sub-second commands read `0 seconds`; those are floored to 1 ms (kept as the fastest bucket)
  rather than dropped, which would bias Codex medians upward.

Everything downstream reads one centralized dataset, `command_calls.jsonl`; nothing re-scans the
trace or re-classifies. The raw command text survives only in the private normalized JSONL, so
classification defaults to the all-users merged trace and fails loudly on a sanitized input.

## The dataset — command_calls.jsonl

One JSON row per shell/command call — the single source of truth for any executable/runtime question.

| field | meaning |
|---|---|
| `provider` | `claude` \| `codex` |
| `user`, `project` | who ran it / which repo |
| `trace_key`, `session_id`, `round_index`, `tool_index`, `tool_call_id` | call identity |
| `tool_name` | `Bash` \| `exec_command` \| `shell` \| `shell_command` |
| `command` | the full raw command text |
| `latency_ms` | per-call latency (`tool_internal_latency_ms` else `tool_wall_latency_ms`); `null` if none |
| `executables` | list, one entry per occurrence (`["git","git"]`); `[]` if opaque |
| `kinds` | aligned with `executables`: `"tool"` \| `"plumbing"` |
| `n_exe` | `len(executables)` |
| `source` | `deterministic` (all named) \| `partial` (some named) \| `unresolved` (none named) |
| `reason` | why a call is unresolved/partial (`dynamic`/`parse-error`/…), else `null` |

Two standard reads: **popularity** counts `executables[]` across all rows; **runtime** keeps rows
with `source == "deterministic"` and `n_exe == 1` and a latency value (0-second calls floored to
1 ms).

## Code structure

- `classify_commands.py` (needs `uv run --extra bash`) — scans the whole trace once and streams
  `command_calls.jsonl`. tree-sitter-bash gives the *structure* (splitting, heredoc/`$()` isolation,
  pipeline/loop/subshell nesting); a semantic label layer gives the *executable meaning*
  (`seg_head`/`label_exe`/`PLUMBING`/wrapper + python rules). The parser is lazy, so importing this
  module as a library needs no parser.
- `analyze_popularity.py` — one tally per `executables[]` entry → `executable_popularity.csv` + the
  pooled, per-provider, and compact top-15 per-provider figures.
- `analyze_executable_runtime.py` — single-executable latency box plots → `executable_runtime.csv` +
  figure.
- `analyze_command_stats.py` — coverage/shape statistics plus the shell-command share of all tool
  calls and summed effective tool time → `command_stats.json` + `command_stats.md` (the website
  tables). Counts cover command launches; summed time additionally includes Codex `write_stdin`
  continuation/wait calls. All-tool denominators come from the adjacent exact `tool_time_by_kind`
  CSV, and the script cross-checks that its shell-launch slice matches `command_calls.jsonl` before
  writing.

## Running it

```bash
BASE=artifacts/tool_calls/bash_command_breakdown

uv run --extra bash python $BASE/classify_commands.py   # classify the whole trace (~32s)
uv run python $BASE/analyze_popularity.py               # popularity CSV + 3 figures
uv run python $BASE/analyze_executable_runtime.py       # runtime CSV + figure
uv run python artifacts/tool_calls/tool_time_by_kind/plot.py  # all-tool count/time denominators
uv run python $BASE/analyze_command_stats.py            # command_stats.json + command_stats.md
```

Flags — `classify_commands.py`: `-i/--input`, `-o/--output-dir`, `--tools`, `--progress-every`.
`analyze_popularity.py`: `--top` (45), `--tools-only`. `analyze_executable_runtime.py`:
`--min-calls` (30), `--top` (30), `--tools-only`. `analyze_command_stats.py`:
`--all-tool-stats` (the matching `tool_total_time_by_kind.csv`).

## Outputs

All gitignored; only the four `.py` scripts and this README are tracked.

| file | contents |
|---|---|
| **`command_calls.jsonl`** | the centralized dataset — every call, executables + latency |
| `executable_popularity.csv` + 3 PNGs | ranked usage, pooled, per-provider, and compact top-15 per-provider |
| `executable_runtime.csv` + PNG | per-executable latency percentiles + box plots |
| `command_stats.json` / `command_stats.md` | coverage/shape stats + the website table |

## Notes / limits

- **echo is high because it is counted faithfully.** Agents use `echo` heavily as a progress/label
  idiom (Claude averages ~2.3 echos per echo-using command); every occurrence is one echo process, so
  it counts. It is tagged `plumbing`, so the `--tools-only` view drops it.
- **Codex timing granularity.** Codex's latency is parsed from a `"Wall time: N seconds"` line, so it
  is quantized to whole seconds (values cluster at 1000/2000/…ms; 30000ms ≈ the 30s exec cap). ~41% of
  Codex single-exe calls are fast enough to read `0 seconds` → 0 ms and are floored to 1 ms rather than
  dropped — so a spike at 1 ms in a Codex box is that 0-second cohort. Claude's come from millisecond
  timestamps and are fine-grained.
- Not registered in `run_all.py`: this depends on the private normalized trace, so it can't
  regenerate in the automatic DuckDB figure pipeline.

## SyFI result analysis

### executable_popularity.png

The pooled bar is each executable's share of *all* shell-command invocations, and the working set is
dominated by text search/slicing plus glue. `sed` (13.9%), `grep` (7.0%), `head` (6.4%), `rg` (5.3%)
and `tail` (4.3%) lead the tools, alongside `echo` (9.5%, shell plumbing) and `python-script` (6.8%),
with `git`, `ls`, `docker`, `find`, `nl`, `cd`, `wc`, `cat` filling out the head and 734 rarer
executables folded into `other` (5.3%). So once commands are broken into the programs they run, the
agents' shell work is overwhelmingly reading, searching, and slicing files (plus printing progress) —
not launching heavyweight tools. Bars are tinted tool versus shell plumbing (`echo`/`cd`/`true`) so
the genuine tool set is separable from glue; the header carries the invocation total and the distinct
executable count.

### executable_popularity_by_provider.png

Ranking each agent by its *own* share exposes two different default toolchains for the same file work.
Claude leans on the classic pipeline utilities — `grep` (14.5%), `head` (12.1%), `tail` (6.3%), `ls`
(6.0%) — with `echo` (18.4%) its dominant progress-print idiom. Codex instead reaches first for
`sed` (24.2%), then `python-script` (10.5%), `rg` (9.6%), `docker` (4.7%) and `nl` (4.5%). So the
agents accomplish similar searching/slicing through distinct primitives: Claude via grep + head/tail,
Codex via sed + ripgrep + nl. This also explains Claude's higher executables-per-call (≈3.7 vs ≈1.6):
it strings more of these small utilities together per command.

### executable_runtime.png

Per-executable latency over single-executable calls, one panel per agent, boxes ordered slowest-median
at top. Fast primitives (`grep`, `ls`, `sed`, `rg`, `tail`) sit at a few to tens of milliseconds,
while `docker`, `pytest` and `python-script` run seconds, with whiskers reaching tens of seconds (and
Codex's 30s exec cap). Two things the figure makes explicit: only single-executable calls can be timed
— multi-exe pipelines/chains are excluded, which is ~78% of Claude's calls, so the Claude panel is
drawn from a much thinner slice (its coverage subtitle) than Codex's; and Codex clocks wall time in
whole seconds, so its many sub-second commands read 0s and are floored to 1 ms — hence the 1 ms floor
at the bottom of the Codex panel, versus Claude's genuine millisecond spread. Read Codex medians with
that granularity in mind.
