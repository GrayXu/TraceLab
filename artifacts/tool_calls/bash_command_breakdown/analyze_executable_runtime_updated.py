#!/usr/bin/env python3
"""Plot executable runtime using the full observable Codex command lifecycle.

The original runtime figure uses each shell tool call's effective latency. That is correct for an
individual interaction, but a Codex ``exec_command`` can return ``running`` and finish only through
a later ``write_stdin``. This companion analysis keeps the same single-executable attribution and
plot design while replacing a Codex root call's latency with:

    initial exec_command.emitted_at -> first linked result with command_status == "finished"

The lifecycle is reconstructed by ``artifacts/utils/command_chains.py``. Aborted, failed, still
running, and otherwise incomplete chains have no observed finish and are excluded. Claude and
non-``exec_command`` shell tools keep their effective per-call latency.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[2]
UTILS_DIR = REPO_ROOT / "artifacts" / "utils"
sys.path.insert(0, str(UTILS_DIR))
sys.path.insert(0, str(EXP_DIR))

import png_sidecar  # noqa: E402
import trace_db  # noqa: E402
from command_chains import command_chains  # noqa: E402
from style import provider_order  # noqa: E402
from analyze_executable_runtime import (  # noqa: E402
    plot_runtime,
    plot_total_latency_top15,
    write_summary_csv,
)

DEFAULT_COMMANDS = EXP_DIR / "command_calls.jsonl"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_COMMANDS,
        help=f"centralized command dataset (default: {DEFAULT_COMMANDS.name})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        required=True,
        help="DuckDB materialized from the same normalized trace as command_calls.jsonl",
    )
    parser.add_argument("-o", "--output-dir", type=Path, default=EXP_DIR)
    parser.add_argument("--min-calls", type=int, default=30)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--tools-only", action="store_true")
    return parser.parse_args(argv)


def load_finished_command_wall_times(con) -> tuple[dict[tuple, float], Counter]:
    """Return scoped root-id -> observed wall milliseconds plus final-status coverage."""
    cursor = command_chains(con)
    columns = [item[0] for item in cursor.description]
    wall_times: dict[tuple, float] = {}
    statuses: Counter = Counter()
    for values in cursor.fetchall():
        row = dict(zip(columns, values, strict=True))
        status = row.get("final_status")
        statuses[status or "<unknown>"] += 1
        wall_ms = row.get("wall_time_until_finished_ms")
        if status != "finished" or wall_ms is None:
            continue
        key = (
            row.get("provider"),
            row.get("user"),
            row.get("session_id"),
            row.get("initial_tool_call_id"),
        )
        wall_times[key] = float(wall_ms)
    return wall_times, statuses


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"missing input: {args.input}\n(run classify_commands.py first)", file=sys.stderr)
        return 2
    if not args.db.exists():
        print(f"missing DuckDB: {args.db}", file=sys.stderr)
        return 2

    con = trace_db.connect(args.db, read_only=True)
    try:
        command_wall_ms, chain_statuses = load_finished_command_wall_times(con)
    finally:
        con.close()

    lat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    kind: dict[str, str] = {}
    total_calls: Counter = Counter()
    single_calls: Counter = Counter()
    excluded_incomplete: Counter = Counter()
    missing_latency: Counter = Counter()

    with args.input.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            rec = json.loads(line)
            provider = rec.get("provider")
            total_calls[provider] += 1
            if rec.get("n_exe") != 1 or rec.get("executable_parse_status") != "success":
                continue
            single_calls[provider] += 1

            if provider == "codex" and rec.get("tool_name") == "exec_command":
                key = (
                    provider,
                    rec.get("user"),
                    rec.get("session_id"),
                    rec.get("tool_call_id"),
                )
                ms = command_wall_ms.get(key)
                if ms is None:
                    excluded_incomplete[provider] += 1
                    continue
            else:
                ms = rec.get("latency_ms")
                if ms is None:
                    missing_latency[provider] += 1
                    continue

            if ms <= 0:
                ms = 1.0
            executable = rec["executables"][0]
            lat[provider][executable].append(float(ms))
            kinds = rec.get("kinds") or []
            kind[executable] = kinds[0] if kinds else "tool"

    coverage = {
        provider: {
            "total": total_calls[provider],
            "timed": sum(len(values) for values in lat[provider].values()),
        }
        for provider in total_calls
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "executable_runtime_updated.csv"
    write_summary_csv(lat, kind, csv_path)
    png = plot_runtime(
        lat,
        kind,
        coverage,
        args.output_dir,
        args.min_calls,
        args.top,
        args.tools_only,
        figure_title="Executable runtime — observable full-command duration",
        x_label="observable command duration (log scale)",
        subtitle_suffix=(
            "Claude: effective call latency; Codex: exec start → first finished result; "
            "incomplete/multi-exe excluded"
        ),
        output_name="executable_runtime_updated.png",
    )
    total_csv, total_png = plot_total_latency_top15(
        lat,
        kind,
        args.output_dir,
        args.tools_only,
        stem="executable_total_latency_updated_top15",
        figure_title="Summed completed-command time — Top 15 shell executables",
        x_label="Summed completion time (hours)",
    )

    png_sidecar.make_self_contained(
        args.output_dir,
        code_files=[
            Path(__file__),
            EXP_DIR / "analyze_executable_runtime.py",
            *png_sidecar.util_code_files(),
        ],
        readme_path=EXP_DIR / "README.md",
        png_names=[png.name],
        data_glob=csv_path.name,
    )
    png_sidecar.make_self_contained(
        args.output_dir,
        code_files=[
            Path(__file__),
            EXP_DIR / "analyze_executable_runtime.py",
            *png_sidecar.util_code_files(),
        ],
        readme_path=EXP_DIR / "README.md",
        png_names=[total_png.name],
        data_glob=total_csv.name,
    )

    print(f"Codex command-chain final statuses: {dict(chain_statuses)}", file=sys.stderr)
    for provider in provider_order(lat):
        timed = sum(len(values) for values in lat[provider].values())
        print(
            f"{provider:8s} command calls={total_calls[provider]:>9,}  "
            f"single-exe={single_calls[provider]:>9,}  updated-timed={timed:>9,}  "
            f"incomplete={excluded_incomplete[provider]:,}  "
            f"missing-latency={missing_latency[provider]:,}",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
