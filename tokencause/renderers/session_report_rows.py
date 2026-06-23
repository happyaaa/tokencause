"""Row preparation helpers for session diagnosis reports."""

from __future__ import annotations

from collections import Counter

from tokencause.core.formatting import compact_number, money
from tokencause.core.models import CostDriver
from tokencause.core.tokens import short_preview
from tokencause.renderers.redaction import redact_text
from tokencause.renderers.session_report_models import SessionReportView


OBSERVABLE_SOURCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Discovery / search", ("search_output",)),
    ("Tool results", ("other_tool_output", "tool_output", "build_log", "install_log")),
    ("Debug / verification", ("test_log", "error_log")),
    ("Conversation", ("assistant_message", "user_message")),
    ("Tool calls", ("tool_call",)),
)

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
)


def clean_driver_evidence(driver: CostDriver) -> str:
    text = driver.evidence
    lower = text.lower()
    if driver.name == "Long tool output" and (
        "[{'type':" in text
        or '"type":' in text
        or "wall time:" in lower
        or "chunk_id:" in lower
        or "tool result" in lower
    ):
        return "Largest tool output was a large command/tool result payload; inspect the appendix for the source command or output category."
    return text


def report_metric_cards(view: SessionReportView) -> list[tuple[str, str]]:
    if view.case_file.attribution_quality.level != "low":
        return view.metric_cards
    return [
        ("Model usage tokens", compact_number(view.scope.model_billed_tokens)),
        ("Classifiable tokens", compact_number(view.scope.classifiable_tokens)),
        ("Attribution quality", view.case_file.attribution_quality.level.title()),
        ("Events", str(len(view.trace.events))),
    ]


def observable_source_group_rows(category_tokens: Counter[str]) -> list[tuple[str, int, int, str]]:
    total = sum(category_tokens.values()) or 1
    rows: list[tuple[str, int, int, str]] = []
    seen: set[str] = set()
    for group, categories in OBSERVABLE_SOURCE_GROUPS:
        tokens = sum(category_tokens.get(category, 0) for category in categories)
        seen.update(categories)
        if tokens:
            present = [category for category in categories if category_tokens.get(category, 0)]
            rows.append((group, tokens, total, f"{compact_number(tokens)} tokens from {', '.join(present)}"))
    other_tokens = sum(tokens for category, tokens in category_tokens.items() if category not in seen)
    if other_tokens:
        rows.append(("Other", other_tokens, total, f"{compact_number(other_tokens)} tokens"))
    return rows


def report_actionable_driver_rows(view: SessionReportView, limit: int = 6) -> list[tuple[str, int, int, str]]:
    actionable_drivers = [driver for driver in view.drivers if driver.name != "Cache-heavy context"]
    if view.case_file.attribution_quality.level == "low":
        actionable_drivers = [driver for driver in actionable_drivers if driver.name != "Repeated file/artifact context"]
    total = max((driver.impact_tokens for driver in actionable_drivers), default=1)
    return [
        (
            f"{index}. {driver.name}",
            driver.impact_tokens,
            total,
            (
                "Context drift was observed, but this is not ranked as a primary driver while attribution quality is low."
                if view.case_file.attribution_quality.level == "low" and driver.name == "Session drift"
                else clean_driver_evidence(driver)
            ),
        )
        for index, driver in enumerate(actionable_drivers[:limit], start=1)
    ]


def report_process_rows(view: SessionReportView) -> list[tuple[str, str]]:
    rows = [("Shape", view.case_file.process_summary.shape), ("Narrative", view.case_file.process_summary.narrative)]
    for phase in view.case_file.process_summary.phases:
        if phase.tokens <= 0 and phase.events <= 0:
            continue
        evidence = "; ".join(phase.evidence[:2])
        detail = f"{compact_number(phase.tokens)} tokens ({phase.share:.0%}), {phase.events} event(s)"
        if evidence:
            detail += f" - {evidence}"
        rows.append((phase.name.replace("_", " ").title(), detail))
    return rows


def report_risk_rows(view: SessionReportView) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for risk in view.case_file.risk_signals:
        evidence = "; ".join(short_preview(item, 140) for item in risk.evidence[:3])
        detail = f"{risk.severity} - {risk.why}"
        if evidence:
            detail += f" Evidence: {evidence}"
        rows.append((risk.name, detail))
    return rows


def report_attribution_quality_rows(view: SessionReportView) -> list[tuple[str, str]]:
    quality = view.case_file.attribution_quality
    value = view.case_file.value_evidence
    signals = "; ".join(value.signals[:3]) or "No strong efficiency warning signal detected."
    return [
        ("Attribution Quality", f"{quality.level} - {quality.reason}"),
        ("Unclassified Process Share", f"{quality.unclassified_share:.0%}"),
        ("Assistant/Other Token Share", f"{quality.assistant_or_other_share:.0%}"),
        ("Efficiency Evidence", f"{value.level} - {value.why}"),
        ("Value Signals", signals),
    ]


def report_billing_rows(view: SessionReportView) -> list[tuple[str, str]]:
    rows = [
        ("model usage tokens", compact_number(view.scope.model_billed_tokens) if view.scope.model_billed_tokens else "not reported"),
        ("cached input tokens", compact_number(view.scope.cache_tokens)),
        ("model output tokens", compact_number(view.scope.model_output_tokens)),
        ("visible transcript tokens", compact_number(view.scope.observable_tokens)),
    ]
    cache_driver = next((driver for driver in view.drivers if driver.name == "Cache-heavy context"), None)
    if cache_driver is not None:
        rows.append(("billing signal", f"{cache_driver.name}: {cache_driver.summary}"))
    if view.scope.estimated_cost_usd is not None and view.scope.estimated_cost_usd > 0:
        rows.append(("estimated cost", money(view.scope.estimated_cost_usd)))
    else:
        rows.append(("estimated cost", "not estimated"))
    return rows


def report_attribution_rows(view: SessionReportView) -> list[tuple[str, str]]:
    return [
        ("visible transcript tokens", compact_number(view.scope.observable_tokens)),
        ("classifiable tokens", compact_number(view.scope.classifiable_tokens)),
        ("actionable diagnostic tokens", compact_number(view.scope.actionable_diagnostic_tokens)),
        ("billing/cache tokens", compact_number(view.scope.cache_tokens)),
        ("model output tokens", compact_number(view.scope.model_output_tokens)),
        (
            "driver match coverage",
            f"{compact_number(view.scope.diagnostic_coverage_tokens)} classifiable tokens matched one or more diagnostic categories",
        ),
        ("scope note", "Driver match coverage is not waste. Categories can overlap and this is not a billing total."),
    ]


def display_file_ref(file_ref: str, cwd: str) -> str:
    if cwd and file_ref.startswith(cwd.rstrip("/") + "/"):
        return redact_text(file_ref[len(cwd.rstrip("/")) + 1 :], cwd)
    return redact_text(file_ref, cwd)


def is_project_source_file(file_ref: str, cwd: str) -> bool:
    lower = file_ref.lower()
    if not lower.endswith(SOURCE_FILE_SUFFIXES):
        return False
    if cwd and file_ref.startswith(cwd.rstrip("/") + "/"):
        return True
    return not file_ref.startswith("/")


def report_file_carryover_rows(view: SessionReportView, limit: int = 8) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for item in view.case_file.file_carryovers[:limit]:
        detail = f"{item.appearances}x, {compact_number(item.tokens)} estimated tokens, {compact_number(item.repeated_tokens)} after first appearance"
        if item.first_event_index > 0 and item.last_event_index > 0:
            detail += f", events {item.first_event_index}-{item.last_event_index}"
        if item.categories:
            detail += f", {', '.join(item.categories)}"
        rows.append((display_file_ref(item.file_ref, view.trace.cwd), detail))
    return rows


def report_project_source_carryover_rows(view: SessionReportView, limit: int = 8) -> list[tuple[str, str]]:
    items = [
        item
        for item in view.case_file.file_carryovers
        if item.appearances >= 2 and is_project_source_file(item.file_ref, view.trace.cwd)
    ]
    items.sort(key=lambda item: (item.appearances, item.repeated_tokens, item.tokens), reverse=True)
    rows: list[tuple[str, str]] = []
    for item in items[:limit]:
        detail = f"{item.appearances}x, {compact_number(item.repeated_tokens)} after first appearance"
        if item.first_event_index > 0 and item.last_event_index > 0:
            detail += f", events {item.first_event_index}-{item.last_event_index}"
        rows.append((display_file_ref(item.file_ref, view.trace.cwd), detail))
    return rows


def report_drift_timeline_rows(view: SessionReportView) -> list[tuple[str, str]]:
    return [
        (
            f"{item.label} @ {item.event_index}",
            f"{compact_number(item.tokens)} tokens - {item.detail}",
        )
        for item in view.case_file.drift_timeline
    ]
