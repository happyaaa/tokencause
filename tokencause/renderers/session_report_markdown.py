"""Markdown rendering for session diagnosis reports."""

from __future__ import annotations

from tokencause.core.formatting import compact_number, money
from tokencause.core.tokens import short_preview
from tokencause.renderers.session_report_models import SessionReportView
from tokencause.renderers.session_report_rows import (
    clean_driver_evidence,
    observable_source_group_rows,
    report_drift_timeline_rows,
    report_file_carryover_rows,
    report_project_source_carryover_rows,
)


def render_session_report_markdown(view: SessionReportView) -> str:
    likely_cause = view.case_file.likely_causes[0] if view.case_file.likely_causes else None
    lines = [f"# {view.heading}", ""]
    for label, value in view.session_rows:
        lines.append(f"- **{label}:** `{value}`" if "/" in value or value.startswith(".") else f"- **{label}:** {value}")
    if view.scope.estimated_cost_usd is not None:
        lines.append(f"- **estimated cost:** {money(view.scope.estimated_cost_usd)}")

    lines.extend(["", "## Likely Cause"])
    if likely_cause:
        lines.append(f"**{likely_cause.name}** ({likely_cause.confidence} confidence)")
        lines.extend(["", likely_cause.why])
    else:
        lines.append("No likely cause identified.")

    lines.extend(["", "## Evidence"])
    actionable_evidence = [item for item in view.case_file.evidence if item.supports != "Billing/cache signal"]
    if actionable_evidence:
        for item in actionable_evidence:
            lines.append(f"- **{item.name}:** {item.value}")
            if item.detail:
                lines.append(f"  - {short_preview(item.detail, 180)}")
    else:
        fallback_drivers = [driver for driver in view.drivers if driver.name != "Cache-heavy context"][:4]
        if fallback_drivers:
            for driver in fallback_drivers:
                lines.append(f"- **{driver.name}:** {clean_driver_evidence(driver)}")
        else:
            lines.append("- No high-signal evidence detected.")

    lines.extend(["", "## Attribution Quality"])
    quality = view.case_file.attribution_quality
    lines.append(f"- **{quality.level}:** {quality.reason}")
    lines.append(f"- **Unclassified process share:** {quality.unclassified_share:.0%}")
    lines.append(f"- **Assistant/other token share:** {quality.assistant_or_other_share:.0%}")

    lines.extend(["", "## Value Evidence"])
    value = view.case_file.value_evidence
    lines.append(f"- **{value.level}:** {value.why}")
    for signal in value.signals:
        lines.append(f"  - {signal}")

    lines.extend(["", "## Next Run Plan"])
    if view.case_file.next_run_plan:
        lines.extend(f"{index}. {item}" for index, item in enumerate(view.case_file.next_run_plan, start=1))
    else:
        lines.append("1. Inspect the largest commands, files, and repeated context.")

    lines.extend(["", "## Engineering Process"])
    lines.append(f"**{view.case_file.process_summary.shape}**")
    lines.extend(["", view.case_file.process_summary.narrative])
    for phase in view.case_file.process_summary.phases:
        if phase.tokens <= 0 and phase.events <= 0:
            continue
        lines.append(
            f"- **{phase.name.replace('_', ' ').title()}:** {compact_number(phase.tokens)} tokens "
            f"({phase.share:.0%}), {phase.events} event(s)"
        )
        for item in phase.evidence[:2]:
            lines.append(f"  - {item}")

    lines.extend(["", "## Risk Signals"])
    if view.case_file.risk_signals:
        for risk in view.case_file.risk_signals:
            lines.append(f"- **{risk.name} ({risk.severity}):** {risk.why}")
            for item in risk.evidence[:3]:
                lines.append(f"  - {short_preview(item, 180)}")
    else:
        lines.append("- No high-signal risk detected.")

    lines.extend(["", "## Diagnostic Trace", view.case_file.cause_sentence])

    project_source_rows = report_project_source_carryover_rows(view)
    lines.extend(["", "## Project Source Carryover"])
    if project_source_rows:
        for file_ref, detail in project_source_rows:
            lines.append(f"- **{file_ref}:** {detail}")
    else:
        lines.append("- No repeated project source files detected.")

    file_carryover_rows = report_file_carryover_rows(view)
    lines.extend(["", "## All File / Artifact Carryover"])
    if file_carryover_rows:
        for file_ref, detail in file_carryover_rows:
            lines.append(f"- **{file_ref}:** {detail}")
    else:
        lines.append("- No repeated file/artifact carryover detected.")

    drift_rows = report_drift_timeline_rows(view)
    lines.extend(["", "## Drift Timeline"])
    if drift_rows:
        for label, detail in drift_rows:
            lines.append(f"- **{label}:** {detail}")
    else:
        lines.append("- No clear context drift timeline detected.")

    lines.extend(["", "## Reusable Workflow Lessons"])
    if view.case_file.workflow_lessons:
        for lesson in view.case_file.workflow_lessons:
            lines.append(f"- **{lesson.title}:** {lesson.lesson}")
            lines.append(f"  - Trigger: {lesson.trigger}")
    else:
        lines.append("- No reusable workflow lesson detected.")

    lines.extend(
        [
            "",
            "## Token Attribution",
            f"- **Actionable observable tokens:** {compact_number(view.scope.observable_tokens)}",
            f"- **Billing/cache tokens:** {compact_number(view.scope.cache_tokens)}",
            f"- **Model output tokens:** {compact_number(view.scope.model_output_tokens)}",
            f"- **Driver match coverage:** {compact_number(view.scope.diagnostic_coverage_tokens)} observable tokens matched one or more diagnostic categories",
            "",
            "## Billing / Accounting",
            f"- **Model billed tokens:** {compact_number(view.scope.model_billed_tokens)}",
            f"- **Cached input tokens:** {compact_number(view.scope.cache_tokens)}",
            f"- **Observable transcript tokens:** {compact_number(view.scope.observable_tokens)}",
            "- Driver match coverage is not waste. Categories can overlap and this is not a billing total.",
        ]
    )

    actionable_drivers = [driver for driver in view.drivers if driver.name != "Cache-heavy context"]
    lines.extend(["", "## Actionable Drivers"])
    if actionable_drivers:
        total = view.trace.observable_tokens or 1
        for driver in actionable_drivers[:8]:
            lines.append(f"- **{driver.name}:** {compact_number(driver.impact_tokens)} tokens ({driver.impact_tokens / total:.0%})")
            lines.append(f"  - {clean_driver_evidence(driver)}")
    else:
        lines.append("- No high-signal actionable drivers detected.")

    lines.extend(["", "## Limits"])
    lines.extend(f"- {item}" for item in view.case_file.limits)

    if view.case_file.token_attribution:
        lines.extend(["", "## Appendix: Observable Token Sources"])
        for group, tokens, source_total, detail in observable_source_group_rows(view.category_tokens):
            lines.append(f"- **{group}:** {compact_number(tokens)} tokens ({tokens / (source_total or 1):.0%})")
            lines.append(f"  - {detail}")

        lines.extend(["", "## Appendix: Raw Token Categories"])
        for item in view.case_file.token_attribution[:8]:
            lines.append(f"- **{item.name}:** {compact_number(item.tokens)} tokens ({item.share:.0%})")
    return "\n".join(lines)
