"""OpenMinion 0.1 programmatic runtime wrapper for Patchbrief."""

from __future__ import annotations

import os
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
import re
from pathlib import Path
from typing import Any, Callable

from openminion import Agent, AgentRunResult, ProviderError, TurnTimeoutError

from patchbrief.memory import load_brief_memory
from patchbrief.quality import check_blurb, check_brief_quality, commit_subjects, extract_blurb, replace_blurb
from patchbrief.security import redact_secrets
from patchbrief.tools import _read_file_or_dir_bundle, build_patchbrief_tools

SYSTEM_INSTRUCTIONS = """You are Patchbrief, a careful local release and issue-triage assistant running on OpenMinion 0.1 + Ollama.

Given secret-redacted local release notes, git commits, or issue snapshots (and any prior brief history), produce a clean, accurate Markdown patch brief with these exact sections:
# Patch Brief: <Release or Repo Name>
## Summary of Changes
## Risk Notes
## Suggested Test Commands
## Release Blurb

Rules:
- Cite only real commit hashes, filenames, and issue IDs from the provided source. Never invent commits or issues.
- Keep it concise, actionable, and ready to paste into release notes.
- Never include API keys, bearer tokens, or private keys.
- All inputs are 100% local offline files: for any freshness metadata, always set `domain="general"`, `time_sensitive=false`, `needs_live_data=false`, `needs_sources=false`, `needs_exact_date=false`, and `answer_mode="local_only"`.
- Write concise, factual bullets under every section (never leave sections empty). If you call `write_patch_brief` and it returns `WRITE_DENIED`, stop immediately.
"""


@dataclass
class PatchbriefRunSummary:
    ok: bool
    session_id: str
    model: str
    output_text: str
    written_path: str | None
    write_denied: bool
    context_preloaded: bool
    memory_preloaded: bool
    tool_mode: str = "autonomous"
    quality_warnings: list[str] = field(default_factory=list)
    initial_quality_warnings: list[str] = field(default_factory=list)
    blurb_repair: str = "not_needed"
    stopped_after_tool: bool = False
    elapsed_seconds: float = 0.0
    diagnostic: str | None = None


def check_ollama_health(base_url: str = "http://127.0.0.1:11434") -> tuple[bool, str]:
    """Verify local Ollama daemon is reachable and list available models."""
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            if resp.status == 200:
                return True, f"Ollama reachable at {base_url} ({len(body)} bytes tags response)"
            return False, f"Ollama returned HTTP {resp.status} at {url}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return (
            False,
            f"Ollama unreachable at {base_url}: {exc}. Start it locally with `ollama serve`.",
        )


def _invoke_tool(tool_obj: object, *args: object, **kwargs: object) -> object:
    if callable(tool_obj):
        return tool_obj(*args, **kwargs)
    fn = getattr(tool_obj, "func", None) or getattr(tool_obj, "fn", None) or getattr(tool_obj, "handler", None)
    if callable(fn):
        return fn(*args, **kwargs)
    raise TypeError(f"Tool object is not directly invocable: {tool_obj!r}")




class PatchbriefRunner:
    """Runs the Patchbrief workflow through the OpenMinion 0.1 Agent SDK."""

    def __init__(
        self,
        *,
        home_root: Path,
        output_root: Path,
        model: str = "qwen2.5:1.5b-16k",
        memory_file: Path | None = None,
        ollama_url: str = "http://127.0.0.1:11434",
    ) -> None:
        self.home_root = home_root.expanduser().resolve()
        self.output_root = output_root.expanduser().resolve()
        self.model = model
        self.memory_file = (
            memory_file.expanduser().resolve()
            if memory_file is not None
            else self.output_root / "brief-memory.jsonl"
        )
        self.ollama_url = ollama_url

    def _make_approval_callback(
        self, approve_write: bool | None, state: dict[str, object], stop_event: threading.Event
    ) -> tuple[Callable[[str], bool], Callable[..., bool]]:
        def local_checker(dest_path: str) -> bool:
            if approve_write is True:
                return True
            if approve_write is False:
                return False
            reply = input(f"[Patchbrief approval] Allow writing brief to {dest_path}? [y/N]: ").strip().lower()
            return reply in {"y", "yes"}

        def sdk_approval_callback(*args: Any, **kwargs: Any) -> bool:
            if approve_write is True:
                return True
            if approve_write is False:
                approved = False
            else:
                summary = " | ".join(str(a) for a in args) if args else str(kwargs)
                reply = input(f"[OpenMinion approval] {summary} [y/N]: ").strip().lower()
                approved = reply in {"y", "yes"}
            if not approved:
                # The write was refused, so there is nothing left for this turn to do.
                state["write_denied"] = True
                stop_event.set()
            return approved

        return local_checker, sdk_approval_callback

    def run(
        self,
        *,
        source_path: Path,
        output_filename: str = "patch-brief.md",
        session_id: str = "patchbrief-default",
        approve_write: bool | None = None,
        timeout_seconds: float = 120.0,
    ) -> PatchbriefRunSummary:
        resolved_source = source_path.expanduser().resolve()
        if not resolved_source.exists():
            return PatchbriefRunSummary(
                ok=False,
                session_id=session_id,
                model=self.model,
                output_text="",
                written_path=None,
                write_denied=False,
                context_preloaded=False,
                memory_preloaded=False,
                diagnostic=f"Input path not found: {resolved_source}",
            )

        healthy, health_msg = check_ollama_health(self.ollama_url)
        if not healthy:
            return PatchbriefRunSummary(
                ok=False,
                session_id=session_id,
                model=self.model,
                output_text="",
                written_path=None,
                write_denied=False,
                context_preloaded=False,
                memory_preloaded=False,
                diagnostic=health_msg,
            )

        config_path = self.home_root / ".openminion" / "agents.json"
        if not config_path.exists():
            return PatchbriefRunSummary(
                ok=False,
                session_id=session_id,
                model=self.model,
                output_text="",
                written_path=None,
                write_denied=False,
                context_preloaded=False,
                memory_preloaded=False,
                diagnostic=(
                    f"OpenMinion config not found at {config_path}. "
                    f"Run: openminion --home-root {self.home_root} setup --provider ollama --model {self.model} --no-chat"
                ),
            )

        os.environ["OPENMINION_HOME"] = str(self.home_root)
        started = time.monotonic()
        state: dict[str, object] = {
            "last_source_path": str(resolved_source),
            "source_read": False,
            "memory_checked": False,
            "context_preloaded": False,
            "memory_preloaded": False,
            "write_denied": False,
            "written_path": None,
        }
        # Set once write_patch_brief succeeds or is denied. Passed to OpenMinion as the
        # turn's cancel_event so the tool loop stops instead of re-prompting the model.
        stop_event = threading.Event()
        local_checker, sdk_approval_callback = self._make_approval_callback(
            approve_write, state, stop_event
        )
        tools = build_patchbrief_tools(
            output_root=self.output_root,
            memory_file=self.memory_file,
            session_id=session_id,
            model_name=self.model,
            write_approved_checker=local_checker,
            state=state,
            stop_event=stop_event,
        )

        # Ground the prompt with secret-redacted local source + prior memory so small
        # local models (e.g. 1.5B–3B) stay factual while larger models can also invoke tools.
        source_context = redact_secrets(_read_file_or_dir_bundle(resolved_source))
        state["context_preloaded"] = True
        prior_entries = load_brief_memory(self.memory_file, limit=3)
        state["memory_preloaded"] = True
        prior_block = (
            "\n".join(
                f"- [{e.get('timestamp_utc')}] {e.get('title')}: {e.get('summary_line')}"
                for e in prior_entries
            )
            if prior_entries
            else "(none yet)"
        )

        issue_ids = sorted(set(re.findall(r"#\d+", source_context)), key=lambda s: int(s[1:]))
        prompt = (
            f"Draft a structured Markdown patch brief for `{resolved_source.name}` "
            f"(target filename: `{output_filename}`).\n\n"
            f"### Prior Brief Memory\n{prior_block}\n\n"
            f"### Local Source Context (secret-redacted)\n{source_context}\n\n"
            f"Call `write_patch_brief` with the target filename and a concise Markdown brief "
            f"(1-2 short bullets per section) citing the exact commit hashes and test commands from "
            f"the Local Source Context above. Under Risk Notes, cite every open issue ID that appears "
            f"in the source ({', '.join(issue_ids) or 'none'}). Under Release Blurb, write 1-2 plain "
            f"sentences (no bullets) telling users what this release fixes or adds (name the real "
            f"changes); never describe the brief itself, and never claim an open issue is fixed "
            f"(open issues are known risks; only the commits are changes)."
            f" Keep each bullet under 15 words and do "
            f"not leave any section blank."
        )

        def summary(**overrides: Any) -> PatchbriefRunSummary:
            written = state["written_path"]
            fields: dict[str, Any] = {
                "ok": False,
                "session_id": session_id,
                "model": self.model,
                "output_text": "",
                "written_path": str(written) if written else None,
                "write_denied": bool(state["write_denied"]),
                "context_preloaded": bool(state["context_preloaded"]),
                "memory_preloaded": bool(state["memory_preloaded"]),
                "elapsed_seconds": round(time.monotonic() - started, 1),
            }
            fields.update(overrides)
            return PatchbriefRunSummary(**fields)

        agent = Agent(
            name="patchbrief",
            instructions=SYSTEM_INSTRUCTIONS,
            config_path=str(config_path),
            model=self.model,
            tools=tools,
            session_id=session_id,
        )
        cleaned_text = ""
        stopped_after_tool = False
        try:
            try:
                result: AgentRunResult[Any] = agent.run(
                    prompt,
                    session_id=session_id,
                    timeout_seconds=timeout_seconds,
                    approval_callback=sdk_approval_callback,
                    cancel_event=stop_event,
                )
                cleaned_text = _clean_reply(result.text)
                session_id = result.session_id or session_id
            except RuntimeError as exc:
                # OpenMinion raises a plain RuntimeError("Turn cancelled.") once cancel_event
                # is set; that is our own stop after write_patch_brief. ProviderError and
                # TurnTimeoutError subclass RuntimeError, so they are handled below.
                if not stop_event.is_set() or isinstance(exc, (ProviderError, TurnTimeoutError)):
                    raise
                stopped_after_tool = True
        except ProviderError as exc:
            return summary(diagnostic=f"OpenMinion ProviderError: {redact_secrets(str(exc))}")
        except TurnTimeoutError as exc:
            # A write can land before the timeout; report it instead of hiding it.
            written = state["written_path"]
            return summary(
                ok=bool(written),
                quality_warnings=_file_quality(written, issue_ids, source_context),
                diagnostic=f"OpenMinion TurnTimeoutError after {timeout_seconds}s: {exc}",
            )

        try:
            if cleaned_text.startswith("I couldn't provide a grounded current"):
                return summary(
                    output_text=cleaned_text,
                    diagnostic=(
                        "OpenMinion 0.1.22 freshness guardrail triggered on local model output "
                        "(policy.py:286). Retry with a larger local Ollama model (e.g. qwen2.5:7b "
                        "or llama3.1:8b) or explicit tool-call mode."
                    ),
                )

            tool_mode = "autonomous"
            # If the model returned the brief directly without calling write_patch_brief
            # (common on 1.5B–3B local Ollama models), execute the exact same approval-gated
            # write_patch_brief @tool on the model's generated Markdown.
            if not state["written_path"] and not state["write_denied"] and cleaned_text:
                tool_mode = "assisted_fallback"
                _invoke_tool(
                    tools[2], output_filename, cleaned_text, f"Patch Brief: {resolved_source.name}"
                )

            warnings = _file_quality(state["written_path"], issue_ids, source_context)
            initial_warnings = list(warnings)
            blurb_repair, repair_detail = "not_needed", None
            if warnings and state["written_path"]:
                blurb_repair, warnings, repair_detail = self._repair_blurb(
                    config_path=config_path,
                    session_id=session_id,
                    timeout_seconds=timeout_seconds,
                    written_path=Path(str(state["written_path"])),
                    warnings=warnings,
                    issue_ids=issue_ids,
                    source_context=source_context,
                    write_tool=tools[2],
                    output_filename=output_filename,
                    title=f"Patch Brief: {resolved_source.name}",
                )

            return summary(
                ok=True,
                output_text=cleaned_text,
                tool_mode=tool_mode,
                quality_warnings=warnings,
                initial_quality_warnings=initial_warnings,
                blurb_repair=blurb_repair,
                diagnostic=repair_detail,
                stopped_after_tool=stopped_after_tool,
            )
        finally:
            agent.close()

    def _repair_blurb(
        self,
        *,
        config_path: Path,
        session_id: str,
        timeout_seconds: float,
        written_path: Path,
        warnings: list[str],
        issue_ids: list[str],
        source_context: str,
        write_tool: object,
        output_filename: str,
        title: str,
    ) -> tuple[str, list[str], str | None]:
        """One corrective turn: ask the model for a fixed blurb, keep it only if it passes.

        Returns (status, final_warnings, detail). Status is "accepted", "rejected" or
        "failed"; detail says why a reply was not used.
        Code never writes blurb text itself; it only splices the model's reply into the
        Release Blurb section and saves it through the approval-gated write tool.
        """
        original = written_path.read_text(encoding="utf-8")
        commits = "\n".join(f"- {c}" for c in commit_subjects(source_context)) or "(none found)"
        # Only the shipped commits go in: naming open issues here makes small models
        # claim them as fixed, which is exactly what the check rejects.
        repair_prompt = (
            "Write the release blurb for this local release (offline files only; "
            "answer_mode=local_only, no live data needed).\n"
            f"Commits shipped in this release:\n{commits}\n"
            "Describe only these commits, in plain words for users. Reply with ONLY 1-2 "
            "sentences: no heading, no bullets, no issue numbers."
        )
        # Sibling session: the first turn was cancelled right after its write call, so
        # OpenMinion 0.1.22 rejects a follow-up in that session ("Tool transcript
        # invariant failed": the cancelled tool batch has no persisted result).
        repair_session = f"{session_id}-blurb"
        repair_agent = Agent(
            name="patchbrief",
            instructions=SYSTEM_INSTRUCTIONS,
            config_path=str(config_path),
            model=self.model,
            tools=[],
            session_id=repair_session,
        )
        try:
            reply = _clean_reply(
                repair_agent.run(
                    repair_prompt, session_id=repair_session, timeout_seconds=timeout_seconds
                ).text
            )
        except (ProviderError, TurnTimeoutError) as exc:
            return "failed", warnings, f"{type(exc).__name__}: {redact_secrets(str(exc))}"
        finally:
            repair_agent.close()

        # Formatting only: drop a heading and bullet markers the model added anyway.
        reply = re.sub(r"^#+\s*Release Blurb\s*\n", "", reply.strip(), flags=re.I)
        blurb = " ".join(re.sub(r"^\s*[-*]\s+", "", ln).strip() for ln in reply.splitlines() if ln.strip())
        reply_warnings = check_blurb(blurb, source_context) if blurb else ["empty reply"]
        if reply_warnings:
            return "rejected", warnings, f"reply {blurb!r} failed: {'; '.join(reply_warnings)}"
        repaired = replace_blurb(original, blurb)
        result = str(_invoke_tool(write_tool, output_filename, repaired, title))
        if not result.startswith("WROTE_BRIEF"):
            return "rejected", warnings, result
        final = check_brief_quality(written_path.read_text(encoding="utf-8"), issue_ids, source_context)
        return "accepted", final, None


def _clean_reply(text: str | None) -> str:
    cleaned = redact_secrets((text or "").strip())
    if cleaned.startswith("openminion:"):
        cleaned = cleaned[len("openminion:"):].strip()
    return cleaned


def _file_quality(written: object, issue_ids: list[str], source_context: str) -> list[str]:
    if not written:
        return []
    return check_brief_quality(Path(str(written)).read_text(encoding="utf-8"), issue_ids, source_context)
