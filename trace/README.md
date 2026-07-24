# trace

Generated private traces, sanitized release traces, and materialized DuckDB files live here and
are ignored by Git.

## Local collection

The current-user collector defaults to:

```text
trace/llm_round_trace.jsonl
```

Store each all-user corpus collection under one UTC timestamp:

```text
trace/collections/YYYYMMDDTHHMMSSZ/
├── hosts/
│   ├── llm_round_trace_v2.TIMESTAMP.HOST.all_users.jsonl
│   └── llm_round_trace_v2.TIMESTAMP.HOST.all_users.collection_report.json
├── merge_report.json
├── merged.private.jsonl
├── merged.public.jsonl
└── merged.public.duckdb
```

Choose the collection ID once and reuse it on every host:

```bash
collection_id="$(date -u +%Y%m%dT%H%M%SZ)"
scripts/collect_all_users_sudo.sh \
  --collection-id "$collection_id" \
  --no-summary \
  --fresh-extract
```

Fresh host snapshots ensure previously seen rounds receive current normalizer fields. The
aggregation step must still union those snapshots with the historical private archive so deleted local
sessions do not disappear.

## Corpus refresh

Union the historical private archive first, followed by every fresh host snapshot. Duplicate identity is
`(provider, session_id, round_id)` and the newest copy wins. Sanitize the resulting
`merged.private.jsonl` before building `merged.public.duckdb`.

`trace/collections/current` points to the selected timestamped collection. The generated files at
the `trace/` root are limited to the latest release-facing `syfi_coding_trace.jsonl.gz` and
`syfi_coding_trace.duckdb`; replace them only after the timestamped public trace passes privacy and
validator checks.
