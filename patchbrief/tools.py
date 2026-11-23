"""OpenMinion @tool definitions for Patchbrief."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Callable

from openminion import tool

from patchbrief.memory import BriefMemoryEntry, append_brief_memory, load_brief_memory, utc_now_iso
from patchbrief.quality import unquote_prose_checks
from patchbrief.security import redact_secrets, resolve_safe_path


def _read_git_repo_summary(repo_dir: Path, max_commits: int = 8) -> str:
    """Read recent git commits and diffstat from a local repository (read-only)."""
    log_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "log", f"-n{max_commits}", "--oneline"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if log_proc.returncode != 0:
        raise RuntimeError(f"git log failed: {log_proc.stderr.strip()}")

    diff_proc = subprocess.run(
        ["git", "-C", str(repo_dir), "diff", "--stat", "HEAD~1..HEAD"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    diff_stat = diff_proc.stdout.strip() if diff_proc.returncode == 0 else "(single commit or no parent diff)"
    return (
        f"Source type: local git repository ({repo_dir.name})\n\n"
        f"Recent commits:\n{log_proc.stdout.strip()}\n\n"
        f"Latest commit diffstat:\n{diff_stat}\n"
    )


def _read_file_or_dir_bundle(target: Path, max_bytes: int = 16_000) -> str:
    """Read a local markdown/text file or directory of release notes/issues."""
    if target.is_file():
        raw = target.read_text(encoding="utf-8", errors="replace")[:max_bytes]
        return f"Source type: file ({target.name})\n\n{raw}"

    if (target / ".git").exists():
        return _read_git_repo_summary(target)

    files = sorted(
        p for p in target.iterdir()
        if p.is_file() and p.suffix.lower() in {".md", ".txt", ".diff", ".patch", ".json"}
    )
    if not files:
        raise FileNotFoundError(
            f"No readable release/issue files (.md, .txt, .diff, .patch, .json) found in {target}"
        )

    chunks: list[str] = [f"Source type: directory bundle ({target.name})"]
    remaining = max_bytes
    for fpath in files:
        if remaining <= 0:
            break
        text = fpath.read_text(encoding="utf-8", errors="replace")[:remaining]
        remaining -= len(text)
        chunks.append(f"--- FILE: {fpath.name} ---\n{text}")
    return "\n\n".join(chunks)


def build_patchbrief_tools(
    *,
    output_root: Path,
    memory_file: Path,
    session_id: str,
    model_name: str,
    write_approved_checker: Callable[[str], bool],
    state: dict[str, object],
    stop_event: threading.Event | None = None,
    known_commands: set[str] | None = None,
) -> list[object]:
    """Create bound OpenMinion @tool callables for one Patchbrief run."""

    @tool(
        name="read_local_patch_source",
        description=(
            "Read a local git repository path, changelog file, or release-notes directory "
            "and return secret-redacted commit/issue context for drafting a patch brief."
        ),
        idempotent=True,
        tags=("patchbrief", "read"),
    )
    def read_local_patch_source(source_path: str) -> str:
        target = Path(source_path).expanduser().resolve()
        if not target.exists():
            raise FileNotFoundError(f"Input source path does not exist: {target}")
        raw = _read_file_or_dir_bundle(target)
        redacted = redact_secrets(raw)
        state["last_source_path"] = str(target)
        state["source_read"] = True
        return redacted

    @tool(
        name="load_previous_briefs",
        description=(
            "Load the most recent recorded patch briefs from local memory so this run can "
            "note continuity or regressions since the last brief."
        ),
        idempotent=True,
        tags=("patchbrief", "memory"),
    )
    def load_previous_briefs(limit: int = 3) -> str:
        entries = load_brief_memory(memory_file, limit=max(1, min(limit, 10)))
        state["memory_checked"] = True
        if not entries:
            return "No previous patch briefs recorded yet."
        lines = []
        for idx, item in enumerate(entries, start=1):
            lines.append(
                f"{idx}. [{item.get('timestamp_utc', 'unknown')}] "
                f"{item.get('title', 'Untitled')} -> {item.get('output_path', '')} "
                f"(summary: {item.get('summary_line', '')})"
            )
        return "\n".join(lines)

    @tool(
        name="write_patch_brief",
        description=(
            "Write a structured Markdown patch brief to a filename under the allowed output "
            "directory. Requires explicit operator approval before writing."
        ),
        dangerous=True,
        idempotent=False,
        tags=("patchbrief", "write"),
    )
    def write_patch_brief(filename: str, markdown_content: str, title: str = "Patch Brief") -> str:
        if not filename or not filename.strip():
            raise ValueError("filename must be non-empty")
        safe_name = Path(filename.strip()).name
        if not safe_name.endswith(".md"):
            safe_name = f"{safe_name}.md"
        dest = resolve_safe_path(str(output_root / safe_name), allowed_root=output_root)

        if not write_approved_checker(str(dest)):
            state["write_denied"] = True
            if stop_event is not None:
                stop_event.set()
            return f"WRITE_DENIED: Operator declined writing patch brief to {dest}."

        cleaned = redact_secrets(markdown_content.strip()) + "\n"
        if known_commands is not None:
            cleaned = unquote_prose_checks(cleaned, known_commands)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(cleaned, encoding="utf-8")

        first_line = next(
            (ln.strip().lstrip("# ").strip() for ln in cleaned.splitlines() if ln.strip()),
            title,
        )
        append_brief_memory(
            memory_file,
            BriefMemoryEntry(
                timestamp_utc=utc_now_iso(),
                session_id=session_id,
                source_path=str(state.get("last_source_path", "unknown")),
                output_path=str(dest),
                title=title,
                summary_line=first_line[:160],
                model=model_name,
            ),
        )
        state["written_path"] = str(dest)
        if stop_event is not None:
            stop_event.set()  # the brief is saved; end the turn instead of re-prompting
        return f"WROTE_BRIEF: {dest} ({len(cleaned)} bytes)"

    return [read_local_patch_source, load_previous_briefs, write_patch_brief]
