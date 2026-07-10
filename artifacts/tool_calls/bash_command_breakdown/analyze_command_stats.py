#!/usr/bin/env python3
"""Coverage & shape statistics for the command dataset -> ``command_stats.json``.

A standalone summary of ``command_calls.jsonl``: how many calls, how many executable occurrences,
the **n_exe distribution** (single- vs multi-executable), the ``source`` breakdown, and — the point
of the file — how much of the data can actually feed the single-executable runtime analysis
(``n_exe == 1`` **and** ``source == "deterministic"`` **and** a latency value; 0-second calls are
floored to 1 ms in the figure, not dropped). Only single-exe calls can attribute one latency to one
executable; pipelines/chains can't, so this quantifies the coverage the runtime box plots draw from.

Reported overall and per provider. Reads only ``command_calls.jsonl``; writes ``command_stats.json``
(machine-readable) **and** ``command_stats.md`` (a GFM table for website display — the gallery reads
it via ``experiments.ts`` ``tables[]``), plus a short human summary to stderr. No trace, no DuckDB,
no re-classification.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = EXP_DIR / "command_calls.jsonl"
DEFAULT_OUTPUT = EXP_DIR / "command_stats.json"
PROVIDERS = ("claude", "codex")
SOURCES = ("deterministic", "partial", "unresolved")


def blank() -> dict:
    return {"calls": 0, "occ": 0, "n0": 0, "n1": 0, "n2plus": 0,
            "src": Counter(), "exes": set(), "single_det": 0, "single_timed": 0}


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
    acc["src"][rec.get("source")] += 1
    for e in rec.get("executables") or []:
        acc["exes"].add(e)
    # runtime funnel: a single-executable, cleanly-parsed call with a latency value. 0-second
    # (sub-second, Codex whole-second rounding) calls DO count — the runtime figure floors them to
    # 1 ms rather than dropping them; only a genuinely absent latency is excluded.
    if n == 1 and rec.get("source") == "deterministic":
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
        "by_source": {k: acc["src"][k] for k in SOURCES if acc["src"][k]},
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
            "definition": "n_exe==1 & source==deterministic & latency_ms!=null "
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


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                   help=f"centralized dataset (default: {DEFAULT_INPUT.name})")
    p.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT,
                   help=f"stats JSON to write (default: {DEFAULT_OUTPUT.name})")
    p.add_argument("--markdown", type=Path, default=EXP_DIR / "command_stats.md",
                   help="GFM stats table for website display (default: command_stats.md)")
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
    stats = {
        "generated_from": args.input.name,
        "overall": overall_s,
        "providers": prov_s,
        "n_exe_histogram": {str(k): hist[k] for k in sorted(hist)},
    }

    tmp = args.output.with_suffix(args.output.suffix + ".tmp")
    tmp.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(args.output)

    # website table: Metric × {<providers>, Total} — provider columns first, pooled "Total" last,
    # matching the site convention (e.g. cache_hit_ratio_table.md: | Metric | Claude | Codex | Total |)
    table_cols = {**{p.capitalize(): prov_s[p] for p in present}, "Total": overall_s}
    args.markdown.write_text(markdown_table(table_cols), encoding="utf-8")

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
