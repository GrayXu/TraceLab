"""Stream normalized-shaped rows from the canonical trace DuckDB."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))

import trace_db  # noqa: E402


DEFAULT_DB = REPO_ROOT / "trace" / "syfi_coding_trace.duckdb"
EPOCH = datetime(1970, 1, 1)


def timestamp_string(epoch_microseconds: int | None) -> str | None:
    if epoch_microseconds is None:
        return None
    moment = EPOCH + timedelta(microseconds=epoch_microseconds)
    return moment.isoformat(timespec="milliseconds") + "Z"


def grouped_children(cursor, field_names: tuple[str, ...]):
    current = cursor.fetchone()
    while current is not None:
        round_pk = current[0]
        children: list[dict[str, Any]] = []
        while current is not None and current[0] == round_pk:
            children.append(dict(zip(field_names, current[1:])))
            current = cursor.fetchone()
        yield round_pk, children


def iter_rounds(db_path: Path, *, include_tools: bool) -> Iterator[tuple[int, dict[str, Any]]]:
    round_connection = trace_db.connect(db_path, read_only=True)
    timing_connection = trace_db.connect(db_path, read_only=True)
    tool_connection = trace_db.connect(db_path, read_only=True) if include_tools else None
    try:
        round_cursor = round_connection.execute(
            """
            SELECT round_pk, provider, session_id, round_id, trace_key
            FROM rounds
            ORDER BY round_pk
            """
        )
        timing_cursor = timing_connection.execute(
            """
            SELECT
              round_pk,
              event_type,
              source,
              CAST(epoch_us(timestamp) AS BIGINT) AS timestamp_us,
              tool_call_id,
              tool_index
            FROM timing_events
            ORDER BY round_pk, event_index
            """
        )
        timing_groups = grouped_children(
            timing_cursor,
            ("event_type", "source", "timestamp_us", "tool_call_id", "tool_index"),
        )
        next_timing = next(timing_groups, None)

        tool_groups = None
        next_tools = None
        if tool_connection is not None:
            tool_cursor = tool_connection.execute(
                """
                SELECT
                  round_pk,
                  tool_index,
                  tool_name,
                  tool_call_id,
                  CAST(epoch_us(emitted_at) AS BIGINT) AS emitted_at_us,
                  CAST(epoch_us(result_at) AS BIGINT) AS result_at_us,
                  tool_wall_latency_ms,
                  tool_internal_latency_ms,
                  input_chars,
                  result_chars
                FROM tool_calls
                ORDER BY round_pk, tool_index
                """
            )
            tool_groups = grouped_children(
                tool_cursor,
                (
                    "tool_index",
                    "tool_name",
                    "tool_call_id",
                    "emitted_at_us",
                    "result_at_us",
                    "tool_wall_latency_ms",
                    "tool_internal_latency_ms",
                    "input_chars",
                    "result_chars",
                ),
            )
            next_tools = next(tool_groups, None)

        round_record = round_cursor.fetchone()
        while round_record is not None:
            round_pk, provider, session_id, round_id, trace_key = round_record
            timing_events: list[dict[str, Any]] = []
            if next_timing is not None and next_timing[0] == round_pk:
                timing_events = next_timing[1]
                next_timing = next(timing_groups, None)
            for event in timing_events:
                event["timestamp"] = timestamp_string(event.pop("timestamp_us"))

            tools: list[dict[str, Any]] = []
            if next_tools is not None and next_tools[0] == round_pk:
                tools = next_tools[1]
                next_tools = next(tool_groups, None) if tool_groups is not None else None
            for tool in tools:
                tool["emitted_at"] = timestamp_string(tool.pop("emitted_at_us"))
                tool["result_at"] = timestamp_string(tool.pop("result_at_us"))

            yield int(round_pk), {
                "provider": provider,
                "session_id": session_id,
                "round_id": round_id,
                "trace_key": trace_key,
                "timing_events": timing_events,
                "tools": tools,
            }
            round_record = round_cursor.fetchone()
    finally:
        round_connection.close()
        timing_connection.close()
        if tool_connection is not None:
            tool_connection.close()
