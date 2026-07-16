#!/usr/bin/env python3
"""Classify every shell command by the executable(s) it runs, one row per call — the whole pipeline.

Scans the **whole** normalized trace (every command tool-call — never a sample) and, for each call,
records the executables it can name plus that call's latency, streaming one JSON row per call to
**``command_calls.jsonl``** — the single source of truth every downstream analysis reads. Nothing
after this re-scans the trace or re-classifies.

Why a real parser: shell splitting is genuinely hard (unquoted newlines and ``#`` comments are
separators, heredocs and ``$(...)`` bodies are *not* commands, pipelines/loops/subshells nest). A
hand-rolled tokenizer keeps hitting edge cases, so we parse each command with **tree-sitter-bash**
and walk its AST — the structure comes for free and correctly. We keep the *semantic* label layer
(``seg_head`` / ``label_exe`` / ``PLUMBING`` / wrapper + python rules) on top: the AST says *which
words form each command*, and that layer says *which executable those words mean*. This resolves
**98.86%** of calls deterministically, so the pipeline is fully offline — no LLM, no endpoint.

Precision over recall / self-validating: we only claim ``deterministic`` when the parse is clean.
A parse error (``root.has_error`` — e.g. a truncated command, or Codex's ``apply_patch`` patch body
which is data, not shell) → ``source="unresolved"`` (empty executables) rather than a guessed label.
Genuinely dynamic program names (``$TOOL``, ``$(which x)`` / ``eval`` / ``bash -c "$X"``) also stay
``unresolved``; those are undecidable without running the command. That ~1.14% partial/unresolved
tail is retained honestly, never guessed.

The parser is loaded lazily (only when a command is actually parsed), so importing ``normalize`` /
``KEYWORDS`` / ``PLUMBING`` from this module as a library needs no tree-sitter install.

Counting unit — **one label per executable occurrence** (per command node), not per line:
``grep x | tail`` → ``grep``+``tail``; commands inside ``$(...)`` count too (they run).
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator

EXP_DIR = Path(__file__).resolve().parent
REPO_ROOT = EXP_DIR.parents[2]  # experiment -> category -> artifacts -> repo root
sys.path.insert(0, str(REPO_ROOT / "artifacts" / "utils"))
from accumulators import tool_latency_ms  # noqa: E402

TRACE_DIR = REPO_ROOT / "trace"
DEFAULT_INPUT = TRACE_DIR / "llm_round_trace_v2.merged.all_users.jsonl"
# command/shell execute tools
DEFAULT_TOOLS = ("Bash", "exec_command", "shell", "shell_command")

ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# A bare program name, optionally invoked by path — incl. hidden dirs (.venv/bin/python) and
# ./ ../ / prefixes.
HEAD_OK = re.compile(r"^(\.{1,2}/|/)?\.?[A-Za-z0-9_@][A-Za-z0-9_./+@:-]*$")
HAS_ALPHA = re.compile(r"[A-Za-z]")
PY = re.compile(r"^(python[0-9.]*|py)$")
# HTML-escaped operators (&gt; &amp; …): a few traces carry them; bash would read the bare `&` as
# backgrounding and mis-parse, so treat any such line as complicated -> not_sure.
HTML_ENTITIES = ("&gt;", "&lt;", "&amp;", "&quot;", "&apos;", "&#")
# Codex's file editor: `apply_patch <<'PATCH' … PATCH` (or a bare `apply_patch *** Begin Patch …`).
# The body is a patch/diff — DATA, not shell — so we name the editor and never parse the body.
APPLY_PATCH = re.compile(r"\s*apply_patch\b")

# Shell plumbing / builtins. These ARE classified (every executable is tallied faithfully); they
# are only *tagged* `plumbing` so a downstream step can include or exclude them. `test` and `[` are
# the POSIX conditional utility — real programs, tagged plumbing.
PLUMBING = {"cd", "pushd", "popd", "export", "source", ".", "set", "unset", ":", "true",
            "echo", "printf", "pwd", "test", "["}
WRAP = {"sudo", "env", "time", "nohup", "nice", "stdbuf", "command", "exec", "ionice",
        "timeout", "xargs"}
# Wrappers that take their own flags (and sometimes a value / positional) before the real command;
# we skip those to reach the program (`env -u VAR cmd`, `sudo -u user cmd`, `timeout 30 cmd`).
WRAP_FLAG_WRAPPERS = {"sudo", "env", "nice", "timeout", "stdbuf", "ionice", "xargs"}
WRAP_VALUE_FLAGS = {"-u", "--unset", "-C", "--chdir", "-n", "--adjustment", "-s", "--signal",
                    "-k", "--kill-after", "-I", "-P", "-d", "-E", "--replace"}
_DURATION = re.compile(r"^\d+(\.\d+)?[smhd]?$")  # timeout's positional duration (30, 1h, 500ms…)
RUN_WRAP = {"uv", "poetry", "pipenv", "pdm"}  # followed by "run [flags…] <program>"
# `uv run` (etc.) flags that take a value — skip the flag AND its value to reach the real program.
RUN_VALUE_FLAGS = {"--with", "--with-editable", "--with-requirements", "--python", "-p", "--extra",
                   "--group", "--directory", "--project", "--index", "--index-url",
                   "--default-index", "--find-links", "-f", "--constraint", "-c", "--override",
                   "--package", "--config-setting", "-C"}
# Loop control — real ``command`` nodes in the AST, but not executables (mirror resolve's drops).
KEYWORDS = {"for", "while", "until", "if", "then", "elif", "else", "fi", "do", "done",
            "case", "esac", "function", "select", "in", "[[", "]]", "!", "coproc", "{", "}",
            "break", "continue"}
SYN = {"docker-compose": "docker", "pip3": "pip", "pip2": "pip"}

# tree-sitter node types: literal argument words vs. dynamic (runtime-determined) ones.
_WORDISH = {"word", "number", "string", "raw_string", "concatenation", "ansi_c_string"}
_DYNAMIC = {"simple_expansion", "expansion", "command_substitution", "arithmetic_expansion",
            "process_substitution"}


def basename(tok: str) -> str:
    return tok.rsplit("/", 1)[-1]


# --------------------------------------------------------------------------------------------------
# semantic label layer (shared; independent of how commands are split)
# --------------------------------------------------------------------------------------------------
def normalize(raw: Any) -> tuple[str | None, Any]:
    """Reduce a tool input to ('str', command) | ('argv', argv_list) | (None, None)."""
    inp = raw
    if isinstance(inp, str):
        try:
            inp = json.loads(inp)
        except Exception:
            return ("str", inp)
    if not isinstance(inp, dict):
        return (None, None)
    cmd = inp.get("command")
    if cmd is None:
        cmd = inp.get("cmd")
    if isinstance(cmd, list):
        base = basename(str(cmd[0])) if cmd else ""
        if base in {"bash", "sh", "zsh"}:
            for i, a in enumerate(cmd):
                if a in {"-c", "-lc", "-lic", "-ic", "-ec"} and i + 1 < len(cmd):
                    return ("str", str(cmd[i + 1]))
        return ("argv", [str(x) for x in cmd])
    if isinstance(cmd, str):
        return ("str", cmd)
    return (None, None)


def _skip_wrap_opts(seg: list[str], i: int, wrapper: str) -> int:
    """Advance past a flag-taking wrapper's own options to reach the wrapped program.

    Handles `env -u VAR / NAME=val`, `sudo -u user`, `nice -n 10`, `timeout [-s SIG] 30`, etc.
    """
    n = len(seg)
    while i < n:
        t = seg[i]
        if ENV_ASSIGN.match(t):                       # env NAME=val
            i += 1
        elif t.startswith("-"):
            i += 2 if ("=" not in t and t in WRAP_VALUE_FLAGS and i + 1 < n) else 1
        elif wrapper == "timeout" and _DURATION.match(t):
            i += 1                                     # timeout's positional DURATION
        else:
            break
    return i


def seg_head(seg: list[str]) -> tuple[str | None, int]:
    """Head of a command word-list after stripping env-assigns + transparent wrappers.

    Returns ('exe'|'keyword'|'bad'|None, index). Handles `uv|poetry|… run [flags[ value]] <program>`
    and flag-taking wrappers (`env -u`, `sudo -u`, `timeout 30`, …) by skipping to the program.
    """
    i, n = 0, len(seg)
    while i < n:
        t = seg[i]
        if ENV_ASSIGN.match(t):
            i += 1
            continue
        base = basename(t)
        if base in WRAP:
            i = _skip_wrap_opts(seg, i + 1, base) if base in WRAP_FLAG_WRAPPERS else i + 1
            continue
        if base in RUN_WRAP and i + 1 < n and seg[i + 1] == "run":
            i += 2
            while i < n and seg[i].startswith("-"):
                if "=" not in seg[i] and seg[i] in RUN_VALUE_FLAGS and i + 1 < n:
                    i += 2                    # value-taking flag: skip flag AND its value
                else:
                    i += 1                    # boolean flag, or `--flag=value` single token
            continue
        if base in KEYWORDS:
            return ("keyword", i)
        if not HEAD_OK.match(t) or not HAS_ALPHA.search(base):
            return ("bad", i)
        return ("exe", i)
    return (None, -1)


def label_exe(seg: list[str]) -> str:
    base = basename(seg[0])
    rest = seg[1:]
    if PY.match(base):
        if "-m" in rest:
            j = rest.index("-m")
            if j + 1 < len(rest):
                return basename(rest[j + 1])  # python -m pytest -> pytest
            return "python"
        for a in rest:
            if not a.startswith("-") and a.endswith(".py"):
                return "python-script"
        return "python"
    return SYN.get(base, base)


# --------------------------------------------------------------------------------------------------
# tree-sitter structure layer (lazy parser; walks the AST into command word-lists)
# --------------------------------------------------------------------------------------------------
_PARSER = None


def _parser():
    """Load tree-sitter-bash lazily, so importing this module as a library needs no parser."""
    global _PARSER
    if _PARSER is None:
        try:
            import tree_sitter_bash as tsb
            from tree_sitter import Language, Parser
        except ImportError as exc:  # noqa: BLE001
            raise SystemExit(
                "tree-sitter-bash is required to classify commands. Run with:\n"
                "  uv run --extra bash python .../classify_commands.py\n"
                f"(import failed: {exc})")
        _PARSER = Parser(Language(tsb.language()))
    return _PARSER


def _resolve_name(node) -> tuple[str, bool]:
    """A command_name node -> (token for the label layer, is_dynamic).

    Path-invoked or partially-dynamic names collapse to their basename: ``.venv/bin/python`` and
    ``"$PWD/.../python"`` both -> ``python``. Only a name whose *basename* is itself dynamic
    (``$TOOL``, ``$(which x)``) is undecidable.
    """
    txt = node.text.decode("utf-8", "replace").strip()
    base = txt.rsplit("/", 1)[-1].strip("\"'")
    if "$" in base or "`" in base or "\x00" in base:
        return "\x00", True                  # dynamic program name — undecidable
    if "/" in txt or "$" in txt:
        return base, False                   # path (maybe with a dynamic dir) but literal basename
    return txt, False


def _command_words(node) -> tuple[list[str], bool]:
    """Literal words of one `command` node (head + args); flag if the head is dynamic."""
    words: list[str] = []
    head_dyn = False
    for ch in node.children:
        t = ch.type
        if t == "command_name":
            token, head_dyn = _resolve_name(ch)
            words.append(token)
        elif t in _WORDISH:
            words.append(ch.text.decode("utf-8", "replace"))
        elif t in _DYNAMIC:
            words.append("\x00")             # opaque arg placeholder (never a python .py / -m NAME)
        elif t == "variable_assignment":
            continue                          # env-assign prefix (FOO=bar)
    return words, head_dyn


def _collect(node, heredoc: bool, acc: list) -> None:
    """Gather every `command` node (descending into pipelines/lists/loops/subshells/$()), with a
    flag for whether its stdin is a heredoc. Heredoc bodies are separate nodes -> never commands."""
    if node.type == "redirected_statement":
        hd = any(ch.type == "heredoc_redirect" for ch in node.children)
        for ch in node.children:
            _collect(ch, heredoc or hd, acc)
        return
    if node.type == "command":
        acc.append((node, heredoc))
        for ch in node.children:             # still descend into $(...) in the args
            _collect(ch, False, acc)
        return
    for ch in node.children:
        _collect(ch, heredoc, acc)


def _label_node(node, heredoc: bool) -> tuple[str | None, str | None]:
    """One command node -> (label, None) | (None, reason). reason None means 'nothing to count'."""
    words, head_dyn = _command_words(node)
    if not words:
        return None, None                    # e.g. a bare assignment: no command, not a not_sure
    if head_dyn:
        return None, "dynamic"
    st, idx = seg_head(words)
    if st == "keyword":
        return None, None                    # break/continue etc — real AST commands, not execs
    if st != "exe":
        return None, st or "none"
    lbl = label_exe(words[idx:])
    if "\x00" in lbl:                         # e.g. `python -m $MODULE`
        return None, "dynamic"
    if lbl == "python" and (heredoc or "-" in words[idx + 1:]):
        lbl = "python-script"                # stdin/heredoc script, matching our convention
    return lbl, None


_SKELETON_OPERATORS = {"|", "&&", "||", "&"}
_SKELETON_COMPLEX_NODES = {"command_substitution", "process_substitution"}


def _contains_complex_skeleton_node(node) -> bool:
    """Whether a simple command hides another command whose relationship we would lose."""
    for child in node.children:
        if child.type in _SKELETON_COMPLEX_NODES or _contains_complex_skeleton_node(child):
            return True
    return False


def _skeleton_node(node, heredoc: bool = False) -> str | None:
    """Render a deliberately small executable/operator grammar, or reject the whole structure.

    Supported grammar: normalized commands; ``|``/``&&``/``||``/``&``; semicolon/newline sequences
    (canonicalized to ``;``); loops and complex structures collapsed to reserved ``<...>`` tokens;
    and ``!`` negation. Only a structurally invalid node returns ``None``.
    """
    if node.type == "program":
        children = [child for child in node.children if child.type != "comment"]
        if not children:
            return None
        parts: list[str] = []
        expect_statement = True
        for child in children:
            if child.type in {";", "&"}:
                if expect_statement:
                    return None
                parts.append(child.type)
                expect_statement = True
                continue
            if not child.is_named:
                return None
            # Adjacent named children are newline-separated statements because tree-sitter omits
            # newline separator tokens. Both newlines and explicit semicolons canonicalize to `;`.
            part = _skeleton_node(child, heredoc)
            if part is None:
                return None
            if not expect_statement:
                parts.append(";")
            parts.append(part)
            expect_statement = False
        if parts[-1] == ";":
            parts.pop()  # a trailing semicolon carries no additional structure
        return " ".join(parts) if parts else None

    if node.type in {"for_statement", "while_statement"}:
        return "<loops>"

    if node.type in {"if_statement", "case_statement"}:
        return "<conditional>"

    if node.type == "function_definition":
        return "<function>"

    if node.type in {"subshell", "compound_statement"}:
        return "<subshell>"

    if node.type in {"variable_assignment", "declaration_command"}:
        return "<assign>"

    if node.type == "negated_command":
        children = [child for child in node.children if child.is_named]
        if len(children) != 1:
            return None
        part = _skeleton_node(children[0], heredoc)
        return f"! {part}" if part is not None else None

    if node.type == "redirected_statement":
        body = node.child_by_field_name("body")
        if body is None:
            return None
        has_heredoc = any(child.type == "heredoc_redirect" for child in node.children)
        return _skeleton_node(body, heredoc or has_heredoc)

    if node.type == "command":
        has_substitution = _contains_complex_skeleton_node(node)
        label, reason = _label_node(node, heredoc)
        if label is not None and reason is None:
            suffix = " <substitution>" if has_substitution else ""
            return label + suffix
        return "<substitution>" if has_substitution else "<unknown>"

    if node.type not in {"pipeline", "list"}:
        return "<unknown>"

    parts: list[str] = []
    for child in node.children:
        if child.type in _SKELETON_OPERATORS:
            parts.append(child.type)
            continue
        if not child.is_named:
            return None
        part = _skeleton_node(child, heredoc)
        if part is None:
            return None
        parts.append(part)

    if not parts or len(parts) % 2 == 0:
        return None
    if any(
        (index % 2 == 0 and part in _SKELETON_OPERATORS)
        or (index % 2 == 1 and part not in _SKELETON_OPERATORS)
        for index, part in enumerate(parts)
    ):
        return None
    return " ".join(parts)


def extract(raw: Any) -> dict[str, Any]:
    """Parse one call into labels plus a simple executable/operator command skeleton."""
    kind, val = normalize(raw)
    out: dict[str, Any] = {
        "labels": [],
        "not_sure": 0,
        "reason": None,
        "command": "",
        "command_skeleton": "",
    }
    if kind is None:
        out["not_sure"] = 1
        out["reason"] = "undecodable"
        return out

    if kind == "argv":                        # codex exec array form — one command, no shell syntax
        out["command"] = " ".join(val)
        st, idx = seg_head(val)
        if st == "exe":
            label = label_exe(val[idx:])
            out["labels"].append(label)
            out["command_skeleton"] = label
        else:
            out["not_sure"] = 1
            out["reason"] = f"argv-{st}"
            out["command_skeleton"] = "<unknown>"
        return out

    s = val if isinstance(val, str) else ""
    out["command"] = s
    if not s.strip():
        out["not_sure"] = 1
        out["reason"] = "empty"
        return out
    if APPLY_PATCH.match(s):                   # editor; the patch body is data, not shell
        out["labels"].append("apply_patch")
        out["command_skeleton"] = "apply_patch"
        return out
    for marker in HTML_ENTITIES:
        if marker in s:
            out["not_sure"] = 1
            out["reason"] = "html-entity"
            return out

    tree = _parser().parse(s.encode("utf-8", "replace"))
    root = tree.root_node
    if root.has_error:
        # A malformed / truncated command, or a payload we can't structure safely -> don't guess.
        out["not_sure"] = 1
        out["reason"] = "parse-error"
        return out

    out["command_skeleton"] = _skeleton_node(root) or ""

    acc: list = []
    _collect(root, False, acc)
    reasons: list[str] = []
    for node, hd in acc:
        lbl, why = _label_node(node, hd)
        if lbl is not None:
            out["labels"].append(lbl)
        elif why:
            out["not_sure"] += 1
            reasons.append(why)
    if reasons and not out["labels"]:
        out["reason"] = reasons[0]
    elif reasons:
        out["reason"] = "partial:" + reasons[0]
    return out


# --------------------------------------------------------------------------------------------------
# trace scan / output (unchanged schema)
# --------------------------------------------------------------------------------------------------
def open_trace_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def command_calls(row: dict, tool_filter: set[str]) -> Iterator[tuple[str, dict, dict]]:
    """Yield (provider, row, tool) for command tool-calls in a full-trace row."""
    prov = row.get("provider")
    tools = row.get("tools")
    if isinstance(tools, list):
        for t in tools:
            if isinstance(t, dict) and t.get("tool_name") in tool_filter:
                yield prov, row, t


def kind_of(exe: str) -> str:
    """plumbing (shell builtin/conditional) vs tool — the single source of truth for the tag."""
    return "plumbing" if exe in PLUMBING else "tool"


def classify_source(labels: list[str], not_sure: int) -> str:
    """deterministic (fully named), partial (some named + some unknown), unresolved (nothing named).

    ``unresolved`` is the small tail of dynamic names, parse errors, and opaque payloads that
    the deterministic layer refuses to guess. Empty executables; the honest "other" bucket.
    """
    if not_sure == 0:
        return "deterministic"
    return "partial" if labels else "unresolved"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT,
                   help=f"Normalized trace JSONL (raw input + latency). Default: {DEFAULT_INPUT}")
    p.add_argument("-o", "--output-dir", type=Path, default=EXP_DIR,
                   help="Where to write the per-call dataset (default: this folder).")
    p.add_argument("--tools", default=",".join(DEFAULT_TOOLS),
                   help=f"Command tool allowlist. Default: {','.join(DEFAULT_TOOLS)}")
    p.add_argument("--progress-every", type=int, default=200_000,
                   help="Log a progress line every N rows (0 disables).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.input.exists():
        print(f"input not found: {args.input}", file=sys.stderr)
        return 2
    tool_filter = {t.strip() for t in args.tools.split(",") if t.strip()}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "command_calls.jsonl"

    calls: Counter = Counter()
    with_input: Counter = Counter()
    tally: dict[str, Counter] = defaultdict(Counter)
    source_tally: dict[str, Counter] = defaultdict(Counter)
    reasons: dict[str, Counter] = defaultdict(Counter)
    no_latency: Counter = Counter()

    rows = written = 0
    with open_trace_text(args.input) as fh, out_path.open("w", encoding="utf-8") as out:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows += 1
            if args.progress_every and rows % args.progress_every == 0:
                print(f"  ...scanned {rows:,} rows", file=sys.stderr)
            for prov, r, tool in command_calls(row, tool_filter):
                calls[prov] += 1
                raw_input = tool.get("input")
                if raw_input is None:
                    continue  # sanitized (no raw command) — accounted for the guard below
                with_input[prov] += 1
                res = extract(raw_input)
                labels = res["labels"]
                source = classify_source(labels, res["not_sure"])
                source_tally[prov][source] += 1
                if res["not_sure"]:
                    reasons[prov][res["reason"] or "not_sure"] += 1
                for e in labels:
                    tally[prov][e] += 1
                ms = tool_latency_ms(tool)
                if ms is None:
                    no_latency[prov] += 1
                rec = {
                    "provider": prov,
                    "user": r.get("user"),
                    "project": r.get("project"),
                    "trace_key": r.get("trace_key"),
                    "session_id": r.get("session_id"),
                    "round_index": r.get("round_index"),
                    "tool_index": tool.get("tool_index"),
                    "tool_call_id": tool.get("tool_call_id"),
                    "tool_name": tool.get("tool_name"),
                    "command": res["command"],
                    "command_skeleton": res["command_skeleton"],
                    "latency_ms": round(float(ms), 3) if ms is not None else None,
                    "executables": labels,
                    "kinds": [kind_of(e) for e in labels],
                    "n_exe": len(labels),
                    "source": source,
                    "reason": res["reason"],
                }
                out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1

    providers = [p for p in ("claude", "codex") if p in calls] or list(calls)

    sanitized = [p for p in providers if calls[p] > 0 and with_input[p] == 0]
    if sanitized:
        print(f"ERROR: no raw tools[].input for {', '.join(sanitized)}; "
              f"'{args.input}' looks sanitized. This needs the normalized trace "
              f"(default: {DEFAULT_INPUT.name}).", file=sys.stderr)
        return 1

    print(f"input:  {args.input}")
    print(f"rows scanned: {rows:,}   wrote {written:,} per-call rows -> {out_path.name}")
    total_unresolved = 0
    for prov in providers:
        tot = with_input[prov]
        occ = sum(tally[prov].values())
        st = source_tally[prov]
        total_unresolved += st["unresolved"]
        det_share = (st["deterministic"] + st["partial"]) / tot if tot else 0
        print(f"\n=== {prov}: {calls[prov]:,} command calls ({tot:,} with input) -> "
              f"{occ:,} executable occurrences")
        print(f"    source: deterministic {st['deterministic']:,}  partial {st['partial']:,}  "
              f"unresolved {st['unresolved']:,}   "
              f"({det_share:.1%} named deterministically)")
        print(f"    top unresolved reasons: {dict(reasons[prov].most_common(4))}   "
              f"no-latency calls: {no_latency[prov]:,}")
        for e, c in tally[prov].most_common(12):
            print(f"      {c:8,}  {e}")

    tot_all = sum(with_input[p] for p in providers)
    print(f"\nunresolved (other / not a nameable command): {total_unresolved:,} "
          f"({total_unresolved / tot_all:.2%} of {tot_all:,} calls)")
    print(f"wrote -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
