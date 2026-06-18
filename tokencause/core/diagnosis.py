"""Diagnosis heuristics for explaining expensive AI coding sessions."""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .formatting import compact_number
from .models import (
    BroadExploration,
    CodexContentEvent,
    CostDriver,
    EnvironmentIssue,
    HumanDiagnosis,
    RepeatedArtifact,
    RepeatedChunk,
    RetryLoop,
    SessionDrift,
    SessionEvent,
    SessionTrace,
)


ENVIRONMENT_ISSUE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "missing dependency",
        (
            "modulenotfounderror",
            "module not found",
            "no module named",
            "cannot find module",
            "command not found",
            "package not found",
            "could not find a version",
            "missing dependency",
        ),
    ),
    (
        "permission",
        (
            "permission denied",
            "operation not permitted",
            "eacces",
            "eperm",
            "not executable",
        ),
    ),
    (
        "network",
        (
            "network is unreachable",
            "connection refused",
            "connection reset",
            "could not resolve",
            "temporary failure in name resolution",
            "timed out",
            "timeout",
            "proxy",
            "403 forbidden",
            "401 unauthorized",
            "ssl:",
        ),
    ),
    (
        "configuration",
        (
            "environment variable",
            "env var",
            "api key",
            "token is not set",
            "not configured",
            "missing config",
            "config file not found",
        ),
    ),
    (
        "version mismatch",
        (
            "version mismatch",
            "unsupported version",
            "requires python",
            "requires node",
            "incompatible",
            "peer dependency",
        ),
    ),
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

BILLING_ONLY_DRIVERS = {"Cache-heavy context"}
READ_ONLY_COMMAND_PATTERN = re.compile(r"^\s*(?:cat|sed|nl|head|tail|less|more)\b")


def short_preview(text: str, limit: int = 180) -> str:
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)] + "..."


def event_source_label(event: CodexContentEvent | SessionEvent, limit: int = 120) -> str:
    if event.command:
        return short_preview(event.command, limit)
    if event.file_refs:
        return short_preview(", ".join(event.file_refs[:3]), limit)
    preview = event.preview or ""
    lower = preview.lower()
    if (
        "base64" in lower
        or "{'type': 'image'" in lower
        or '"type": "image"' in lower
        or "{'type': 'text'" in lower
        or '"type": "text"' in lower
        or "tool_result" in lower
    ):
        return f"{event.category or 'tool'} payload"
    return short_preview(preview, limit)


def environment_issue_kind(command: str, text: str) -> str:
    lower = f"{command}\n{text}".lower()
    if READ_ONLY_COMMAND_PATTERN.search(command) and not re.search(r"(?:process exited with code|exit code):?\s*[1-9-]", lower):
        return ""
    for kind, hints in ENVIRONMENT_ISSUE_HINTS:
        if any(hint in lower for hint in hints):
            return kind
    return ""


def is_broad_exploration_command(command: str) -> bool:
    lower = command.strip().lower()
    if not lower:
        return False
    broad_patterns = (
        r"(^|\s)find\s+\.",
        r"(^|\s)rg\s+--files\b",
        r"(^|\s)rg\s+-n\s+['\"]?\.['\"]?",
        r"(^|\s)grep\s+-r\b",
        r"(^|\s)ls\s+-r\b",
        r"(^|\s)tree\b",
        r"(^|\s)fd\s+\.?\s*$",
    )
    return any(re.search(pattern, lower) for pattern in broad_patterns)


def build_environment_issues(events: list[CodexContentEvent] | list[SessionEvent]) -> list[EnvironmentIssue]:
    grouped: dict[str, list[CodexContentEvent | SessionEvent]] = defaultdict(list)
    for event in events:
        if event.category not in ("error_log", "install_log", "build_log", "test_log"):
            continue
        kind = environment_issue_kind(event.command, event.preview)
        if kind:
            grouped[kind].append(event)

    issues: list[EnvironmentIssue] = []
    for kind, issue_events in grouped.items():
        tokens = sum(event.tokens for event in issue_events)
        if tokens < 100:
            continue
        top = max(issue_events, key=lambda event: event.tokens)
        issues.append(
            EnvironmentIssue(
                kind=kind,
                count=len(issue_events),
                tokens=tokens,
                command=top.command,
                preview=top.preview,
            )
        )
    return sorted(issues, key=lambda issue: issue.tokens, reverse=True)


def build_broad_exploration(
    events: list[CodexContentEvent] | list[SessionEvent],
    file_tokens: dict[str, int],
    command_tokens: dict[str, int],
) -> BroadExploration | None:
    search_events = [event for event in events if event.category == "search_output"]
    if not search_events:
        return None

    search_tokens = sum(event.tokens for event in search_events)
    command_total = sum(command_tokens.values())
    unique_files = len(file_tokens)
    broad_events = [event for event in search_events if is_broad_exploration_command(event.command)]
    search_commands = len({event.command for event in search_events if event.command})
    examples = tuple(
        dict.fromkeys(short_preview(event.command or event.preview, 120) for event in broad_events + search_events)
    )[:3]

    has_broad_search = len(broad_events) >= 1 and search_tokens >= 400
    many_searches = search_commands >= 4 and search_tokens >= 800
    many_files = unique_files >= 12 and search_tokens >= 800
    search_dominated = command_total > 0 and search_tokens / command_total >= 0.35 and search_tokens >= 800
    if not (has_broad_search or many_searches or many_files or search_dominated):
        return None

    return BroadExploration(
        search_commands=search_commands,
        broad_commands=len(broad_events),
        unique_files=unique_files,
        search_tokens=search_tokens,
        command_tokens=command_total,
        examples=examples,
    )


def build_session_file_tokens(events: list[SessionEvent]) -> dict[str, int]:
    file_tokens: dict[str, int] = defaultdict(int)
    for event in events:
        if not event.file_refs:
            continue
        share = max(event.tokens // len(event.file_refs), 1)
        for file_ref in event.file_refs:
            file_tokens[file_ref] += share
    return dict(file_tokens)


def build_session_command_tokens(events: list[SessionEvent]) -> dict[str, int]:
    command_tokens: dict[str, int] = defaultdict(int)
    for event in events:
        if event.command:
            command_tokens[event.command] += event.tokens
    return dict(command_tokens)


def build_session_repeated_chunks(events: list[SessionEvent]) -> list[RepeatedChunk]:
    grouped: dict[str, list[SessionEvent]] = defaultdict(list)
    for event in events:
        if event.content_hash and event.tokens > 0:
            grouped[event.content_hash].append(event)

    chunks: list[RepeatedChunk] = []
    for content_hash, group in grouped.items():
        if len(group) < 2:
            continue
        tokens_each = max(event.tokens for event in group)
        duplicate_tokens = tokens_each * (len(group) - 1)
        top = max(group, key=lambda event: event.tokens)
        chunks.append(
            RepeatedChunk(
                content_hash=content_hash,
                count=len(group),
                tokens_each=tokens_each,
                duplicate_tokens=duplicate_tokens,
                category=top.category,
                preview=top.preview,
            )
        )
    return sorted(chunks, key=lambda chunk: chunk.duplicate_tokens, reverse=True)


def build_session_repeated_artifacts(events: list[SessionEvent]) -> list[RepeatedArtifact]:
    refs: dict[str, list[SessionEvent]] = defaultdict(list)
    for event in events:
        for file_ref in event.file_refs:
            refs[file_ref].append(event)

    artifacts: list[RepeatedArtifact] = []
    for file_ref, group in refs.items():
        if len(group) < 2:
            continue
        tokens = sum(max(event.tokens // max(len(event.file_refs), 1), 1) for event in group)
        categories = tuple(sorted({event.category for event in group if event.category}))
        artifacts.append(RepeatedArtifact(file_ref=file_ref, count=len(group), tokens=tokens, categories=categories))
    return sorted(artifacts, key=lambda artifact: artifact.tokens, reverse=True)


def build_session_retry_loops(events: list[SessionEvent]) -> list[RetryLoop]:
    grouped: dict[str, list[SessionEvent]] = defaultdict(list)
    for event in events:
        lower = f"{event.category}\n{event.status}\n{event.command}\n{event.preview}\n{event.error}".lower()
        is_failure = (
            event.category in ("error_log", "test_log")
            or event.status not in ("ok", "success", "succeeded", "")
            or any(hint in lower for hint in ("error", "failed", "failure", "traceback", "timeout"))
        )
        if not is_failure:
            continue
        key = event.command or event.error or event.preview[:120]
        if key:
            grouped[key].append(event)

    loops: list[RetryLoop] = []
    for key, group in grouped.items():
        if len(group) < 2:
            continue
        tokens = sum(event.tokens for event in group)
        top = max(group, key=lambda event: event.tokens)
        loops.append(
            RetryLoop(
                key=key,
                count=len(group),
                tokens=tokens,
                command=top.command,
                preview=top.error or top.preview,
            )
        )
    return sorted(loops, key=lambda loop: loop.tokens, reverse=True)


def build_session_drift(trace: SessionTrace) -> SessionDrift | None:
    totals = [usage.total_tokens for usage in trace.usage_events if usage.total_tokens > 0]
    if len(totals) < 6:
        totals = [event.tokens for event in trace.events if event.tokens > 0]
    if len(totals) < 6:
        return None

    window = max(2, min(5, len(totals) // 3))
    early = totals[:window]
    late = totals[-window:]
    early_avg = int(sum(early) / len(early))
    late_avg = int(sum(late) / len(late))
    if early_avg <= 0:
        return None
    ratio = late_avg / early_avg
    if ratio < 1.8 or late_avg - early_avg < 500:
        return None
    return SessionDrift(
        early_avg_tokens=early_avg,
        late_avg_tokens=late_avg,
        ratio=ratio,
        peak_tokens=max(totals),
        samples=len(totals),
    )


def _driver_summary(tokens: int, total: int, description: str) -> str:
    return f"{tokens / (total or 1):.0%} of observable tokens {description}."


def _artifact_kind(file_ref: str) -> str:
    lower = file_ref.lower()
    if any(name in lower for name in ("package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "cargo.lock")):
        return "lockfile"
    if "generated" in lower:
        return "generated file"
    if "fixture" in lower or "fixtures" in lower:
        return "fixture data"
    if "snapshot" in lower:
        return "snapshot artifact"
    if "schema" in lower:
        return "schema artifact"
    if ".min." in lower:
        return "minified asset"
    if lower.endswith((".jsonl", ".json")):
        return "large structured data"
    return "source/context file"


def build_session_trace_cost_drivers(trace: SessionTrace) -> list[CostDriver]:
    events = trace.events
    total = trace.observable_tokens or 1
    drivers: list[CostDriver] = []
    file_tokens = build_session_file_tokens(events)
    command_tokens = build_session_command_tokens(events)

    tool_categories = {"tool_output", "other_tool_output", "search_output", "build_log", "test_log", "install_log", "error_log"}
    long_tool_outputs = sorted(
        [event for event in events if event.category in tool_categories and event.tokens >= 800],
        key=lambda event: event.tokens,
        reverse=True,
    )
    long_tool_tokens = sum(event.tokens for event in long_tool_outputs)
    if long_tool_tokens:
        top = long_tool_outputs[0]
        drivers.append(
            CostDriver(
                name="Long tool output",
                impact_tokens=long_tool_tokens,
                summary=_driver_summary(long_tool_tokens, total, "came from large tool outputs"),
                evidence=f"Largest output was ~{top.tokens} tokens from `{event_source_label(top)}`.",
            )
        )

    repeated_chunks = build_session_repeated_chunks(events)
    repeated_tokens = sum(chunk.duplicate_tokens for chunk in repeated_chunks)
    if repeated_tokens:
        top = repeated_chunks[0]
        drivers.append(
            CostDriver(
                name="Repeated context",
                impact_tokens=repeated_tokens,
                summary=_driver_summary(repeated_tokens, total, "were estimated duplicate repeated context"),
                evidence=f"Top repeated chunk appeared {top.count}x with ~{top.duplicate_tokens} duplicate tokens.",
            )
        )

    repeated_artifacts = build_session_repeated_artifacts(events)
    if repeated_artifacts:
        top = repeated_artifacts[0]
        drivers.append(
            CostDriver(
                name="Repeated file/artifact context",
                impact_tokens=top.tokens,
                summary=_driver_summary(top.tokens, total, "referenced the top repeated local file/artifact"),
                evidence=(
                    f"`{top.file_ref}` ({_artifact_kind(top.file_ref)}) appeared "
                    f"{top.count}x across {', '.join(top.categories) or 'unknown'} output."
                ),
            )
        )

    cache_tokens = trace.cached_input_tokens
    if cache_tokens:
        drivers.append(
            CostDriver(
                name="Cache-heavy context",
                impact_tokens=cache_tokens,
                summary=f"{cache_tokens / (trace.model_total_tokens or total or 1):.0%} of model tokens were cached input context.",
                evidence=f"Cache input totaled ~{cache_tokens} tokens across {len(trace.usage_events)} model usage event(s).",
            )
        )

    category_tokens: Counter[str] = Counter()
    for event in events:
        category_tokens[event.category] += event.tokens
    error_test_tokens = category_tokens["error_log"] + category_tokens["test_log"]
    failure_count = sum(1 for event in events if event.category in ("error_log", "test_log"))
    if error_test_tokens:
        drivers.append(
            CostDriver(
                name="Error/test log noise",
                impact_tokens=error_test_tokens,
                summary=_driver_summary(error_test_tokens, total, "were error-like or test-log output"),
                evidence=f"{failure_count} error-like outputs detected.",
            )
        )

    retry_loops = build_session_retry_loops(events)
    retry_loop_tokens = sum(loop.tokens for loop in retry_loops)
    if retry_loop_tokens:
        top = retry_loops[0]
        label = short_preview(top.command or top.preview, 120)
        drivers.append(
            CostDriver(
                name="Retry/failure loop",
                impact_tokens=retry_loop_tokens,
                summary=_driver_summary(retry_loop_tokens, total, "came from repeated failure groups"),
                evidence=f"`{label}` repeated {top.count}x and contributed ~{top.tokens} tokens.",
            )
        )

    drift = build_session_drift(trace)
    if drift is not None:
        drivers.append(
            CostDriver(
                name="Session drift",
                impact_tokens=max(drift.late_avg_tokens - drift.early_avg_tokens, 0),
                summary=f"Late-session model calls were {drift.ratio:.1f}x larger than early calls.",
                evidence=(
                    f"Average total tokens rose from ~{drift.early_avg_tokens} to ~{drift.late_avg_tokens}; "
                    f"peak was ~{drift.peak_tokens} across {drift.samples} samples."
                ),
            )
        )

    environment_issues = build_environment_issues(events)
    environment_tokens = sum(issue.tokens for issue in environment_issues)
    if environment_tokens:
        top = environment_issues[0]
        label = event_source_label(top)
        drivers.append(
            CostDriver(
                name="Environment issue",
                impact_tokens=environment_tokens,
                summary=_driver_summary(environment_tokens, total, "looked like environment/setup failures"),
                evidence=f"Top issue was {top.kind} from `{label}` across {top.count} matching output(s).",
            )
        )

    broad_exploration = build_broad_exploration(events, file_tokens, command_tokens)
    if broad_exploration is not None:
        examples = ", ".join(f"`{example}`" for example in broad_exploration.examples) or "search/read commands"
        drivers.append(
            CostDriver(
                name="Broad exploration",
                impact_tokens=broad_exploration.search_tokens,
                summary=_driver_summary(broad_exploration.search_tokens, total, "came from broad search/exploration output"),
                evidence=(
                    f"{broad_exploration.search_commands} search command(s), "
                    f"{broad_exploration.broad_commands} broad command(s), "
                    f"{broad_exploration.unique_files} file reference(s). Examples: {examples}."
                ),
            )
        )

    expensive_file_tokens = sum(
        tokens for file_ref, tokens in file_tokens.items() if any(hint in file_ref.lower() for hint in EXPENSIVE_FILE_HINTS)
    )
    if expensive_file_tokens:
        examples = [file_ref for file_ref in file_tokens if any(hint in file_ref.lower() for hint in EXPENSIVE_FILE_HINTS)][:3]
        drivers.append(
            CostDriver(
                name="Expensive file context",
                impact_tokens=expensive_file_tokens,
                summary=_driver_summary(expensive_file_tokens, total, "referenced expensive files"),
                evidence=f"Examples: {', '.join(examples)}.",
            )
        )

    return sorted(drivers, key=lambda driver: driver.impact_tokens, reverse=True)


def actionable_cost_drivers(drivers: list[CostDriver]) -> list[CostDriver]:
    return [driver for driver in drivers if driver.name not in BILLING_ONLY_DRIVERS]


def _driver_by_name(drivers: list[CostDriver]) -> dict[str, CostDriver]:
    return {driver.name: driver for driver in drivers}


def _diagnosis_evidence(drivers: list[CostDriver], names: tuple[str, ...], limit: int = 4) -> list[str]:
    selected: list[str] = []
    lookup = _driver_by_name(drivers)
    for name in names:
        driver = lookup.get(name)
        if driver and driver.evidence:
            selected.append(f"{driver.name}: {driver.evidence}")
    for driver in actionable_cost_drivers(drivers):
        if len(selected) >= limit:
            break
        item = f"{driver.name}: {driver.evidence}"
        if item not in selected:
            selected.append(item)
    return selected[:limit]


def _billing_note(drivers: list[CostDriver]) -> str:
    cache_driver = _driver_by_name(drivers).get("Cache-heavy context")
    if not cache_driver:
        return ""
    return (
        f"Cached input totaled about {compact_number(cache_driver.impact_tokens)} across the session. "
        "Treat cached input as a billing/accounting signal; "
        "the actionable workflow cause is usually the context that made the session large before caching."
    )


def workflow_pattern_label(trace: SessionTrace, drivers: list[CostDriver]) -> str:
    names = {driver.name for driver in drivers}
    if {"Broad exploration", "Long tool output", "Repeated file/artifact context"} <= names:
        return "One long session mixed discovery, coding, and verification"
    if {"Long tool output", "Error/test log noise"} <= names or {"Long tool output", "Retry/failure loop"} <= names:
        return "Debug loop carried raw logs"
    if "Environment issue" in names:
        return "Environment blocker loop"
    if "Expensive file context" in names:
        return "Low-signal file pollution"
    if "Repeated file/artifact context" in names:
        return "Repeated artifact carryover"
    if "Broad exploration" in names:
        return "Discovery never reset"
    if "Session drift" in names or trace.session_drift is not None:
        return "Long-running context drift"
    if "Repeated context" in names:
        return "Repeated context accumulation"
    if "Long tool output" in names:
        return "Tool output carryover"
    if "Cache-heavy context" in names:
        return "Cache-heavy long-running session"
    return "No dominant workflow pattern"


def diagnosis_evidence_metrics(trace: SessionTrace, drivers: list[CostDriver]) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    if trace.broad_exploration is not None:
        metrics["search_commands"] = trace.broad_exploration.search_commands
        metrics["file_refs"] = trace.broad_exploration.unique_files
        metrics["search_tokens"] = trace.broad_exploration.search_tokens
    if trace.long_tool_outputs:
        top = trace.long_tool_outputs[0]
        metrics["largest_output_tokens"] = top.tokens
        if top.command:
            metrics["largest_output_command"] = top.command
    if trace.repeated_artifacts:
        top_artifact = trace.repeated_artifacts[0]
        metrics["repeated_artifact"] = top_artifact.file_ref
        metrics["repeated_artifact_count"] = top_artifact.count
        metrics["repeated_artifact_tokens"] = top_artifact.tokens
    if trace.retry_loops:
        top_loop = trace.retry_loops[0]
        metrics["retry_count"] = top_loop.count
        metrics["retry_tokens"] = top_loop.tokens
    if trace.session_drift is not None:
        metrics["early_avg_tokens"] = trace.session_drift.early_avg_tokens
        metrics["late_avg_tokens"] = trace.session_drift.late_avg_tokens
        metrics["drift_ratio"] = round(trace.session_drift.ratio, 2)
    cache_driver = _driver_by_name(drivers).get("Cache-heavy context")
    if cache_driver:
        metrics["cached_input_tokens"] = cache_driver.impact_tokens
    return metrics


def workflow_subtype(metrics: dict[str, Any], drivers: list[CostDriver]) -> str:
    impact_by_name = _driver_by_name(drivers)
    names = set(impact_by_name)
    search_commands = int(metrics.get("search_commands") or 0)
    file_refs = int(metrics.get("file_refs") or 0)
    search_tokens = int(metrics.get("search_tokens") or 0)
    artifact_count = int(metrics.get("repeated_artifact_count") or 0)
    artifact_tokens = int(metrics.get("repeated_artifact_tokens") or 0)
    retry_count = int(metrics.get("retry_count") or 0)
    retry_tokens = int(metrics.get("retry_tokens") or 0)
    largest_output_tokens = int(metrics.get("largest_output_tokens") or 0)
    candidates: list[tuple[int, str]] = []
    if search_commands >= 20 or file_refs >= 200:
        candidates.append((max(search_tokens, search_commands * 10_000, file_refs * 500), "Search-heavy discovery"))
    if artifact_count >= 10:
        candidates.append((max(artifact_tokens * 2, artifact_count * 5_000), "Repeated artifact carryover"))
    if largest_output_tokens >= 5_000:
        candidates.append((largest_output_tokens * 10, "Long command output"))
    if "Error/test log noise" in names:
        candidates.append((impact_by_name["Error/test log noise"].impact_tokens, "Test/log output carryover"))
    if retry_count >= 3 or "Retry/failure loop" in names:
        retry_impact = impact_by_name.get("Retry/failure loop")
        candidates.append((max(retry_tokens, retry_count * 20_000, retry_impact.impact_tokens if retry_impact else 0), "Retry loop"))
    if "drift_ratio" in metrics or "Session drift" in names:
        drift_impact = impact_by_name.get("Session drift")
        candidates.append((drift_impact.impact_tokens if drift_impact else 50_000, "Long-session drift"))
    if "Expensive file context" in names:
        candidates.append((impact_by_name["Expensive file context"].impact_tokens, "Low-signal file pollution"))
    if "Environment issue" in names:
        environment_impact = impact_by_name["Environment issue"].impact_tokens
        largest_actionable = max((driver.impact_tokens for driver in actionable_cost_drivers(drivers)), default=0)
        if largest_actionable and environment_impact >= largest_actionable * 0.75:
            candidates.append((environment_impact, "Environment blocker"))
    if not candidates and "Long tool output" in names:
        candidates.append((impact_by_name["Long tool output"].impact_tokens, "Long output carryover"))
    if not candidates and "Cache-heavy context" in names:
        candidates.append((impact_by_name["Cache-heavy context"].impact_tokens, "Cache-heavy context"))
    ranked: list[str] = []
    for _score, label in sorted(candidates, reverse=True):
        if label not in ranked:
            ranked.append(label)
        if len(ranked) >= 2:
            break
    return " + ".join(ranked)


def build_human_diagnosis(trace: SessionTrace, drivers: list[CostDriver] | None = None) -> HumanDiagnosis:
    drivers = drivers if drivers is not None else build_session_trace_cost_drivers(trace)
    primary_driver = drivers[0].name if drivers else "None detected"
    actionable = actionable_cost_drivers(drivers)
    actionable_driver = actionable[0].name if actionable else primary_driver
    names = {driver.name for driver in drivers}
    billing_note = _billing_note(drivers)
    pattern_label = workflow_pattern_label(trace, drivers)
    evidence_metrics = diagnosis_evidence_metrics(trace, drivers)
    subtype = workflow_subtype(evidence_metrics, drivers)

    if not drivers:
        return HumanDiagnosis(
            root_cause="No dominant token-cost cause was detected from the observable session data.",
            workflow_failure="The session did not expose enough repeated context, long output, failures, or broad exploration to identify a clear workflow pattern.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=[],
            next_actions=["Inspect the largest commands, files, and repeated context before changing the workflow."],
            avoid_next_time=["Keep session scope explicit so future runs expose clearer diagnosis signals."],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if {"Broad exploration", "Long tool output", "Repeated file/artifact context"} <= names:
        return HumanDiagnosis(
            root_cause=(
                "The session got expensive because the agent explored too broadly before narrowing the implementation surface, "
                "then kept carrying large tool outputs and repeated files/artifacts forward."
            ),
            workflow_failure=(
                "Discovery, implementation, and verification were mixed into one long session instead of being split into scoped phases."
            ),
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(
                drivers,
                ("Broad exploration", "Long tool output", "Repeated file/artifact context", "Repeated context"),
            ),
            next_actions=[
                "Start the next run from a short checkpoint: goal, files already inspected, current hypothesis, and one next command.",
                "Narrow the task to one subsystem or file range before running more search/read commands.",
                "Summarize tool output longer than about 100 lines before continuing.",
            ],
            avoid_next_time=[
                "Do discovery in a short session, then start implementation from a compact summary.",
                "Cap search/read output and avoid carrying generated artifacts or long logs across turns.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if {"Long tool output", "Error/test log noise"} <= names or {"Long tool output", "Retry/failure loop"} <= names:
        return HumanDiagnosis(
            root_cause=(
                "The session got expensive because large tool output from commands, tests, or errors entered context "
                "and stayed there while the agent debugged."
            ),
            workflow_failure="A failure/debug loop produced more raw output than the next decision needed.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Long tool output", "Error/test log noise", "Retry/failure loop")),
            next_actions=[
                "Keep only the first failure summary and the last 80-120 log lines.",
                "Rerun the narrowest failing test or command instead of broad verification.",
                "If the same failure repeats, stop rerunning and change the diagnostic strategy.",
            ],
            avoid_next_time=[
                "Use scoped tests and log tails before asking the agent to continue.",
                "Summarize repeated failures instead of pasting or carrying full logs.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if "Environment issue" in names:
        return HumanDiagnosis(
            root_cause="The session got expensive because setup or environment blockers consumed the agent loop.",
            workflow_failure=(
                "Dependency, permission, network, config, or version issues were debugged inside the long coding session instead of being isolated first."
            ),
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Environment issue", "Retry/failure loop", "Long tool output")),
            next_actions=[
                "Fix the environment blocker outside the long agent loop.",
                "Resume with one short error summary and one validation command.",
            ],
            avoid_next_time=[
                "Validate dependencies, permissions, env vars, network access, and runtime versions before broad agent debugging.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if "Repeated file/artifact context" in names or "Repeated context" in names:
        return HumanDiagnosis(
            root_cause="The session got expensive because stable context was loaded repeatedly instead of being compacted.",
            workflow_failure="The workflow kept re-reading or re-stating files, artifacts, or transcript chunks across turns.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Repeated file/artifact context", "Repeated context", "Expensive file context")),
            next_actions=[
                "Create a compact memo for stable files/artifacts and refer to that memo instead of raw content.",
                "Inspect narrower file ranges when more detail is needed.",
            ],
            avoid_next_time=[
                "Summarize stable context once and restart from that summary when the session gets long.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if "Broad exploration" in names:
        return HumanDiagnosis(
            root_cause="The session got expensive because the agent searched or read too much workspace context before narrowing the hypothesis.",
            workflow_failure="The task began with broad exploration instead of a constrained subsystem, file range, or question.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Broad exploration", "Long tool output", "Expensive file context")),
            next_actions=[
                "State one hypothesis and one target subsystem before continuing.",
                "Use focused search/read commands and cap output size.",
            ],
            avoid_next_time=[
                "Ask for a brief discovery pass, then start a new implementation run from the narrowed summary.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if "Long tool output" in names:
        return HumanDiagnosis(
            root_cause="The session got expensive because large tool output entered the transcript.",
            workflow_failure="The workflow passed raw command output forward when a smaller summary or tail would have been enough.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Long tool output", "Error/test log noise")),
            next_actions=[
                "Replace broad output with scoped commands, `tail -100`, or a short summary.",
            ],
            avoid_next_time=[
                "Cap command output before it enters the agent context.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if "Session drift" in names:
        return HumanDiagnosis(
            root_cause="The session got expensive because later turns became much larger than early turns.",
            workflow_failure="A long-running session kept accumulating history after the useful working state had stabilized.",
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Session drift", "Repeated context", "Cache-heavy context")),
            next_actions=[
                "Start a fresh session from a checkpoint summary before continuing.",
            ],
            avoid_next_time=[
                "Split long tasks at stable checkpoints instead of carrying the full transcript forward.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    if primary_driver == "Cache-heavy context":
        return HumanDiagnosis(
            root_cause="The largest signal is cached input context, which explains token accounting more than workflow waste by itself.",
            workflow_failure=(
                "The session likely accumulated a large stable context. TokenCause needs more observable file/tool/failure signals to identify the exact workflow cause."
            ),
            workflow_pattern_label=pattern_label,
            workflow_subtype=subtype,
            evidence_metrics=evidence_metrics,
            evidence=_diagnosis_evidence(drivers, ("Cache-heavy context",)),
            next_actions=[
                "Open the session drilldown and inspect largest tool outputs, repeated files, and broad searches.",
                "Compact or restart from a short working summary if the context is no longer changing.",
            ],
            avoid_next_time=[
                "Do not treat cache-heavy usage alone as the root cause; look for what made the cached context large.",
            ],
            billing_note=billing_note,
            primary_driver=primary_driver,
            actionable_driver=actionable_driver,
        )

    return HumanDiagnosis(
        root_cause=f"The strongest actionable signal is {actionable_driver}.",
        workflow_failure="TokenCause detected a cost pattern, but it does not yet match a higher-level workflow diagnosis.",
        workflow_pattern_label=pattern_label,
        workflow_subtype=subtype,
        evidence_metrics=evidence_metrics,
        evidence=_diagnosis_evidence(drivers, tuple(driver.name for driver in actionable[:3])),
        next_actions=["Inspect the evidence for the top actionable driver and reduce that raw context source first."],
        avoid_next_time=["Keep the task scope narrow and summarize stable context before continuing."],
        billing_note=billing_note,
        primary_driver=primary_driver,
        actionable_driver=actionable_driver,
    )
