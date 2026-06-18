"""Codex session adapter."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any
import json
import re
import sqlite3

from tokencause.core.diagnosis import (
    build_broad_exploration,
    build_environment_issues,
)
from tokencause.core.files import extract_file_refs, should_count_file_ref
from tokencause.core.models import (
    CodexCacheResult,
    CodexContentEvent,
    CodexExplainReport,
    CodexThread,
    RepeatedArtifact,
    RepeatedChunk,
    RetryLoop,
    SessionDrift,
)
from tokencause.core.tokens import content_hash, estimate_tokens, short_preview
from tokencause.core.values import as_int, get_path
from tokencause.storage.cache import (
    codex_report_cache_key,
    codex_report_from_json,
    codex_report_to_json,
)

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


def command_exit_code(output: str) -> int | None:
    match = re.search(r"(?:Process exited with code|Exit code):?\s*(-?\d+)", output)
    if not match:
        return None
    return as_int(match.group(1), default=0)


def is_error_output(command: str, output: str) -> bool:
    lower = f"{command}\n{output}".lower()
    exit_code = command_exit_code(output)
    if exit_code is not None:
        return exit_code != 0
    return "traceback (most recent call last)" in lower or "uncaught exception" in lower


def is_test_output(command: str, output: str) -> bool:
    lower = f"{command}\n{output}".lower()
    return any(hint in lower for hint in ("pytest", "unittest", "npm test", "pnpm test", "yarn test", "failures", " passed", " failed"))


def is_build_output(command: str, output: str) -> bool:
    lower = f"{command}\n{output}".lower()
    return any(
        hint in lower
        for hint in (
            "xcodebuild",
            "swift build",
            "cargo build",
            "go build",
            "npm run build",
            "pnpm build",
            "yarn build",
            "next build",
            "vite build",
            "tsc ",
            "tsc\n",
            "webpack",
            "rollup",
        )
    )


def is_install_output(command: str, output: str) -> bool:
    lower = f"{command}\n{output}".lower()
    return any(
        hint in lower
        for hint in (
            "npm install",
            "npm i ",
            "pnpm install",
            "yarn install",
            "pip install",
            "uv pip install",
            "poetry install",
            "bundle install",
            "cargo install",
            "brew install",
            "added ",
            "packages are looking for funding",
        )
    )


def is_search_output(command: str, output: str) -> bool:
    lower = f"{command}\n{output}".lower()
    return any(
        hint in lower
        for hint in (
            "rg ",
            "ripgrep",
            "grep ",
            "find ",
            "fd ",
            "ag ",
            "git grep",
            "search_query",
        )
    )


def codex_tool_output_category(command: str, output: str) -> str:
    if is_error_output(command, output):
        return "error_log"
    if is_test_output(command, output):
        return "test_log"
    if is_build_output(command, output):
        return "build_log"
    if is_install_output(command, output):
        return "install_log"
    if is_search_output(command, output):
        return "search_output"
    return "other_tool_output"


def is_failed_test_output(event: CodexContentEvent) -> bool:
    lower = event.preview.lower()
    return event.category == "test_log" and any(hint in lower for hint in ("failed", "failures", "error", "traceback"))


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
    with closing(sqlite3.connect(db_path)) as connection:
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
    raise ValueError(
        "Pass --last or --thread-id. "
        "Try `tokencause codex scan`, then `tokencause codex explain --last`."
    )


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
        category = codex_tool_output_category(command, output)
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
    hash_events: dict[str, list[CodexContentEvent]] = defaultdict(list)
    artifact_counter: Counter[str] = Counter()
    artifact_categories: dict[str, set[str]] = defaultdict(set)
    for event in content_events:
        category_tokens[event.category] += event.tokens
        if event.content_hash:
            hash_counter[event.content_hash] += 1
            hash_events[event.content_hash].append(event)
        if event.category not in ("user_message", "assistant_message"):
            for file_ref in event.file_refs:
                if should_count_file_ref(file_ref, thread.cwd):
                    file_tokens[file_ref] += event.tokens
                    artifact_counter[file_ref] += 1
                    artifact_categories[file_ref].add(event.category)
        if event.command:
            command_tokens[event.command] += event.tokens

    tool_output_categories = {"test_log", "build_log", "install_log", "search_output", "other_tool_output", "error_log"}
    long_tool_outputs = sorted(
        [event for event in content_events if event.category in tool_output_categories and event.tokens >= 800],
        key=lambda event: event.tokens,
        reverse=True,
    )
    failure_events = [
        event
        for event in content_events
        if event.category == "error_log" or is_failed_test_output(event)
    ]
    repeated_chunks = build_repeated_chunks(hash_events)
    repeated_artifacts = build_repeated_artifacts(artifact_counter, file_tokens, artifact_categories)
    retry_loops = build_retry_loops(
        [event for event in failure_events if event.category in tool_output_categories]
    )
    session_drift = build_session_drift(usage_events)
    environment_issues = build_environment_issues(content_events)
    broad_exploration = build_broad_exploration(content_events, file_tokens, command_tokens)

    return CodexExplainReport(
        thread=thread,
        content_events=content_events,
        usage_events=usage_events,
        category_tokens=dict(sorted(category_tokens.items(), key=lambda row: row[1], reverse=True)),
        file_tokens=dict(sorted(file_tokens.items(), key=lambda row: row[1], reverse=True)),
        command_tokens=dict(sorted(command_tokens.items(), key=lambda row: row[1], reverse=True)),
        repeated_hashes={key: count for key, count in hash_counter.items() if count > 1},
        repeated_chunks=repeated_chunks,
        repeated_artifacts=repeated_artifacts,
        long_tool_outputs=long_tool_outputs,
        failure_events=failure_events,
        retry_loops=retry_loops,
        session_drift=session_drift,
        environment_issues=environment_issues,
        broad_exploration=broad_exploration,
    )


def parse_codex_rollout_cached(thread: CodexThread, cache_dir: Path | None) -> CodexCacheResult:
    if cache_dir is None:
        return CodexCacheResult(report=parse_codex_rollout(thread), status="disabled")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = codex_report_cache_key(thread)
    cache_path = cache_dir / f"{key}.json"
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return CodexCacheResult(report=codex_report_from_json(cached), status="hit")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            status = "stale"
    else:
        status = "miss"
    report = parse_codex_rollout(thread)
    try:
        cache_path.write_text(json.dumps(codex_report_to_json(report), ensure_ascii=False), encoding="utf-8")
    except OSError:
        status = "write-failed"
    return CodexCacheResult(report=report, status=status)


def build_repeated_chunks(hash_events: dict[str, list[CodexContentEvent]]) -> list[RepeatedChunk]:
    chunks: list[RepeatedChunk] = []
    for hash_value, events in hash_events.items():
        if len(events) <= 1:
            continue
        representative = max(events, key=lambda event: event.tokens)
        duplicate_tokens = sum(event.tokens for event in events[1:])
        if duplicate_tokens < 100:
            continue
        chunks.append(
            RepeatedChunk(
                content_hash=hash_value,
                count=len(events),
                tokens_each=representative.tokens,
                duplicate_tokens=duplicate_tokens,
                category=representative.category,
                preview=representative.preview,
            )
        )
    return sorted(chunks, key=lambda chunk: chunk.duplicate_tokens, reverse=True)


def build_repeated_artifacts(
    artifact_counter: Counter[str],
    file_tokens: dict[str, int],
    artifact_categories: dict[str, set[str]],
) -> list[RepeatedArtifact]:
    artifacts: list[RepeatedArtifact] = []
    for file_ref, count in artifact_counter.items():
        if count <= 1:
            continue
        tokens = file_tokens.get(file_ref, 0)
        if tokens < 100:
            continue
        artifacts.append(
            RepeatedArtifact(
                file_ref=file_ref,
                count=count,
                tokens=tokens,
                categories=tuple(sorted(artifact_categories.get(file_ref, set()))),
            )
        )
    return sorted(artifacts, key=lambda artifact: (artifact.tokens, artifact.count), reverse=True)


def build_retry_loops(failure_events: list[CodexContentEvent]) -> list[RetryLoop]:
    grouped: dict[str, list[CodexContentEvent]] = defaultdict(list)
    for event in failure_events:
        key = event.command.strip() or event.content_hash
        if not key:
            continue
        grouped[key].append(event)

    loops: list[RetryLoop] = []
    for key, events in grouped.items():
        if len(events) <= 1:
            continue
        total_tokens = sum(event.tokens for event in events)
        if total_tokens < 800:
            continue
        representative = max(events, key=lambda event: event.tokens)
        loops.append(
            RetryLoop(
                key=key,
                count=len(events),
                tokens=total_tokens,
                command=representative.command,
                preview=representative.preview,
            )
        )
    return sorted(loops, key=lambda loop: loop.tokens, reverse=True)


def average_int(values: list[int]) -> int:
    if not values:
        return 0
    return int(sum(values) / len(values))


def build_session_drift(usage_events: list[dict[str, int]]) -> SessionDrift | None:
    totals = [event.get("total_tokens", 0) for event in usage_events if event.get("total_tokens", 0) > 0]
    if len(totals) < 6:
        return None
    window = max(2, len(totals) // 3)
    early = totals[:window]
    late = totals[-window:]
    early_avg = average_int(early)
    late_avg = average_int(late)
    if early_avg <= 0:
        return None
    ratio = late_avg / early_avg
    if ratio < 1.75:
        return None
    return SessionDrift(
        early_avg_tokens=early_avg,
        late_avg_tokens=late_avg,
        ratio=ratio,
        peak_tokens=max(totals),
        samples=len(totals),
    )
