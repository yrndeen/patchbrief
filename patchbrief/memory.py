"""Lightweight local memory store recording prior patch briefs for repeat sessions."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patchbrief.security import redact_secrets


@dataclass(frozen=True)
class BriefMemoryEntry:
    timestamp_utc: str
    session_id: str
    source_path: str
    output_path: str
    title: str
    summary_line: str
    model: str


def append_brief_memory(memory_file: Path, entry: BriefMemoryEntry) -> None:
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(entry)
    payload["title"] = redact_secrets(payload["title"])
    payload["summary_line"] = redact_secrets(payload["summary_line"])
    with memory_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_brief_memory(memory_file: Path, limit: int = 5) -> list[dict[str, Any]]:
    if not memory_file.exists():
        return []
    entries: list[dict[str, Any]] = []
    for raw_line in memory_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries[-limit:]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
