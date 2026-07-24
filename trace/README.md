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
│   ├── HOST.all_users.jsonl
│   └── HOST.all_users.collection_report.json
├── merge_report.json
├── merged.private.jsonl
├── merged.public.jsonl
└── merged.public.duckdb
```

Choose the collection ID once and reuse it on every host:

```bash
collection_id="$(date -u +%Y%m%dT%H%M%SZ)"
host_label="$(hostname -s)"
scripts/collect_all_users_sudo.sh \
  --output "trace/collections/${collection_id}/hosts/${host_label}.all_users.jsonl" \
  --no-summary \
  --fresh-extract
```

Fresh host snapshots ensure previously seen rounds receive current normalizer fields. The
aggregation step must still union those snapshots with the prior private archive so deleted local
sessions do not disappear.

## Corpus refresh

Union the prior private archive first, followed by every fresh host snapshot. Duplicate identity is
`(provider, session_id, round_id)` and the newest copy wins. Sanitize the resulting
`merged.private.jsonl` before building `merged.public.duckdb`.

`trace/llm_round_trace_v2.merged.all_users.jsonl` is a current-version symlink to the selected
timestamped private historical archive.
`trace/syfi_coding_trace.jsonl`, `.jsonl.gz`, and `.duckdb` are release-facing aliases. Replace
them only after the timestamped public trace passes privacy and validator checks.
