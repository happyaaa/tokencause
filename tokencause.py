#!/usr/bin/env python3
"""Analyze agent run traces for cost, latency, and context waste."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import statistics
import sys
from datetime import datetime, timezone
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KNOWN_EXPENSIVE_MODEL_HINTS = (
    "opus",
    "fable",
    "mythos",
    "gpt-5",
    "o3",
    "reasoning",
)

CHEAP_STEP_HINTS = (
    "search",
    "grep",
    "glob",
    "list",
    "read",
    "summarize",
    "classify",
    "route",
    "plan",
)

FILE_REF_RE = re.compile(
    r"(?:\.{0,2}/|~?/|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|json|md|toml|yaml|yml|lock|txt|sql|css|html|sh)"
)

EXPENSIVE_FILE_HINTS = (
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "generated",
    "fixture",
    "fixtures",
    "snapshot",
    "schema",
    ".min.",
)


@dataclass
class TraceEvent:
    raw: dict[str, Any]
    index: int
    run_id: str = "default"
    step: str = "unknown"
    model: str = "unknown"
    tool: str = "none"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""
    context_hash: str = ""
    context_items: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Finding:
    title: str
    detail: str
    severity: str = "info"


@dataclass
class Recommendation:
    title: str
    detail: str
    estimated_savings_usd: float = 0.0


@dataclass
class Analysis:
    events: list[TraceEvent]
    total_cost: float
    total_tokens: int
    total_latency_ms: int
    cost_by_model: dict[str, float]
    cost_by_step: dict[str, float]
    tokens_by_model: dict[str, int]
    latency_by_step: dict[str, int]
    failures: list[TraceEvent]
    repeated_context: dict[str, int]
    repeated_items: dict[str, int]
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    estimated_savings_usd: float = 0.0


@dataclass
class CodexThread:
    id: str
    title: str
    rollout_path: Path
    cwd: str
    updated_at: int
    tokens_used: int = 0


@dataclass
class CodexContentEvent:
    category: str
    tokens: int
    preview: str
    timestamp: str = ""
    file_refs: tuple[str, ...] = ()
    command: str = ""
    content_hash: str = ""


@dataclass
class CodexExplainReport:
    thread: CodexThread
    content_events: list[CodexContentEvent]
    usage_events: list[dict[str, int]]
    category_tokens: dict[str, int]
    file_tokens: dict[str, int]
    command_tokens: dict[str, int]
    repeated_hashes: dict[str, int]
    long_tool_outputs: list[CodexContentEvent]
    failure_events: list[CodexContentEvent]

    @property
    def observable_tokens(self) -> int:
        return sum(event.tokens for event in self.content_events)

    @property
    def model_total_tokens(self) -> int:
        return sum(event.get("total_tokens", 0) for event in self.usage_events)

    @property
    def model_input_tokens(self) -> int:
        return sum(event.get("input_tokens", 0) for event in self.usage_events)

    @property
    def cached_input_tokens(self) -> int:
        return sum(event.get("cached_input_tokens", 0) for event in self.usage_events)

    @property
    def model_output_tokens(self) -> int:
        return sum(event.get("output_tokens", 0) for event in self.usage_events)


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def first_present(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def get_path(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current if current not in (None, "") else default


def first_path(data: dict[str, Any], paths: tuple[tuple[str, ...], ...], default: Any = None) -> Any:
    for path in paths:
        value = get_path(data, path, None)
        if value not in (None, ""):
            return value
    return default


def normalize_context_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def infer_litellm_step(raw: dict[str, Any]) -> str:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    litellm_params = raw.get("litellm_params") if isinstance(raw.get("litellm_params"), dict) else {}
    messages = first_path(raw, (("messages",), ("request", "messages"), ("kwargs", "messages")), [])
    if isinstance(messages, list):
        joined = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict) and message.get("role") in ("system", "user")
        ).lower()
    else:
        joined = ""

    explicit = first_present(
        {**metadata, **litellm_params, **raw},
        ("step", "step_name", "name", "route", "call_type", "endpoint"),
        "",
    )
    if explicit:
        return str(explicit)
    for hint in CHEAP_STEP_HINTS:
        if hint in joined:
            return hint
    return "llm_call"


def parse_litellm_event(raw: dict[str, Any], index: int) -> TraceEvent:
    usage = first_path(raw, (("usage",), ("response", "usage"), ("modelResponse", "usage")), {})
    if not isinstance(usage, dict):
        usage = {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    model = first_path(
        raw,
        (
            ("model",),
            ("model_name",),
            ("standard_logging_object", "model"),
            ("response", "model"),
            ("litellm_params", "model"),
        ),
        "unknown",
    )
    cost = first_path(
        raw,
        (
            ("response_cost",),
            ("cost",),
            ("cost_usd",),
            ("spend",),
            ("standard_logging_object", "response_cost"),
            ("standard_logging_object", "cost"),
        ),
        0.0,
    )
    latency = first_path(
        raw,
        (
            ("latency_ms",),
            ("duration_ms",),
            ("response_ms",),
            ("standard_logging_object", "response_ms"),
        ),
        0,
    )
    return TraceEvent(
        raw=raw,
        index=index,
        run_id=str(
            first_present(
                {**metadata, **raw},
                ("run_id", "trace_id", "session_id", "user_api_key", "user_id", "end_user"),
                "default",
            )
        ),
        step=infer_litellm_step(raw),
        model=str(model),
        tool=str(first_present({**metadata, **raw}, ("tool", "tool_name", "call_type"), "llm")),
        input_tokens=as_int(first_present(raw, ("input_tokens", "prompt_tokens"), usage.get("prompt_tokens", 0))),
        output_tokens=as_int(
            first_present(raw, ("output_tokens", "completion_tokens"), usage.get("completion_tokens", 0))
        ),
        cost_usd=as_float(cost),
        latency_ms=as_int(latency),
        status=str(first_present(raw, ("status", "outcome", "response_status"), "ok")).lower(),
        error=str(first_present(raw, ("error", "error_message", "exception", "failure_reason"), "")),
        context_hash=str(first_present({**metadata, **raw}, ("context_hash", "prompt_hash", "request_hash"), "")),
        context_items=normalize_context_items(first_present({**metadata, **raw}, ("context_items", "files", "documents"), None)),
    )


def parse_event(raw: dict[str, Any], index: int) -> TraceEvent:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return TraceEvent(
        raw=raw,
        index=index,
        run_id=str(first_present(raw, ("run_id", "runId", "session_id", "trace_id"), "default")),
        step=str(first_present(raw, ("step", "name", "span_name", "operation"), "unknown")),
        model=str(first_present(raw, ("model", "model_name", "modelName"), "unknown")),
        tool=str(first_present(raw, ("tool", "tool_name", "toolName"), "none")),
        input_tokens=as_int(first_present(raw, ("input_tokens", "prompt_tokens"), usage.get("prompt_tokens", 0))),
        output_tokens=as_int(
            first_present(raw, ("output_tokens", "completion_tokens"), usage.get("completion_tokens", 0))
        ),
        cost_usd=as_float(first_present(raw, ("cost_usd", "cost", "spend"), 0.0)),
        latency_ms=as_int(first_present(raw, ("latency_ms", "duration_ms", "elapsed_ms"), 0)),
        status=str(first_present(raw, ("status", "outcome"), "ok")).lower(),
        error=str(first_present(raw, ("error", "error_message", "exception"), "")),
        context_hash=str(first_present(raw, ("context_hash", "prompt_hash", "contextHash"), "")),
        context_items=normalize_context_items(first_present(raw, ("context_items", "files", "documents"), None)),
    )


def load_jsonl(path: Path, parser: str = "generic") -> list[TraceEvent]:
    events: list[TraceEvent] = []
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
            if parser == "litellm":
                events.append(parse_litellm_event(raw, index))
            else:
                events.append(parse_event(raw, index))
    return events


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Cheap local estimate. Exact model tokenizers are intentionally not required for local-first use.
    return max(1, len(text) // 4)


def short_preview(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "..."


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def extract_file_refs(text: str) -> tuple[str, ...]:
    return tuple(sorted(set(FILE_REF_RE.findall(text or ""))))


def command_from_arguments(arguments: Any) -> str:
    if isinstance(arguments, dict):
        return str(arguments.get("cmd") or arguments.get("command") or "")
    if not isinstance(arguments, str):
        return ""
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments[:160]
    if isinstance(parsed, dict):
        return str(parsed.get("cmd") or parsed.get("command") or "")
    return ""


def codex_state_db(codex_home: Path | None = None) -> Path:
    home = codex_home or Path.home() / ".codex"
    return home / "state_5.sqlite"


def load_codex_threads(codex_home: Path | None = None, limit: int = 20) -> list[CodexThread]:
    db_path = codex_state_db(codex_home)
    if not db_path.exists():
        raise FileNotFoundError(f"Codex state database not found: {db_path}")
    query = """
        select id, title, rollout_path, cwd, updated_at, tokens_used
        from threads
        where rollout_path is not null and rollout_path != ''
        order by updated_at desc
        limit ?
    """
    with sqlite3.connect(db_path) as connection:
        rows = connection.execute(query, (limit,)).fetchall()
    return [
        CodexThread(
            id=str(row[0]),
            title=str(row[1] or ""),
            rollout_path=Path(str(row[2])),
            cwd=str(row[3] or ""),
            updated_at=as_int(row[4]),
            tokens_used=as_int(row[5]),
        )
        for row in rows
    ]


def pick_codex_thread(codex_home: Path | None = None, last: bool = False, thread_id: str | None = None) -> CodexThread:
    threads = load_codex_threads(codex_home, limit=200)
    if thread_id:
        for thread in threads:
            if thread.id.startswith(thread_id):
                return thread
        raise ValueError(f"Codex thread not found: {thread_id}")
    if last:
        if not threads:
            raise ValueError("No Codex threads found")
        return threads[0]
    raise ValueError("Pass --last or --thread-id")


def classify_codex_event(record: dict[str, Any], previous_call_commands: dict[str, str]) -> CodexContentEvent | None:
    timestamp = str(record.get("timestamp") or "")
    record_type = record.get("type")
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}

    if record_type == "event_msg" and payload.get("type") == "user_message":
        text = str(payload.get("message") or "")
        return build_codex_content_event("user_message", text, timestamp)

    if record_type == "event_msg" and payload.get("type") == "agent_message":
        text = str(payload.get("message") or "")
        return build_codex_content_event("assistant_message", text, timestamp)

    if record_type == "response_item" and payload.get("type") == "message":
        parts = payload.get("content")
        text = ""
        if isinstance(parts, list):
            text = "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict))
        return build_codex_content_event("assistant_message", text, timestamp)

    if record_type == "response_item" and payload.get("type") in ("function_call", "custom_tool_call"):
        call_id = str(payload.get("call_id") or "")
        name = str(payload.get("name") or "tool")
        command = command_from_arguments(payload.get("arguments") or payload.get("input"))
        if call_id and command:
            previous_call_commands[call_id] = command
        text = f"{name} {command}".strip()
        return build_codex_content_event("tool_call", text, timestamp, command=command)

    if record_type == "response_item" and payload.get("type") in ("function_call_output", "custom_tool_call_output"):
        call_id = str(payload.get("call_id") or "")
        output = str(payload.get("output") or "")
        command = previous_call_commands.get(call_id, "")
        category = "tool_output"
        lower = f"{command}\n{output}".lower()
        if any(hint in lower for hint in ("traceback", "error", "failed", "exception")):
            category = "error_log"
        elif any(hint in lower for hint in ("pytest", "unittest", "test", "failures", "passed")):
            category = "test_log"
        return build_codex_content_event(category, output, timestamp, command=command)

    if record_type == "event_msg" and payload.get("type") == "token_count":
        return None

    return None


def build_codex_content_event(
    category: str,
    text: str,
    timestamp: str,
    command: str = "",
) -> CodexContentEvent | None:
    if not text:
        return None
    return CodexContentEvent(
        category=category,
        tokens=estimate_tokens(text),
        preview=short_preview(text),
        timestamp=timestamp,
        file_refs=extract_file_refs(text),
        command=command,
        content_hash=content_hash(text),
    )


def parse_codex_rollout(thread: CodexThread) -> CodexExplainReport:
    if not thread.rollout_path.exists():
        raise FileNotFoundError(f"Codex rollout file not found: {thread.rollout_path}")

    content_events: list[CodexContentEvent] = []
    usage_events: list[dict[str, int]] = []
    previous_call_commands: dict[str, str] = {}

    with thread.rollout_path.open("r", encoding="utf-8") as file:
        for line in file:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue
            payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
            if record.get("type") == "event_msg" and payload.get("type") == "token_count":
                usage = get_path(payload, ("info", "last_token_usage"), {})
                if isinstance(usage, dict):
                    usage_events.append({key: as_int(value) for key, value in usage.items()})
                continue
            event = classify_codex_event(record, previous_call_commands)
            if event is not None:
                content_events.append(event)

    category_tokens: dict[str, int] = defaultdict(int)
    file_tokens: dict[str, int] = defaultdict(int)
    command_tokens: dict[str, int] = defaultdict(int)
    hash_counter: Counter[str] = Counter()
    for event in content_events:
        category_tokens[event.category] += event.tokens
        if event.content_hash:
            hash_counter[event.content_hash] += 1
        for file_ref in event.file_refs:
            file_tokens[file_ref] += event.tokens
        if event.command:
            command_tokens[event.command] += event.tokens

    long_tool_outputs = sorted(
        [event for event in content_events if event.category in ("tool_output", "test_log", "error_log") and event.tokens >= 800],
        key=lambda event: event.tokens,
        reverse=True,
    )
    failure_events = [
        event
        for event in content_events
        if event.category == "error_log" or any(word in event.preview.lower() for word in ("error", "failed", "traceback"))
    ]

    return CodexExplainReport(
        thread=thread,
        content_events=content_events,
        usage_events=usage_events,
        category_tokens=dict(sorted(category_tokens.items(), key=lambda row: row[1], reverse=True)),
        file_tokens=dict(sorted(file_tokens.items(), key=lambda row: row[1], reverse=True)),
        command_tokens=dict(sorted(command_tokens.items(), key=lambda row: row[1], reverse=True)),
        repeated_hashes={key: count for key, count in hash_counter.items() if count > 1},
        long_tool_outputs=long_tool_outputs,
        failure_events=failure_events,
    )


def render_codex_scan(threads: list[CodexThread]) -> str:
    lines = ["TokenCause Codex sessions", ""]
    if not threads:
        return "No Codex sessions found."
    for thread in threads:
        updated = datetime.fromtimestamp(thread.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        title = short_preview(thread.title, 80)
        lines.append(f"- {thread.id[:8]}  {updated}  {thread.tokens_used} tokens  {title}")
    return "\n".join(lines)


def render_codex_explain(report: CodexExplainReport) -> str:
    lines = [
        "TokenCause Codex explain",
        f"session: {report.thread.id}",
        f"title: {short_preview(report.thread.title, 120)}",
        f"cwd: {report.thread.cwd}",
        f"rollout: {report.thread.rollout_path}",
        "",
        "usage counters:",
        f"- thread tokens_used: {report.thread.tokens_used}",
        f"- summed model total tokens: {report.model_total_tokens}",
        f"- summed model input tokens: {report.model_input_tokens}",
        f"- summed cached input tokens: {report.cached_input_tokens}",
        f"- summed model output tokens: {report.model_output_tokens}",
        f"- observable transcript tokens: {report.observable_tokens}",
        "",
        "token breakdown from observable transcript:",
    ]
    total = report.observable_tokens or 1
    for category, tokens in top_items(report.category_tokens, 8):
        lines.append(f"- {category}: {tokens} tokens ({tokens / total:.0%})")

    lines.extend(["", "top files/artifacts:"])
    for file_ref, tokens in top_items(report.file_tokens, 8):
        marker = " expensive-file" if any(hint in file_ref.lower() for hint in EXPENSIVE_FILE_HINTS) else ""
        lines.append(f"- {file_ref}: {tokens} tokens{marker}")
    if not report.file_tokens:
        lines.append("- none detected")

    lines.extend(["", "top commands:"])
    for command, tokens in top_items(report.command_tokens, 5):
        lines.append(f"- {short_preview(command, 120)}: {tokens} tokens")
    if not report.command_tokens:
        lines.append("- none detected")

    lines.extend(["", "cost drivers:"])
    if report.repeated_hashes:
        repeats = sum(count - 1 for count in report.repeated_hashes.values())
        lines.append(f"- repeated context: {len(report.repeated_hashes)} repeated chunks, {repeats} duplicate appearances")
    if report.long_tool_outputs:
        top = report.long_tool_outputs[0]
        lines.append(f"- long tool output: largest output is {top.tokens} tokens ({short_preview(top.command or top.preview, 120)})")
    if report.failure_events:
        lines.append(f"- retry/failure surface: {len(report.failure_events)} error-like outputs")
    if not report.repeated_hashes and not report.long_tool_outputs and not report.failure_events:
        lines.append("- no obvious high-signal cost drivers detected")

    lines.extend(["", "recommendations:"])
    recommendations = codex_recommendations(report)
    if recommendations:
        lines.extend(f"- {item}" for item in recommendations)
    else:
        lines.append("- No specific recommendation yet.")
    return "\n".join(lines)


def codex_recommendations(report: CodexExplainReport) -> list[str]:
    recommendations: list[str] = []
    if report.repeated_hashes:
        recommendations.append("Compact or split sessions when repeated context starts accumulating.")
    if report.long_tool_outputs:
        recommendations.append("Truncate long command/test output; keep the error summary and last relevant lines.")
    expensive_files = [name for name in report.file_tokens if any(hint in name.lower() for hint in EXPENSIVE_FILE_HINTS)]
    if expensive_files:
        recommendations.append(f"Ignore or summarize expensive files such as {', '.join(expensive_files[:3])}.")
    if report.failure_events:
        recommendations.append("Deduplicate repeated failures and avoid rerunning identical commands without changing state.")
    return recommendations[:5]


def analyze(events: list[TraceEvent], budget_usd: float | None = None) -> Analysis:
    cost_by_model: dict[str, float] = defaultdict(float)
    cost_by_step: dict[str, float] = defaultdict(float)
    tokens_by_model: dict[str, int] = defaultdict(int)
    latency_by_step: dict[str, int] = defaultdict(int)
    context_counter: Counter[str] = Counter()
    item_counter: Counter[str] = Counter()
    failures: list[TraceEvent] = []

    for event in events:
        cost_by_model[event.model] += event.cost_usd
        cost_by_step[event.step] += event.cost_usd
        tokens_by_model[event.model] += event.total_tokens
        latency_by_step[event.step] += event.latency_ms
        if event.context_hash:
            context_counter[event.context_hash] += 1
        for item in event.context_items:
            item_counter[item] += 1
        if event.status not in ("ok", "success", "completed") or event.error:
            failures.append(event)

    analysis = Analysis(
        events=events,
        total_cost=sum(event.cost_usd for event in events),
        total_tokens=sum(event.total_tokens for event in events),
        total_latency_ms=sum(event.latency_ms for event in events),
        cost_by_model=dict(sorted(cost_by_model.items(), key=lambda row: row[1], reverse=True)),
        cost_by_step=dict(sorted(cost_by_step.items(), key=lambda row: row[1], reverse=True)),
        tokens_by_model=dict(sorted(tokens_by_model.items(), key=lambda row: row[1], reverse=True)),
        latency_by_step=dict(sorted(latency_by_step.items(), key=lambda row: row[1], reverse=True)),
        failures=failures,
        repeated_context={key: count for key, count in context_counter.items() if count > 1},
        repeated_items={key: count for key, count in item_counter.items() if count > 1},
    )
    analysis.findings = build_findings(analysis, budget_usd)
    analysis.recommendations = build_recommendations(analysis)
    cap_overlapping_savings(analysis)
    return analysis


def cap_overlapping_savings(analysis: Analysis) -> None:
    raw_savings = sum(item.estimated_savings_usd for item in analysis.recommendations)
    if raw_savings <= 0 or analysis.total_cost <= 0:
        analysis.estimated_savings_usd = 0.0
        return
    # Recommendations often overlap: repeated context can also be part of the most expensive step.
    # Cap the first-pass estimate to keep the report conservative.
    cap = analysis.total_cost * 0.75
    if raw_savings <= cap:
        analysis.estimated_savings_usd = raw_savings
        return
    scale = cap / raw_savings
    for recommendation in analysis.recommendations:
        recommendation.estimated_savings_usd *= scale
    analysis.estimated_savings_usd = cap


def build_findings(analysis: Analysis, budget_usd: float | None) -> list[Finding]:
    findings: list[Finding] = []
    events = analysis.events
    if not events:
        return [Finding("没有可分析事件", "输入 trace 为空。", "warning")]

    if budget_usd is not None and analysis.total_cost > budget_usd:
        findings.append(
            Finding(
                "超过预算",
                f"本次运行成本 ${analysis.total_cost:.4f}，超过预算 ${budget_usd:.4f}。",
                "warning",
            )
        )

    if analysis.total_cost > 0:
        top_step, top_cost = next(iter(analysis.cost_by_step.items()))
        share = top_cost / analysis.total_cost
        if share >= 0.5:
            findings.append(
                Finding(
                    "成本集中在单一步骤",
                    f"`{top_step}` 占总成本 {share:.0%}，优先检查这个步骤的模型选择和上下文大小。",
                    "warning",
                )
            )

    expensive_on_cheap = [
        event
        for event in events
        if any(hint in event.model.lower() for hint in KNOWN_EXPENSIVE_MODEL_HINTS)
        and any(hint in f"{event.step} {event.tool}".lower() for hint in CHEAP_STEP_HINTS)
    ]
    if expensive_on_cheap:
        sample = expensive_on_cheap[0]
        findings.append(
            Finding(
                "昂贵模型可能用于低价值步骤",
                f"例如第 {sample.index} 行 `{sample.step}` / `{sample.tool}` 使用 `{sample.model}`。搜索、路由、摘要类步骤通常可先尝试便宜模型。",
                "warning",
            )
        )

    if analysis.repeated_context:
        count = sum(value - 1 for value in analysis.repeated_context.values())
        findings.append(
            Finding(
                "发现重复上下文",
                f"有 {len(analysis.repeated_context)} 个 context_hash 被重复使用，额外重复出现 {count} 次。可以考虑缓存摘要或裁剪重复 context。",
                "info",
            )
        )

    if analysis.repeated_items:
        repeated = sorted(analysis.repeated_items.items(), key=lambda row: row[1], reverse=True)[:3]
        names = ", ".join(f"{name} x{count}" for name, count in repeated)
        findings.append(
            Finding(
                "文件/文档被反复塞入上下文",
                f"重复最多的是：{names}。检查这些内容是否应该压缩成稳定摘要。",
                "info",
            )
        )

    if analysis.failures:
        findings.append(
            Finding(
                "存在失败步骤",
                f"发现 {len(analysis.failures)} 个失败/异常事件。失败重试可能造成隐性成本。",
                "warning",
            )
        )

    latencies = [event.latency_ms for event in events if event.latency_ms > 0]
    if len(latencies) >= 3:
        median = statistics.median(latencies)
        slow = [event for event in events if event.latency_ms > median * 3 and event.latency_ms > 5_000]
        if slow:
            sample = slow[0]
            findings.append(
                Finding(
                    "存在明显慢步骤",
                    f"第 {sample.index} 行 `{sample.step}` 耗时 {sample.latency_ms / 1000:.1f}s，显著高于中位数 {median / 1000:.1f}s。",
                    "info",
                )
            )

    if not findings:
        findings.append(Finding("未发现明显浪费", "当前 trace 没有触发成本、延迟或重复上下文规则。", "info"))
    return findings


def is_expensive_model(model: str) -> bool:
    return any(hint in model.lower() for hint in KNOWN_EXPENSIVE_MODEL_HINTS)


def is_low_value_step(event: TraceEvent) -> bool:
    return any(hint in f"{event.step} {event.tool}".lower() for hint in CHEAP_STEP_HINTS)


def build_recommendations(analysis: Analysis) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    if not analysis.events or analysis.total_cost <= 0:
        return recommendations

    expensive_low_value = [
        event for event in analysis.events if event.cost_usd > 0 and is_expensive_model(event.model) and is_low_value_step(event)
    ]
    if expensive_low_value:
        cost = sum(event.cost_usd for event in expensive_low_value)
        steps = sorted({event.step for event in expensive_low_value})[:5]
        recommendations.append(
            Recommendation(
                "把低风险步骤降级到便宜模型",
                f"`{', '.join(steps)}` 这类步骤用了昂贵模型。优先把 search/read/route/summary 切到 mini/Haiku 级别模型，再保留主推理步骤使用强模型。",
                cost * 0.55,
            )
        )

    repeated_context_events = [
        event for event in analysis.events if event.context_hash and analysis.repeated_context.get(event.context_hash, 0) > 1
    ]
    if repeated_context_events:
        duplicate_cost = 0.0
        seen: set[str] = set()
        for event in repeated_context_events:
            if event.context_hash in seen:
                duplicate_cost += event.cost_usd
            else:
                seen.add(event.context_hash)
        if duplicate_cost > 0:
            recommendations.append(
                Recommendation(
                    "缓存重复上下文或稳定摘要",
                    "同一个 `context_hash` 在一次 run 中重复出现。可以缓存 context pack、文件摘要或 retrieval 结果，避免每轮重新塞完整上下文。",
                    duplicate_cost * 0.65,
                )
            )

    if analysis.failures:
        failure_cost = sum(event.cost_usd for event in analysis.failures)
        if failure_cost > 0:
            recommendations.append(
                Recommendation(
                    "给失败重试加预算护栏",
                    "失败事件已经产生真实成本。建议按 run 设置 max retries、per-step budget，并在连续失败后降级为人工确认或更小上下文重试。",
                    failure_cost * 0.8,
                )
            )

    if analysis.repeated_items:
        repeated_event_cost = sum(
            event.cost_usd
            for event in analysis.events
            if any(item in analysis.repeated_items for item in event.context_items)
        )
        if repeated_event_cost > 0:
            recommendations.append(
                Recommendation(
                    "把反复读取的文件压缩成 memo",
                    "有文件/文档被多次放入上下文。对 README、schema、配置文件这类稳定内容生成 memo，后续步骤引用 memo 而不是原文。",
                    repeated_event_cost * 0.25,
                )
            )

    top_steps = list(analysis.cost_by_step.items())[:1]
    if top_steps:
        step, cost = top_steps[0]
        if cost / analysis.total_cost >= 0.4:
            recommendations.append(
                Recommendation(
                    "先优化最贵步骤",
                    f"`{step}` 是当前最大成本来源。先对这个步骤做 prompt 裁剪、上下文上限和模型路由，收益会比平均优化所有步骤更高。",
                    float(cost) * 0.2,
                )
            )

    return sorted(recommendations, key=lambda item: item.estimated_savings_usd, reverse=True)[:5]


def money(value: float) -> str:
    return f"${value:.4f}"


def seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


def top_items(mapping: dict[str, float | int], limit: int = 5) -> list[tuple[str, float | int]]:
    return list(mapping.items())[:limit]


def render_markdown(analysis: Analysis, source_path: Path, budget_usd: float | None) -> str:
    projected_cost = max(analysis.total_cost - analysis.estimated_savings_usd, 0.0)
    lines = [
        "# TokenCause Report",
        "",
        f"- 输入文件：`{source_path}`",
        f"- 事件数：{len(analysis.events)}",
        f"- 总成本：{money(analysis.total_cost)}",
        f"- 总 token：{analysis.total_tokens}",
        f"- 总耗时：{seconds(analysis.total_latency_ms)}",
        f"- 粗略可省：{money(analysis.estimated_savings_usd)}",
        f"- 优化后估算：{money(projected_cost)}",
    ]
    if budget_usd is not None:
        lines.append(f"- 预算：{money(budget_usd)}")
    lines.append("")

    lines.extend(["## 主要发现", ""])
    for finding in analysis.findings:
        lines.append(f"- **[{finding.severity}] {finding.title}**：{finding.detail}")
    lines.append("")

    lines.extend(["## 优先降本动作", ""])
    if analysis.recommendations:
        for index, recommendation in enumerate(analysis.recommendations, start=1):
            lines.append(
                f"{index}. **{recommendation.title}**：{recommendation.detail} 预计节省 {money(recommendation.estimated_savings_usd)}。"
            )
    else:
        lines.append("- 暂无明确降本动作。")
    lines.append("")

    lines.extend(["## 成本按模型", ""])
    for model, cost in top_items(analysis.cost_by_model):
        tokens = analysis.tokens_by_model.get(model, 0)
        lines.append(f"- `{model}`：{money(float(cost))}，{tokens} tokens")
    lines.append("")

    lines.extend(["## 成本按步骤", ""])
    for step, cost in top_items(analysis.cost_by_step):
        lines.append(f"- `{step}`：{money(float(cost))}")
    lines.append("")

    lines.extend(["## 最慢步骤", ""])
    for step, latency in top_items(analysis.latency_by_step):
        lines.append(f"- `{step}`：{seconds(int(latency))}")
    lines.append("")

    if analysis.failures:
        lines.extend(["## 失败事件", ""])
        for event in analysis.failures[:10]:
            message = event.error or event.status
            lines.append(f"- 第 {event.index} 行 `{event.step}` / `{event.model}`：{message}")
        lines.append("")

    return "\n".join(lines)


def render_console(analysis: Analysis, source_path: Path, budget_usd: float | None) -> str:
    lines = [
        "TokenCause",
        f"input: {source_path}",
        f"events: {len(analysis.events)}",
        f"total cost: {money(analysis.total_cost)}",
        f"total tokens: {analysis.total_tokens}",
        f"total latency: {seconds(analysis.total_latency_ms)}",
        f"estimated savings: {money(analysis.estimated_savings_usd)}",
    ]
    if budget_usd is not None:
        lines.append(f"budget: {money(budget_usd)}")
    lines.append("")
    lines.append("findings:")
    for finding in analysis.findings:
        lines.append(f"- [{finding.severity}] {finding.title}: {finding.detail}")
    if analysis.recommendations:
        lines.append("")
        lines.append("recommended actions:")
        for recommendation in analysis.recommendations:
            lines.append(f"- {recommendation.title}: save about {money(recommendation.estimated_savings_usd)}")
    return "\n".join(lines)


def run_analysis_command(args: argparse.Namespace, parser_name: str) -> int:
    trace_path = Path(args.trace)
    try:
        events = load_jsonl(trace_path, parser=parser_name)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    analysis = analyze(events, args.budget)
    report = render_markdown(analysis, trace_path, args.budget)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

    if args.markdown:
        print(report)
    else:
        print(render_console(analysis, trace_path, args.budget))
        if args.out:
            print(f"\nmarkdown report: {args.out}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    return run_analysis_command(args, "generic")


def command_analyze_litellm(args: argparse.Namespace) -> int:
    return run_analysis_command(args, "litellm")


def command_codex_scan(args: argparse.Namespace) -> int:
    try:
        threads = load_codex_threads(Path(args.codex_home).expanduser() if args.codex_home else None, limit=args.limit)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_codex_scan(threads))
    return 0


def command_codex_explain(args: argparse.Namespace) -> int:
    try:
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
        thread = pick_codex_thread(codex_home, last=args.last, thread_id=args.thread_id)
        report = parse_codex_rollout(thread)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    output = render_codex_explain(report)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")
    print(output)
    if args.out:
        print(f"\nreport: {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokencause",
        description="Analyze agent run traces for cost, latency, failures, and context waste.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a JSONL trace file.")
    analyze_parser.add_argument("trace", help="Path to a JSONL trace file.")
    analyze_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this run.")
    analyze_parser.add_argument("--out", help="Write a Markdown report to this path.")
    analyze_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    analyze_parser.set_defaults(func=command_analyze)

    litellm_parser = subparsers.add_parser("analyze-litellm", help="Analyze LiteLLM proxy/log JSONL.")
    litellm_parser.add_argument("trace", help="Path to a LiteLLM JSONL log file.")
    litellm_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this run.")
    litellm_parser.add_argument("--out", help="Write a Markdown report to this path.")
    litellm_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    litellm_parser.set_defaults(func=command_analyze_litellm)

    codex_parser = subparsers.add_parser("codex", help="Analyze local Codex Desktop/CLI sessions.")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)

    codex_scan_parser = codex_subparsers.add_parser("scan", help="List recent Codex sessions.")
    codex_scan_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_scan_parser.add_argument("--limit", type=int, default=10, help="Number of sessions to list.")
    codex_scan_parser.set_defaults(func=command_codex_scan)

    codex_explain_parser = codex_subparsers.add_parser("explain", help="Explain why a Codex session used tokens.")
    codex_explain_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_explain_parser.add_argument("--last", action="store_true", help="Explain the most recently updated Codex session.")
    codex_explain_parser.add_argument("--thread-id", help="Explain a specific Codex thread id or id prefix.")
    codex_explain_parser.add_argument("--out", help="Write the explanation to this path.")
    codex_explain_parser.set_defaults(func=command_codex_explain)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
