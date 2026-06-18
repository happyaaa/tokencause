"""Claude Code session adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from tokencause.core.files import extract_file_refs
from tokencause.core.models import ClaudeSession, TraceEvent
from tokencause.core.tokens import estimate_tokens, short_preview
from tokencause.core.values import (
    as_float,
    as_int,
    first_present,
    normalize_context_items,
)

def otel_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key in ("stringValue", "intValue", "doubleValue", "boolValue", "bytesValue"):
        if key in value:
            return value[key]
    if "arrayValue" in value and isinstance(value["arrayValue"], dict):
        values = value["arrayValue"].get("values", [])
        return [otel_value(item) for item in values]
    if "kvlistValue" in value and isinstance(value["kvlistValue"], dict):
        return otel_attributes(value["kvlistValue"].get("values", []))
    return value


def otel_attributes(items: Any) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if isinstance(items, dict):
        return {str(key): otel_value(value) for key, value in items.items()}
    if not isinstance(items, list):
        return attrs
    for item in items:
        if isinstance(item, dict) and "key" in item:
            attrs[str(item["key"])] = otel_value(item.get("value"))
    return attrs


def otel_attr(attrs: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    return first_present(attrs, keys, default)


def otel_record_name(document: dict[str, Any]) -> str:
    return str(first_present(document, ("name", "metric", "event", "event_name", "event.name", "type"), ""))


def otel_body_text(body: Any) -> str:
    value = otel_value(body)
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value or "")


def otel_metric_points(document: dict[str, Any]) -> list[tuple[str, dict[str, Any], float]]:
    points: list[tuple[str, dict[str, Any], float]] = []
    flat_name = otel_record_name(document)
    if flat_name.startswith("claude_code.") and "log" not in flat_name and not any(
        key in document for key in ("resourceMetrics", "resourceLogs", "body")
    ):
        value = first_present(document, ("asDouble", "asInt", "doubleValue", "intValue", "value"), 0)
        attrs = otel_attributes(document.get("attributes", {}))
        points.append((flat_name, attrs, as_float(value)))
        return points
    for resource_metric in document.get("resourceMetrics", []) if isinstance(document.get("resourceMetrics"), list) else []:
        if not isinstance(resource_metric, dict):
            continue
        for scope_metric in resource_metric.get("scopeMetrics", []) if isinstance(resource_metric.get("scopeMetrics"), list) else []:
            if not isinstance(scope_metric, dict):
                continue
            for metric in scope_metric.get("metrics", []) if isinstance(scope_metric.get("metrics"), list) else []:
                if not isinstance(metric, dict):
                    continue
                name = str(metric.get("name") or "")
                data = metric.get("sum") or metric.get("gauge") or {}
                if not isinstance(data, dict):
                    continue
                for point in data.get("dataPoints", []) if isinstance(data.get("dataPoints"), list) else []:
                    if not isinstance(point, dict):
                        continue
                    value = first_present(point, ("asDouble", "asInt", "doubleValue", "intValue", "value"), 0)
                    points.append((name, otel_attributes(point.get("attributes", [])), as_float(value)))
    return points


def otel_log_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    flat_name = otel_record_name(document)
    if flat_name.startswith("claude_code.") and (
        "body" in document
        or "message" in document
        or "log" in flat_name
        or "error" in flat_name
        or "tool_result" in flat_name
    ):
        records.append(document)
        return records
    for resource_log in document.get("resourceLogs", []) if isinstance(document.get("resourceLogs"), list) else []:
        if not isinstance(resource_log, dict):
            continue
        for scope_log in resource_log.get("scopeLogs", []) if isinstance(resource_log.get("scopeLogs"), list) else []:
            if not isinstance(scope_log, dict):
                continue
            for record in scope_log.get("logRecords", []) if isinstance(scope_log.get("logRecords"), list) else []:
                if isinstance(record, dict):
                    records.append(record)
    return records


def claude_otel_token_bucket(token_type: str) -> str:
    normalized = token_type.strip().lower().replace("-", "_").replace(".", "_")
    if normalized in (
        "output",
        "completion",
        "completion_token",
        "completion_tokens",
        "output_token",
        "output_tokens",
        "response",
        "response_tokens",
    ):
        return "output"
    return "input"


def parse_claude_otel_document(document: dict[str, Any], start_index: int = 1) -> list[TraceEvent]:
    metric_events: dict[tuple[str, str], dict[str, Any]] = {}
    for name, attrs, value in otel_metric_points(document):
        if not name.startswith("claude_code."):
            continue
        session_id = str(otel_attr(attrs, ("session.id", "session_id", "sessionId"), "default"))
        model = str(otel_attr(attrs, ("model", "model.name", "model_name"), "claude"))
        key = (session_id, model)
        event = metric_events.setdefault(
            key,
            {
                "run_id": session_id,
                "step": "claude_otel_metrics",
                "model": model,
                "tool": "claude-otel-metrics",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "context_items": [],
            },
        )
        token_type = str(otel_attr(attrs, ("type", "token.type", "usage.type", "usage_type"), "")).lower()
        if name == "claude_code.token.usage":
            if claude_otel_token_bucket(token_type) == "output":
                event["output_tokens"] += int(value)
            else:
                event["input_tokens"] += int(value)
        elif name == "claude_code.cost.usage":
            event["cost_usd"] += float(value)

    events: list[TraceEvent] = []
    index = start_index
    for raw in metric_events.values():
        events.append(
            TraceEvent(
                raw=raw,
                index=index,
                run_id=str(raw["run_id"]),
                step=str(raw["step"]),
                model=str(raw["model"]),
                tool=str(raw["tool"]),
                input_tokens=as_int(raw["input_tokens"]),
                output_tokens=as_int(raw["output_tokens"]),
                cost_usd=as_float(raw["cost_usd"]),
                context_items=normalize_context_items(raw["context_items"]),
            )
        )
        index += 1

    for record in otel_log_records(document):
        attrs = otel_attributes(record.get("attributes", []))
        event_name = str(
            first_present(record, ("event", "event_name", "event.name", "name", "type"), "")
            or otel_attr(attrs, ("event.name", "name", "type"), "claude_code.log")
        )
        if not event_name.startswith("claude_code."):
            continue
        body = otel_body_text(first_present(record, ("body", "message", "text"), ""))
        tool = str(otel_attr(attrs, ("tool.name", "tool_name", "tool"), event_name.replace("claude_code.", "")))
        file_refs = list(extract_file_refs(body))
        file_attr = otel_attr(attrs, ("file.path", "filePath", "file_path", "path"), "")
        if file_attr:
            file_refs.append(str(file_attr))
        status = "error" if "error" in event_name else "ok"
        events.append(
            TraceEvent(
                raw={"attributes": attrs, "body": body},
                index=index,
                run_id=str(otel_attr(attrs, ("session.id", "session_id", "sessionId"), "default")),
                step=event_name.replace("claude_code.", ""),
                model=str(otel_attr(attrs, ("model", "model.name", "model_name"), "claude")),
                tool=tool,
                input_tokens=0,
                output_tokens=estimate_tokens(body),
                cost_usd=0.0,
                latency_ms=as_int(otel_attr(attrs, ("duration_ms", "latency_ms"), 0)),
                status=status,
                error=body if status == "error" else "",
                context_items=normalize_context_items(tuple(dict.fromkeys(file_refs))),
            )
        )
        index += 1
    return events


def merge_claude_otel_metric_events(events: list[TraceEvent]) -> list[TraceEvent]:
    merged: dict[tuple[str, str], TraceEvent] = {}
    output: list[TraceEvent] = []
    for event in events:
        if event.tool != "claude-otel-metrics":
            output.append(event)
            continue
        key = (event.run_id, event.model)
        existing = merged.get(key)
        if existing is None:
            merged[key] = event
            output.append(event)
            continue
        existing.input_tokens += event.input_tokens
        existing.output_tokens += event.output_tokens
        existing.cost_usd += event.cost_usd
    for index, event in enumerate(output, start=1):
        event.index = index
    return output


def load_claude_otel(path: Path) -> list[TraceEvent]:
    documents: list[dict[str, Any]] = []
    if path.suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as file:
            for index, line in enumerate(file, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{index}: invalid JSON: {exc}") from exc
                if not isinstance(raw, dict):
                    raise ValueError(f"{path}:{index}: expected a JSON object")
                documents.append(raw)
    else:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: invalid JSON: {exc}") from exc
        if isinstance(raw, list):
            documents.extend(item for item in raw if isinstance(item, dict))
        elif isinstance(raw, dict):
            documents.append(raw)
        else:
            raise ValueError(f"{path}: expected a JSON object or array")

    events: list[TraceEvent] = []
    next_index = 1
    for document in documents:
        parsed = parse_claude_otel_document(document, start_index=next_index)
        events.extend(parsed)
        next_index += len(parsed)
    events = merge_claude_otel_metric_events(events)
    if not events:
        raise ValueError(f"No Claude OpenTelemetry records found in {path}")
    return events


def claude_projects_dir(claude_home: Path | None = None) -> Path:
    home = claude_home or Path.home() / ".claude"
    return home / "projects"


def count_jsonl_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8") as file:
            return sum(1 for line in file if line.strip())
    except OSError:
        return 0


def read_claude_session_cwd(path: Path) -> str:
    try:
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    raw = json.loads(stripped)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict) and isinstance(raw.get("cwd"), str) and raw["cwd"]:
                    return raw["cwd"]
    except OSError:
        return ""
    return ""


def load_claude_sessions(claude_home: Path | None = None, limit: int = 20) -> list[ClaudeSession]:
    root = claude_projects_dir(claude_home)
    if not root.exists():
        raise FileNotFoundError(f"Claude projects directory not found: {root}")
    sessions: list[ClaudeSession] = []
    for path in root.glob("*/*.jsonl"):
        try:
            stat = path.stat()
        except OSError:
            continue
        session_id = path.stem
        project = path.parent.name
        cwd = read_claude_session_cwd(path)
        sessions.append(
            ClaudeSession(
                id=session_id,
                path=path,
                project=project,
                cwd=cwd,
                updated_at=stat.st_mtime,
                messages=count_jsonl_lines(path),
            )
        )
    return sorted(sessions, key=lambda session: session.updated_at, reverse=True)[:limit]


def pick_claude_session(
    claude_home: Path | None = None,
    last: bool = False,
    session_id: str | None = None,
    session_file: str | None = None,
) -> ClaudeSession:
    if session_file:
        path = Path(session_file).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Claude session file not found: {path}")
        stat = path.stat()
        return ClaudeSession(
            id=path.stem,
            path=path,
            project=path.parent.name,
            cwd=read_claude_session_cwd(path),
            updated_at=stat.st_mtime,
            messages=count_jsonl_lines(path),
        )
    sessions = load_claude_sessions(claude_home, limit=200)
    if session_id:
        for session in sessions:
            if session.id.startswith(session_id):
                return session
        raise ValueError(f"Claude session not found: {session_id}")
    if last:
        if not sessions:
            raise ValueError("No Claude sessions found")
        return sessions[0]
    raise ValueError(
        "Pass --last, --session-id, or --session-file. "
        "Try `tokencause claude scan`, then `tokencause claude explain --last`."
    )


def text_from_claude_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
                elif item.get("type"):
                    parts.append(str(item.get("type")))
        return "\n".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or content)
    return ""


def claude_tool_file_refs(value: Any) -> tuple[str, ...]:
    refs: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("filePath", "path", "filename") and isinstance(item, str):
                refs.append(item)
            refs.extend(claude_tool_file_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.extend(claude_tool_file_refs(item))
    elif isinstance(value, str):
        refs.extend(extract_file_refs(value))
    return tuple(dict.fromkeys(refs))


def parse_claude_jsonl(session: ClaudeSession) -> list[TraceEvent]:
    events: list[TraceEvent] = []
    last_user_preview = "claude_message"
    with session.path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw, dict):
                continue
            message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
            role = str(message.get("role") or raw.get("type") or "unknown")
            content_text = text_from_claude_content(message.get("content"))
            if role == "user" and content_text:
                last_user_preview = short_preview(content_text, 80)
            usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
            input_tokens = as_int(usage.get("input_tokens")) + as_int(usage.get("cache_creation_input_tokens")) + as_int(usage.get("cache_read_input_tokens"))
            output_tokens = as_int(usage.get("output_tokens"))
            tool_result = raw.get("toolUseResult")
            if input_tokens == 0 and output_tokens == 0 and isinstance(tool_result, (dict, list, str)):
                output_tokens = estimate_tokens(str(tool_result))
            if input_tokens == 0 and output_tokens == 0 and content_text:
                if role == "assistant":
                    output_tokens = estimate_tokens(content_text)
                elif role == "user":
                    input_tokens = estimate_tokens(content_text)
            if input_tokens == 0 and output_tokens == 0:
                continue
            model = str(message.get("model") or raw.get("model") or "claude")
            tool = "claude-tool-result" if isinstance(tool_result, (dict, list, str)) else "claude-message"
            file_refs = list(extract_file_refs(content_text))
            if isinstance(tool_result, (dict, list, str)):
                file_refs.extend(claude_tool_file_refs(tool_result))
            event = TraceEvent(
                raw=raw,
                index=index,
                run_id=str(raw.get("sessionId") or session.id),
                step=last_user_preview,
                model=model,
                tool=tool,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0,
                latency_ms=0,
                status="ok",
                error="",
                context_hash=str(raw.get("parentUuid") or ""),
                context_items=normalize_context_items(tuple(dict.fromkeys(file_refs))),
            )
            events.append(event)
    if not events:
        raise ValueError(f"No analyzable Claude records found in {session.path}")
    return events
