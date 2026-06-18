"""Structured diagnosis case files for AI coding sessions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .attribution import TokenAttribution, build_token_attribution
from .diagnosis import build_human_diagnosis, build_session_trace_cost_drivers
from .evidence import EvidenceItem, build_evidence_from_metrics
from .formatting import compact_number
from .models import CostDriver, SessionEvent, SessionTrace


@dataclass
class ObservedFact:
    name: str
    value: str
    detail: str = ""


@dataclass
class LikelyCause:
    name: str
    confidence: str
    why: str
    evidence: list[str] = field(default_factory=list)


@dataclass
class FileCarryover:
    file_ref: str
    appearances: int
    tokens: int
    repeated_tokens: int
    first_event_index: int
    last_event_index: int
    categories: list[str] = field(default_factory=list)


@dataclass
class DriftTimelinePoint:
    label: str
    event_index: int
    tokens: int
    detail: str


@dataclass
class SessionCaseFile:
    session_id: str
    source: str
    title: str
    cwd: str
    observed_facts: list[ObservedFact]
    token_attribution: list[TokenAttribution]
    evidence: list[EvidenceItem]
    drivers: list[CostDriver]
    likely_causes: list[LikelyCause]
    cause_sentence: str
    file_carryovers: list[FileCarryover]
    drift_timeline: list[DriftTimelinePoint]
    recommendations: list[str]
    limits: list[str]


def _confidence(evidence_count: int, drivers: list[CostDriver]) -> str:
    actionable_drivers = [driver for driver in drivers if driver.name != "Cache-heavy context"]
    if evidence_count >= 3 and len(actionable_drivers) >= 2:
        return "medium-high"
    if evidence_count >= 2 or actionable_drivers:
        return "medium"
    return "low"


def _observed_facts(trace: SessionTrace) -> list[ObservedFact]:
    facts = [
        ObservedFact("Observable tokens", compact_number(trace.observable_tokens), "Estimated from visible session events."),
        ObservedFact("Model total tokens", compact_number(trace.model_total_tokens), "Provider/model counters when present."),
        ObservedFact("Cached input tokens", compact_number(trace.cached_input_tokens), "Provider/model cache counters when present."),
        ObservedFact("Events", str(len(trace.events)), "Normalized events in the canonical SessionTrace."),
    ]
    if trace.cwd:
        facts.append(ObservedFact("Project cwd", trace.cwd, "Local project directory recorded by the adapter."))
    return facts


def _event_file_token_share(event: SessionEvent) -> int:
    return max(event.tokens // max(len(event.file_refs), 1), 1)


def build_file_carryovers(trace: SessionTrace) -> list[FileCarryover]:
    grouped: dict[str, list[tuple[SessionEvent, int]]] = defaultdict(list)
    for event in trace.events:
        if not event.file_refs:
            continue
        share = _event_file_token_share(event)
        for file_ref in event.file_refs:
            grouped[file_ref].append((event, share))

    carryovers: list[FileCarryover] = []
    for file_ref, entries in grouped.items():
        entries.sort(key=lambda item: item[0].index)
        if not entries:
            continue
        tokens = sum(share for _event, share in entries)
        first_tokens = entries[0][1]
        categories = sorted({event.category for event, _share in entries if event.category})
        carryovers.append(
            FileCarryover(
                file_ref=file_ref,
                appearances=len(entries),
                tokens=tokens,
                repeated_tokens=max(tokens - first_tokens, 0),
                first_event_index=entries[0][0].index,
                last_event_index=entries[-1][0].index,
                categories=categories,
            )
        )
    return sorted(carryovers, key=lambda item: (item.repeated_tokens, item.appearances, item.tokens), reverse=True)


def build_drift_timeline(trace: SessionTrace) -> list[DriftTimelinePoint]:
    if trace.usage_events:
        samples = [
            (index, usage.total_tokens)
            for index, usage in enumerate(trace.usage_events, start=1)
            if usage.total_tokens > 0
        ]
        source = "model usage sample"
    else:
        samples = [(event.index, event.tokens) for event in trace.events if event.tokens > 0]
        source = "session event"
    if len(samples) < 6:
        return []

    values = [tokens for _index, tokens in samples]
    window = max(2, min(5, len(values) // 3))
    early_avg = int(sum(values[:window]) / window)
    late_avg = int(sum(values[-window:]) / window)
    threshold = max(int(early_avg * 1.8), early_avg + 500)
    peak_index, peak_tokens = max(samples, key=lambda item: item[1])
    points = [
        DriftTimelinePoint(
            label="Early baseline",
            event_index=samples[0][0],
            tokens=early_avg,
            detail=f"Average of the first {window} {source}s.",
        )
    ]
    first_drift = next(((index, tokens) for index, tokens in samples[window:] if tokens >= threshold), None)
    if first_drift is not None:
        points.append(
            DriftTimelinePoint(
                label="First drift signal",
                event_index=first_drift[0],
                tokens=first_drift[1],
                detail=f"First {source} above the drift threshold of {compact_number(threshold)} tokens.",
            )
        )
    points.append(
        DriftTimelinePoint(
            label="Peak context",
            event_index=peak_index,
            tokens=peak_tokens,
            detail=f"Largest {source} observed in this session.",
        )
    )
    points.append(
        DriftTimelinePoint(
            label="Late baseline",
            event_index=samples[-1][0],
            tokens=late_avg,
            detail=f"Average of the last {window} {source}s.",
        )
    )
    return points


SOURCE_FILE_SUFFIXES = (
    ".swift",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".m",
    ".mm",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
)


def _display_file_ref(file_ref: str, cwd: str) -> str:
    if cwd and file_ref.startswith(cwd.rstrip("/") + "/"):
        return file_ref[len(cwd.rstrip("/")) + 1 :]
    return file_ref


def _is_project_source_file(item: FileCarryover, cwd: str) -> bool:
    file_ref = item.file_ref
    lower = file_ref.lower()
    if not lower.endswith(SOURCE_FILE_SUFFIXES):
        return False
    if cwd and not file_ref.startswith(cwd.rstrip("/") + "/"):
        return not file_ref.startswith("/")
    if file_ref.startswith("/") and not cwd:
        return False
    return True


def _sentence_fragment(text: str) -> str:
    return text.rstrip(".")


def build_cause_sentence(trace: SessionTrace, drivers: list[CostDriver], carryovers: list[FileCarryover]) -> str:
    parts: list[str] = []
    project_source_files = [
        item
        for item in carryovers
        if item.appearances >= 2 and _is_project_source_file(item, trace.cwd)
    ]
    project_source_files.sort(key=lambda item: (item.appearances, item.repeated_tokens, item.tokens), reverse=True)
    repeated_files = project_source_files[:2] or [item for item in carryovers if item.appearances >= 2][:2]
    if repeated_files:
        parts.append(
            ", ".join(
                f"`{_display_file_ref(item.file_ref, trace.cwd)}` appeared {item.appearances}x"
                for item in repeated_files
            )
        )
    long_output = next((driver for driver in drivers if driver.name == "Long tool output"), None)
    if long_output:
        parts.append(f"a large tool payload entered context ({compact_number(long_output.impact_tokens)} total long-output tokens)")
    if trace.cached_input_tokens:
        parts.append(f"cached input accumulated {compact_number(trace.cached_input_tokens)} across {len(trace.usage_events)} model calls")
    drift = next((driver for driver in drivers if driver.name == "Session drift"), None)
    if drift:
        parts.append(_sentence_fragment(drift.evidence))
    if not parts:
        actionable = next((driver for driver in drivers if driver.name != "Cache-heavy context"), None)
        if actionable:
            parts.append(actionable.evidence)
    if not parts:
        return "TokenCause did not find enough concrete evidence to explain this session beyond the raw token totals."
    return "This session looks expensive because " + "; ".join(parts) + "."


def build_session_case_file(trace: SessionTrace, drivers: list[CostDriver] | None = None) -> SessionCaseFile:
    drivers = drivers if drivers is not None else build_session_trace_cost_drivers(trace)
    diagnosis = build_human_diagnosis(trace, drivers)
    evidence = build_evidence_from_metrics(diagnosis.evidence_metrics)
    file_carryovers = build_file_carryovers(trace)
    drift_timeline = build_drift_timeline(trace)
    likely_causes = [
        LikelyCause(
            name=diagnosis.workflow_subtype or diagnosis.workflow_pattern_label or diagnosis.actionable_driver or "No clear cause",
            confidence=_confidence(len(evidence), drivers),
            why=diagnosis.root_cause,
            evidence=[item.name for item in evidence if item.supports != "Billing/cache signal"],
        )
    ]
    limits = [
        "TokenCause can only diagnose signals present in local session records.",
        "Driver impact can overlap; diagnostic token totals are not billing totals.",
        "Workflow causes are heuristic inferences from observable evidence, not ground truth.",
    ]
    if trace.cached_input_tokens:
        limits.append("Cached input explains billing shape but not necessarily the underlying workflow mistake.")
    return SessionCaseFile(
        session_id=trace.id,
        source=trace.source,
        title=trace.title,
        cwd=trace.cwd,
        observed_facts=_observed_facts(trace),
        token_attribution=build_token_attribution(trace),
        evidence=evidence,
        drivers=drivers,
        likely_causes=likely_causes,
        cause_sentence=build_cause_sentence(trace, drivers, file_carryovers),
        file_carryovers=file_carryovers,
        drift_timeline=drift_timeline,
        recommendations=diagnosis.next_actions,
        limits=limits,
    )


def session_case_file_to_json(case_file: SessionCaseFile) -> dict[str, Any]:
    return {
        "session_id": case_file.session_id,
        "source": case_file.source,
        "title": case_file.title,
        "cwd": case_file.cwd,
        "observed_facts": [fact.__dict__ for fact in case_file.observed_facts],
        "token_attribution": [item.__dict__ for item in case_file.token_attribution],
        "evidence": [item.__dict__ for item in case_file.evidence],
        "drivers": [
            {
                "name": driver.name,
                "impact_tokens": driver.impact_tokens,
                "summary": driver.summary,
                "evidence": driver.evidence,
            }
            for driver in case_file.drivers
        ],
        "likely_causes": [cause.__dict__ for cause in case_file.likely_causes],
        "cause_sentence": case_file.cause_sentence,
        "file_carryovers": [item.__dict__ for item in case_file.file_carryovers],
        "drift_timeline": [item.__dict__ for item in case_file.drift_timeline],
        "recommendations": list(case_file.recommendations),
        "limits": list(case_file.limits),
    }
