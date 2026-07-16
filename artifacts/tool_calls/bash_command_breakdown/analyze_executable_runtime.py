#!/usr/bin/env python3
"""Analyze executable runtime — per-executable latency box plots over single-executable calls.

Where ``analyze_popularity.py`` answers *what* each agent runs, this answers *how long each
executable takes*. Reads only the centralized dataset ``command_calls.jsonl`` (from
``classify_commands.py``): every row already carries its executables, source, and latency, so this
is a fast read — no trace, no DuckDB, no re-classification.

To attribute one latency to one executable we keep only **single-executable calls** and drop
pipelines/chains (their wall time can't be split across stages). A single-executable call is a
row with ``n_exe == 1`` and ``source == "deterministic"`` — the parser read exactly one executable
(a plain ``pytest`` / ``grep x f`` / ``docker build …`` / a ``python - <<PY`` heredoc script). The
``partial`` and ``unresolved`` rows are excluded: partial ran other things too, unresolved names none.

Latency floor: Codex reports wall time in whole seconds (parsed from a ``Wall time: N seconds`` line),
so a sub-second command reads ``0 seconds`` → 0 ms. Rather than drop those fast calls (which would
bias Codex medians upward), we **floor 0 ms to 1 ms** so they stay in as the very-fastest bucket.
Only a genuinely absent latency is excluded.

Box plots mirror ``artifacts/tool_calls/tool_latency_distribution``.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[2]  # experiment -> category -> artifacts -> repo root
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))

import png_sidecar  # noqa: E402
from style import (  # noqa: E402
    BOX_EDGE,
    BOX_FACE,
    MUTED_TEXT,
    TEXT_COLOR,
    mticker,
    plt,
    polish_axes,
    provider_order,
    provider_title,
    plot_color,
    save_plot,
    short_label,
)
from formatters import format_latency_tick, latency_ticks  # noqa: E402

DEFAULT_INPUT = EXP_DIR / "command_calls.jsonl"


def _boxplot(ax, data, labels, **kw):
    """boxplot with the label kwarg that this matplotlib understands (renamed in 3.9)."""
    try:
        return ax.boxplot(data, tick_labels=labels, **kw)
    except TypeError:
        return ax.boxplot(data, labels=labels, **kw)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                   help=f"centralized dataset (default: {DEFAULT_INPUT.name})")
    p.add_argument("-o", "--output-dir", type=Path, default=EXP_DIR)
    p.add_argument("--min-calls", type=int, default=30,
                   help="Min single-exe timed calls for an executable to appear (default: 30).")
    p.add_argument("--top", type=int, default=30, help="Cap executables per provider panel (default: 30).")
    p.add_argument("--tools-only", action="store_true", help="Drop shell plumbing (cd/echo/…).")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"missing input: {args.input}\n(run classify_commands.py first)", file=sys.stderr)
        return 2

    # provider -> exe -> [latency_ms]
    lat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    kind: dict[str, str] = {}
    total_calls: Counter = Counter()
    single_calls: Counter = Counter()
    no_latency: Counter = Counter()

    with args.input.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            prov = rec.get("provider")
            total_calls[prov] += 1
            if rec.get("n_exe") != 1 or rec.get("source") != "deterministic":
                continue  # multi-executable, opaque, or partial — can't attribute one runtime
            single_calls[prov] += 1
            ms = rec.get("latency_ms")
            if ms is None:
                no_latency[prov] += 1
                continue
            if ms <= 0:
                ms = 1.0  # 0-second commands (Codex reports wall time in whole seconds; a sub-second
                          # call reads "Wall time: 0 seconds") -> floor to 1 ms so they stay in, not drop
            exe = rec["executables"][0]
            lat[prov][exe].append(float(ms))
            kinds = rec.get("kinds") or []
            kind[exe] = kinds[0] if kinds else "tool"

    # per-provider coverage: how many single-executable calls (of all calls) actually feed the boxes
    coverage = {
        prov: {
            "total": total_calls[prov],
            "timed": sum(len(v) for v in lat[prov].values()),  # single-exe, deterministic, latency>0
        }
        for prov in total_calls
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "executable_runtime.csv"
    write_summary_csv(lat, kind, csv_path)
    png = plot_runtime(lat, kind, coverage, args.output_dir, args.min_calls, args.top, args.tools_only)
    total_csv, total_png = plot_total_latency_top15(
        lat,
        kind,
        args.output_dir,
        args.tools_only,
        stem="executable_total_latency_top15",
        figure_title="Summed raw latency — Top 15 shell executables",
        x_label="Summed raw latency (hours)",
    )

    png_sidecar.make_self_contained(
        args.output_dir,
        code_files=[Path(__file__), *png_sidecar.util_code_files()],
        readme_path=EXP_DIR / "README.md",
        png_names=[png.name],
        data_glob="executable_runtime.csv",
    )
    png_sidecar.make_self_contained(
        args.output_dir,
        code_files=[Path(__file__), *png_sidecar.util_code_files()],
        readme_path=EXP_DIR / "README.md",
        png_names=[total_png.name],
        data_glob=total_csv.name,
    )

    for prov in provider_order(lat):
        timed = sum(len(v) for v in lat[prov].values())
        print(f"{prov:8s} command calls={total_calls[prov]:>9,}  "
              f"single-exe={single_calls[prov]:>9,}  timed-single={timed:>9,}  "
              f"(no-latency {no_latency[prov]:,})", file=sys.stderr)
    return 0


def write_summary_csv(lat, kind, out: Path) -> None:
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["provider", "executable", "kind", "calls", "p50_ms", "p90_ms", "p99_ms",
                    "mean_ms", "min_ms", "max_ms"])
        for prov in provider_order(lat):
            for exe, vals in sorted(lat[prov].items(), key=lambda kv: -len(kv[1])):
                arr = np.array(vals, dtype=float)
                p50, p90, p99 = np.percentile(arr, [50, 90, 99])
                w.writerow([
                    prov, exe, kind.get(exe, "tool"), len(vals),
                    f"{p50:.0f}", f"{p90:.0f}", f"{p99:.0f}", f"{arr.mean():.0f}",
                    f"{arr.min():.0f}", f"{arr.max():.0f}",
                ])
    print(f"Saved {out}", file=sys.stderr)


def plot_total_latency_top15(
    lat,
    kind,
    out_dir: Path,
    tools_only: bool,
    *,
    stem: str,
    figure_title: str,
    x_label: str,
    top: int = 15,
) -> tuple[Path, Path]:
    """Write and plot each provider's top executables by summed attributable latency."""
    panels = []
    rows = []
    for prov in provider_order(lat):
        eligible = [
            (exe, vals, float(sum(vals)))
            for exe, vals in lat[prov].items()
            if not (tools_only and kind.get(exe) == "plumbing")
        ]
        eligible.sort(key=lambda item: (-item[2], item[0]))
        provider_total_ms = sum(item[2] for item in eligible)
        shown = eligible[:top]
        panels.append((prov, shown, len(eligible), provider_total_ms))
        for rank, (exe, vals, total_ms) in enumerate(shown, 1):
            rows.append(
                {
                    "provider": prov,
                    "rank": rank,
                    "executable": exe,
                    "kind": kind.get(exe, "tool"),
                    "calls": len(vals),
                    "total_latency_ms": total_ms,
                    "total_latency_hours": total_ms / 3_600_000,
                    "share_of_attributable_latency": (
                        total_ms / provider_total_ms if provider_total_ms else 0.0
                    ),
                }
            )

    csv_path = out_dir / f"{stem}.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        fieldnames = [
            "provider",
            "rank",
            "executable",
            "kind",
            "calls",
            "total_latency_ms",
            "total_latency_hours",
            "share_of_attributable_latency",
        ]
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Saved {csv_path}", file=sys.stderr)

    fig, axes = plt.subplots(1, len(panels), figsize=(13.6, 7.0), squeeze=False)
    for panel_index, (ax, (prov, shown, distinct, provider_total_ms)) in enumerate(
        zip(axes.ravel(), panels, strict=True)
    ):
        labels = [f"{short_label(exe, 20)}  (n={len(vals):,})" for exe, vals, _ in shown]
        hours = [total_ms / 3_600_000 for _exe, _vals, total_ms in shown]
        y = list(range(len(shown)))
        color = plot_color(prov, panel_index)
        bars = ax.barh(y, hours, color=color, alpha=0.82, height=0.76)
        ax.set_yticks(y)
        ax.set_yticklabels(labels, fontsize=11.5)
        ax.invert_yaxis()
        xmax = max(hours, default=1.0)
        ax.set_xlim(0, xmax * 1.21)
        for bar, value in zip(bars, hours, strict=True):
            ax.text(
                value + xmax * 0.015,
                bar.get_y() + bar.get_height() / 2,
                f"{value:,.1f}h",
                va="center",
                fontsize=9.2,
                color=TEXT_COLOR,
            )
        ax.set_title(
            f"{provider_title(prov)} — top {len(shown)} of {distinct:,} · "
            f"{provider_total_ms / 3_600_000:,.1f}h total",
            loc="left",
            pad=8,
            fontsize=14,
            color=color,
        )
        ax.set_xlabel(x_label, fontsize=13, labelpad=8)
        ax.tick_params(axis="x", labelsize=11)
        polish_axes(ax, grid_axis="x")

    fig.suptitle(figure_title, y=0.992, fontsize=16, fontweight="semibold")
    fig.tight_layout(rect=(0, 0, 1, 0.982), w_pad=2.3)
    png_path = out_dir / f"{stem}.png"
    save_plot(fig, png_path)
    return csv_path, png_path


def plot_runtime(
    lat,
    kind,
    coverage,
    out_dir: Path,
    min_calls: int,
    top: int,
    tools_only: bool,
    *,
    figure_title: str = "Executable runtime — single-executable shell calls",
    x_label: str = "runtime per call (log scale)",
    subtitle_suffix: str = (
        "0-second commands floored to 1 ms; multi-exe pipelines & chains excluded"
    ),
    output_name: str = "executable_runtime.png",
) -> Path:
    providers = provider_order(lat)
    panels = []
    global_max = 1.0
    for prov in providers:
        eligible = [
            (exe, vals) for exe, vals in lat[prov].items()
            if len(vals) >= min_calls and not (tools_only and kind.get(exe) == "plumbing")
        ]
        # keep the most-common executables (>= min_calls, top N by call count), then order the
        # panel by median runtime so the boxes form a slow -> fast cascade
        eligible.sort(key=lambda ev: -len(ev[1]))
        shown = eligible[:top]
        # slowest median at top; break median ties by mean (after the 0-second→1 ms floor many fast
        # executables share a 1 ms median, so the mean gives a meaningful secondary ordering)
        shown.sort(key=lambda ev: (-float(np.median(ev[1])), -float(np.mean(ev[1]))))
        dropped = len(lat[prov]) - len(shown)
        panels.append((prov, shown, dropped))
        for _exe, vals in shown:
            global_max = max(global_max, max(vals))

    panel_heights = [max(2.8, 0.34 * len(shown) + 1.7) for _p, shown, _d in panels]
    fig, axes = plt.subplots(len(panels), 1, figsize=(14.0, sum(panel_heights)),
                             squeeze=False, gridspec_kw={"height_ratios": panel_heights})
    fig.suptitle(figure_title, y=0.998, fontsize=17)

    for ax, (prov, shown, dropped) in zip(axes.ravel(), panels):
        data = [vals for _exe, vals in shown]
        labels = [f"{short_label(exe, 26)}  (n={len(vals):,})" for exe, vals in shown]
        ax.set_xscale("log")
        ax.set_xticks(latency_ticks(global_max))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(format_latency_tick))
        ax.xaxis.set_minor_formatter(mticker.NullFormatter())
        polish_axes(ax, grid_axis="x", minor=True)

        box = _boxplot(ax, data, labels, vert=False, showfliers=False, whis=(5, 95),
                       patch_artist=True, widths=0.62,
                       medianprops={"color": TEXT_COLOR, "linewidth": 1.4},
                       whiskerprops={"color": MUTED_TEXT, "linewidth": 1.0},
                       capprops={"color": MUTED_TEXT, "linewidth": 1.0})
        for patch in box["boxes"]:
            patch.set_facecolor(BOX_FACE)
            patch.set_edgecolor(BOX_EDGE)
            patch.set_alpha(0.82)
            patch.set_linewidth(1.0)
        ax.invert_yaxis()  # slowest median at the top

        title = (f"{provider_title(prov)} — {len(shown)} executables by median runtime "
                 f"(≥{min_calls} single-exe calls)")
        if dropped > 0:
            title += f"; {dropped} rarer not shown"
        cov = coverage.get(prov, {})
        total, timed = cov.get("total", 0), cov.get("timed", 0)
        frac = (timed / total) if total else 0.0
        ax.text(0.0, 1.05, title, transform=ax.transAxes, ha="left", va="bottom",
                fontsize=13.5, fontweight="semibold", color=TEXT_COLOR)
        ax.text(0.0, 1.01,
                f"single-executable calls only — {timed:,} of {total:,} ({frac:.0%}); "
                f"{subtitle_suffix}",
                transform=ax.transAxes, ha="left", va="bottom", fontsize=9.3, color=MUTED_TEXT)
        ax.set_xlabel(x_label, fontsize=12.5, labelpad=8)
        ax.tick_params(axis="y", labelsize=11)

    fig.tight_layout(rect=(0, 0, 1, 0.99), h_pad=2.4)
    out = out_dir / output_name
    save_plot(fig, out)
    return out


if __name__ == "__main__":
    raise SystemExit(main())
