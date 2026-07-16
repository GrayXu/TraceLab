#!/usr/bin/env python3
"""Analyze executable popularity — what percentage of shell commands is which executable.

Reads only the centralized dataset ``command_calls.jsonl`` (from ``classify_commands.py``): one row
per shell call already carries its final ``executables`` + ``kinds`` + parse status, so this is a
cheap CSV + plot pass — no trace, no DuckDB, no re-classification.

Counts one tally per executable *occurrence* across every call (successful and partial rows
contribute; failed rows carry no executable and add nothing).

Writes:
- ``executable_popularity.csv`` — full ranking (all executables), the machine-readable result.
- ``executable_popularity.png`` — pooled: each executable's share of *all* shell-command
  executables, top-N with the long tail rolled into a small ``other``, tools vs plumbing tagged.
- ``executable_popularity_by_provider.png`` — claude (left) and codex (right) as separate panels,
  each ranked by its OWN share, so the toolchain contrast reads at a glance.
- ``executable_popularity_top15_by_provider.png`` — a compact, presentation-ready version of the
  provider comparison, limited to each agent's 15 most popular executables.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[2]  # experiment -> category -> artifacts -> repo root
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))

from style import (  # noqa: E402
    BAR_BLUE,
    MUTED_TEXT,
    TEXT_COLOR,
    mticker,
    plot_color,
    plt,
    polish_axes,
    provider_title,
    save_plot,
    short_label,
)
import png_sidecar  # noqa: E402

DEFAULT_INPUT = EXP_DIR / "command_calls.jsonl"
PROVIDERS = ("claude", "codex")
PLUMBING_COLOR = "#94a3b8"  # muted slate — shell plumbing (echo/cd/true/…), tagged not hidden
OTHER_COLOR = "#cbd5e1"


def load_dataset(path: Path) -> tuple[dict[str, dict], int]:
    """Fold the centralized dataset into per-executable, per-provider tallies.

    ``comb[exe] = {claude, codex, kind}`` — one count per executable occurrence, per provider.
    """
    comb: dict[str, dict] = defaultdict(lambda: {"claude": 0, "codex": 0, "kind": "tool"})
    rows = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            prov = rec.get("provider")
            if prov not in PROVIDERS:
                continue
            rows += 1
            exes = rec.get("executables") or []
            kinds = rec.get("kinds") or []
            for i, exe in enumerate(exes):
                c = comb[exe]
                c[prov] += 1
                c["kind"] = kinds[i] if i < len(kinds) else c["kind"]
    return comb, rows


def write_csv(comb: dict[str, dict], out: Path) -> tuple[int, int]:
    tot_c = sum(v["claude"] for v in comb.values())
    tot_x = sum(v["codex"] for v in comb.values())
    tot = tot_c + tot_x
    ranked = sorted(comb.items(),
                    key=lambda kv: (-(kv[1]["claude"] + kv[1]["codex"]), kv[0]))
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "executable", "kind", "claude_count", "codex_count", "total",
                    "claude_share", "codex_share", "pooled_share"])
        for i, (exe, v) in enumerate(ranked, 1):
            c, x = v["claude"], v["codex"]
            t = c + x
            w.writerow([
                i, exe, v["kind"], c, x, t,
                f"{c / tot_c:.6f}" if tot_c else "0",
                f"{x / tot_x:.6f}" if tot_x else "0",
                f"{t / tot:.6f}" if tot else "0",
            ])
    return tot_c, tot_x


def to_plot_rows(comb: dict[str, dict], tot_c: int, tot_x: int) -> list[dict]:
    tot = tot_c + tot_x
    out = []
    for exe, v in comb.items():
        t = v["claude"] + v["codex"]
        out.append({
            "exe": exe,
            "kind": v["kind"],
            "total": t,
            "claude_share": v["claude"] / tot_c if tot_c else 0.0,
            "codex_share": v["codex"] / tot_x if tot_x else 0.0,
            "pooled_share": t / tot if tot else 0.0,
        })
    return out


def fig_pooled(rows: list[dict], out_dir: Path, top: int, tools_only: bool) -> Path:
    """Fig 1 — pooled popularity: what % is which executable, top-N + a small `other`."""
    data = [r for r in rows if not (tools_only and r["kind"] == "plumbing")]
    data.sort(key=lambda r: -r["pooled_share"])
    head, tail = data[:top], data[top:]

    labels = [short_label(r["exe"], 20) for r in head]
    shares = [r["pooled_share"] * 100 for r in head]
    colors = [BAR_BLUE if r["kind"] == "tool" else PLUMBING_COLOR for r in head]
    if tail:
        labels.append(f"other ({len(tail)})")
        shares.append(sum(r["pooled_share"] for r in tail) * 100)
        colors.append(OTHER_COLOR)

    n = len(labels)
    fig, ax = plt.subplots(figsize=(9.6, max(4.6, 0.32 * n + 1.3)))
    y = list(range(n))
    ax.barh(y, shares, color=colors, height=0.76, edgecolor="white", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()  # biggest at top

    xmax = max(shares)
    for yi, s in zip(y, shares):
        ax.text(s + xmax * 0.012, yi, f"{s:.1f}%", va="center", ha="left",
                fontsize=8.6, color=TEXT_COLOR)
    ax.set_xlim(0, xmax * 1.16)
    ax.xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100, decimals=0))
    ax.set_xlabel("share of all shell-command executable invocations")
    polish_axes(ax, grid_axis="x")

    scope = "tools only (plumbing excluded)" if tools_only else "all executables (plumbing tagged)"
    ax.text(0, 1.045, "Executable popularity across all shell commands", transform=ax.transAxes,
            fontsize=15, fontweight="semibold", color=TEXT_COLOR, va="bottom")
    ax.text(0, 1.013, f"{sum(r['total'] for r in rows):,} invocations · {len(rows):,} distinct "
                      f"executables · top {top} shown · {scope}",
            transform=ax.transAxes, fontsize=9.3, color=MUTED_TEXT, va="bottom")

    if not tools_only:
        handles = [
            plt.Rectangle((0, 0), 1, 1, color=BAR_BLUE),
            plt.Rectangle((0, 0), 1, 1, color=PLUMBING_COLOR),
            plt.Rectangle((0, 0), 1, 1, color=OTHER_COLOR),
        ]
        ax.legend(handles, ["tool", "shell plumbing", "other (tail)"],
                  loc="center right", bbox_to_anchor=(0.995, 0.5), fontsize=9)

    out = out_dir / "executable_popularity.png"
    save_plot(fig, out)
    return out


def fig_by_provider(
    rows: list[dict],
    out_dir: Path,
    top: int,
    tools_only: bool,
    *,
    filename: str = "executable_popularity_by_provider.png",
    title: str = "What each agent runs — top executables, each panel ranked by its own share",
    xlabel: str = "share of this agent's shell executable invocations",
    xlabel_fontsize: float | None = None,
    ytick_fontsize: float | None = None,
    title_y: float = 0.995,
    layout_top: float = 0.965,
) -> Path:
    """Fig 2 — claude (left) and codex (right) as separate panels, each ranked by its OWN share."""
    data = [r for r in rows if not (tools_only and r["kind"] == "plumbing")]

    panels = []
    for prov, key in (("claude", "claude_share"), ("codex", "codex_share")):
        sub = sorted(data, key=lambda r: -r[key])[:top]
        panels.append({
            "prov": prov,
            "color": plot_color(prov, 0 if prov == "claude" else 1),
            "labels": [short_label(r["exe"], 18) for r in sub],
            "vals": [r[key] * 100 for r in sub],
        })
    xmax = max((max(p["vals"]) for p in panels if p["vals"]), default=1.0)

    fig, axes = plt.subplots(1, 2, figsize=(12.8, max(5.0, 0.32 * top + 1.9)))
    for ax, p in zip(axes, panels):
        y = list(range(len(p["vals"])))
        ax.barh(y, p["vals"], color=p["color"], height=0.76, edgecolor="white", linewidth=0.4)
        ax.set_yticks(y)
        ax.set_yticklabels(p["labels"], fontsize=ytick_fontsize)
        ax.invert_yaxis()
        for yi, v in zip(y, p["vals"]):
            ax.text(v + xmax * 0.012, yi, f"{v:.1f}%", va="center", ha="left",
                    fontsize=7.6, color=TEXT_COLOR)
        ax.set_xlim(0, xmax * 1.16)
        ax.xaxis.set_major_formatter(mticker.PercentFormatter(xmax=100, decimals=0))
        ax.set_xlabel(xlabel, fontsize=xlabel_fontsize)
        polish_axes(ax, grid_axis="x")
        ax.set_title(provider_title(p["prov"]), loc="left", pad=8, fontsize=13.5,
                     fontweight="semibold", color=p["color"])

    fig.suptitle(title, x=0.5, y=title_y, fontsize=14.5,
                 fontweight="semibold", color=TEXT_COLOR)
    fig.tight_layout(rect=(0, 0, 1, layout_top))

    out = out_dir / filename
    save_plot(fig, out)
    return out


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                   help=f"centralized dataset (default: {DEFAULT_INPUT.name})")
    p.add_argument("-o", "--output-dir", type=Path, default=EXP_DIR,
                   help="Where to write the CSV + PNGs (default: this folder).")
    p.add_argument("--top", type=int, default=45,
                   help="How many executables to show individually; the rest roll into a small "
                        "`other` bar (default: 45).")
    p.add_argument("--tools-only", action="store_true",
                   help="Drop shell plumbing (echo/cd/true/…) from the figures.")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"missing input: {args.input}\n(run classify_commands.py first)", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)

    comb, n_rows = load_dataset(args.input)
    csv_path = args.output_dir / "executable_popularity.csv"
    tot_c, tot_x = write_csv(comb, csv_path)
    tot = tot_c + tot_x

    rows = to_plot_rows(comb, tot_c, tot_x)
    f1 = fig_pooled(rows, args.output_dir, args.top, args.tools_only)
    f2 = fig_by_provider(rows, args.output_dir, args.top, args.tools_only)
    f3 = fig_by_provider(
        rows,
        args.output_dir,
        15,
        args.tools_only,
        filename="executable_popularity_top15_by_provider.png",
        title="Most popular shell executables — Top 15 for Claude and Codex",
        xlabel="Share of executable invocations",
        xlabel_fontsize=12,
        ytick_fontsize=11.5,
        title_y=0.975,
        layout_top=0.985,
    )

    png_sidecar.make_self_contained(
        args.output_dir,
        code_files=[Path(__file__), *png_sidecar.util_code_files()],
        readme_path=EXP_DIR / "README.md",
        png_names=[f1.name, f2.name, f3.name],
        data_glob="executable_popularity.csv",
    )

    print(f"rows read: {n_rows:,} calls  ->  {tot:,} executable occurrences "
          f"(claude {tot_c:,}  codex {tot_x:,})")
    print(f"  distinct executables: {len(comb):,}")
    top = sorted(comb.items(), key=lambda kv: -(kv[1]["claude"] + kv[1]["codex"]))[:15]
    for exe, v in top:
        t = v["claude"] + v["codex"]
        print(f"    {t:8,}  {t / tot:5.1%}  {v['kind']:<9} {exe}")
    print(f"wrote {csv_path.name} + 3 figures -> {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
