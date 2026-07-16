#!/usr/bin/env python3
"""Export stored command executable facts and print compact coverage statistics.

Executable parsing and privacy sanitization happen once in ``scripts/sanitize_round_trace.py``.
This artifact deliberately does not inspect raw command inputs; it only queries the public DuckDB.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[2]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import trace_db  # noqa: E402
from executable_facts import COMMAND_TOOL_NAMES, kind_of  # noqa: E402

DEFAULT_INPUT = REPO_ROOT / "trace" / "llm_round_trace_v2.merged.all_users.public.jsonl"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    trace_db.add_db_args(parser, default_output_dir=EXP_DIR)
    parser.set_defaults(input=DEFAULT_INPUT)
    parser.add_argument(
        "--tools",
        default=",".join(sorted(COMMAND_TOOL_NAMES)),
        help="Comma-separated command-launch tool names.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tool_names = [name.strip() for name in args.tools.split(",") if name.strip()]
    if not tool_names:
        print("--tools must contain at least one tool name", file=sys.stderr)
        return 2

    con = trace_db.open_from_args(args)
    placeholders = ", ".join("?" for _ in tool_names)
    query = f"""
        SELECT
          r.provider, r."user", r.project, r.trace_key, r.session_id, r.round_index,
          tc.tool_index, tc.tool_call_id, tc.tool_name,
          {trace_db.EFFECTIVE_TOOL_LATENCY_MS_SQL} AS latency_ms,
          tc.executables, tc.executable_parse_status, tc.executable_parse_reason,
          tc.command_skeleton
        FROM tool_calls tc
        JOIN rounds r USING (round_pk)
        WHERE tc.tool_name IN ({placeholders})
        ORDER BY r.ingest_seq, tc.tool_index
    """
    cursor = con.execute(query, tool_names)
    columns = [item[0] for item in cursor.description]

    status_by_provider: dict[str, Counter[str]] = defaultdict(Counter)
    occurrences: Counter[str] = Counter()
    no_latency: Counter[str] = Counter()
    written = 0
    output = args.output_dir / "command_calls.jsonl"
    with output.open("w", encoding="utf-8") as fh:
        while rows := cursor.fetchmany(10_000):
            for values in rows:
                record = dict(zip(columns, values, strict=True))
                labels = record.get("executables") or []
                record["executables"] = labels
                record["kinds"] = [kind_of(label) for label in labels]
                record["n_exe"] = len(labels)
                provider = str(record.get("provider") or "unknown")
                status = str(record.get("executable_parse_status") or "missing")
                status_by_provider[provider][status] += 1
                occurrences[provider] += len(labels)
                if record.get("latency_ms") is None:
                    no_latency[provider] += 1
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                written += 1
    con.close()

    print(f"wrote {written:,} command calls -> {output}")
    for provider in sorted(status_by_provider):
        status = status_by_provider[provider]
        print(
            f"{provider}: {sum(status.values()):,} calls; {occurrences[provider]:,} executable "
            f"occurrences; success={status['success']:,}, partial={status['partial']:,}, "
            f"failed={status['failed']:,}, missing={status['missing']:,}; "
            f"no latency={no_latency[provider]:,}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
