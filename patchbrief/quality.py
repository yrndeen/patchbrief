"""Deterministic quality checks for a written patch brief (no text generation)."""

from __future__ import annotations

import re

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "bad", "be", "by", "can", "even", "for", "from",
    "if", "in", "into", "is", "it", "its", "not", "of", "on", "or", "so", "still", "that",
    "the", "their", "them", "they", "this", "to", "when", "with", "without", "after",
    "before", "instead", "add", "adds", "added", "fix", "fixes", "fixed", "feat", "chore",
    "bump", "stop", "state", "request", "operators", "asked", "line", "sits", "logs",
    "have", "should", "high", "medium", "error", "errors", "file", "security", "enabled",
    "affects", "present", "issue", "issues",
}
_COMMIT_LINE = re.compile(r"^\s*[-*]?\s*`?([0-9a-f]{7,40})`?\s+(.+)$", re.M)
_ISSUE_LINE = re.compile(r"#\d+\b[^\n]*")
_FIX_CLAIM = re.compile(r"\b(fix(es|ed)?|resolv(e|es|ed)|clos(e|es|ed)|address(es|ed)?)\b", re.I)


def _words(text: str) -> set[str]:
    words = set()
    for w in re.findall(r"[a-z][a-z0-9_-]{3,}", text.lower()):
        if w in _STOPWORDS:
            continue
        words.add(w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w)
    return words


def _prose(text: str) -> str:
    """Drop path-like code spans so `tests/test_worker.py` doesn't count as a change."""
    return re.sub(r"`[^`]*[/.][^`]*`", " ", text)


def commit_subjects(source_context: str) -> list[str]:
    """Commit subject lines (`<hash> <subject>`) found in the source context."""
    return [m.group(2).strip() for m in _COMMIT_LINE.finditer(source_context)]


def change_terms(source_context: str) -> set[str]:
    """Distinctive words from commit subjects: what this release actually changes."""
    terms: set[str] = set()
    for subject in commit_subjects(source_context):
        subject = re.sub(r"^\w+(\(([^)]*)\))?!?:\s*", r"\2 ", subject)
        terms |= _words(_prose(subject))
    return terms


def open_issue_terms(source_context: str) -> set[str]:
    """Words that only describe open issues, never a shipped commit."""
    issue_words: set[str] = set()
    for line in _ISSUE_LINE.findall(source_context):
        issue_words |= _words(_prose(line))
    return issue_words - change_terms(source_context)


def extract_blurb(markdown: str) -> str:
    m = re.search(r"#+\s*Release Blurb\s*\n(.*?)(?:\n#+\s|\Z)", markdown, re.S | re.I)
    return m.group(1).strip() if m else ""


def replace_blurb(markdown: str, new_blurb: str) -> str:
    """Swap the Release Blurb body for model-written text, leaving other sections as-is."""
    m = re.search(r"(#+\s*Release Blurb\s*\n)(.*?)(\n#+\s|\Z)", markdown, re.S | re.I)
    if not m:
        return markdown.rstrip() + f"\n\n## Release Blurb\n\n{new_blurb.strip()}\n"
    return markdown[: m.start(2)] + f"\n{new_blurb.strip()}\n" + markdown[m.end(2):]


def check_blurb(blurb: str, source_context: str) -> list[str]:
    warnings: list[str] = []
    if len(re.findall(r"[.!?](?:\s|$)", blurb)) < 1 or len(blurb.split()) < 8:
        return ["release blurb missing or under one full sentence"]
    if re.search(r"^\s*([-*]|\d+\.)\s", blurb, re.M):
        warnings.append("release blurb is a list, not 1-2 plain sentences")
    if re.search(r"\b(this|the) (patch )?brief\b|\bsections?\b", blurb, re.I):
        warnings.append("release blurb describes the brief instead of the release")
    if re.search(r"couldn't provide a grounded|live[- ]data|\bretry with\b", blurb, re.I):
        warnings.append("release blurb is a runtime/guardrail message, not release copy")
    prose_words = _words(_prose(blurb))
    terms = change_terms(source_context)
    if terms and not prose_words & terms:
        warnings.append("release blurb names no real change from the commits")
    claimed = re.findall(r"#\d+", blurb)
    issue_only = prose_words & open_issue_terms(source_context)
    if _FIX_CLAIM.search(blurb) and (claimed or issue_only):
        warnings.append("release blurb claims an open issue is fixed")
    return warnings


def check_brief_quality(markdown: str, issue_ids: list[str], source_context: str = "") -> list[str]:
    """Warnings for missing issue citations or a blurb that isn't about the real release."""
    warnings = [f"missing issue citation {i}" for i in issue_ids if i not in markdown]
    return warnings + check_blurb(extract_blurb(markdown), source_context)


_TEST_SECTION = re.compile(r"(#+\s*Suggested Test Commands\s*\n)(.*?)(?=\n#+\s|\Z)", re.S | re.I)
_PROSE_SPAN = re.compile(r"^[A-Za-z][A-Za-z ]*$")


def source_commands(source_context: str) -> set[str]:
    """Code spans in the source: the only test commands the release notes actually give."""
    return {span.strip() for span in re.findall(r"`([^`\n]+)`", source_context)}


def unquote_prose_checks(markdown: str, commands: set[str]) -> str:
    """In Suggested Test Commands, drop backticks around plain-English checks.

    A span of 3+ plain words that the source never wrote as code (e.g. "local SIGTERM soak
    check") is a manual check, not a command, so it is shown as text.
    """
    def fix_span(m: re.Match[str]) -> str:
        span = m.group(1).strip()
        if span in commands or not _PROSE_SPAN.match(span) or len(span.split()) < 3:
            return m.group(0)
        return span

    m = _TEST_SECTION.search(markdown)
    if not m:
        return markdown
    body = re.sub(r"`([^`\n]+)`", fix_span, m.group(2))
    return markdown[: m.start(2)] + body + markdown[m.end(2):]
