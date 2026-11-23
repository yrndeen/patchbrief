# Release Candidate v0.4.2 — Local Queue Worker

## Commits Since v0.4.1
- `a18c42f` fix(worker): bound retry backoff to 60s and stop infinite retry loops on 400 Bad Request
- `b72e190` feat(cli): add `--dry-run` flag to `queue drain` so operators can inspect pending jobs without mutating state
- `c9013aa` fix(storage): wrap SQLite batch insert in an explicit transaction to prevent partial writes after SIGTERM
- `d44f812` chore(deps): bump `httpx` from 0.27.2 to 0.28.1

## Known Open Issues
- #142: When the SQLite file sits on a read-only mount, the worker logs a generic `OperationalError` instead of an actionable startup error.
- #147: `queue drain --dry-run` still exits 0 even if 1+ jobs have expired TTLs; operators asked for a warning line in stdout.

## Notes
- Migration required? No schema changes.
- Breaking changes? None.
- Recommended checks: `pytest tests/test_worker.py`, `pytest tests/test_cli.py`, and a local SIGTERM soak check.
