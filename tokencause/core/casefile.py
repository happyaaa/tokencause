"""Structured diagnosis case files for AI coding sessions."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .attribution import TokenAttribution, build_token_attribution
from .diagnosis import build_human_diagnosis, build_session_trace_cost_drivers
from .evidence import EvidenceItem, build_evidence_from_metrics
from .files import is_agent_internal_ref
from .formatting import compact_number
from .models import CostDriver, SessionEvent, SessionTrace
from .process import ProcessSummary, build_process_summary, process_summary_to_json
from .risk import RiskSignal, build_risk_signals, risk_signals_to_json


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
class WorkflowLesson:
    title: str
    lesson: str
    trigger: str


@dataclass
class AttributionQuality:
    level: str
    reason: str
    unclassified_share: float
    assistant_or_other_share: float


@dataclass
class ValueEvidence:
    level: str
    why: str
    signals: list[str] = field(default_factory=list)


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
    process_summary: ProcessSummary
    risk_signals: list[RiskSignal]
    attribution_quality: AttributionQuality
    value_evidence: ValueEvidence
    next_run_plan: list[str]
    recommendations: list[str]
    workflow_lessons: list[WorkflowLesson]
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
        ObservedFact("visible transcript tokens", compact_number(trace.observable_tokens), "Estimated from visible session events."),
        ObservedFact("model usage tokens", compact_number(trace.model_total_tokens), "Provider/model counters when present."),
        ObservedFact("cached input tokens", compact_number(trace.cached_input_tokens), "Provider/model cache counters when present."),
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
            if is_agent_internal_ref(file_ref, trace.cwd):
                continue
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


def build_cause_sentence(
    trace: SessionTrace,
    drivers: list[CostDriver],
    carryovers: list[FileCarryover],
    attribution_quality: AttributionQuality | None = None,
) -> str:
    if attribution_quality and attribution_quality.level == "low":
        return (
            "TokenCause cannot make a high-confidence workflow cause claim because most observable tokens "
            "came from assistant/context payloads that are not separable into concrete files, commands, and tool results. "
            "Treat repeated context, retry, long-output, and drift signals as secondary evidence."
        )
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


def build_workflow_lessons(
    trace: SessionTrace,
    drivers: list[CostDriver],
    carryovers: list[FileCarryover],
    recommendations: list[str],
    attribution_quality: AttributionQuality | None = None,
) -> list[WorkflowLesson]:
    driver_names = {driver.name for driver in drivers}
    lessons: list[WorkflowLesson] = []

    if attribution_quality and attribution_quality.level == "low":
        lessons.append(
            WorkflowLesson(
                title="Improve source attribution before workflow claims",
                lesson=(
                    "Treat workflow conclusions as secondary until the session source can separate assistant/context payloads "
                    "from concrete commands, files, and tool results."
                ),
                trigger=attribution_quality.reason,
            )
        )

    project_source = next(
        (
            item
            for item in carryovers
            if item.appearances >= 3 and _is_project_source_file(item, trace.cwd)
        ),
        None,
    )
    repeated_any = next((item for item in carryovers if item.appearances >= 3 and not is_agent_internal_ref(item.file_ref, trace.cwd)), None)
    repeated = project_source or repeated_any
    if repeated:
        lessons.append(
            WorkflowLesson(
                title="Promote repeated context into a checkpoint memo",
                lesson=(
                    f"When `{_display_file_ref(repeated.file_ref, trace.cwd)}` or similar artifacts recur, "
                    "carry forward a short summary instead of raw file/tool content."
                ),
                trigger=f"{_display_file_ref(repeated.file_ref, trace.cwd)} appeared {repeated.appearances}x.",
            )
        )

    if "Broad exploration" in driver_names:
        broad_driver = next(driver for driver in drivers if driver.name == "Broad exploration")
        lessons.append(
            WorkflowLesson(
                title="Narrow discovery before implementation",
                lesson=(
                    "Start the next run with one subsystem, file range, or hypothesis before allowing broad search commands."
                ),
                trigger=broad_driver.evidence,
            )
        )

    if "Long tool output" in driver_names or "Error/test log noise" in driver_names:
        output_driver = next(
            (driver for driver in drivers if driver.name in {"Long tool output", "Error/test log noise"}),
            None,
        )
        lessons.append(
            WorkflowLesson(
                title="Summarize raw tool output before continuing",
                lesson=(
                    "Keep only the decision-relevant tail, failure summary, or changed lines before the next model call."
                ),
                trigger=output_driver.evidence if output_driver else "Large tool output entered the session context.",
            )
        )

    if "Retry/failure loop" in driver_names or "Environment issue" in driver_names:
        retry_driver = next(
            (driver for driver in drivers if driver.name in {"Retry/failure loop", "Environment issue"}),
            None,
        )
        lessons.append(
            WorkflowLesson(
                title="Change strategy after repeated failures",
                lesson=(
                    "After repeated failures, stop rerunning the same command and switch to a narrower diagnostic step."
                ),
                trigger=retry_driver.evidence if retry_driver else "Repeated failure signals were detected.",
            )
        )

    if "Session drift" in driver_names or trace.cached_input_tokens:
        lessons.append(
            WorkflowLesson(
                title="Restart from a compact checkpoint when context stabilizes",
                lesson=(
                    "Start a fresh session with goals, touched files, decisions, and open blockers instead of carrying the raw transcript."
                ),
                trigger=(
                    f"Cached input accumulated {compact_number(trace.cached_input_tokens)}."
                    if trace.cached_input_tokens
                    else "Late-session context grew beyond the early baseline."
                ),
            )
        )

    if not lessons and recommendations:
        lessons.append(
            WorkflowLesson(
                title="Turn the next action into a reusable rule",
                lesson=recommendations[0],
                trigger="No stronger repeated workflow pattern was detected.",
            )
        )
    return lessons[:4]


def build_attribution_quality(trace: SessionTrace, token_attribution: list[TokenAttribution], process_summary: ProcessSummary) -> AttributionQuality:
    tokens_by_name = {item.name: item.tokens for item in token_attribution}
    observable = trace.observable_tokens or 1
    assistant_or_other = (
        tokens_by_name.get("assistant_message", 0)
        + tokens_by_name.get("other", 0)
        + tokens_by_name.get("other_tool_output", 0)
    )
    other_phase = next((phase for phase in process_summary.phases if phase.name == "other"), None)
    unclassified_share = other_phase.share if other_phase else 0.0
    assistant_or_other_share = assistant_or_other / observable
    dominant = max(unclassified_share, assistant_or_other_share)
    if dominant > 0.70:
        return AttributionQuality(
            level="low",
            reason=(
                f"Most tokens came from {trace.source.title()}-side assistant/context payloads that TokenCause cannot yet break down into files, commands, and tool results."
            ),
            unclassified_share=unclassified_share,
            assistant_or_other_share=assistant_or_other_share,
        )
    if dominant > 0.35:
        return AttributionQuality(
            level="medium",
            reason="A meaningful share of tokens is assistant/context or unclassified payloads; diagnosis should be read as directional.",
            unclassified_share=unclassified_share,
            assistant_or_other_share=assistant_or_other_share,
        )
    return AttributionQuality(
        level="high",
        reason="Most observable tokens were classifiable into workflow phases or concrete token categories.",
        unclassified_share=unclassified_share,
        assistant_or_other_share=assistant_or_other_share,
    )


def build_value_evidence(process_summary: ProcessSummary, risks: list[RiskSignal], drivers: list[CostDriver]) -> ValueEvidence:
    risk_names = {risk.name for risk in risks}
    driver_names = {driver.name for driver in drivers}
    verification_phase = next((phase for phase in process_summary.phases if phase.name == "verification"), None)
    verification_share = verification_phase.share if verification_phase else 0.0
    signals: list[str] = []
    level = "mixed"
    if "Weak verification" in risk_names:
        signals.append("Implementation happened with weak verification evidence.")
    if "Context pollution risk" in risk_names or "Repeated context" in driver_names:
        signals.append("Repeated or low-signal context increased review cost.")
    if "Retry loop before final answer" in risk_names:
        signals.append("Repeated failures consumed part of the session.")
    if "Large review surface" in risk_names:
        signals.append("The session created a large review surface.")
    if process_summary.shape in {"Implementation with verification", "Balanced coding session"} and not signals:
        return ValueEvidence(
            level="strong",
            why="Most visible work translated into implementation and verification evidence.",
            signals=["Implementation and verification were both visible."],
        )
    if len(signals) >= 3 or ("Weak verification" in risk_names and "Retry loop before final answer" in risk_names):
        level = "weak"
        if verification_share >= 0.25 and "Weak verification" not in risk_names:
            why = "Verification consumed many tokens, but large review surface, drift, or repeated context reduced confidence in session efficiency."
        else:
            why = "Most tokens did not translate into strong verification evidence; repeated context, retry loops, or review surface risks dominated the signal."
    else:
        why = "Some tokens produced useful progress signals, but workflow risks make efficiency uncertain."
    return ValueEvidence(level=level, why=why, signals=signals[:4])


def build_next_run_plan(
    trace: SessionTrace,
    carryovers: list[FileCarryover],
    recommendations: list[str],
    risks: list[RiskSignal],
    attribution_quality: AttributionQuality | None = None,
) -> list[str]:
    if attribution_quality and attribution_quality.level == "low":
        return [
            "Treat this report as directional until the source transcript can separate files, commands, and tool results.",
            "Start the next run from a short checkpoint summary instead of continuing the raw session.",
            "Keep one validation command and summarize any large tool output before continuing.",
        ]
    plan = ["Start from a 5-line checkpoint summary: goal, current hypothesis, touched files, open blocker, validation command."]
    project_sources = [
        item
        for item in carryovers
        if item.appearances >= 2 and _is_project_source_file(item, trace.cwd)
    ][:3]
    if project_sources:
        files = ", ".join(f"`{_display_file_ref(item.file_ref, trace.cwd)}`" for item in project_sources[:3])
        plan.append(f"Inspect only {files} first; do not reopen broad workspace context until that hypothesis fails.")
    else:
        plan.append("Pick one subsystem or file range before running broad search/read commands.")
    if any(risk.name == "Weak verification" for risk in risks):
        plan.append("End the next run with one explicit test/build/lint command and keep only the final result summary.")
    if any(risk.name == "Context pollution risk" for risk in risks):
        plan.append("Summarize long logs/tool output before the next model call; avoid carrying raw payloads forward.")
    for recommendation in recommendations:
        if "checkpoint" in recommendation.lower() and any("checkpoint" in item.lower() for item in plan):
            continue
        plan.append(recommendation)
        break
    return list(dict.fromkeys(plan))[:3]


def build_session_case_file(trace: SessionTrace, drivers: list[CostDriver] | None = None) -> SessionCaseFile:
    drivers = drivers if drivers is not None else build_session_trace_cost_drivers(trace)
    diagnosis = build_human_diagnosis(trace, drivers)
    evidence = build_evidence_from_metrics(diagnosis.evidence_metrics)
    file_carryovers = build_file_carryovers(trace)
    drift_timeline = build_drift_timeline(trace)
    token_attribution = build_token_attribution(trace)
    process_summary = build_process_summary(trace, drivers)
    risk_signals = build_risk_signals(trace, process_summary, drivers, file_carryovers)
    attribution_quality = build_attribution_quality(trace, token_attribution, process_summary)
    likely_causes = [
        LikelyCause(
            name=(
                "Diagnosis limited - low attribution quality"
                if attribution_quality.level == "low"
                else diagnosis.workflow_subtype or diagnosis.workflow_pattern_label or diagnosis.actionable_driver or "No clear cause"
            ),
            confidence="low" if attribution_quality.level == "low" else _confidence(len(evidence), drivers),
            why=(
                attribution_quality.reason + " Secondary signals: " + diagnosis.root_cause
                if attribution_quality.level == "low"
                else diagnosis.root_cause
            ),
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
    recommendations = diagnosis.next_actions
    return SessionCaseFile(
        session_id=trace.id,
        source=trace.source,
        title=trace.title,
        cwd=trace.cwd,
        observed_facts=_observed_facts(trace),
        token_attribution=token_attribution,
        evidence=evidence,
        drivers=drivers,
        likely_causes=likely_causes,
        cause_sentence=build_cause_sentence(trace, drivers, file_carryovers, attribution_quality),
        file_carryovers=file_carryovers,
        drift_timeline=drift_timeline,
        process_summary=process_summary,
        risk_signals=risk_signals,
        attribution_quality=attribution_quality,
        value_evidence=build_value_evidence(process_summary, risk_signals, drivers),
        next_run_plan=build_next_run_plan(trace, file_carryovers, recommendations, risk_signals, attribution_quality),
        recommendations=recommendations,
        workflow_lessons=build_workflow_lessons(trace, drivers, file_carryovers, recommendations, attribution_quality),
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
        "process_summary": process_summary_to_json(case_file.process_summary),
        "risk_signals": risk_signals_to_json(case_file.risk_signals),
        "attribution_quality": case_file.attribution_quality.__dict__,
        "value_evidence": case_file.value_evidence.__dict__,
        "next_run_plan": list(case_file.next_run_plan),
        "recommendations": list(case_file.recommendations),
        "workflow_lessons": [item.__dict__ for item in case_file.workflow_lessons],
        "limits": list(case_file.limits),
    }
