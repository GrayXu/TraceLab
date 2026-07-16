#!/usr/bin/env python3
"""Propose privacy-safe executable labels from the full executable inventory.

This is the review stage, not yet the public-trace sanitizer. Names on an explicit whitelist remain
visible. Every other name is conservatively treated as domain-specific and receives a ``custom_N``
label. The generated review CSV deliberately contains the private original-to-public mapping and
must remain local/ignored; only the whitelist and this script are suitable to commit.

Bootstrap policy: an observed executable is proposed as public/common only when its basename is
present in a standard system binary directory, is a Bash builtin, or is in the small curated set of
well-known cross-platform developer tools below. Bootstrap is one-time and never overwrites an
existing whitelist. Normal runs use only the frozen whitelist, so results do not depend on the host.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

EXP_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = EXP_DIR / "executable_popularity.csv"
DEFAULT_WHITELIST = EXP_DIR / "public_common_executables.txt"
DEFAULT_REVIEW = EXP_DIR / "executable_privacy_review.csv"
DEFAULT_MAPPING = EXP_DIR / "executable_sanitization_mapping.json"
STANDARD_BINARY_DIRS = (Path("/bin"), Path("/usr/bin"), Path("/sbin"), Path("/usr/sbin"))

# Public tools that are commonly present outside the base system directories. Keep this list
# conservative: a false negative becomes custom_N and is safe; a false positive can disclose a
# project-specific name. Less-standard public tools (for example ``modal``) stay in review unless a
# human explicitly adds them to the frozen whitelist.
CURATED_PUBLIC_COMMON = frozenset(
    """
    accelerate alembic ansible apt apt-cache apt-get apply_patch aws az bandit bazel black brew
    bun bundle bundle3.2 cargo ccache clang clang++ cmake conda coverage cuobjdump cython diff-cover
    docker docker-compose dpkg dpkg-deb dpkg-query esbuild ffmpeg
    flake8 g++ gcc gcloud gdb gh git go gofmt gradle grpcurl gunicorn helm hf huggingface-cli
    hyperfine ipython isort jar java javac jq jupyter just kafka-topics.sh kubectl latexmk litellm lldb
    lm_eval lsof lspci make mamba meson micromamba module mpiexec mpirun mysql mypy ncu netcat
    ninja node npm npx
    nsys numactl nvcc nvdisasm nvidia-smi objdump openssl pandoc perf php pip pip3 pkg-config pnpm
    poetry pre-commit protoc ps psql py-spy py_compile pybind11 pyflakes pylint pytest python
    python-script quarto ray redis-cli redis-server rg rsync ruby ruff rustc rustfmt rustup scp
    sccache semgrep shellcheck sftp sglang sphinx sqlite3 ssh sshpass strace streamlit systemctl taskset
    terraform tmux torchrun tox uv uvicorn uvx valgrind vllm wandb wget yarn ycsb.sh yq
    """.split()
)

# Standard-library Python entry points normalized from ``python -m NAME``.
CURATED_PUBLIC_COMMON |= frozenset(
    {
        "compileall",
        "ensurepip",
        "http.server",
        "json.tool",
        "unittest",
        "venv",
    }
)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--mapping-output", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument(
        "--bootstrap-whitelist",
        action="store_true",
        help="create the initial frozen whitelist from standard commands and curated public tools",
    )
    return parser.parse_args(argv)


def load_inventory(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows or "executable" not in rows[0]:
        raise ValueError(f"{path} is not an executable_popularity.csv inventory")
    names = [row["executable"] for row in rows]
    if len(names) != len(set(names)):
        raise ValueError(f"{path} contains duplicate executable names")
    return rows


def system_command_names() -> set[str]:
    names: set[str] = set()
    for directory in STANDARD_BINARY_DIRS:
        try:
            names.update(entry.name for entry in directory.iterdir())
        except OSError:
            continue
    return names


def bash_builtin_names() -> set[str]:
    result = subprocess.run(
        ["bash", "-c", "compgen -b"],
        check=True,
        capture_output=True,
        text=True,
    )
    return set(result.stdout.split())


def write_initial_whitelist(path: Path, observed: set[str]) -> set[str]:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing whitelist: {path}")
    public = observed & (system_command_names() | bash_builtin_names() | CURATED_PUBLIC_COMMON)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("# Frozen public/common executable whitelist. One exact normalized label per line.\n")
        fh.write("# Additions require human review; unknown names are sanitized to custom_N.\n")
        for name in sorted(public):
            fh.write(name + "\n")
    return public


def load_whitelist(path: Path) -> set[str]:
    names = {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not names:
        raise ValueError(f"whitelist is empty: {path}")
    return names


def write_review(
    rows: list[dict[str, str]],
    whitelist: set[str],
    review_path: Path,
    mapping_path: Path,
) -> tuple[int, int, int, int]:
    # Inventory rank is deterministic for a fixed trace and makes the most important private names
    # custom_1, custom_2, ... in the review. The persisted mapping—not this ordering rule—will be
    # the stability source when this is later integrated into the sanitizer.
    custom_names = [row["executable"] for row in rows if row["executable"] not in whitelist]
    mapping = {name: f"custom_{index}" for index, name in enumerate(custom_names, 1)}

    review_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "rank",
        "executable",
        "classification",
        "public_label",
        "kind",
        "claude_count",
        "codex_count",
        "total",
        "pooled_share",
    ]
    with review_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            name = row["executable"]
            is_public = name in whitelist
            writer.writerow(
                {
                    **{key: row[key] for key in fieldnames if key in row},
                    "classification": "public/common" if is_public else "domain-specific",
                    "public_label": name if is_public else mapping[name],
                }
            )
    review_path.chmod(0o600)

    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping_path.write_text(
        json.dumps(mapping, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    mapping_path.chmod(0o600)
    public_rows = [row for row in rows if row["executable"] in whitelist]
    public_occurrences = sum(int(row["total"]) for row in public_rows)
    total_occurrences = sum(int(row["total"]) for row in rows)
    return len(public_rows), len(custom_names), public_occurrences, total_occurrences


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"missing inventory: {args.input} (run analyze_popularity.py first)", file=sys.stderr)
        return 2

    rows = load_inventory(args.input)
    observed = {row["executable"] for row in rows}
    if args.bootstrap_whitelist:
        whitelist = write_initial_whitelist(args.whitelist, observed)
        print(f"Bootstrapped {len(whitelist):,} names -> {args.whitelist}", file=sys.stderr)
    else:
        if not args.whitelist.exists():
            print(
                f"missing whitelist: {args.whitelist}\n"
                "run once with --bootstrap-whitelist, then review the file",
                file=sys.stderr,
            )
            return 2
        whitelist = load_whitelist(args.whitelist)

    public, custom, public_occ, total_occ = write_review(
        rows,
        whitelist,
        args.review_output,
        args.mapping_output,
    )
    print(
        f"public/common: {public:,} names; domain-specific: {custom:,} names; "
        f"public occurrence coverage: {public_occ:,}/{total_occ:,} "
        f"({public_occ / total_occ:.2%})"
    )
    print(f"review -> {args.review_output}")
    print(f"PRIVATE mapping -> {args.mapping_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
