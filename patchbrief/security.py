"""Secret redaction and safe local path guards for Patchbrief."""

from __future__ import annotations

import re
from pathlib import Path

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(ghp_[A-Za-z0-9]{20,})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"(Authorization:\s*Bearer\s+)[^\s\"'`]+"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"\b(Bearer\s+)[A-Za-z0-9._-]{16,}\b"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"(?i)((?:api[_-]?key|secret|token|password)\s*[=:]\s*)[^\s\"'`,;]+"), r"\1[REDACTED]"),
)


def redact_secrets(text: str) -> str:
    """Scrub common API keys, bearer tokens, and private keys before logging or writing."""
    cleaned = text
    for pattern, replacement in SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned


def resolve_safe_path(path_str: str, allowed_root: Path | None = None) -> Path:
    """Resolve a path and optionally ensure it stays inside allowed_root."""
    resolved = Path(path_str).expanduser().resolve()
    if allowed_root is not None:
        root = allowed_root.expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"Refusing path outside allowed root ({root}): {resolved}"
            ) from exc
    return resolved
