"""Codex report cache serialization."""

from pathlib import Path
from typing import Any
import hashlib

from tokencause.constants import JSON_TEXT_PREVIEW_LIMIT
from tokencause.core.diagnosis import build_broad_exploration, build_environment_issues
from tokencause.core.models import (
    BroadExploration,
    CodexContentEvent,
    CodexExplainReport,
    CodexThread,
    EnvironmentIssue,
    RepeatedArtifact,
    RepeatedChunk,
    RetryLoop,
    SessionDrift,
)
from tokencause.core.tokens import short_preview
from tokencause.core.values import as_float, as_int

CODEX_CACHE_SCHEMA_VERSION = "codex-report-v2"


def codex_report_cache_key(thread: CodexThread) -> str:
    stat = thread.rollout_path.stat()
    source = f"{CODEX_CACHE_SCHEMA_VERSION}|{thread.rollout_path.resolve()}|{stat.st_mtime_ns}|{stat.st_size}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def codex_thread_to_json(thread: CodexThread) -> dict[str, Any]:
    return {
        "id": thread.id,
        "title": short_preview(thread.title, JSON_TEXT_PREVIEW_LIMIT),
        "rollout_path": str(thread.rollout_path),
        "cwd": thread.cwd,
        "updated_at": thread.updated_at,
        "tokens_used": thread.tokens_used,
    }


def codex_thread_from_json(data: dict[str, Any]) -> CodexThread:
    return CodexThread(
        id=str(data.get("id") or ""),
        title=str(data.get("title") or ""),
        rollout_path=Path(str(data.get("rollout_path") or "")),
        cwd=str(data.get("cwd") or ""),
        updated_at=as_int(data.get("updated_at")),
        tokens_used=as_int(data.get("tokens_used")),
    )


def codex_content_event_to_json(event: CodexContentEvent) -> dict[str, Any]:
    return {
        "category": event.category,
        "tokens": event.tokens,
        "preview": event.preview,
        "timestamp": event.timestamp,
        "file_refs": list(event.file_refs),
        "command": event.command,
        "content_hash": event.content_hash,
    }


def codex_content_event_from_json(data: dict[str, Any]) -> CodexContentEvent:
    file_refs = data.get("file_refs") if isinstance(data.get("file_refs"), list) else []
    return CodexContentEvent(
        category=str(data.get("category") or ""),
        tokens=as_int(data.get("tokens")),
        preview=str(data.get("preview") or ""),
        timestamp=str(data.get("timestamp") or ""),
        file_refs=tuple(str(item) for item in file_refs),
        command=str(data.get("command") or ""),
        content_hash=str(data.get("content_hash") or ""),
    )


def repeated_chunk_from_json(data: dict[str, Any]) -> RepeatedChunk:
    return RepeatedChunk(
        content_hash=str(data.get("content_hash") or ""),
        count=as_int(data.get("count")),
        tokens_each=as_int(data.get("tokens_each")),
        duplicate_tokens=as_int(data.get("duplicate_tokens")),
        category=str(data.get("category") or ""),
        preview=str(data.get("preview") or ""),
    )


def repeated_artifact_from_json(data: dict[str, Any]) -> RepeatedArtifact:
    categories = data.get("categories") if isinstance(data.get("categories"), list) else []
    return RepeatedArtifact(
        file_ref=str(data.get("file_ref") or ""),
        count=as_int(data.get("count")),
        tokens=as_int(data.get("tokens")),
        categories=tuple(str(category) for category in categories),
    )


def retry_loop_from_json(data: dict[str, Any]) -> RetryLoop:
    return RetryLoop(
        key=str(data.get("key") or ""),
        count=as_int(data.get("count")),
        tokens=as_int(data.get("tokens")),
        command=str(data.get("command") or ""),
        preview=str(data.get("preview") or ""),
    )


def session_drift_from_json(data: dict[str, Any] | None) -> SessionDrift | None:
    if not isinstance(data, dict):
        return None
    return SessionDrift(
        early_avg_tokens=as_int(data.get("early_avg_tokens")),
        late_avg_tokens=as_int(data.get("late_avg_tokens")),
        ratio=as_float(data.get("ratio")),
        peak_tokens=as_int(data.get("peak_tokens")),
        samples=as_int(data.get("samples")),
    )


def session_drift_to_json(drift: SessionDrift | None) -> dict[str, Any] | None:
    if drift is None:
        return None
    return {
        "early_avg_tokens": drift.early_avg_tokens,
        "late_avg_tokens": drift.late_avg_tokens,
        "ratio": drift.ratio,
        "peak_tokens": drift.peak_tokens,
        "samples": drift.samples,
    }


def environment_issue_from_json(data: dict[str, Any]) -> EnvironmentIssue:
    return EnvironmentIssue(
        kind=str(data.get("kind") or ""),
        count=as_int(data.get("count")),
        tokens=as_int(data.get("tokens")),
        command=str(data.get("command") or ""),
        preview=str(data.get("preview") or ""),
    )


def broad_exploration_from_json(data: dict[str, Any] | None) -> BroadExploration | None:
    if not isinstance(data, dict):
        return None
    examples = data.get("examples", [])
    return BroadExploration(
        search_commands=as_int(data.get("search_commands")),
        broad_commands=as_int(data.get("broad_commands")),
        unique_files=as_int(data.get("unique_files")),
        search_tokens=as_int(data.get("search_tokens")),
        command_tokens=as_int(data.get("command_tokens")),
        examples=tuple(str(item) for item in examples if str(item).strip()) if isinstance(examples, list) else (),
    )


def broad_exploration_to_json(exploration: BroadExploration | None) -> dict[str, Any] | None:
    if exploration is None:
        return None
    return {
        "search_commands": exploration.search_commands,
        "broad_commands": exploration.broad_commands,
        "unique_files": exploration.unique_files,
        "search_tokens": exploration.search_tokens,
        "command_tokens": exploration.command_tokens,
        "examples": list(exploration.examples),
    }


def codex_report_to_json(report: CodexExplainReport) -> dict[str, Any]:
    return {
        "schema": CODEX_CACHE_SCHEMA_VERSION,
        "thread": codex_thread_to_json(report.thread),
        "content_events": [codex_content_event_to_json(event) for event in report.content_events],
        "usage_events": report.usage_events,
        "category_tokens": report.category_tokens,
        "file_tokens": report.file_tokens,
        "command_tokens": report.command_tokens,
        "repeated_hashes": report.repeated_hashes,
        "repeated_chunks": [chunk.__dict__ for chunk in report.repeated_chunks],
        "repeated_artifacts": [
            {
                "file_ref": artifact.file_ref,
                "count": artifact.count,
                "tokens": artifact.tokens,
                "categories": list(artifact.categories),
            }
            for artifact in report.repeated_artifacts
        ],
        "long_tool_outputs": [codex_content_event_to_json(event) for event in report.long_tool_outputs],
        "failure_events": [codex_content_event_to_json(event) for event in report.failure_events],
        "retry_loops": [loop.__dict__ for loop in report.retry_loops],
        "session_drift": session_drift_to_json(report.session_drift),
        "environment_issues": [issue.__dict__ for issue in report.environment_issues],
        "broad_exploration": broad_exploration_to_json(report.broad_exploration),
    }


def codex_report_from_json(data: dict[str, Any]) -> CodexExplainReport:
    if data.get("schema") != CODEX_CACHE_SCHEMA_VERSION:
        raise ValueError("Unsupported Codex cache schema")
    content_events = [
        codex_content_event_from_json(item)
        for item in data.get("content_events", [])
        if isinstance(item, dict)
    ]
    return CodexExplainReport(
        thread=codex_thread_from_json(data.get("thread") if isinstance(data.get("thread"), dict) else {}),
        content_events=content_events,
        usage_events=[item for item in data.get("usage_events", []) if isinstance(item, dict)],
        category_tokens={str(key): as_int(value) for key, value in dict(data.get("category_tokens", {})).items()},
        file_tokens={str(key): as_int(value) for key, value in dict(data.get("file_tokens", {})).items()},
        command_tokens={str(key): as_int(value) for key, value in dict(data.get("command_tokens", {})).items()},
        repeated_hashes={str(key): as_int(value) for key, value in dict(data.get("repeated_hashes", {})).items()},
        repeated_chunks=[
            repeated_chunk_from_json(item)
            for item in data.get("repeated_chunks", [])
            if isinstance(item, dict)
        ],
        repeated_artifacts=[
            repeated_artifact_from_json(item)
            for item in data.get("repeated_artifacts", [])
            if isinstance(item, dict)
        ],
        long_tool_outputs=[
            codex_content_event_from_json(item)
            for item in data.get("long_tool_outputs", [])
            if isinstance(item, dict)
        ],
        failure_events=[
            codex_content_event_from_json(item)
            for item in data.get("failure_events", [])
            if isinstance(item, dict)
        ],
        retry_loops=[
            retry_loop_from_json(item)
            for item in data.get("retry_loops", [])
            if isinstance(item, dict)
        ],
        session_drift=session_drift_from_json(data.get("session_drift") if isinstance(data.get("session_drift"), dict) else None),
        environment_issues=[
            environment_issue_from_json(item)
            for item in data.get("environment_issues", [])
            if isinstance(item, dict)
        ] or build_environment_issues(content_events),
        broad_exploration=(
            broad_exploration_from_json(data.get("broad_exploration") if isinstance(data.get("broad_exploration"), dict) else None)
            or build_broad_exploration(
                content_events,
                {str(key): as_int(value) for key, value in dict(data.get("file_tokens", {})).items()},
                {str(key): as_int(value) for key, value in dict(data.get("command_tokens", {})).items()},
            )
        ),
    )
