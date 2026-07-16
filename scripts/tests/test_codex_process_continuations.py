from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))

from extract_codex_rounds import extract_codex_session  # noqa: E402
from sanitize_round_trace import StableIdSanitizer, sanitize_row  # noqa: E402
from trace_db import connect, materialize  # noqa: E402


def _record(timestamp: str, top_type: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "type": top_type, "payload": payload}


def _token_count(timestamp: str, sequence: int) -> dict:
    usage = {
        "input_tokens": 100 + sequence,
        "cached_input_tokens": 80,
        "output_tokens": 10,
        "reasoning_output_tokens": 2,
        "total_tokens": 110 + sequence,
    }
    return _record(
        timestamp,
        "event_msg",
        {
            "type": "token_count",
            "info": {
                "last_token_usage": usage,
                "total_token_usage": {**usage, "total_tokens": 1_000 + sequence},
            },
        },
    )


def _continued_session_records(final_output: str) -> list[dict]:
    records = [
        _record(
            "2026-01-01T00:00:00.000Z",
            "session_meta",
            {"id": "11111111-1111-1111-1111-111111111111", "cwd": "/private/repo"},
        ),
        _record(
            "2026-01-01T00:00:00.000Z",
            "turn_context",
            {"turn_id": "22222222-2222-2222-2222-222222222222", "model": "codex-test"},
        ),
        _record(
            "2026-01-01T00:00:00.000Z",
            "response_item",
            {
                "type": "function_call",
                "call_id": "call_root",
                "name": "exec_command",
                "arguments": json.dumps({"cmd": "long-command", "yield_time_ms": 1_000}),
            },
        ),
        _token_count("2026-01-01T00:00:00.100Z", 1),
        _record(
            "2026-01-01T00:00:01.000Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_root",
                "output": (
                    "Chunk ID: abc\nWall time: 1.0000 seconds\n"
                    "Process running with session ID 74219\nOutput:\n"
                ),
            },
        ),
        _record(
            "2026-01-01T00:00:02.000Z",
            "response_item",
            {
                "type": "function_call",
                "call_id": "call_poll",
                "name": "write_stdin",
                "arguments": json.dumps(
                    {"session_id": 74219, "chars": "", "yield_time_ms": 2_000}
                ),
            },
        ),
        _token_count("2026-01-01T00:00:02.100Z", 2),
        _record(
            "2026-01-01T00:00:04.000Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_poll",
                "output": (
                    "Chunk ID: def\nWall time: 2.0000 seconds\n"
                    "Process running with session ID 74219\nOutput:\nstill working\n"
                ),
            },
        ),
        _record(
            "2026-01-01T00:00:05.000Z",
            "response_item",
            {
                "type": "function_call",
                "call_id": "call_finish",
                "name": "write_stdin",
                "arguments": json.dumps(
                    {"session_id": 74219, "chars": "", "yield_time_ms": 3_000}
                ),
            },
        ),
        _token_count("2026-01-01T00:00:05.100Z", 3),
        _record(
            "2026-01-01T00:00:08.000Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_finish",
                "output": final_output,
            },
        ),
    ]
    if "Process exited with code" in final_output:
        # Codex may emit this duplicate runner event after the function result. It must not replace
        # the terminal write_stdin timestamp chosen above.
        records.append(
            _record(
                "2026-01-01T00:00:08.100Z",
                "event_msg",
                {
                    "type": "exec_command_end",
                    "call_id": "call_root",
                    "exit_code": 0,
                    "aggregated_output": "done\n",
                },
            )
        )
    return records


def _extract(tmp_path: Path, records: list[dict]) -> list[dict]:
    session = tmp_path / "session.jsonl"
    session.write_text("".join(json.dumps(record) + "\n" for record in records))
    return extract_codex_session(session)


def test_immediate_exec_records_its_own_terminal_span(tmp_path: Path) -> None:
    records = _continued_session_records("")[:4]
    records.append(
        _record(
            "2026-01-01T00:00:01.000Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_root",
                "output": (
                    "Chunk ID: abc\nWall time: 1.0000 seconds\n"
                    "Process exited with code 3\nOutput:\nfailed\n"
                ),
            },
        )
    )
    # A replayed running result after the exit must not regress the root back to running.
    records.append(
        _record(
            "2026-01-01T00:00:01.100Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_root",
                "output": (
                    "Chunk ID: stale\nWall time: 1.0000 seconds\n"
                    "Process running with session ID 74219\nOutput:\n"
                ),
            },
        )
    )
    root = _extract(tmp_path, records)[0]["tools"][0]

    assert root["process_session_id"] is None
    assert root["root_tool_call_id"] == "call_root"
    assert root["process_state"] == "exited"
    assert root["process_exit_code"] == 3
    assert root["process_finished_at"] == "2026-01-01T00:00:01.000Z"
    assert root["process_total_wall_latency_ms"] == 1_000
    assert root["is_error"] is True


def test_continued_exec_is_linked_and_backfilled(tmp_path: Path) -> None:
    rounds = _extract(
        tmp_path,
        _continued_session_records(
            "Chunk ID: ghi\nWall time: 3.0000 seconds\n"
            "Process exited with code 0\nOutput:\ndone\n"
        ),
    )

    root = rounds[0]["tools"][0]
    poll = rounds[1]["tools"][0]
    finish = rounds[2]["tools"][0]

    assert root["process_session_id"] == "74219"
    assert root["root_tool_call_id"] == "call_root"
    assert root["process_state"] == "exited"
    assert root["process_exit_code"] == 0
    assert root["process_finished_at"] == "2026-01-01T00:00:08.000Z"
    assert root["process_total_wall_latency_ms"] == 8_000
    assert root["tool_wall_latency_ms"] == 1_000
    assert root["is_error"] is False

    assert poll["process_session_id"] == "74219"
    assert poll["root_tool_call_id"] == "call_root"
    assert poll["process_state"] == "running"
    assert finish["process_session_id"] == "74219"
    assert finish["root_tool_call_id"] == "call_root"
    assert finish["process_state"] == "exited"
    assert finish["process_exit_code"] == 0


def test_exec_command_end_before_running_result_is_only_provisional(tmp_path: Path) -> None:
    records = _continued_session_records(
        "Chunk ID: ghi\nWall time: 3.0000 seconds\n"
        "Process exited with code 0\nOutput:\ndone\n"
    )
    records.insert(
        4,
        _record(
            "2026-01-01T00:00:00.900Z",
            "event_msg",
            {
                "type": "exec_command_end",
                "call_id": "call_root",
                "exit_code": 0,
                "aggregated_output": "initial command interaction ended\n",
            },
        ),
    )

    rounds = _extract(tmp_path, records)
    root = rounds[0]["tools"][0]
    finish = rounds[2]["tools"][0]

    assert root["process_session_id"] == "74219"
    assert root["process_state"] == "exited"
    assert root["process_exit_code"] == 0
    assert root["process_finished_at"] == "2026-01-01T00:00:08.000Z"
    assert root["process_total_wall_latency_ms"] == 8_000
    assert finish["root_tool_call_id"] == "call_root"


def test_continuation_error_does_not_guess_a_finish(tmp_path: Path) -> None:
    rounds = _extract(
        tmp_path,
        _continued_session_records(
            "write_stdin failed: stdin is closed for this session; "
            "rerun exec_command with tty=true to keep stdin open"
        ),
    )
    root = rounds[0]["tools"][0]
    failed_write = rounds[2]["tools"][0]

    assert root["process_state"] == "running"
    assert root["process_finished_at"] is None
    assert root["process_total_wall_latency_ms"] is None
    assert failed_write["process_state"] == "continuation_error"
    assert failed_write["root_tool_call_id"] == "call_root"


def test_sanitizer_preserves_links_with_pseudonymous_ids(tmp_path: Path) -> None:
    rounds = _extract(
        tmp_path,
        _continued_session_records(
            "Chunk ID: ghi\nWall time: 3.0000 seconds\n"
            "Process exited with code 7\nOutput:\nfailed\n"
        ),
    )
    # One sanitizer instance preserves identifier relationships across every row in the trace.
    ids = StableIdSanitizer("test-seed")
    sanitized = [sanitize_row(row, ids) for row in rounds]
    root = sanitized[0]["tools"][0]
    poll = sanitized[1]["tools"][0]
    finish = sanitized[2]["tools"][0]

    assert "input" not in root and "input" not in poll and "input" not in finish
    assert root["process_session_id"] != "74219"
    assert root["process_session_id"] == poll["process_session_id"]
    assert poll["process_session_id"] == finish["process_session_id"]
    assert root["root_tool_call_id"] == root["tool_call_id"]
    assert poll["root_tool_call_id"] == root["tool_call_id"]
    assert finish["root_tool_call_id"] == root["tool_call_id"]
    assert root["process_exit_code"] == 7
    assert root["process_total_wall_latency_ms"] == 8_000

    other_session = json.loads(json.dumps(rounds[0]))
    other_session["session_id"] = "codex:unrelated-session"
    other = sanitize_row(other_session, ids)["tools"][0]
    assert other["process_session_id"] != root["process_session_id"]


def test_duckdb_keeps_process_linkage_scalars(tmp_path: Path) -> None:
    rounds = _extract(
        tmp_path,
        _continued_session_records(
            "Chunk ID: ghi\nWall time: 3.0000 seconds\n"
            "Process exited with code 0\nOutput:\ndone\n"
        ),
    )
    trace = tmp_path / "normalized.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rounds))
    db = tmp_path / "trace.duckdb"
    materialize(trace, db)

    con = connect(db, read_only=True)
    try:
        root = con.execute(
            "SELECT process_session_id, process_state, process_exit_code, root_tool_call_id, "
            "process_finished_at, process_total_wall_latency_ms "
            "FROM tool_calls WHERE tool_call_id = 'call_root'"
        ).fetchone()
    finally:
        con.close()

    assert root[:4] == ("74219", "exited", 0, "call_root")
    assert root[4] is not None
    assert root[5] == 8_000
