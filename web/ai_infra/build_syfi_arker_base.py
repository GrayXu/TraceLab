#!/usr/bin/env python3
"""Build the Arker base VM used by SYFI QA.

Arker does not have a separate E2B-style template-build object. A prepared VM is the
template: runtime sandboxes fork from this base VM, inheriting its filesystem via
copy-on-write. This script creates that base VM, installs the Python dependencies,
uploads the public SYFI DuckDB to /data, and verifies the database in-place.

Run with the Arker SDK available, for example:

    source ~/.bashrc
    uv run --with arker python web/ai_infra/build_syfi_arker_base.py

The resulting VM id is the value to use as the production fork source
(`source_vm_id`) for request-time Arker VMs.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "trace" / "syfi_coding_trace.duckdb"
DEFAULT_NAME = "syfi-qa-base"
DEFAULT_SOURCE_VM = "ubuntu"
DEFAULT_SOURCE_ORG = "ArkerHQ"
REMOTE_DB = "/data/syfi_coding_trace.duckdb"
REMOTE_OUT = "/out"


def env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def require_env(*names: str) -> str:
    value = env_value(*names)
    if not value:
        raise SystemExit(f"{' or '.join(names)} is not set")
    return value


def db_summary(db_path: Path) -> dict[str, Any]:
    import duckdb

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return {
            "rounds": con.execute("SELECT count(*) FROM rounds").fetchone()[0],
            "tool_calls": con.execute("SELECT count(*) FROM tool_calls").fetchone()[0],
            "timing_events": con.execute("SELECT count(*) FROM timing_events").fetchone()[0],
            "providers": con.execute(
                """
                SELECT provider, count(*) AS rounds
                FROM rounds
                GROUP BY provider
                ORDER BY rounds DESC
                """
            ).fetchall(),
        }
    finally:
        con.close()


def import_arker():
    try:
        from arker import Arker, ArkerError
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "The Arker Python SDK is not installed. Run this script with:\n"
            "  uv run --with arker python web/ai_infra/build_syfi_arker_base.py"
        ) from exc
    return Arker, ArkerError


def print_json(label: str, value: Any) -> None:
    print(f"{label}: {json.dumps(value, sort_keys=True)}", flush=True)


def run_checked(vm: Any, command: str, *, timeout: int, label: str) -> Any:
    started = time.perf_counter()
    result = vm.run(
        command,
        timeout=timeout,
        acquire="cpu,memory,disk",
        release="cpu",
    )
    run_id = getattr(result, "run_id", None)
    state = getattr(result, "state", None) or getattr(result, "type", None)
    deadline = time.monotonic() + timeout + 60
    polls = 0
    while state == "running" and run_id and time.monotonic() < deadline:
        time.sleep(0.5)
        result = vm.get_run(run_id)
        state = getattr(result, "state", None) or getattr(result, "type", None)
        polls += 1

    stdout = result.stdout.decode("utf-8", "replace") if getattr(result, "stdout", None) else ""
    stderr = result.stderr.decode("utf-8", "replace") if getattr(result, "stderr", None) else ""
    exit_code = getattr(result, "exit_code", None)
    print_json(
        label,
        {
            "state": state,
            "exit_code": exit_code,
            "run_id": run_id,
            "polls": polls,
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "stdout_tail": stdout[-4000:],
            "stderr_tail": stderr[-4000:],
        },
    )
    if state != "completed" or exit_code != 0:
        raise SystemExit(f"{label} failed with state={state} exit_code={exit_code}")
    return result


def install_command(*, remote_db: str, remote_out: str) -> str:
    return f"""
set -eux
mkdir -p {shlex.quote(Path(remote_db).parent.as_posix())} {shlex.quote(remote_out)}
if ! python3 -m pip --version >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-pip
fi
python3 -m pip install --break-system-packages --upgrade duckdb matplotlib
chmod 0755 {shlex.quote(Path(remote_db).parent.as_posix())} {shlex.quote(remote_out)}
"""


def verify_command(*, remote_db: str) -> str:
    code = (
        "import duckdb, json, os; "
        f"db={remote_db!r}; "
        "print(json.dumps({'db_exists': os.path.exists(db), "
        "'db_size': os.path.getsize(db) if os.path.exists(db) else None})); "
        "con=duckdb.connect(db, read_only=True); "
        "print(json.dumps({'rounds': con.execute('select count(*) from rounds').fetchone()[0]})); "
        "con.close()"
    )
    return f"python3 -c {shlex.quote(code)}"


def build_base(args: argparse.Namespace) -> dict[str, Any]:
    require_env("ARKER_API_KEY")
    Arker, _ArkerError = import_arker()

    db_path = args.db.resolve()
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")

    summary = db_summary(db_path)
    print_json(
        "local_db",
        {
            "path": str(db_path),
            "size_mb": round(db_path.stat().st_size / 1024 / 1024, 1),
            **summary,
        },
    )

    ar = Arker(region=args.region)
    started = time.perf_counter()
    vm = ar.fork(
        source_vm_name=args.source_vm,
        source_org_id=args.source_org_id,
        name=args.name,
        public=args.public,
        egress=args.egress,
        vcpu_count=args.vcpu_count,
        memory_mib=args.memory_mib,
        disk_mib=args.disk_mib,
        durable=args.durable,
    )
    print_json(
        "arker_base_created",
        {
            "vm_id": vm.id,
            "name": args.name,
            "source_vm": args.source_vm,
            "source_org_id": args.source_org_id,
            "region": args.region,
            "vcpu_count": args.vcpu_count,
            "memory_mib": args.memory_mib,
            "disk_mib": args.disk_mib,
            "public": args.public,
            "egress": args.egress,
            "durable": args.durable,
            "duration_ms": round((time.perf_counter() - started) * 1000),
        },
    )

    try:
        run_checked(
            vm,
            install_command(remote_db=args.remote_db, remote_out=args.remote_out),
            timeout=args.install_timeout,
            label="arker_install",
        )

        started = time.perf_counter()
        raw = db_path.read_bytes()
        vm.sync(args.remote_db, raw)
        print_json(
            "arker_db_uploaded",
            {
                "vm_id": vm.id,
                "remote_db": args.remote_db,
                "bytes": len(raw),
                "duration_ms": round((time.perf_counter() - started) * 1000),
            },
        )

        run_checked(
            vm,
            f"chmod 0444 {shlex.quote(args.remote_db)} && {verify_command(remote_db=args.remote_db)}",
            timeout=args.verify_timeout,
            label="arker_verify",
        )
    except BaseException:
        if args.delete_on_failure:
            try:
                vm.delete()
                print_json("arker_base_deleted_after_failure", {"vm_id": vm.id})
            except Exception as exc:  # noqa: BLE001 - cleanup best effort
                print_json("arker_delete_error", {"vm_id": vm.id, "error": f"{type(exc).__name__}: {exc}"})
        raise

    result = {
        "vm_id": vm.id,
        "name": args.name,
        "remote_db": args.remote_db,
        "remote_out": args.remote_out,
        "source_for_runtime": {"source_vm_id": vm.id},
    }
    print_json("arker_base_ready", result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--name", default=DEFAULT_NAME)
    parser.add_argument("--source-vm", default=DEFAULT_SOURCE_VM)
    parser.add_argument("--source-org-id", default=DEFAULT_SOURCE_ORG)
    parser.add_argument("--region", default="us-west-2")
    parser.add_argument("--remote-db", default=REMOTE_DB)
    parser.add_argument("--remote-out", default=REMOTE_OUT)
    parser.add_argument("--vcpu-count", type=int, default=1)
    parser.add_argument("--memory-mib", type=int, default=2048)
    parser.add_argument("--disk-mib", type=int, default=4096)
    parser.add_argument("--install-timeout", type=int, default=600)
    parser.add_argument("--verify-timeout", type=int, default=120)
    parser.add_argument("--public", action="store_true", help="Allow other orgs to fork this base VM.")
    parser.add_argument(
        "--egress",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow outbound network during base build. Runtime child VMs should override this to false.",
    )
    parser.add_argument(
        "--durable",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Enable Arker durable mode for this base VM.",
    )
    parser.add_argument(
        "--delete-on-failure",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Delete the partially built base VM if install/upload/verify fails.",
    )
    return parser.parse_args()


def main() -> int:
    build_base(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
