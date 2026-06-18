"""Canonical TokenCause session trace schema helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CodexExplainReport, SessionEvent, SessionTrace, TokenUsage, TraceEvent
from .tokens import estimate_tokens, short_preview


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_present(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_string_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _usage_dict(raw: dict[str, Any]) -> dict[str, Any]:
    usage = raw.get("usage")
    return usage if isinstance(usage, dict) else {}


def _usage_int(raw: dict[str, Any], usage: dict[str, Any], keys: tuple[str, ...]) -> int:
    compact_keys = tuple(key.replace("_", "") for key in keys)
    return _as_int(_first_present(raw, keys, _first_present(usage, keys + compact_keys, 0)))


def infer_session_event_category(raw: dict[str, Any]) -> str:
    category = str(_first_present(raw, ("category", "event_category", "event_type", "type"), "")).strip()
    if category:
        return category

    status = str(_first_present(raw, ("status", "outcome"), "ok")).lower()
    error = str(_first_present(raw, ("error", "error_message", "exception"), ""))
    command = str(_first_present(raw, ("command", "cmd"), ""))
    tool = str(_first_present(raw, ("tool", "tool_name", "toolName"), ""))
    step = str(_first_present(raw, ("step", "name", "span_name", "operation"), ""))
    text = str(_first_present(raw, ("text", "preview", "content", "message", "output"), ""))
    lower = f"{step}\n{tool}\n{command}\n{text}\n{error}".lower()

    if status not in ("ok", "success", "succeeded", "") or error:
        return "error_log"
    if command or tool not in ("", "none", "assistant"):
        if any(hint in lower for hint in ("pytest", "unittest", "test failed", "assertionerror")):
            return "test_log"
        if any(hint in lower for hint in ("pip install", "npm install", "pnpm install", "yarn install")):
            return "install_log"
        if any(hint in lower for hint in ("rg ", "grep ", "find ", "fd ", "tree", "ls -r")):
            return "search_output"
        return "tool_output"
    if str(_first_present(raw, ("role",), "")).lower() == "user":
        return "user_message"
    return "assistant_message"


def parse_session_event(raw: dict[str, Any], index: int) -> SessionEvent:
    usage = _usage_dict(raw)
    text = str(_first_present(raw, ("text", "preview", "content", "message", "output"), ""))
    input_tokens = _usage_int(raw, usage, ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"))
    output_tokens = _usage_int(raw, usage, ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"))
    tokens = _as_int(_first_present(raw, ("tokens", "estimated_tokens", "observable_tokens"), 0))
    if tokens <= 0:
        tokens = input_tokens + output_tokens
    if tokens <= 0 and text:
        tokens = estimate_tokens(text)

    return SessionEvent(
        raw=raw,
        index=index,
        category=infer_session_event_category(raw),
        tokens=tokens,
        preview=short_preview(str(_first_present(raw, ("preview",), text)), 240),
        timestamp=str(_first_present(raw, ("timestamp", "time", "created_at"), "")),
        command=str(_first_present(raw, ("command", "cmd"), "")),
        file_refs=_normalize_string_tuple(_first_present(raw, ("file_refs", "files", "context_items", "documents"), None)),
        content_hash=str(_first_present(raw, ("content_hash", "context_hash", "prompt_hash", "contextHash"), "")),
        model=str(_first_present(raw, ("model", "model_name", "modelName"), "")),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=_as_float(_first_present(raw, ("cost_usd", "cost", "spend"), 0.0)),
        latency_ms=_as_int(_first_present(raw, ("latency_ms", "duration_ms", "elapsed_ms"), 0)),
        status=str(_first_present(raw, ("status", "outcome"), "ok")).lower(),
        error=str(_first_present(raw, ("error", "error_message", "exception"), "")),
        step=str(_first_present(raw, ("step", "name", "span_name", "operation"), "")),
        tool=str(_first_present(raw, ("tool", "tool_name", "toolName"), "")),
    )


def parse_token_usage(raw: dict[str, Any]) -> TokenUsage:
    usage = _usage_dict(raw)
    input_tokens = _usage_int(raw, usage, ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens"))
    cached_input_tokens = _usage_int(raw, usage, ("cached_input_tokens", "cache_read_input_tokens", "cacheReadInputTokens"))
    output_tokens = _usage_int(raw, usage, ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"))
    total_tokens = _usage_int(raw, usage, ("total_tokens", "totalTokens"))
    if total_tokens <= 0:
        total_tokens = input_tokens + cached_input_tokens + output_tokens
    return TokenUsage(
        input_tokens=input_tokens,
        cached_input_tokens=cached_input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


def load_session_trace_jsonl(path: Path, source: str = "tokencause_trace") -> SessionTrace:
    events: list[SessionEvent] = []
    usage_events: list[TokenUsage] = []
    session_id = ""
    title = ""
    cwd = ""

    with path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{index}: invalid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path}:{index}: expected a JSON object")

            session_id = session_id or str(_first_present(raw, ("session_id", "run_id", "runId", "trace_id"), ""))
            title = title or str(_first_present(raw, ("session_title", "title"), ""))
            cwd = cwd or str(_first_present(raw, ("cwd", "working_directory"), ""))

            kind = str(_first_present(raw, ("kind", "category", "type"), "")).lower()
            if kind in ("token_usage", "usage"):
                usage_events.append(parse_token_usage(raw))
                continue
            event = parse_session_event(raw, index)
            events.append(event)
            if event.total_tokens:
                usage_events.append(
                    TokenUsage(
                        input_tokens=event.input_tokens,
                        output_tokens=event.output_tokens,
                        total_tokens=event.total_tokens,
                    )
                )

    return SessionTrace(
        id=session_id or path.stem,
        source=source,
        title=title,
        cwd=cwd,
        events=events,
        usage_events=usage_events,
    )


def session_trace_to_trace_events(trace: SessionTrace) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    for index, event in enumerate(trace.events, start=1):
        input_tokens = event.input_tokens
        output_tokens = event.output_tokens
        if input_tokens + output_tokens <= 0:
            input_tokens = event.tokens
        events.append(
            TraceEvent(
                raw=event.raw,
                index=event.index or index,
                run_id=trace.id,
                step=event.step or event.category or "unknown",
                model=event.model or "unknown",
                tool=event.tool or ("shell" if event.command else "none"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=event.cost_usd,
                latency_ms=event.latency_ms,
                status=event.status,
                error=event.error,
                context_hash=event.content_hash,
                context_items=event.file_refs,
            )
        )
    return events


def trace_event_to_session_event(event: TraceEvent) -> SessionEvent:
    raw = event.raw
    message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
    tool_result = raw.get("toolUseResult")
    command = ""
    preview = event.error or event.step
    if isinstance(tool_result, dict):
        command = str(tool_result.get("command") or tool_result.get("commandName") or tool_result.get("tool") or "")
        preview = str(tool_result.get("output") or tool_result.get("content") or tool_result)
    elif isinstance(tool_result, (list, str)):
        preview = str(tool_result)
    elif isinstance(message.get("content"), str):
        preview = str(message.get("content"))

    category_tool = event.tool
    if event.tool == "claude-message":
        category_tool = "none"
    raw_for_category = {
        "status": event.status,
        "error": event.error,
        "command": command,
        "tool": category_tool,
        "step": event.step,
        "preview": preview,
    }
    if event.tool == "claude-tool-result":
        raw_for_category["type"] = ""

    tokens = event.total_tokens or estimate_tokens(preview)
    return SessionEvent(
        raw=raw,
        index=event.index,
        category=infer_session_event_category(raw_for_category),
        tokens=tokens,
        preview=short_preview(preview, 240),
        command=command,
        file_refs=event.context_items,
        content_hash=event.context_hash,
        model=event.model,
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        cost_usd=event.cost_usd,
        latency_ms=event.latency_ms,
        status=event.status,
        error=event.error,
        step=event.step,
        tool=event.tool,
    )


def trace_events_to_session_trace(
    events: list[TraceEvent],
    *,
    session_id: str,
    source: str,
    title: str = "",
    cwd: str = "",
) -> SessionTrace:
    session_events = [trace_event_to_session_event(event) for event in events]
    usage_events: list[TokenUsage] = []
    for event in events:
        message = event.raw.get("message") if isinstance(event.raw.get("message"), dict) else {}
        usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
        cache_read = _as_int(usage.get("cache_read_input_tokens"))
        cache_write = _as_int(usage.get("cache_creation_input_tokens"))
        usage_events.append(
            TokenUsage(
                input_tokens=event.input_tokens,
                cached_input_tokens=cache_read + cache_write,
                output_tokens=event.output_tokens,
                total_tokens=event.total_tokens,
            )
        )
    return SessionTrace(
        id=session_id,
        source=source,
        title=title,
        cwd=cwd,
        events=session_events,
        usage_events=usage_events,
    )


def codex_report_to_session_trace(report: CodexExplainReport) -> SessionTrace:
    def to_session_event(event: CodexContentEvent) -> SessionEvent:
        return SessionEvent(
            category=event.category,
            tokens=event.tokens,
            preview=event.preview,
            timestamp=event.timestamp,
            command=event.command,
            file_refs=event.file_refs,
            content_hash=event.content_hash,
        )

    session_events = [to_session_event(event) for event in report.content_events]
    return SessionTrace(
        id=report.thread.id,
        source="codex",
        title=report.thread.title,
        cwd=report.thread.cwd,
        events=session_events,
        usage_events=[
            TokenUsage(
                input_tokens=_as_int(usage.get("input_tokens", 0)),
                cached_input_tokens=_as_int(usage.get("cached_input_tokens", 0)),
                output_tokens=_as_int(usage.get("output_tokens", 0)),
                total_tokens=_as_int(usage.get("total_tokens", 0)),
            )
            for usage in report.usage_events
        ],
        repeated_chunks=report.repeated_chunks,
        repeated_artifacts=report.repeated_artifacts,
        long_tool_outputs=[to_session_event(event) for event in report.long_tool_outputs],
        retry_loops=report.retry_loops,
        session_drift=report.session_drift,
        environment_issues=report.environment_issues,
        broad_exploration=report.broad_exploration,
    )
