from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))

from extract_codex_rounds import (  # noqa: E402
    command_observation,
    extract_codex_session,
    infer_error_from_output,
)
from command_chains import command_chains  # noqa: E402
from sanitize_round_trace import StableIdSanitizer, sanitize_row  # noqa: E402
from executable_facts import extract_executable_facts  # noqa: E402
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


def test_model_less_replay_is_not_emitted_or_carried_into_live_round(
    tmp_path: Path,
) -> None:
    records = [
        _record(
            "2026-01-01T00:00:00.000Z",
            "session_meta",
            {"id": "11111111-1111-1111-1111-111111111111", "cwd": "/private/repo"},
        ),
        _record(
            "2026-01-01T00:00:00.010Z",
            "event_msg",
            {"type": "user_message", "message": "replayed parent input"},
        ),
        _token_count("2026-01-01T00:00:00.100Z", 1),
        _record(
            "2026-01-01T00:00:01.000Z",
            "turn_context",
            {
                "turn_id": "22222222-2222-2222-2222-222222222222",
                "model": "codex-test",
            },
        ),
        _record(
            "2026-01-01T00:00:01.010Z",
            "event_msg",
            {"type": "user_message", "message": "live child input"},
        ),
        _token_count("2026-01-01T00:00:01.100Z", 2),
    ]

    rounds = _extract(tmp_path, records)

    assert len(rounds) == 1
    assert rounds[0]["model"] == "codex-test"
    assert rounds[0]["current_user_message_count"] == 1
    assert rounds[0]["current_user_message_chars"] == len("live child input")


def test_special_command_result_formats_are_classified() -> None:
    aborted = "aborted by user after 7.2s"
    failed = "exec_command failed: CreateProcess { message: \"rejected\" }"
    wrapped_success = (
        "Wall time: 0.0000 seconds\nOutput:\n"
        '{"output":"Success","metadata":{"exit_code":0,"duration_seconds":0.0}}'
    )

    assert command_observation(aborted, "exec_command") == ("aborted", None, None)
    assert infer_error_from_output(aborted) is True
    assert command_observation(failed, "exec_command") == ("failed", None, None)
    assert infer_error_from_output(failed) is True
    assert command_observation(wrapped_success, "exec_command") == ("finished", None, 0)
    assert infer_error_from_output(wrapped_success) is False


def test_immediate_exec_records_finished_status(tmp_path: Path) -> None:
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

    assert "continuation_of_tool_call_id" not in root
    assert root["command_status"] == "finished"
    assert root["command_exit_code"] == 3
    assert root["is_error"] is True


def test_continued_exec_keeps_per_call_status_and_links(tmp_path: Path) -> None:
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

    assert "continuation_of_tool_call_id" not in root
    assert root["command_status"] == "running"
    assert "command_exit_code" not in root
    assert root["tool_wall_latency_ms"] == 1_000
    assert root["is_error"] is False

    assert poll["continuation_of_tool_call_id"] == "call_root"
    assert poll["command_status"] == "running"
    assert finish["continuation_of_tool_call_id"] == "call_root"
    assert finish["command_status"] == "finished"
    assert finish["command_exit_code"] == 0


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

    assert root["command_status"] == "running"
    assert "command_exit_code" not in root
    assert finish["continuation_of_tool_call_id"] == "call_root"
    assert finish["command_status"] == "finished"


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

    assert root["command_status"] == "running"
    assert failed_write["command_status"] == "session_error"
    assert failed_write["is_error"] is True
    assert failed_write["continuation_of_tool_call_id"] == "call_root"


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
    assert "continuation_of_tool_call_id" not in root
    assert poll["continuation_of_tool_call_id"] == root["tool_call_id"]
    assert finish["continuation_of_tool_call_id"] == root["tool_call_id"]
    assert root["command_status"] == "running"
    assert finish["command_status"] == "finished"
    assert finish["command_exit_code"] == 7
    assert "_process_session_id" not in root
    assert "_process_session_id" not in poll
    assert "_process_session_id" not in finish


def test_sanitizer_adds_privacy_safe_executable_facts() -> None:
    row = {
        "provider": "codex",
        "tools": [
            {
                "tool_name": "exec_command",
                "tool_call_id": "call_command",
                "input": {"cmd": "git status && vibe-serve | python script.py"},
            },
            {
                "tool_name": "write_stdin",
                "tool_call_id": "call_wait",
                "input": {"session_id": 123, "chars": ""},
            },
        ],
    }
    sanitized = sanitize_row(row, StableIdSanitizer("test-seed"))
    command, continuation = sanitized["tools"]

    assert command["executables"] == ["git", "custom_1", "python-script"]
    assert command["executable_parse_status"] == "success"
    assert command["executable_parse_reason"] is None
    assert command["command_skeleton"] == "git && custom_1 | python-script"
    assert "input" not in command
    assert "executables" not in continuation
    assert "command_skeleton" not in continuation
    assert "input" not in continuation


def test_executable_facts_keep_structure_and_report_failures() -> None:
    structured = extract_executable_facts(
        {"cmd": "make || echo nope; for item in a; do ls; done"}
    )
    malformed = extract_executable_facts({"cmd": "echo '"})

    assert structured == {
        "executables": ["make", "echo", "ls"],
        "executable_parse_status": "success",
        "executable_parse_reason": None,
        "command_skeleton": "make || echo ; <loops>",
    }
    assert malformed == {
        "executables": [],
        "executable_parse_status": "failed",
        "executable_parse_reason": "parse-error",
        "command_skeleton": "",
    }


def test_sanitizer_reports_parser_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    import sanitize_round_trace as sanitizer

    def unavailable(_raw):
        raise RuntimeError("parser unavailable")

    monkeypatch.setattr(sanitizer, "extract_executable_facts", unavailable)
    row = {
        "provider": "codex",
        "tools": [
            {
                "tool_name": "exec_command",
                "tool_call_id": "call_command",
                "input": {"cmd": "git status"},
            }
        ],
    }
    tool = sanitize_row(row, StableIdSanitizer("test-seed"))["tools"][0]

    assert tool["executables"] == []
    assert tool["executable_parse_status"] == "failed"
    assert tool["executable_parse_reason"] == "parser-unavailable"
    assert tool["command_skeleton"] == ""
    assert "input" not in tool


def test_duckdb_materializes_sanitized_executable_facts(tmp_path: Path) -> None:
    row = {
        "provider": "claude",
        "tools": [
            {
                "tool_name": "Bash",
                "tool_call_id": "toolu_1234567890abcdef",
                "input": {"command": "harbor run || git status"},
            }
        ],
    }
    sanitized = sanitize_row(row, StableIdSanitizer("test-seed"))
    trace = tmp_path / "sanitized.jsonl"
    trace.write_text(json.dumps(sanitized) + "\n")
    db = tmp_path / "sanitized.duckdb"
    materialize(trace, db)

    con = connect(db, read_only=True)
    try:
        facts = con.execute(
            "SELECT executables, executable_parse_status, executable_parse_reason, "
            "command_skeleton FROM tool_calls"
        ).fetchone()
    finally:
        con.close()

    assert facts == (["custom_1", "git"], "success", None, "custom_1 || git")


def test_duckdb_keeps_minimal_command_facts_and_derives_lifecycle(tmp_path: Path) -> None:
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
            "SELECT continuation_of_tool_call_id, command_status, command_exit_code "
            "FROM tool_calls WHERE tool_call_id = 'call_root'"
        ).fetchone()
        finish = con.execute(
            "SELECT continuation_of_tool_call_id, command_status, command_exit_code "
            "FROM tool_calls WHERE tool_call_id = 'call_finish'"
        ).fetchone()
        derived = command_chains(con).fetchone()
        columns = [item[0] for item in con.description]
    finally:
        con.close()

    assert root == (None, "running", None)
    assert finish == ("call_root", "finished", 0)
    row = dict(zip(columns, derived, strict=True))
    assert row["initial_tool_call_id"] == "call_root"
    assert row["initial_status"] == "running"
    assert row["final_status"] == "finished"
    assert row["command_exit_code"] == 0
    assert row["continuation_calls"] == 2
    assert row["finishing_tool_call_id"] == "call_finish"
    assert row["wall_time_until_finished_ms"] == 8_000
    assert row["tool_call_time_sum_ms"] == 6_000


def test_command_chain_utility_handles_immediate_exec(tmp_path: Path) -> None:
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
                    "Process exited with code 0\nOutput:\ndone\n"
                ),
            },
        )
    )
    rounds = _extract(tmp_path, records)
    trace = tmp_path / "immediate.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rounds))
    db = tmp_path / "immediate.duckdb"
    materialize(trace, db)

    con = connect(db, read_only=True)
    try:
        derived = command_chains(con).fetchone()
        columns = [item[0] for item in con.description]
    finally:
        con.close()

    row = dict(zip(columns, derived, strict=True))
    assert row["initial_status"] == "finished"
    assert row["final_status"] == "finished"
    assert row["continuation_calls"] == 0
    assert row["finishing_tool_call_id"] == "call_root"
    assert row["wall_time_until_finished_ms"] == 1_000
    assert row["tool_call_time_sum_ms"] == 1_000


def test_command_chain_utility_keeps_aborted_status_incomplete(tmp_path: Path) -> None:
    records = _continued_session_records("")[:4]
    records.append(
        _record(
            "2026-01-01T00:00:01.000Z",
            "response_item",
            {
                "type": "function_call_output",
                "call_id": "call_root",
                "output": "aborted by user after 1.0s",
            },
        )
    )
    rounds = _extract(tmp_path, records)
    assert rounds[0]["tools"][0]["command_status"] == "aborted"
    assert rounds[0]["tools"][0]["is_error"] is True

    trace = tmp_path / "aborted.jsonl"
    trace.write_text("".join(json.dumps(row) + "\n" for row in rounds))
    db = tmp_path / "aborted.duckdb"
    materialize(trace, db)
    con = connect(db, read_only=True)
    try:
        derived = command_chains(con).fetchone()
        columns = [item[0] for item in con.description]
    finally:
        con.close()

    row = dict(zip(columns, derived, strict=True))
    assert row["initial_status"] == "aborted"
    assert row["final_status"] == "aborted"
    assert row["observed_finished_at"] is None
    assert row["wall_time_until_finished_ms"] is None
