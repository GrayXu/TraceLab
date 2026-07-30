#!/usr/bin/env bash
# Replay every public TraceLab round serially against DashScope native generation.
# No request is sent unless --send is passed. The API key is read only from the
# environment and is never written to the workload, logs, or command output.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
send=false
preserve_tool_waits=false

usage() {
  cat <<'EOF'
Usage: scripts/replay_all_bailian_serial.sh [--send] [--preserve-tool-waits]

Without --send, generate and validate the full serial workload only; no request
is sent. --send requires the three BAILIAN_TEST_* connection variables plus:

  BAILIAN_TEST_TOKENIZER  Local tokenizer.json/model directory, or Hugging Face repo id.
  BAILIAN_TEST_TEXT_FILE  A sufficiently large UTF-8 corpus for synthetic prompts.

Optional variables:
  BAILIAN_TEST_DB          Trace DB (default: trace/syfi_coding_trace.duckdb)
  BAILIAN_TEST_WORKLOAD    Generated workload CSV (default: trace/bailian_serial.csv)
  BAILIAN_TEST_LOG_PATH    Per-round JSONL log (default: timestamped file under trace/)
  BAILIAN_TEST_SUMMARY_PATH JSON summary (default: timestamped file under trace/)
  BAILIAN_TEST_TOKEN_POOL_LIMIT Synthetic token-pool cap (default: 20000000)
  BAILIAN_TEST_REBUILD_WORKLOAD=1  Regenerate the CSV even when it already exists.

The default intentionally removes trace-observed tool waits so the single request
stream remains continuous. Pass --preserve-tool-waits for closed-loop timing replay.
EOF
}

while (($#)); do
  case "$1" in
    --send) send=true ;;
    --preserve-tool-waits) preserve_tool_waits=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

db_path="${BAILIAN_TEST_DB:-$repo_root/trace/syfi_coding_trace.duckdb}"
workload_path="${BAILIAN_TEST_WORKLOAD:-$repo_root/trace/bailian_serial.csv}"
if [[ ! -r "$db_path" ]]; then
  echo "trace DB is not readable: $db_path" >&2
  exit 1
fi

if [[ "${BAILIAN_TEST_REBUILD_WORKLOAD:-0}" == "1" || ! -s "$workload_path" ]]; then
  mkdir -p "$(dirname "$workload_path")"
  uv run python "$repo_root/artifacts/trace_facts/csv_export/convert.py" \
    --db "$db_path" \
    --arrival-rate 1000000 \
    --arrival-pattern constant \
    --session-order stable \
    -o "$workload_path"
fi

run_args=(
  --trace "$workload_path"
  --backend bailian
  --max-active-sessions 1
  --skip-cache-preflight
  --token-pool-limit "${BAILIAN_TEST_TOKEN_POOL_LIMIT:-20000000}"
)
if ! $preserve_tool_waits; then
  run_args+=(--ignore-tool-waits)
fi

if ! $send; then
  echo "dry-run: no request will be sent; pass --send to enable the endpoint."
  cargo run --release --manifest-path "$repo_root/replay/Cargo.toml" --bin session_runner -- \
    "${run_args[@]}" \
    --endpoint-url "${BAILIAN_TEST_ENDPOINT:-unused}" \
    --api-key-env BAILIAN_TEST_API_KEY \
    --model "${BAILIAN_TEST_MODEL:-unused}" \
    --tokenizer "${BAILIAN_TEST_TOKENIZER:-unused}" \
    --text-file "${BAILIAN_TEST_TEXT_FILE:-$repo_root/README.md}" \
    --dry-run
  exit 0
fi

for required_var in \
  BAILIAN_TEST_API_KEY \
  BAILIAN_TEST_ENDPOINT \
  BAILIAN_TEST_MODEL \
  BAILIAN_TEST_TOKENIZER \
  BAILIAN_TEST_TEXT_FILE; do
  if [[ -z "${!required_var:-}" ]]; then
    echo "missing required environment variable: $required_var" >&2
    exit 1
  fi
done
if [[ ! -r "$BAILIAN_TEST_TEXT_FILE" ]]; then
  echo "text corpus is not readable: $BAILIAN_TEST_TEXT_FILE" >&2
  exit 1
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="${BAILIAN_TEST_LOG_PATH:-$repo_root/trace/bailian_serial_${timestamp}.jsonl}"
summary_path="${BAILIAN_TEST_SUMMARY_PATH:-$repo_root/trace/bailian_serial_${timestamp}.summary.json}"
mkdir -p "$(dirname "$log_path")" "$(dirname "$summary_path")"

exec cargo run --release --manifest-path "$repo_root/replay/Cargo.toml" --bin session_runner -- \
  "${run_args[@]}" \
  --endpoint-url "$BAILIAN_TEST_ENDPOINT" \
  --api-key-env BAILIAN_TEST_API_KEY \
  --model "$BAILIAN_TEST_MODEL" \
  --tokenizer "$BAILIAN_TEST_TOKENIZER" \
  --text-file "$BAILIAN_TEST_TEXT_FILE" \
  --log-path "$log_path" \
  --summary-path "$summary_path"
