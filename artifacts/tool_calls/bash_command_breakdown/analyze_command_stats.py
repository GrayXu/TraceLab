#!/usr/bin/env python3
"""Coverage & shape statistics for the command dataset -> ``command_stats.json``.

A standalone summary of ``command_calls.jsonl``: how many calls, how many executable occurrences,
the **n_exe distribution** (single- vs multi-executable), the parse-status breakdown, and — the point
of the file — how much of the data can actually feed the single-executable runtime analysis
(``n_exe == 1`` and successful parsing and a latency value; 0-second calls are floored to 1 ms in
the figure, not dropped). Only single-exe calls can attribute one latency to one
executable; pipelines/chains can't, so this quantifies the coverage the runtime box plots draw from.

Reported overall and per provider. Reads ``command_calls.jsonl`` plus the exact all-tool totals from
the neighboring ``tool_time_by_kind/tool_total_time_by_kind.csv``; writes ``command_stats.json``
(machine-readable) **and** ``command_stats.md`` (GFM tables for website display — the gallery reads
it via ``experiments.ts`` ``tables[]``), plus a short human summary to stderr. No trace, no DuckDB,
no re-classification.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = EXP_DIR / "command_calls.jsonl"
DEFAULT_OUTPUT = EXP_DIR / "command_stats.json"
DEFAULT_ALL_TOOL_STATS = EXP_DIR.parent / "tool_time_by_kind" / "tool_total_time_by_kind.csv"
PROVIDERS = ("claude", "codex")
PARSE_STATUSES = ("success", "partial", "failed")
SHELL_TOOL_NAMES = frozenset(("Bash", "exec_command", "shell", "shell_command"))
SHELL_TIME_TOOL_NAMES = SHELL_TOOL_NAMES | {"write_stdin"}


def blank() -> dict:
    return {"calls": 0, "occ": 0, "n0": 0, "n1": 0, "n2plus": 0,
            "src": Counter(), "exes": set(), "single_det": 0, "single_timed": 0,
            "positive_latency_calls": 0, "total_latency_ms": 0.0}


def fold(acc: dict, rec: dict) -> None:
    """Accumulate one dataset row into a tally bucket."""
    acc["calls"] += 1
    n = rec.get("n_exe") or 0
    acc["occ"] += n
    if n == 0:
        acc["n0"] += 1
    elif n == 1:
        acc["n1"] += 1
    else:
        acc["n2plus"] += 1
    acc["src"][rec.get("executable_parse_status")] += 1
    latency_ms = rec.get("latency_ms")
    if latency_ms is not None and latency_ms > 0:
        acc["positive_latency_calls"] += 1
        acc["total_latency_ms"] += float(latency_ms)
    for e in rec.get("executables") or []:
        acc["exes"].add(e)
    # runtime funnel: a single-executable, cleanly-parsed call with a latency value. 0-second
    # (sub-second, Codex whole-second rounding) calls DO count — the runtime figure floors them to
    # 1 ms rather than dropping them; only a genuinely absent latency is excluded.
    if n == 1 and rec.get("executable_parse_status") == "success":
        acc["single_det"] += 1
        if rec.get("latency_ms") is not None:
            acc["single_timed"] += 1


def summarize(acc: dict) -> dict:
    calls = acc["calls"]

    def share(x: int) -> float:
        return round(x / calls, 4) if calls else 0.0

    return {
        "calls": calls,
        "executable_occurrences": acc["occ"],
        "distinct_executables": len(acc["exes"]),
        "mean_exe_per_call": round(acc["occ"] / calls, 3) if calls else 0.0,
        "positive_latency_calls": acc["positive_latency_calls"],
        "total_effective_latency_ms": acc["total_latency_ms"],
        "by_parse_status": {
            key: acc["src"][key] for key in PARSE_STATUSES if acc["src"][key]
        },
        "n_exe": {
            "single_1": acc["n1"],
            "multi_2plus": acc["n2plus"],
            "zero_0": acc["n0"],
            "single_share_of_calls": share(acc["n1"]),
            "multi_share_of_calls": share(acc["n2plus"]),
        },
        "runtime_attributable": {
            "count": acc["single_timed"],
            "share_of_calls": share(acc["single_timed"]),
            "single_exe_deterministic": acc["single_det"],
            "single_exe_no_latency": acc["single_det"] - acc["single_timed"],
            "definition": "n_exe==1 & executable_parse_status==success & latency_ms!=null "
                          "(0s calls floored to 1ms in the runtime figure, not dropped)",
        },
    }


def _fmt(n: int) -> str:
    return f"{n:,}"


def _pct(n: int, share: float) -> str:
    return f"{n:,} ({share:.1%})"


def markdown_table(summaries: dict[str, dict]) -> str:
    """A GFM table (Metric × {Claude, Codex, Total}) for website display."""
    cols = list(summaries)
    rows: list[tuple[str, callable]] = [
        ("Command calls", lambda s: _fmt(s["calls"])),
        ("Single-executable calls (n=1)",
         lambda s: _pct(s["n_exe"]["single_1"], s["n_exe"]["single_share_of_calls"])),
        ("Multi-executable calls (n≥2)",
         lambda s: _pct(s["n_exe"]["multi_2plus"], s["n_exe"]["multi_share_of_calls"])),
        ("Unresolved calls (n=0)",
         lambda s: _pct(s["n_exe"]["zero_0"], s["n_exe"]["zero_0"] / s["calls"] if s["calls"] else 0)),
        ("Executable occurrences", lambda s: _fmt(s["executable_occurrences"])),
        ("Executables per call (mean)", lambda s: f"{s['mean_exe_per_call']:.2f}"),
        ("Distinct executables", lambda s: _fmt(s["distinct_executables"])),
    ]
    out = ["| Metric | " + " | ".join(cols) + " |",
           "| :-- | " + " | ".join("--:" for _ in cols) + " |"]
    for label, fn in rows:
        out.append("| " + label + " | " + " | ".join(fn(summaries[c]) for c in cols) + " |")
    return "\n".join(out) + "\n"


def load_shell_share(path: Path, shell_summary: dict) -> dict:
    """Load all-tool denominators and cross-check their shell slice against this dataset."""
    if not path.exists():
        raise FileNotFoundError(
            f"missing all-tool stats: {path} (run artifacts/tool_calls/tool_time_by_kind/plot.py)"
        )

    all_calls = shell_calls = 0
    all_latency_ms = shell_launch_latency_ms = shell_time_latency_ms = 0.0
    with path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            calls = int(row["tool_calls"])
            latency_ms = float(row["total_latency_ms"])
            all_calls += calls
            all_latency_ms += latency_ms
            if row["tool_name"] in SHELL_TOOL_NAMES:
                shell_calls += calls
                shell_launch_latency_ms += latency_ms
            if row["tool_name"] in SHELL_TIME_TOOL_NAMES:
                shell_time_latency_ms += latency_ms

    expected_calls = shell_summary["calls"]
    expected_latency_ms = shell_summary["total_effective_latency_ms"]
    if shell_calls != expected_calls or abs(shell_launch_latency_ms - expected_latency_ms) > 0.5:
        raise ValueError(
            "command_calls.jsonl and all-tool stats do not match: "
            f"shell calls {expected_calls:,} vs {shell_calls:,}; "
            f"shell latency {expected_latency_ms:.0f} ms vs {shell_launch_latency_ms:.0f} ms"
        )

    return {
        "definition": "Bash + exec_command + shell + shell_command",
        "time_definition": "Bash + exec_command + shell + shell_command + write_stdin",
        "latency_definition": (
            "sum of strictly-positive effective latency "
            "(tool_internal_latency_ms else tool_wall_latency_ms), additive over calls"
        ),
        "shell_calls": shell_calls,
        "all_tool_calls": all_calls,
        "call_share": shell_calls / all_calls if all_calls else 0.0,
        "shell_total_effective_latency_ms": shell_time_latency_ms,
        "all_tool_total_effective_latency_ms": all_latency_ms,
        "latency_share": shell_time_latency_ms / all_latency_ms if all_latency_ms else 0.0,
    }


def shell_share_markdown(share: dict) -> str:
    """A compact numerator / denominator / share table for shell tools versus all tools."""
    shell_hours = share["shell_total_effective_latency_ms"] / 3_600_000
    all_hours = share["all_tool_total_effective_latency_ms"] / 3_600_000
    return (
        "\n## Shell-command share of all tool calls\n\n"
        "The call count includes shell-command launches: `Bash`, `exec_command`, `shell`, and "
        "`shell_command`. Aggregated effective time also includes `write_stdin`, because it waits "
        "on or continues an existing command. Time sums strictly positive per-call latency "
        "(internal when available, otherwise wall) and is additive over parallel calls.\n\n"
        "| Metric | Shell-command tools | All tools | Share |\n"
        "| :-- | --: | --: | --: |\n"
        f"| Tool-call count | {share['shell_calls']:,} | {share['all_tool_calls']:,} | "
        f"{share['call_share']:.1%} |\n"
        f"| Aggregated effective time (including `write_stdin`) | {shell_hours:,.2f} h | "
        f"{all_hours:,.2f} h | "
        f"{share['latency_share']:.1%} |\n"
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                   help=f"centralized dataset (default: {DEFAULT_INPUT.name})")
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"stats JSON to write (default: {DEFAULT_OUTPUT.name})")
    p.add_argument("--markdown", type=Path, default=EXP_DIR / "command_stats.md",
                   help="GFM stats table for website display (default: command_stats.md)")
    p.add_argument("--all-tool-stats", type=Path, default=DEFAULT_ALL_TOOL_STATS,
                   help="all-tool count/time CSV used for shell-share denominators")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"missing input: {args.input}\n(run classify_commands.py first)", file=sys.stderr)
        return 2

    overall = blank()
    per_prov: dict[str, dict] = {}
    hist: Counter = Counter()

    with args.input.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            fold(overall, rec)
            hist[rec.get("n_exe") or 0] += 1
            prov = rec.get("provider")
            per_prov.setdefault(prov, blank())
            fold(per_prov[prov], rec)

    present = [p for p in PROVIDERS if p in per_prov] + [p for p in per_prov if p not in PROVIDERS]
    overall_s = summarize(overall)
    prov_s = {p: summarize(per_prov[p]) for p in present}
    shell_share = load_shell_share(args.all_tool_stats, overall_s)
    stats = {
        "generated_from": args.input.name,
        "all_tool_stats_from": str(args.all_tool_stats),
        "overall": overall_s,
        "providers": prov_s,
        "n_exe_histogram": {str(k): hist[k] for k in sorted(hist)},
        "shell_share_of_all_tools": shell_share,
    }

    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(args.output)

    # website table: Metric × {<providers>, Total} — provider columns first, pooled "Total" last,
    # matching the site convention (e.g. cache_hit_ratio_table.md: | Metric | Claude | Codex | Total |)
    table_cols = {**{p.capitalize(): prov_s[p] for p in present}, "Total": overall_s}
    args.markdown.write_text(
        markdown_table(table_cols) + shell_share_markdown(shell_share), encoding="utf-8"
    )

    # human summary
    o = stats["overall"]
    ra = o["runtime_attributable"]
    print(f"{o['calls']:,} calls  ->  {o['executable_occurrences']:,} executable occurrences "
          f"({o['mean_exe_per_call']} per call, {o['distinct_executables']:,} distinct)",
          file=sys.stderr)
    print(f"  single-exe (n_exe=1): {o['n_exe']['single_1']:,} "
          f"({o['n_exe']['single_share_of_calls']:.1%})   "
          f"multi (>=2): {o['n_exe']['multi_2plus']:,}   unresolved (0): {o['n_exe']['zero_0']:,}",
          file=sys.stderr)
    print(f"  runtime-attributable single-exe: {ra['count']:,} "
          f"({ra['share_of_calls']:.1%} of calls)   "
          f"[{ra['single_exe_no_latency']:,} single-exe had no latency]", file=sys.stderr)
    print(f"  shell share of all tools: {shell_share['shell_calls']:,} / "
          f"{shell_share['all_tool_calls']:,} calls ({shell_share['call_share']:.1%}); "
          f"{shell_share['shell_total_effective_latency_ms'] / 3_600_000:,.2f} / "
          f"{shell_share['all_tool_total_effective_latency_ms'] / 3_600_000:,.2f} hours "
          f"({shell_share['latency_share']:.1%})", file=sys.stderr)
    for p in present:
        s = summarize(per_prov[p])
        print(f"  {p:8s} calls={s['calls']:>8,}  single-exe={s['n_exe']['single_1']:>8,} "
              f"({s['n_exe']['single_share_of_calls']:.1%})  "
              f"runtime-attributable={s['runtime_attributable']['count']:>8,} "
              f"({s['runtime_attributable']['share_of_calls']:.1%})", file=sys.stderr)
    print(f"wrote -> {args.output}  +  {args.markdown.name}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
