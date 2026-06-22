"""Review and maintenance risk signals for AI coding sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .files import file_risk_reason, is_agent_internal_ref, is_project_ref
from .formatting import compact_number
from .models import CostDriver, SessionTrace
from .process import ProcessSummary


@dataclass
class RiskSignal:
    name: str
    severity: str
    why: str
    evidence: list[str] = field(default_factory=list)


HIGH_SENSITIVE_HINTS = (
    "auth",
    "login",
    "session",
    "jwt",
    "oauth",
    "payment",
    "billing",
    "stripe",
    "checkout",
    "invoice",
    "security",
    "secret",
    "access_token",
    "refresh_token",
    "api_key",
    ".env",
    "credential",
    "deploy",
    "prod",
)
MEDIUM_SENSITIVE_HINTS = (
    "permission",
    "policy",
    "rbac",
    "migration",
    "schema",
    "database",
    ".sql",
    "infra",
    "terraform",
    "cloud",
)


def _phase(summary: ProcessSummary, name: str):
    return next(phase for phase in summary.phases if phase.name == name)


def _driver_names(drivers: list[CostDriver]) -> set[str]:
    return {driver.name for driver in drivers}


def _project_file_refs(trace: SessionTrace) -> list[str]:
    values: list[str] = []
    for event in trace.events:
        values.extend(file_ref for file_ref in event.file_refs if is_project_ref(file_ref, trace.cwd))
    return values


def _sensitive_matches(trace: SessionTrace) -> tuple[str, list[str]]:
    high: list[str] = []
    medium: list[str] = []
    for value in _project_file_refs(trace):
        lower = value.lower()
        if any(hint in lower for hint in HIGH_SENSITIVE_HINTS):
            high.append(value)
        elif any(hint in lower for hint in MEDIUM_SENSITIVE_HINTS):
            medium.append(value)
    if high:
        return "high", high[:4]
    if medium:
        return "medium", medium[:4]
    return "", []


def _unique_file_refs(trace: SessionTrace) -> set[str]:
    return {file_ref for event in trace.events for file_ref in event.file_refs if is_project_ref(file_ref, trace.cwd)}


def _directory_count(file_refs: set[str]) -> int:
    dirs = set()
    for file_ref in file_refs:
        parts = file_ref.split("/")
        if len(parts) > 1:
            dirs.add("/".join(parts[:-1][:3]))
    return len(dirs)


def _last_phase_index(trace: SessionTrace, phase_name: str) -> int:
    from .process import classify_process_phase

    indexes = [event.index for event in trace.events if classify_process_phase(event, trace.cwd) == phase_name]
    return max(indexes) if indexes else 0


def _generated_artifact_carryovers(carryovers: list[Any], cwd: str = "") -> list[Any]:
    return [item for item in carryovers if not is_agent_internal_ref(item.file_ref, cwd) and file_risk_reason(item.file_ref)]


def build_risk_signals(
    trace: SessionTrace,
    process_summary: ProcessSummary,
    drivers: list[CostDriver],
    carryovers: list[Any],
) -> list[RiskSignal]:
    risks: list[RiskSignal] = []
    names = _driver_names(drivers)
    implementation = _phase(process_summary, "implementation")
    verification = _phase(process_summary, "verification")
    review = _phase(process_summary, "review_coordination")
    last_implementation = _last_phase_index(trace, "implementation")
    last_verification = _last_phase_index(trace, "verification")

    if implementation.events and (verification.share < 0.05 or last_verification < last_implementation):
        risks.append(
            RiskSignal(
                name="Weak verification",
                severity="medium",
                why="Implementation happened without enough later test/build/lint evidence.",
                evidence=[
                    f"Implementation: {implementation.events} event(s), {compact_number(implementation.tokens)} tokens.",
                    "No verification phase appeared after the last implementation event."
                    if last_verification < last_implementation
                    else f"Verification share was only {verification.share:.0%}.",
                ],
            )
        )

    severity, sensitive = _sensitive_matches(trace)
    if sensitive:
        risks.append(
            RiskSignal(
                name="Sensitive area touched",
                severity=severity,
                why="The session referenced files or commands in areas that usually require careful review.",
                evidence=sensitive,
            )
        )

    refs = _unique_file_refs(trace)
    directories = _directory_count(refs)
    if len(refs) >= 50 or "Broad exploration" in names:
        broad = next((driver for driver in drivers if driver.name == "Broad exploration"), None)
        evidence = [f"{len(refs)} unique file reference(s)", f"{directories} directory group(s)"]
        if broad:
            evidence.append(broad.evidence)
        risks.append(
            RiskSignal(
                name="Large review surface",
                severity="high" if len(refs) >= 200 else "medium",
                why="The session touched or inspected enough files that review and coordination cost may dominate raw generation cost.",
                evidence=evidence,
            )
        )

    pollution_drivers = [
        driver
        for driver in drivers
        if driver.name in {"Repeated file/artifact context", "Repeated context", "Long tool output", "Cache-heavy context"}
    ]
    if pollution_drivers:
        risks.append(
            RiskSignal(
                name="Context pollution risk",
                severity="medium",
                why="Repeated files, long tool output, or cache-heavy context may have carried stale or low-signal information forward.",
                evidence=[f"{driver.name}: {driver.evidence}" for driver in pollution_drivers[:3]],
            )
        )

    retry_driver = next((driver for driver in drivers if driver.name == "Retry/failure loop"), None)
    retry_loop = trace.retry_loops[0] if trace.retry_loops else None
    if retry_driver or retry_loop:
        risks.append(
            RiskSignal(
                name="Retry loop before final answer",
                severity="medium",
                why="Repeated failures can make later agent decisions depend on noisy or stale failure context.",
                evidence=[retry_driver.evidence if retry_driver else f"{retry_loop.count} repeated failure(s): {retry_loop.command}"],
            )
        )

    environment_driver = next((driver for driver in drivers if driver.name == "Environment issue"), None)
    if environment_driver:
        risks.append(
            RiskSignal(
                name="Environment workaround risk",
                severity="medium",
                why="Setup, dependency, permission, network, or version issues were debugged inside the agent loop.",
                evidence=[environment_driver.evidence],
            )
        )

    generated = _generated_artifact_carryovers(carryovers, trace.cwd)
    if generated:
        examples = [f"{item.file_ref} ({file_risk_reason(item.file_ref)})" for item in generated[:3]]
        risks.append(
            RiskSignal(
                name="Generated artifact risk",
                severity="medium",
                why="Low-signal generated, schema, fixture, snapshot, lockfile, or minified content appeared repeatedly.",
                evidence=examples,
            )
        )

    if (implementation.events or sensitive) and review.share < 0.03:
        risks.append(
            RiskSignal(
                name="Review-light session",
                severity="medium" if sensitive else "low",
                why="The session had implementation or sensitive-area signals but little git diff/status/review evidence.",
                evidence=[
                    f"Review/coordination share: {review.share:.0%}.",
                    "No strong git diff/status/review phase was detected." if review.events == 0 else f"{review.events} review event(s).",
                ],
            )
        )

    return risks[:8]


def risk_signals_to_json(risks: list[RiskSignal]) -> list[dict[str, object]]:
    return [
        {
            "name": risk.name,
            "severity": risk.severity,
            "why": risk.why,
            "evidence": list(risk.evidence),
        }
        for risk in risks
    ]
