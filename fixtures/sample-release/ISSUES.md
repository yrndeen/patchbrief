# Issue Triage Snapshot (v0.4.2)

- **#142 (High / Ops):** Read-only SQLite mount produces unhelpful traceback at startup. Affects containerized read-only deployments.
- **#147 (Low / DX):** `--dry-run` should warn when expired jobs are present.
- **#151 (Medium / Security):** Redact `Authorization: Bearer ...` headers in debug logs when `--verbose` is enabled.
