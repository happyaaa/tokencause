"""Default local-path redaction for shareable reports."""

from __future__ import annotations

from pathlib import Path
import re


def redact_text(text: str, cwd: str = "") -> str:
    value = str(text)
    home = str(Path.home())
    if cwd:
        value = value.replace(cwd.rstrip("/"), "~/project")
    if home:
        value = value.replace(home, "~")
    value = re.sub(r"~/\.claude/projects/[^/\s`'\"<>]+/[^/\s`'\"<>]+\.jsonl", "~/.claude/projects/[redacted project]/[session].jsonl", value)
    value = re.sub(r"~/\.codex/sessions/[0-9/]+/rollout-[^/\s`'\"<>]+\.jsonl", "~/.codex/sessions/[date]/[rollout].jsonl", value)
    value = re.sub(r"-Users-[^-]+-Documents-GitHub-[^/\s`'\"<>]+", "[redacted project]", value)
    value = re.sub(r"(?:~|/Users/[^/\s]+)?(?:/[^\s`'\"<>]+)*/\.env(?:\.[^\s`'\"<>]+)?", "[redacted env file]", value)
    value = re.sub(r"(?<![\w.-])\.env(?:\.[\w.-]+)?", "[redacted env file]", value)
    return value


def compact_session_id(value: str) -> str:
    if len(value) <= 16:
        return value
    return f"{value[:8]}...{value[-4:]}"
