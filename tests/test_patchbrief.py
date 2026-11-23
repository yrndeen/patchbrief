"""Unit and integration tests for Patchbrief (OpenMinion 0.1 + local Ollama)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from patchbrief.agent import PatchbriefRunner
from patchbrief.memory import load_brief_memory
from patchbrief.quality import source_commands, unquote_prose_checks
from patchbrief.security import redact_secrets, resolve_safe_path
from patchbrief.tools import build_patchbrief_tools


def _tool_map(tools: list[object]) -> dict[str, object]:
    mapping: dict[str, object] = {}
    for t in tools:
        name = getattr(t, "name", None) or getattr(t, "__name__", None)
        assert name is not None
        mapping[name] = t
    return mapping


def _call_tool(tool_obj: object, *args: object, **kwargs: object) -> object:
    if callable(tool_obj):
        return tool_obj(*args, **kwargs)
    fn = getattr(tool_obj, "func", None) or getattr(tool_obj, "fn", None) or getattr(tool_obj, "handler", None)
    assert callable(fn), f"Cannot invoke tool object: {tool_obj!r}"
    return fn(*args, **kwargs)


def test_redact_secrets_strips_keys_tokens_and_private_keys() -> None:
    raw = (
        "Authorization: Bearer super_secret_token_1234567890\n"
        "OPENAI_API_KEY=sk-testsecretkey1234567890abcd\n"
        "token: ghp_1234567890abcdefghijklmnop\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEvAIBADANBgkqhkiG9w0BAQE\n-----END PRIVATE KEY-----\n"
    )
    cleaned = redact_secrets(raw)
    assert "super_secret_token_1234567890" not in cleaned
    assert "sk-testsecretkey1234567890abcd" not in cleaned
    assert "ghp_1234567890abcdefghijklmnop" not in cleaned
    assert "MIIEvAIBADANBgkqhkiG9w0BAQE" not in cleaned
    assert "[REDACTED" in cleaned


def test_resolve_safe_path_blocks_traversal(tmp_path: Path) -> None:
    outside = tmp_path.parent / "escape.md"
    with pytest.raises(ValueError, match="outside allowed root"):
        resolve_safe_path(str(outside), allowed_root=tmp_path)


def test_read_source_refuses_missing_path(tmp_path: Path) -> None:
    state: dict[str, object] = {}
    tools = _tool_map(
        build_patchbrief_tools(
            output_root=tmp_path / "out",
            memory_file=tmp_path / "out" / "brief-memory.jsonl",
            session_id="test-missing",
            model_name="qwen2.5:1.5b",
            write_approved_checker=lambda _: True,
            state=state,
        )
    )
    with pytest.raises(FileNotFoundError):
        _call_tool(tools["read_local_patch_source"], str(tmp_path / "does-not-exist"))


def test_read_source_directory_bundle_redacts_secrets(tmp_path: Path) -> None:
    src_dir = tmp_path / "release"
    src_dir.mkdir()
    (src_dir / "NOTES.md").write_text(
        "# v0.1\nDebug log leaked Authorization: Bearer abcdefghijklmnop123456\n",
        encoding="utf-8",
    )
    state: dict[str, object] = {}
    tools = _tool_map(
        build_patchbrief_tools(
            output_root=tmp_path / "out",
            memory_file=tmp_path / "out" / "brief-memory.jsonl",
            session_id="test-bundle",
            model_name="qwen2.5:1.5b",
            write_approved_checker=lambda _: True,
            state=state,
        )
    )
    content = str(_call_tool(tools["read_local_patch_source"], str(src_dir)))
    assert "NOTES.md" in content
    assert "abcdefghijklmnop123456" not in content
    assert "[REDACTED_TOKEN]" in content
    assert state["source_read"] is True


def test_read_source_local_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "sample_git"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "dev@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Dev"], check=True)
    (repo / "app.py").write_text("print('v1')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "feat: initial worker"], check=True, capture_output=True)
    (repo / "app.py").write_text("print('v2')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-am", "fix: bound retry backoff"], check=True, capture_output=True)

    state: dict[str, object] = {}
    tools = _tool_map(
        build_patchbrief_tools(
            output_root=tmp_path / "out",
            memory_file=tmp_path / "out" / "brief-memory.jsonl",
            session_id="test-git",
            model_name="qwen2.5:1.5b",
            write_approved_checker=lambda _: True,
            state=state,
        )
    )
    summary = str(_call_tool(tools["read_local_patch_source"], str(repo)))
    assert "fix: bound retry backoff" in summary
    assert "app.py" in summary


def test_write_patch_brief_respects_approval_and_records_memory(tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    mem_file = out_dir / "brief-memory.jsonl"

    # 1. Denied write leaves disk clean
    denied_state: dict[str, object] = {}
    denied_tools = _tool_map(
        build_patchbrief_tools(
            output_root=out_dir,
            memory_file=mem_file,
            session_id="sess-deny",
            model_name="qwen2.5:1.5b",
            write_approved_checker=lambda _: False,
            state=denied_state,
        )
    )
    denied_msg = str(
        _call_tool(
            denied_tools["write_patch_brief"],
            "denied-brief.md",
            "# Patch Brief\nBody",
            "Denied Brief",
        )
    )
    assert "WRITE_DENIED" in denied_msg
    assert denied_state["write_denied"] is True
    assert not (out_dir / "denied-brief.md").exists()

    # 2. Approved write persists redacted brief and updates memory
    ok_state: dict[str, object] = {"last_source_path": "/tmp/sample"}
    ok_tools = _tool_map(
        build_patchbrief_tools(
            output_root=out_dir,
            memory_file=mem_file,
            session_id="sess-ok",
            model_name="qwen2.5:1.5b",
            write_approved_checker=lambda _: True,
            state=ok_state,
        )
    )
    ok_msg = str(
        _call_tool(
            ok_tools["write_patch_brief"],
            "v0.4.2-brief.md",
            "# Patch Brief: v0.4.2\nAvoid leaking sk-secretvalue1234567890abcd\n",
            "v0.4.2 Release",
        )
    )
    assert "WROTE_BRIEF" in ok_msg
    written = (out_dir / "v0.4.2-brief.md").read_text(encoding="utf-8")
    assert "sk-secretvalue1234567890abcd" not in written
    assert "[REDACTED_API_KEY]" in written

    entries = load_brief_memory(mem_file)
    assert len(entries) == 1
    assert entries[0]["session_id"] == "sess-ok"

    mem_text = str(_call_tool(ok_tools["load_previous_briefs"], 3))
    assert "v0.4.2 Release" in mem_text


def test_runner_diagnostics_on_missing_input_and_unreachable_ollama(tmp_path: Path) -> None:
    runner = PatchbriefRunner(
        home_root=tmp_path / "om-home",
        output_root=tmp_path / "out",
        ollama_url="http://127.0.0.1:19999",
    )
    missing = runner.run(source_path=tmp_path / "nope", approve_write=False)
    assert missing.ok is False
    assert missing.diagnostic and "Input path not found" in missing.diagnostic

    sample = tmp_path / "sample.md"
    sample.write_text("# Sample\n- fix: bug\n", encoding="utf-8")
    down = runner.run(source_path=sample, approve_write=False)
    assert down.ok is False
    assert down.diagnostic and "Ollama unreachable" in down.diagnostic


def test_check_brief_quality_flags_missing_issue_and_blurb():
    from patchbrief.agent import check_brief_quality

    thin = "# Brief\n\n## Risk Notes\n- #142 open\n\n## Release Blurb\n- fixes\n"
    warns = check_brief_quality(thin, ["#142", "#147"])
    assert "missing issue citation #147" in warns
    assert any("release blurb" in w for w in warns)

    good = (
        "# Brief\n\n## Risk Notes\n- #142 and #147 remain open\n\n## Release Blurb\n"
        "Version 0.4.2 makes CSV exports faster and fixes the login timeout.\n"
    )
    assert check_brief_quality(good, ["#142", "#147"]) == []


def test_check_brief_quality_flags_meta_blurb():
    from patchbrief.agent import check_brief_quality

    meta = "## Risk Notes\n- #142\n\n## Release Blurb\nThis patch brief includes the summary of changes and risk notes.\n"
    assert "release blurb describes the brief instead of the release" in check_brief_quality(meta, ["#142"])


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample-release"


def _fixture_source() -> str:
    from patchbrief.tools import _read_file_or_dir_bundle

    return _read_file_or_dir_bundle(FIXTURE)


def test_check_blurb_requires_a_real_change_from_the_commits():
    from patchbrief.quality import check_blurb

    src = _fixture_source()
    vague = "This release contains several important improvements for everyone."
    assert "release blurb names no real change from the commits" in check_blurb(vague, src)
    real = "Retry backoff is now capped at 60s and `queue drain` gains a `--dry-run` flag."
    assert check_blurb(real, src) == []
    # Test paths in code spans are not release changes.
    paths = "Run `pytest tests/test_worker.py` and `pytest tests/test_cli.py` before shipping."
    assert "release blurb names no real change from the commits" in check_blurb(paths, src)


def test_check_blurb_rejects_claiming_open_issues_fixed():
    from patchbrief.quality import check_blurb

    src = _fixture_source()
    by_id = "This release fixes #142 and wraps SQLite batch inserts in a transaction."
    by_words = "This release fixes the SQLite startup error on read-only mounts."
    for blurb in (by_id, by_words):
        assert "release blurb claims an open issue is fixed" in check_blurb(blurb, src)


def test_check_blurb_rejects_lists_and_guardrail_text():
    from patchbrief.quality import check_blurb

    src = _fixture_source()
    listed = "- Bound retry backoff to 60s.\n- Added a dry-run flag to queue drain."
    assert "release blurb is a list, not 1-2 plain sentences" in check_blurb(listed, src)
    guard = (
        "I couldn't provide a grounded current regulation answer because Missing live-data "
        "evidence. Please retry with live data available."
    )
    assert "release blurb is a runtime/guardrail message, not release copy" in check_blurb(guard, src)


def test_replace_blurb_only_touches_the_blurb_section():
    from patchbrief.quality import extract_blurb, replace_blurb

    md = "# Brief\n\n## Risk Notes\n- #142\n\n## Release Blurb\n- old\n\n## Extra\nkeep me\n"
    out = replace_blurb(md, "Retry backoff is capped at 60s.")
    assert extract_blurb(out) == "Retry backoff is capped at 60s."
    assert "## Risk Notes\n- #142" in out and "## Extra\nkeep me" in out and "- old" not in out


def test_write_tool_sets_stop_event_on_write_and_on_deny(tmp_path: Path) -> None:
    import threading

    for approved in (True, False):
        stop = threading.Event()
        tools = _tool_map(
            build_patchbrief_tools(
                output_root=tmp_path / "out",
                memory_file=tmp_path / "out" / "brief-memory.jsonl",
                session_id="sess-stop",
                model_name="qwen2.5:1.5b",
                write_approved_checker=lambda _, a=approved: a,
                state={},
                stop_event=stop,
            )
        )
        _call_tool(tools["write_patch_brief"], "b.md", "# Patch Brief\nBody", "B")
        assert stop.is_set()


class _FakeAgent:
    reply = ""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def run(self, *_a: object, **_k: object):
        from types import SimpleNamespace

        return SimpleNamespace(text=type(self).reply)

    def close(self) -> None:
        pass


def _repair(monkeypatch, tmp_path: Path, reply: str):
    import patchbrief.agent as agent_mod

    monkeypatch.setattr(agent_mod, "Agent", type("A", (_FakeAgent,), {"reply": reply}))
    out = tmp_path / "out"
    state: dict[str, object] = {}
    tools = build_patchbrief_tools(
        output_root=out,
        memory_file=out / "brief-memory.jsonl",
        session_id="sess-repair",
        model_name="qwen2.5:1.5b",
        write_approved_checker=lambda _: True,
        state=state,
    )
    brief = out / "v.md"
    out.mkdir(parents=True)
    brief.write_text(
        "# Patch Brief\n\n## Risk Notes\n- #142 #147 #151\n\n## Release Blurb\n- Migration? No.\n",
        encoding="utf-8",
    )
    runner = PatchbriefRunner(home_root=tmp_path / "om", output_root=out)
    src = _fixture_source()
    status, warnings, detail = runner._repair_blurb(
        config_path=tmp_path / "agents.json",
        session_id="sess-repair",
        timeout_seconds=5,
        written_path=brief,
        warnings=["release blurb is a list, not 1-2 plain sentences"],
        issue_ids=["#142", "#147", "#151"],
        source_context=src,
        write_tool=tools[2],
        output_filename="v.md",
        title="Patch Brief",
    )
    return status, warnings, detail, brief.read_text(encoding="utf-8")


def test_blurb_repair_accepts_a_passing_model_reply(monkeypatch, tmp_path: Path) -> None:
    reply = "- Retry backoff is now capped at 60s.\n- `queue drain` gains a `--dry-run` flag."
    status, warnings, detail, text = _repair(monkeypatch, tmp_path, reply)
    assert (status, warnings, detail) == ("accepted", [], None)
    assert "Retry backoff is now capped at 60s. `queue drain` gains a `--dry-run` flag." in text
    assert "Migration? No." not in text


def test_blurb_repair_rejects_a_bad_reply_and_keeps_the_file(monkeypatch, tmp_path: Path) -> None:
    status, warnings, detail, text = _repair(
        monkeypatch, tmp_path, "This release fixes the SQLite startup error on read-only mounts."
    )
    assert status == "rejected"
    assert warnings == ["release blurb is a list, not 1-2 plain sentences"]
    assert detail and "claims an open issue is fixed" in detail
    assert "- Migration? No." in text


def test_redaction_keeps_closing_backtick_after_bearer_placeholder() -> None:
    line = "- #151: Redact `Authorization: Bearer ...` headers in debug logs."
    out = redact_secrets(line)
    assert "`Authorization: Bearer [REDACTED_TOKEN]` headers" in out
    assert out.count("`") == 2
    real = redact_secrets("`Authorization: Bearer abcdefghijklmnop123456`")
    assert real == "`Authorization: Bearer [REDACTED_TOKEN]`"


def test_unquote_prose_checks_only_touches_plain_english_spans() -> None:
    source = "Recommended checks: `pytest tests/test_worker.py`, and a local SIGTERM soak check."
    brief = (
        "## Risk Notes\n- `keep this code span` as is\n\n"
        "## Suggested Test Commands\n- `pytest tests/test_worker.py`\n"
        "- `local SIGTERM soak check`\n- `pytest -k retry`\n\n"
        "## Release Blurb\nText.\n"
    )
    out = unquote_prose_checks(brief, source_commands(source))
    assert "- local SIGTERM soak check\n" in out
    assert "- `pytest tests/test_worker.py`" in out
    assert "- `pytest -k retry`" in out
    assert "`keep this code span`" in out


def test_write_tool_unquotes_prose_checks(tmp_path: Path) -> None:
    tools = _tool_map(
        build_patchbrief_tools(
            output_root=tmp_path,
            memory_file=tmp_path / "mem.jsonl",
            session_id="s",
            model_name="m",
            write_approved_checker=lambda _p: True,
            state={},
            known_commands={"pytest tests/test_cli.py"},
        )
    )
    _call_tool(
        tools["write_patch_brief"],
        "b.md",
        "# Patch Brief: x\n## Suggested Test Commands\n- `pytest tests/test_cli.py`\n- `local SIGTERM soak check`\n",
    )
    text = (tmp_path / "b.md").read_text()
    assert "- `pytest tests/test_cli.py`" in text
    assert "- local SIGTERM soak check" in text
