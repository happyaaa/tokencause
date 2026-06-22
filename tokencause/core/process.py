"""Engineering process phase analysis for AI coding sessions."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .files import is_project_ref
from .formatting import compact_number
from .models import CostDriver, SessionEvent, SessionTrace


PHASES = ("discovery", "implementation", "verification", "debugging", "review_coordination", "other")


@dataclass
class ProcessPhase:
    name: str
    tokens: int
    events: int
    share: float
    evidence: list[str] = field(default_factory=list)


@dataclass
class ProcessSummary:
    shape: str
    phases: list[ProcessPhase]
    narrative: str


DISCOVERY_COMMANDS = (
    "rg",
    "grep",
    "find",
    "fd",
    "ls",
    "tree",
    "sed",
    "cat",
    "head",
    "tail",
    "nl",
)
IMPLEMENTATION_HINTS = (
    "apply_patch",
    "patch",
    "edit",
    "write",
    "created file",
    "updated file",
    "modified",
    "save",
)
VERIFICATION_HINTS = (
    "pytest",
    "unittest",
    "npm test",
    "pnpm test",
    "yarn test",
    "go test",
    "cargo test",
    "xcodebuild test",
    "lint",
    "typecheck",
    "tsc",
    "mypy",
    "ruff",
    "eslint",
    "npm run build",
    "pnpm build",
    "swift build",
    "cargo build",
    "go build",
    "xcodebuild",
)
DEBUG_HINTS = (
    "traceback",
    "failed",
    "failure",
    "exception",
    "error:",
    "assertionerror",
    "panic",
    "segfault",
    "permission denied",
    "timeout",
)
REVIEW_HINTS = (
    "git diff",
    "git status",
    "git log",
    "git show",
    "gh pr",
    "gh issue",
    "review",
    "changelog",
    "release notes",
)


def _event_text(event: SessionEvent) -> str:
    return "\n".join(
        part
        for part in (
            event.category,
            event.command,
            event.preview,
            event.step,
            event.tool,
            event.error,
            " ".join(event.file_refs),
        )
        if part
    ).lower()


def _command_starts_with(command: str, names: tuple[str, ...]) -> bool:
    stripped = command.strip().lower()
    if not stripped:
        return False
    return any(stripped == name or stripped.startswith(name + " ") or f" {name} " in stripped for name in names)


def _has_verification_command(command: str) -> bool:
    stripped = command.strip().lower()
    if not stripped:
        return False
    return any(hint in stripped for hint in VERIFICATION_HINTS)


def classify_process_phase(event: SessionEvent, cwd: str = "") -> str:
    text = _event_text(event)
    command = event.command.lower()
    category = event.category.lower()

    if category == "error_log" or event.status not in ("", "ok", "success", "succeeded") or any(hint in text for hint in DEBUG_HINTS):
        return "debugging"
    if category in ("test_log", "build_log") or _has_verification_command(command):
        return "verification"
    if any(hint in text for hint in IMPLEMENTATION_HINTS):
        return "implementation"
    if any(hint in text for hint in REVIEW_HINTS):
        return "review_coordination"
    project_refs = [file_ref for file_ref in event.file_refs if is_project_ref(file_ref, cwd)]
    if category == "search_output" or _command_starts_with(command, DISCOVERY_COMMANDS):
        return "discovery"
    if project_refs and category in ("assistant_message", "tool_output", "other_tool_output", "tool_result"):
        return "discovery"
    if category in ("other_tool_output", "tool_output") and project_refs:
        return "discovery"
    return "other"


def _phase_evidence(name: str, events: list[SessionEvent]) -> list[str]:
    evidence: list[str] = []
    commands = [event.command for event in events if event.command]
    file_refs = {file_ref for event in events for file_ref in event.file_refs if is_project_ref(file_ref)}

    if name == "discovery":
        search_commands = sum(1 for event in events if event.category == "search_output" or _command_starts_with(event.command, DISCOVERY_COMMANDS))
        if search_commands:
            evidence.append(f"{search_commands} search/read command(s)")
        if file_refs:
            evidence.append(f"{len(file_refs)} file reference(s)")
    elif name == "implementation":
        if commands:
            evidence.append(f"{len(commands)} edit-like command(s)")
        if file_refs:
            evidence.append(f"{len(file_refs)} source/artifact reference(s)")
    elif name == "verification":
        if commands:
            evidence.append(f"{len(commands)} test/build/lint command(s)")
        categories = Counter(event.category for event in events)
        if categories:
            evidence.append(", ".join(f"{category}: {count}" for category, count in categories.most_common(2)))
    elif name == "debugging":
        error_like = sum(1 for event in events if event.category == "error_log" or any(hint in _event_text(event) for hint in DEBUG_HINTS))
        if error_like:
            evidence.append(f"{error_like} error/failure-like event(s)")
        if commands:
            evidence.append(f"{len(commands)} command(s) in failure context")
    elif name == "review_coordination":
        if commands:
            evidence.append(f"{len(commands)} git/review command(s)")
    elif name == "other":
        evidence.append(f"{len(events)} unclassified event(s)")

    top_command = next((command for command in commands if command), "")
    if top_command:
        evidence.append(f"Example: `{top_command[:120]}`")
    return evidence[:3]


def _phase_lookup(phases: list[ProcessPhase]) -> dict[str, ProcessPhase]:
    return {phase.name: phase for phase in phases}


def _has_driver(drivers: list[CostDriver], name: str) -> bool:
    return any(driver.name == name for driver in drivers)


def _last_event_index(events: list[SessionEvent], phase_name: str, cwd: str = "") -> int:
    indexes = [event.index for event in events if classify_process_phase(event, cwd) == phase_name]
    return max(indexes) if indexes else 0


def process_shape(trace: SessionTrace, phases: list[ProcessPhase], drivers: list[CostDriver] | None = None) -> str:
    drivers = drivers or []
    lookup = _phase_lookup(phases)
    discovery = lookup["discovery"].share
    implementation = lookup["implementation"].share
    verification = lookup["verification"].share
    debugging = lookup["debugging"].share
    review = lookup["review_coordination"].share
    active = [phase for phase in phases if phase.name != "other" and phase.share >= 0.10]
    last_implementation = _last_event_index(trace.events, "implementation", trace.cwd)
    last_verification = _last_event_index(trace.events, "verification", trace.cwd)
    weak_verification = implementation > 0 and (verification < 0.05 or last_verification < last_implementation)

    if discovery >= 0.45 and verification < 0.05:
        return "Discovery-heavy, weak verification"
    if debugging >= 0.25 or _has_driver(drivers, "Retry/failure loop"):
        return "Debug loop with high log carryover"
    if implementation >= 0.10 and verification >= 0.05 and last_verification >= last_implementation:
        return "Implementation with verification"
    if implementation > 0 and weak_verification:
        return "Implementation without verification"
    if len(active) >= 3:
        return "Long mixed session"
    if (implementation > 0 or discovery >= 0.30) and review < 0.03:
        return "Review-light high-risk session"
    if discovery > 0 and implementation > 0 and verification > 0 and max(phase.share for phase in phases) <= 0.55:
        return "Balanced coding session"
    return "Low-signal session"


def process_narrative(summary: ProcessSummary) -> str:
    ranked = [phase for phase in summary.phases if phase.tokens > 0 and phase.name != "other"]
    ranked.sort(key=lambda phase: phase.tokens, reverse=True)
    if not ranked:
        return "TokenCause did not find enough process signal to classify this session."
    primary = ranked[0]
    secondary = ranked[1] if len(ranked) > 1 else None
    if secondary:
        return (
            f"The session spent most observable tokens on {primary.name.replace('_', ' ')} "
            f"({primary.share:.0%}) and {secondary.name.replace('_', ' ')} ({secondary.share:.0%})."
        )
    return f"The session spent most observable tokens on {primary.name.replace('_', ' ')} ({primary.share:.0%})."


def build_process_summary(trace: SessionTrace, drivers: list[CostDriver] | None = None) -> ProcessSummary:
    grouped: dict[str, list[SessionEvent]] = defaultdict(list)
    for event in trace.events:
        grouped[classify_process_phase(event, trace.cwd)].append(event)

    total_tokens = trace.observable_tokens or sum(event.tokens for event in trace.events) or 1
    phases = []
    for name in PHASES:
        events = grouped.get(name, [])
        tokens = sum(event.tokens for event in events)
        phases.append(
            ProcessPhase(
                name=name,
                tokens=tokens,
                events=len(events),
                share=tokens / total_tokens,
                evidence=_phase_evidence(name, events) if events else [],
            )
        )

    shape = process_shape(trace, phases, drivers)
    summary = ProcessSummary(shape=shape, phases=phases, narrative="")
    summary.narrative = process_narrative(summary)
    if shape == "Discovery-heavy, weak verification":
        summary.narrative += " Discovery dominated while verification evidence stayed weak."
    elif shape == "Implementation without verification":
        summary.narrative += " Implementation happened without enough later test/build/lint evidence."
    elif shape == "Debug loop with high log carryover":
        summary.narrative += " Failure/debug output appears to be a major part of the session shape."
    return summary


def process_summary_to_json(summary: ProcessSummary) -> dict[str, object]:
    return {
        "shape": summary.shape,
        "narrative": summary.narrative,
        "phases": [
            {
                "name": phase.name,
                "tokens": phase.tokens,
                "events": phase.events,
                "share": round(phase.share, 6),
                "evidence": list(phase.evidence),
            }
            for phase in summary.phases
        ],
    }


def process_phase_summary_line(phase: ProcessPhase) -> str:
    return f"{phase.name.replace('_', ' ')}: {compact_number(phase.tokens)} tokens ({phase.share:.0%})"
