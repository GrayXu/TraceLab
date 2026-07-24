from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from merge_round_traces import merge_round_traces, stable_round_identity  # noqa: E402


def write_trace(path: Path, records: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(record, separators=(",", ":")) + "\n" for record in records),
        encoding="utf-8",
    )


def read_trace(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def round_record(
    session_id: str,
    round_id: str,
    *,
    model: str,
    session_file: str,
) -> dict:
    return {
        "provider": "claude",
        "session_id": session_id,
        "round_id": round_id,
        "model": model,
        "session_file": session_file,
    }


def test_merge_retains_history_and_prefers_newer_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    old_path = tmp_path / "old.jsonl"
    new_path = tmp_path / "new.jsonl"
    output_path = tmp_path / "merged.jsonl"
    historical_only = round_record(
        "claude:session-old",
        "msg_old",
        model="claude-opus-4-7",
        session_file="/old/session.jsonl",
    )
    old_overlap = round_record(
        "claude:session-shared",
        "msg_shared",
        model="old-model",
        session_file="/old/path.jsonl",
    )
    new_overlap = round_record(
        "claude:session-shared",
        "msg_shared",
        model="new-model",
        session_file="/new/path.jsonl",
    )
    current_only = round_record(
        "claude:session-new",
        "msg_new",
        model="claude-opus-4-8",
        session_file="/new/session.jsonl",
    )
    write_trace(old_path, [historical_only, old_overlap])
    write_trace(new_path, [new_overlap, current_only])

    report = merge_round_traces([old_path, new_path], output_path)

    assert read_trace(output_path) == [historical_only, new_overlap, current_only]
    assert report["unique_rounds"] == 3
    assert report["duplicate_rows_superseded"] == 1
    assert report["inputs"][0]["rows_kept"] == 1
    assert report["inputs"][1]["rows_kept"] == 2


def test_merge_uses_last_occurrence_within_an_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "merged.jsonl"
    first = round_record(
        "claude:session",
        "msg",
        model="first",
        session_file="/same.jsonl",
    )
    last = {**first, "model": "last"}
    write_trace(input_path, [first, last])

    report = merge_round_traces([input_path], output_path)

    assert read_trace(output_path) == [last]
    assert report["duplicate_rows_superseded"] == 1


def test_stable_identity_requires_all_fields() -> None:
    with pytest.raises(ValueError, match="session_id"):
        stable_round_identity({"provider": "claude", "round_id": "msg"})
