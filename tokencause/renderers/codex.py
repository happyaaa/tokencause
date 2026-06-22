"""Codex session renderers."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import html
import json

from tokencause.constants import __version__, JSON_OUTPUT_SCHEMA_VERSION, JSON_TEXT_PREVIEW_LIMIT
from tokencause.core.casefile import build_session_case_file, session_case_file_to_json
from tokencause.core.diagnosis import build_human_diagnosis, build_session_trace_cost_drivers
from tokencause.core.files import artifact_kind, file_risk_reason
from tokencause.core.formatting import compact_number, money, top_items
from tokencause.core.models import CodexExplainReport, CodexPriceConfig, CostDriver
from tokencause.core.schema import codex_report_to_session_trace
from tokencause.core.tokens import short_preview
from tokencause.renderers.html import (
    HTML_BAR_CSS,
    HTML_FOOTER_CSS,
    html_bar_rows,
    html_footer,
    html_rows,
    html_table_rows,
    overview_recommendations,
)
from tokencause.renderers.session_report import (
    SessionReportAppendix,
    SessionReportView,
    diagnostic_coverage_scope,
    render_session_report_html,
    render_session_report_markdown,
)
from tokencause.renderers.json import (
    aggregate_session_trace_driver_tokens,
    cost_driver_to_json,
    counter_breakdown,
    human_diagnosis_to_json,
    session_trace_summary_to_json,
)
from tokencause.storage.cache import (
    broad_exploration_to_json,
    codex_content_event_to_json,
    codex_thread_to_json,
    session_drift_to_json,
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

def render_codex_scan(threads: list[CodexThread]) -> str:
    lines = ["TokenCause Codex sessions", ""]
    if not threads:
        return "No Codex sessions found."
    for thread in threads:
        updated = datetime.fromtimestamp(thread.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        title = short_preview(thread.title, 80)
        lines.append(f"- {thread.id[:8]}  {updated}  {thread.tokens_used} tokens  {title}")
    return "\n".join(lines)


def render_codex_scan_json(threads: list[CodexThread]) -> str:
    return json.dumps(
        {
            "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
            "version": __version__,
            "kind": "codex_scan",
            "sessions": [codex_thread_to_json(thread) for thread in threads],
        },
        ensure_ascii=False,
        indent=2,
    )


def codex_estimated_cost(report: CodexExplainReport, prices: CodexPriceConfig | None) -> float | None:
    if prices is None or not prices.enabled:
        return None
    cached_input = report.cached_input_tokens
    uncached_input = max(report.model_input_tokens - cached_input, 0)
    return (
        (uncached_input / 1_000_000) * prices.input_per_mtok
        + (cached_input / 1_000_000) * prices.cached_input_per_mtok
        + (report.model_output_tokens / 1_000_000) * prices.output_per_mtok
    )


def build_codex_cost_drivers(report: CodexExplainReport) -> list[CostDriver]:
    return build_session_trace_cost_drivers(codex_report_to_session_trace(report))


def build_codex_human_diagnosis(report: CodexExplainReport, drivers: list[CostDriver]):
    return build_human_diagnosis(codex_report_to_session_trace(report), drivers)


def build_codex_summary(report: CodexExplainReport, drivers: list[CostDriver]) -> list[str]:
    summary: list[str] = []
    for driver in drivers[:4]:
        if driver.name == "Long tool output":
            summary.append(driver.evidence.replace("Largest output was", "A large tool output was"))
        elif driver.name == "Repeated context":
            summary.append(driver.evidence)
        elif driver.name == "Repeated file/artifact context":
            summary.append(driver.evidence)
        elif driver.name == "Error/test log noise":
            summary.append(driver.summary)
        elif driver.name == "Expensive file context":
            summary.append(driver.evidence)
        elif driver.name == "Retry/failure loop":
            summary.append(driver.evidence)
        elif driver.name == "Session drift":
            summary.append(driver.summary)
        elif driver.name == "Environment issue":
            summary.append(driver.evidence)
        elif driver.name == "Broad exploration":
            summary.append(driver.evidence)
    if not summary:
        summary.append("No obvious dominant cost driver was detected from the observable transcript.")
    return summary


def codex_driver_next_action(driver_name: str) -> str:
    if driver_name == "Long tool output":
        return "Rerun with a scoped command, `tail -100`, or a short log summary before continuing."
    if driver_name == "Repeated context":
        return "Create a compact memo for repeated context and avoid reloading the same raw chunk."
    if driver_name == "Repeated file/artifact context":
        return "Inspect narrower file ranges and summarize stable artifacts once."
    if driver_name == "Error/test log noise":
        return "Summarize the first failure, then rerun a narrower test or command."
    if driver_name == "Expensive file context":
        return "Ignore or summarize generated, lockfile, fixture, schema, snapshot, and minified content."
    if driver_name == "Retry/failure loop":
        return "Stop the rerun loop, summarize the blocker, and choose a different diagnostic step."
    if driver_name == "Session drift":
        return "Start a fresh session from a checkpoint summary before continuing."
    if driver_name == "Environment issue":
        return "Fix the setup blocker outside the long agent loop, then resume with the exact error summary."
    if driver_name == "Broad exploration":
        return "Narrow the investigation to one subsystem or file range before running more search/read commands."
    if driver_name == "Cache-heavy context":
        return "Treat this as accounting signal, then inspect the actionable drivers that made the cached context large."
    return "Inspect this driver with its evidence and reduce the largest raw context source first."


def codex_driver_cause(driver: CostDriver) -> str:
    if driver.name == "Long tool output":
        return "Large command or tool output entered the session context."
    if driver.name == "Repeated context":
        return "The same context appeared repeatedly instead of being compacted."
    if driver.name == "Repeated file/artifact context":
        return "The same files or artifacts were loaded repeatedly across turns."
    if driver.name == "Error/test log noise":
        return "Error or test output dominated the observable transcript."
    if driver.name == "Expensive file context":
        return "Bulky low-signal files such as generated artifacts, lockfiles, fixtures, schemas, or snapshots entered context."
    if driver.name == "Retry/failure loop":
        return "A failing command or path was retried without enough strategy change."
    if driver.name == "Session drift":
        return "Later model calls became much larger than early calls."
    if driver.name == "Environment issue":
        return "Setup, dependency, permission, network, config, or version errors consumed the session instead of product work."
    if driver.name == "Broad exploration":
        return "The agent searched or read across too much of the workspace before narrowing the hypothesis."
    if driver.name == "Cache-heavy context":
        return "Cached input explains the billing shape, but the workflow cause is whatever made the retained context large."
    return driver.summary


def build_codex_root_cause_narrative(report: CodexExplainReport, drivers: list[CostDriver]) -> list[dict[str, Any]]:
    total = report.observable_tokens or 1
    actionable = [driver for driver in drivers if driver.name != "Cache-heavy context"]
    billing = [driver for driver in drivers if driver.name == "Cache-heavy context"]
    narrative_drivers = (actionable[:4] + billing[:1])[:5]
    return [
        {
            "driver": driver.name,
            "impact_tokens": driver.impact_tokens,
            "impact_share": round(driver.impact_tokens / total, 6),
            "cause": codex_driver_cause(driver),
            "evidence": driver.evidence,
            "next_action": codex_driver_next_action(driver.name),
        }
        for driver in narrative_drivers
    ]


def build_codex_token_attribution(report: CodexExplainReport, drivers: list[CostDriver]) -> dict[str, Any]:
    observable_tokens = report.observable_tokens
    estimated_waste_tokens = min(sum(driver.impact_tokens for driver in drivers), observable_tokens)
    return {
        "model_billed_tokens": report.model_total_tokens,
        "model_input_tokens": report.model_input_tokens,
        "model_output_tokens": report.model_output_tokens,
        "cache_tokens": report.cached_input_tokens,
        "observable_transcript_tokens": observable_tokens,
        "estimated_waste_tokens": estimated_waste_tokens,
        "estimated_waste_share_of_observable": round(estimated_waste_tokens / (observable_tokens or 1), 6),
        "scope_notes": {
            "model_billed_tokens": "Provider/model token counters when present in the local session.",
            "observable_transcript_tokens": "Tokens estimated from visible prompts, tool calls, and tool outputs in the transcript.",
            "estimated_waste_tokens": "Capped sum of overlapping TokenCause cost-driver impacts; driver match coverage signal, not waste or a billing total.",
            "cache_tokens": "Cached input tokens reported by the model counter when present.",
        },
    }


def render_codex_explain(report: CodexExplainReport, prices: CodexPriceConfig | None = None) -> str:
    drivers = build_codex_cost_drivers(report)
    trace = codex_report_to_session_trace(report)
    human_diagnosis = build_codex_human_diagnosis(report, drivers)
    case_file = build_session_case_file(trace, drivers)
    summary = build_codex_summary(report, drivers)
    narrative = build_codex_root_cause_narrative(report, drivers)
    attribution = build_codex_token_attribution(report, drivers)
    total = report.observable_tokens or 1
    estimated_cost = codex_estimated_cost(report, prices)
    lines = [
        "TokenCause Diagnosis",
        f"session: {report.thread.id}",
        f"title: {short_preview(report.thread.title, 120)}",
        f"cwd: {report.thread.cwd}",
        f"rollout: {report.thread.rollout_path}",
    ]
    if estimated_cost is not None:
        lines.append(f"estimated cost: {money(estimated_cost)}")
    lines.extend(["", "Case file:"])
    lines.append("Observed facts:")
    for fact in case_file.observed_facts[:5]:
        detail = f" — {fact.detail}" if fact.detail else ""
        lines.append(f"- {fact.name}: {fact.value}{detail}")

    lines.append("")
    lines.append("Likely cause:")
    if case_file.likely_causes:
        cause = case_file.likely_causes[0]
        lines.append(f"- {cause.name} ({cause.confidence} confidence)")
        lines.append(f"  {cause.why}")
    else:
        lines.append("- No likely cause identified.")

    lines.append("")
    lines.append("Evidence:")
    if case_file.evidence:
        for item in case_file.evidence[:6]:
            lines.append(f"- {item.name}: {item.value}")
            if item.detail:
                lines.append(f"  {short_preview(item.detail, 140)}")
    else:
        lines.append("- No high-signal evidence detected.")

    lines.append("")
    lines.append("Token attribution:")
    if case_file.token_attribution:
        for item in case_file.token_attribution[:5]:
            lines.append(f"- {item.name}: {item.tokens} tokens ({item.share:.0%})")
    else:
        lines.append("- No observable token attribution available.")

    lines.extend(["", "What to do next:"])
    if case_file.recommendations:
        lines.extend(f"- {item}" for item in case_file.recommendations[:4])
    else:
        lines.append("- Inspect the largest commands, files, and repeated context.")

    lines.extend(["", "Limits:"])
    lines.extend(f"- {item}" for item in case_file.limits[:4])

    lines.extend(["", "Driver Summary:"])
    for index, item in enumerate(summary, start=1):
        lines.append(f"{index}. {item}")

    lines.extend(["", "Root Cause Narrative:"])
    if narrative:
        for index, item in enumerate(narrative, start=1):
            lines.append(f"{index}. {item['driver']} — {item['cause']}")
            lines.append(f"   Evidence: {item['evidence']}")
            lines.append(f"   Next action: {item['next_action']}")
    else:
        lines.append("- No obvious high-signal root cause detected.")

    lines.extend(
        [
            "",
            "Token Attribution:",
            f"- model billed tokens: {attribution['model_billed_tokens']}",
            f"- observable transcript tokens: {attribution['observable_transcript_tokens']}",
            f"- cache tokens: {attribution['cache_tokens']}",
            f"- driver match tokens: {attribution['estimated_waste_tokens']}",
            "- note: driver match coverage means observable tokens matched one or more diagnostic categories; it is not waste or a billing total.",
        ]
    )

    lines.extend(["", "Cost drivers (estimated impact; categories can overlap):"])
    if drivers:
        for index, driver in enumerate(drivers[:6], start=1):
            lines.append(f"{index}. {driver.name} — {driver.impact_tokens / total:.0%}")
            lines.append(f"   {driver.evidence}")
    else:
        lines.append("- No obvious high-signal cost drivers detected.")

    lines.extend(["", "Recommendations:"])
    recommendations = codex_recommendations(report)
    if recommendations:
        lines.extend(f"- {item}" for item in recommendations)
    else:
        lines.append("- No specific recommendation yet.")

    lines.extend(
        [
            "",
            "Raw observability:",
            "",
            "usage counters:",
            f"- thread tokens_used: {report.thread.tokens_used}",
            f"- summed model total tokens: {report.model_total_tokens}",
            f"- summed model input tokens: {report.model_input_tokens}",
            f"- summed cached input tokens: {report.cached_input_tokens}",
            f"- summed model output tokens: {report.model_output_tokens}",
            f"- observable transcript tokens: {report.observable_tokens}",
        ]
    )
    if estimated_cost is not None:
        lines.append(f"- estimated cost: {money(estimated_cost)}")
    lines.extend(["", "token breakdown from observable transcript:"])
    for category, tokens in top_items(report.category_tokens, 8):
        lines.append(f"- {category}: {tokens} tokens ({tokens / total:.0%})")

    lines.extend(["", "top files/artifacts:"])
    for file_ref, tokens in top_items(report.file_tokens, 8):
        marker = " expensive-file" if any(hint in file_ref.lower() for hint in EXPENSIVE_FILE_HINTS) else ""
        lines.append(f"- {file_ref}: {tokens} tokens{marker}")
    if not report.file_tokens:
        lines.append("- none detected")

    lines.extend(["", "top repeated files/artifacts:"])
    for artifact in report.repeated_artifacts[:5]:
        categories = ", ".join(artifact.categories) or "unknown"
        lines.append(f"- {artifact.file_ref}: repeated {artifact.count}x, {artifact.tokens} tokens across {categories}")
    if not report.repeated_artifacts:
        lines.append("- none detected")

    lines.extend(["", "top commands:"])
    for command, tokens in top_items(report.command_tokens, 5):
        lines.append(f"- {short_preview(command, 120)}: {tokens} tokens")
    if not report.command_tokens:
        lines.append("- none detected")

    lines.extend(["", "top repeated chunks:"])
    for chunk in report.repeated_chunks[:5]:
        lines.append(
            f"- {chunk.category}: repeated {chunk.count}x, duplicate impact ~{chunk.duplicate_tokens} tokens, preview: {short_preview(chunk.preview, 100)}"
        )
    if not report.repeated_chunks:
        lines.append("- none detected")

    lines.extend(["", "top retry/failure loops:"])
    for loop in report.retry_loops[:5]:
        label = short_preview(loop.command or loop.preview, 120)
        lines.append(f"- repeated {loop.count}x, ~{loop.tokens} tokens: {label}")
    if not report.retry_loops:
        lines.append("- none detected")

    lines.extend(["", "diagnostic counters:"])
    if report.repeated_hashes:
        repeats = sum(count - 1 for count in report.repeated_hashes.values())
        lines.append(f"- repeated context: {len(report.repeated_hashes)} repeated chunks, {repeats} duplicate appearances")
    if report.repeated_artifacts:
        top = report.repeated_artifacts[0]
        lines.append(f"- repeated file/artifact context: {len(report.repeated_artifacts)} repeated files, top is {top.file_ref} ({top.count}x)")
    if report.long_tool_outputs:
        top = report.long_tool_outputs[0]
        lines.append(f"- long tool output: largest output is {top.tokens} tokens ({short_preview(top.command or top.preview, 120)})")
    if report.failure_events:
        lines.append(f"- retry/failure surface: {len(report.failure_events)} error-like outputs")
    if report.environment_issues:
        top = report.environment_issues[0]
        lines.append(f"- environment issue: {len(report.environment_issues)} issue type(s), top is {top.kind} ({top.count} output(s))")
    if report.broad_exploration is not None:
        lines.append(
            f"- broad exploration: {report.broad_exploration.search_commands} search command(s), "
            f"{report.broad_exploration.broad_commands} broad command(s), {report.broad_exploration.unique_files} file reference(s)"
        )
    if (
        not report.repeated_hashes
        and not report.repeated_artifacts
        and not report.long_tool_outputs
        and not report.failure_events
        and not report.environment_issues
        and report.broad_exploration is None
    ):
        lines.append("- no obvious high-signal cost drivers detected")

    return "\n".join(lines)


def render_codex_explain_markdown(report: CodexExplainReport, prices: CodexPriceConfig | None = None) -> str:
    return render_session_report_markdown(build_codex_session_report_view(report, prices))


def render_codex_html_report(report: CodexExplainReport, prices: CodexPriceConfig | None = None) -> str:
    return render_session_report_html(build_codex_session_report_view(report, prices))


def build_codex_session_report_view(
    report: CodexExplainReport,
    prices: CodexPriceConfig | None = None,
) -> SessionReportView:
    drivers = build_codex_cost_drivers(report)
    trace = codex_report_to_session_trace(report)
    case_file = build_session_case_file(trace, drivers)
    estimated_cost = codex_estimated_cost(report, prices)
    file_rows = [
        (
            file_ref,
            f"{tokens} tokens" + (f" — {file_risk_reason(file_ref)}" if file_risk_reason(file_ref) else ""),
        )
        for file_ref, tokens in top_items(report.file_tokens, 10)
    ]
    command_rows = [
        (short_preview(command, 160), f"{tokens} tokens")
        for command, tokens in top_items(report.command_tokens, 10)
    ]
    repeated_rows = [
        (
            f"{chunk.category} repeated {chunk.count}x",
            f"~{chunk.duplicate_tokens} duplicate tokens - {short_preview(chunk.preview, 140)}",
        )
        for chunk in report.repeated_chunks[:10]
    ]
    repeated_artifact_rows = [
        (
            f"{artifact.file_ref} repeated {artifact.count}x",
            f"{artifact.tokens} tokens, group: {artifact_kind(artifact.file_ref)}, across {', '.join(artifact.categories) or 'unknown'}",
        )
        for artifact in report.repeated_artifacts[:10]
    ]
    metric_cards = [
        ("Thread tokens", compact_number(report.thread.tokens_used)),
        ("Observable tokens", compact_number(report.observable_tokens)),
        ("Model total tokens", compact_number(report.model_total_tokens)),
        ("Estimated cost" if estimated_cost is not None else "Cached input tokens", money(estimated_cost) if estimated_cost is not None else compact_number(report.cached_input_tokens)),
    ]
    return SessionReportView(
        heading="TokenCause Diagnosis",
        session_rows=[
            ("Session", report.thread.id),
            ("Title", short_preview(report.thread.title, 160)),
            ("CWD", report.thread.cwd),
            ("Rollout", str(report.thread.rollout_path)),
        ],
        metric_cards=metric_cards,
        case_file=case_file,
        trace=trace,
        drivers=drivers,
        scope=diagnostic_coverage_scope(trace, drivers, estimated_cost_usd=estimated_cost),
        category_tokens=Counter(report.category_tokens),
        appendix_sections=[
            SessionReportAppendix("Top Files / Artifacts", file_rows),
            SessionReportAppendix("Top Repeated Files / Artifacts", repeated_artifact_rows),
            SessionReportAppendix("Top Commands", command_rows),
            SessionReportAppendix("Top Repeated Chunks", repeated_rows),
        ],
    )


def html_overview_session_rows(
    reports: list[CodexExplainReport],
    total_thread_tokens: int,
    report_links: dict[str, str] | None = None,
    prices: CodexPriceConfig | None = None,
) -> str:
    rows = []
    for report in reports[:20]:
        drivers = build_codex_cost_drivers(report)
        case_file = build_session_case_file(codex_report_to_session_trace(report), drivers)
        likely_cause = case_file.likely_causes[0] if case_file.likely_causes else None
        cause_label = (likely_cause.name if likely_cause else "") or "No likely cause"
        confidence = (likely_cause.confidence if likely_cause else "") or ""
        evidence = case_file_evidence_summary(case_file)
        updated = datetime.fromtimestamp(report.thread.updated_at, tz=timezone.utc).strftime("%Y-%m-%d")
        percent = report.thread.tokens_used / total_thread_tokens if total_thread_tokens else 0
        session_label = report.thread.id[:8]
        href = report_links.get(report.thread.id) if report_links else None
        session_cell = (
            f'<a href="{html.escape(href)}">{html.escape(session_label)}</a>'
            if href
            else html.escape(session_label)
        )
        cells = [
            session_cell,
            html.escape(updated),
            html.escape(f"{compact_number(report.thread.tokens_used)} ({percent:.0%})"),
        ]
        estimated_cost = codex_estimated_cost(report, prices)
        if estimated_cost is not None:
            cells.append(html.escape(money(estimated_cost)))
        cells.extend(
            [
                html.escape(short_preview(report.thread.title, 80)),
                html.escape(cause_label),
                html.escape(confidence),
                html.escape(short_preview(evidence, 120)),
            ]
        )
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    return "\n".join(rows)


def overview_session_evidence(metrics: dict[str, Any], drivers: list[CostDriver]) -> str:
    if "search_commands" in metrics and "file_refs" in metrics:
        return f"{metrics['search_commands']} searches, {metrics['file_refs']} file refs"
    if "largest_output_tokens" in metrics:
        return f"largest output ~{compact_number(int(metrics['largest_output_tokens']))} tokens"
    if "repeated_artifact" in metrics:
        artifact = short_preview(str(metrics["repeated_artifact"]), 70)
        count = metrics.get("repeated_artifact_count", "?")
        return f"`{artifact}` repeated {count}x"
    if "retry_count" in metrics:
        return f"{metrics['retry_count']}x retry loop"
    if "drift_ratio" in metrics:
        return f"late turns grew {metrics['drift_ratio']}x larger"
    if drivers:
        return short_preview(drivers[0].evidence, 90)
    return "No high-signal evidence detected."


def case_file_evidence_summary(case_file) -> str:
    parts = [f"{item.name}: {item.value}" for item in case_file.evidence if item.supports != "Billing/cache signal"]
    return "; ".join(parts[:3]) if parts else "No high-signal evidence detected."


def render_codex_overview_html(
    reports: list[CodexExplainReport],
    report_links: dict[str, str] | None = None,
    prices: CodexPriceConfig | None = None,
) -> str:
    reports = sorted(reports, key=lambda report: report.thread.tokens_used, reverse=True)
    total_thread_tokens = sum(report.thread.tokens_used for report in reports)
    total_observable_tokens = sum(report.observable_tokens for report in reports)
    show_cost = prices is not None and prices.enabled
    total_estimated_cost = sum(codex_estimated_cost(report, prices) or 0.0 for report in reports)
    category_tokens: Counter[str] = Counter()
    driver_tokens: Counter[str] = Counter()
    for report in reports:
        category_tokens.update(report.category_tokens)
        for driver in build_codex_cost_drivers(report):
            driver_tokens[driver.name] += driver.impact_tokens

    category_rows = [
        (category, tokens, total_observable_tokens, f"{compact_number(tokens)} tokens")
        for category, tokens in category_tokens.most_common(10)
    ]
    billing_rows = [
        (driver, f"{compact_number(tokens)} tokens")
        for driver, tokens in driver_tokens.most_common()
        if driver == "Cache-heavy context"
    ]
    actionable_driver_rows = [
        (driver, tokens, total_observable_tokens, f"{compact_number(tokens)} tokens")
        for driver, tokens in driver_tokens.most_common(10)
        if driver != "Cache-heavy context" and tokens / (total_observable_tokens or 1) >= 0.01
    ]
    if reports:
        top_drivers = build_codex_cost_drivers(reports[0])
        top_diagnosis = build_codex_human_diagnosis(reports[0], top_drivers)
        recommendations = [
            item
            for item in list(dict.fromkeys(top_diagnosis.next_actions + overview_recommendations(driver_tokens)))
            if "cache-read context dominates" not in item
        ][:3]
    else:
        recommendations = overview_recommendations(driver_tokens)

    session_header = (
        "<tr><th>Session</th><th>Updated</th><th>Tokens</th><th>Est. Cost</th><th>Title</th><th>Likely Cause</th><th>Confidence</th><th>Evidence</th></tr>"
        if show_cost
        else "<tr><th>Session</th><th>Updated</th><th>Tokens</th><th>Title</th><th>Likely Cause</th><th>Confidence</th><th>Evidence</th></tr>"
    )
    empty_session_row = (
        "<tr><td>No sessions found.</td><td></td><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
        if show_cost
        else "<tr><td>No sessions found.</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TokenCause Codex Overview</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2937; background: #f8fafc; }}
    main {{ max-width: 1240px; margin: 0 auto; }}
    h1, h2 {{ margin: 0 0 12px; }}
    h1 {{ font-size: 28px; }}
    h2 {{ font-size: 18px; margin-top: 28px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .metric {{ background: white; border: 1px solid #e5e7eb; border-radius: 8px; padding: 14px; }}
    .metric strong {{ display: block; font-size: 22px; margin-top: 6px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; border: 1px solid #e5e7eb; border-radius: 8px; overflow: hidden; }}
    td, th {{ border-bottom: 1px solid #e5e7eb; padding: 10px; vertical-align: top; text-align: left; }}
    th {{ background: #f1f5f9; font-size: 13px; color: #475569; }}
    tr:last-child td {{ border-bottom: 0; }}
    .muted {{ color: #6b7280; }}
{HTML_BAR_CSS}
{HTML_FOOTER_CSS}
  </style>
</head>
<body>
<main>
  <h1>TokenCause Overview</h1>
  <p class="muted">Recent Codex sessions, ranked by token cost and grouped by likely cost drivers.</p>

  <section class="grid">
    <div class="metric">Sessions analyzed<strong>{len(reports)}</strong></div>
    <div class="metric">Actionable tokens<strong>{compact_number(total_observable_tokens)}</strong></div>
    <div class="metric">Billing/cache tokens<strong>{compact_number(total_thread_tokens)}</strong></div>
    <div class="metric">{'Estimated cost' if show_cost else 'Top session share'}<strong>{money(total_estimated_cost) if show_cost else f'{(reports[0].thread.tokens_used / total_thread_tokens if reports and total_thread_tokens else 0):.0%}'}</strong></div>
  </section>

  <h2>Most Expensive Sessions</h2>
  <table>
    {session_header}
    {html_overview_session_rows(reports, total_thread_tokens, report_links, prices) if reports else empty_session_row}
  </table>

  <h2>Billing Signals</h2>
  <table>
    {html_rows(billing_rows) if billing_rows else '<tr><td>None detected.</td><td></td></tr>'}
  </table>

  <h2>Actionable Workflow Drivers <span class="muted">(diagnostic impact; categories can overlap)</span></h2>
  <table>
    {html_bar_rows(actionable_driver_rows) if actionable_driver_rows else '<tr><td>None detected.</td><td></td></tr>'}
  </table>

  <h2>Recommendations</h2>
  <table>
    {html_table_rows([[item] for item in recommendations]) if recommendations else '<tr><td>No specific recommendation yet.</td></tr>'}
  </table>

  <h2>Observable Token Breakdown</h2>
  <table>
    {html_bar_rows(category_rows) if category_rows else '<tr><td>None detected.</td><td></td></tr>'}
  </table>
  {html_footer()}
</main>
</body>
</html>
"""


def codex_recommendations(report: CodexExplainReport) -> list[str]:
    recommendations: list[str] = []
    if report.repeated_chunks:
        top = report.repeated_chunks[0]
        recommendations.append(
            f"Compact or split sessions when repeated context accumulates; top duplicate chunk is ~{top.duplicate_tokens} tokens across {top.count} repeats."
        )
    if report.repeated_artifacts:
        top = report.repeated_artifacts[0]
        recommendations.append(
            f"Summarize or narrow repeated file context; `{top.file_ref}` appeared {top.count}x and contributed about {top.tokens} observable tokens."
        )
    if report.long_tool_outputs:
        top = report.long_tool_outputs[0]
        evidence = short_preview(top.command or top.preview, 100)
        recommendations.append(
            f"Truncate long command/test output; `{evidence}` contributed about {top.tokens} observable tokens."
        )
    top_file_names = [name for name, _tokens in top_items(report.file_tokens, 8)]
    expensive_files = [name for name in top_file_names if any(hint in name.lower() for hint in EXPENSIVE_FILE_HINTS)]
    if expensive_files:
        examples = ", ".join(
            f"{name} ({file_risk_reason(name)})" if file_risk_reason(name) else name
            for name in expensive_files[:3]
        )
        recommendations.append(f"Ignore or summarize expensive files seen in this session: {examples}.")
    if report.retry_loops:
        top = report.retry_loops[0]
        label = short_preview(top.command or top.preview, 100)
        recommendations.append(
            f"Stop repeated failure loops before rerunning; `{label}` repeated {top.count}x."
        )
    elif report.failure_events:
        recommendations.append(
            f"Deduplicate repeated failures; this session has {len(report.failure_events)} error-like outputs."
        )
    if report.session_drift is not None:
        recommendations.append(
            f"Consider compacting or starting a new session after drift; late calls are {report.session_drift.ratio:.1f}x larger than early calls."
        )
    if report.environment_issues:
        top = report.environment_issues[0]
        label = short_preview(top.command or top.preview, 100)
        recommendations.append(
            f"Resolve the {top.kind} environment blocker outside the agent loop; `{label}` matched {top.count} setup/error output(s)."
        )
    if report.broad_exploration is not None:
        examples = ", ".join(f"`{example}`" for example in report.broad_exploration.examples[:2])
        recommendations.append(
            f"Narrow broad exploration before continuing; {report.broad_exploration.search_commands} search command(s) produced about {report.broad_exploration.search_tokens} observable tokens"
            + (f" ({examples})." if examples else ".")
        )
    return recommendations[:5]


def codex_explain_to_json_dict(report: CodexExplainReport, prices: CodexPriceConfig | None = None) -> dict[str, Any]:
    drivers = build_codex_cost_drivers(report)
    trace = codex_report_to_session_trace(report)
    human_diagnosis = build_human_diagnosis(trace, drivers)
    case_file = build_session_case_file(trace, drivers)
    summary = build_codex_summary(report, drivers)
    narrative = build_codex_root_cause_narrative(report, drivers)
    attribution = build_codex_token_attribution(report, drivers)
    estimated_cost = codex_estimated_cost(report, prices)
    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "codex_session",
        "session": codex_thread_to_json(report.thread),
        "summary": {
            "items": summary,
            "thread_tokens_used": report.thread.tokens_used,
            "model_total_tokens": report.model_total_tokens,
            "model_input_tokens": report.model_input_tokens,
            "cached_input_tokens": report.cached_input_tokens,
            "model_output_tokens": report.model_output_tokens,
            "observable_tokens": report.observable_tokens,
            "estimated_cost_usd": round(estimated_cost, 6) if estimated_cost is not None else None,
        },
        "human_diagnosis": human_diagnosis_to_json(human_diagnosis),
        "case_file": session_case_file_to_json(case_file),
        "cost_drivers": [cost_driver_to_json(driver, report.observable_tokens) for driver in drivers],
        "canonical_trace": session_trace_summary_to_json(trace),
        "root_cause_narrative": narrative,
        "token_attribution": attribution,
        "recommendations": codex_recommendations(report),
        "observability": {
            "token_breakdown": dict(top_items(report.category_tokens)),
            "top_files": dict(top_items(report.file_tokens, 20)),
            "top_commands": dict(top_items(report.command_tokens, 20)),
            "repeated_chunks": [chunk.__dict__ for chunk in report.repeated_chunks[:20]],
            "repeated_artifacts": [
                {
                    "file_ref": artifact.file_ref,
                    "count": artifact.count,
                    "tokens": artifact.tokens,
                    "categories": list(artifact.categories),
                }
                for artifact in report.repeated_artifacts[:20]
            ],
            "long_tool_outputs": [codex_content_event_to_json(event) for event in report.long_tool_outputs[:20]],
            "failure_events": [codex_content_event_to_json(event) for event in report.failure_events[:20]],
            "retry_loops": [loop.__dict__ for loop in report.retry_loops[:20]],
            "session_drift": session_drift_to_json(report.session_drift),
            "environment_issues": [issue.__dict__ for issue in report.environment_issues[:20]],
            "broad_exploration": broad_exploration_to_json(report.broad_exploration),
        },
    }


def render_codex_explain_json(report: CodexExplainReport, prices: CodexPriceConfig | None = None) -> str:
    return json.dumps(codex_explain_to_json_dict(report, prices), ensure_ascii=False, indent=2)


def codex_overview_to_json_dict(
    reports: list[CodexExplainReport],
    report_links: dict[str, str] | None = None,
    prices: CodexPriceConfig | None = None,
) -> dict[str, Any]:
    sorted_reports = sorted(reports, key=lambda report: report.thread.tokens_used, reverse=True)
    total_thread_tokens = sum(report.thread.tokens_used for report in sorted_reports)
    total_observable_tokens = sum(report.observable_tokens for report in sorted_reports)
    total_estimated_cost = sum(codex_estimated_cost(report, prices) or 0.0 for report in sorted_reports)
    traces = [codex_report_to_session_trace(report) for report in sorted_reports]
    category_tokens: Counter[str] = Counter()
    driver_tokens = aggregate_session_trace_driver_tokens(traces)
    billing_tokens: Counter[str] = Counter(
        {driver: tokens for driver, tokens in driver_tokens.items() if driver == "Cache-heavy context"}
    )
    actionable_driver_tokens: Counter[str] = Counter(
        {driver: tokens for driver, tokens in driver_tokens.items() if driver != "Cache-heavy context"}
    )
    sessions: list[dict[str, Any]] = []
    all_drivers: dict[str, list[CostDriver]] = {}
    for report in sorted_reports:
        drivers = build_codex_cost_drivers(report)
        all_drivers[report.thread.id] = drivers
        category_tokens.update(report.category_tokens)
    for report in sorted_reports[:20]:
        drivers = all_drivers.get(report.thread.id, [])
        human_diagnosis = build_codex_human_diagnosis(report, drivers)
        case_file = build_session_case_file(codex_report_to_session_trace(report), drivers)
        estimated_cost = codex_estimated_cost(report, prices)
        top_driver = drivers[0] if drivers else None
        sessions.append(
            {
                "id": report.thread.id,
                "title": short_preview(report.thread.title, JSON_TEXT_PREVIEW_LIMIT),
                "cwd": report.thread.cwd,
                "updated_at": report.thread.updated_at,
                "tokens_used": report.thread.tokens_used,
                "token_share": round(report.thread.tokens_used / (total_thread_tokens or 1), 6),
                "observable_tokens": report.observable_tokens,
                "model_total_tokens": report.model_total_tokens,
                "estimated_cost_usd": round(estimated_cost, 6) if estimated_cost is not None else None,
                "top_driver": top_driver.name if top_driver else "None detected",
                "actionable_driver": human_diagnosis.actionable_driver,
                "workflow_pattern_label": human_diagnosis.workflow_pattern_label,
                "workflow_subtype": human_diagnosis.workflow_subtype,
                "evidence_metrics": human_diagnosis.evidence_metrics,
                "top_driver_evidence": top_driver.evidence if top_driver else "",
                "human_diagnosis": human_diagnosis_to_json(human_diagnosis),
                "case_file": session_case_file_to_json(case_file),
                "report_link": report_links.get(report.thread.id) if report_links else None,
            }
        )
    if sorted_reports:
        top_drivers = all_drivers.get(sorted_reports[0].thread.id, [])
        top_diagnosis = build_codex_human_diagnosis(sorted_reports[0], top_drivers)
        recommendations = [
            item
            for item in list(dict.fromkeys(top_diagnosis.next_actions + overview_recommendations(driver_tokens)))
            if "cache-read context dominates" not in item
        ][:3]
    else:
        recommendations = overview_recommendations(driver_tokens)
    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "codex_overview",
        "adapter": "codex",
        "summary": {
            "sessions_analyzed": len(sorted_reports),
            "total_thread_tokens": total_thread_tokens,
            "total_observable_tokens": total_observable_tokens,
            "estimated_cost_usd": round(total_estimated_cost, 6) if prices and prices.enabled else None,
        },
        "sessions": sessions,
        "cost_drivers": counter_breakdown(driver_tokens, total_observable_tokens),
        "billing_signals": counter_breakdown(billing_tokens, total_thread_tokens),
        "actionable_workflow_drivers": [
            item
            for item in counter_breakdown(actionable_driver_tokens, total_observable_tokens)
            if item["share"] >= 0.01
        ],
        "canonical_trace": {
            "schema": "SessionTrace",
            "sessions": [session_trace_summary_to_json(trace) for trace in traces[:20]],
        },
        "token_breakdown": counter_breakdown(category_tokens, total_observable_tokens),
        "recommendations": recommendations,
    }


def render_codex_overview_json(
    reports: list[CodexExplainReport],
    report_links: dict[str, str] | None = None,
    prices: CodexPriceConfig | None = None,
) -> str:
    return json.dumps(codex_overview_to_json_dict(reports, report_links=report_links, prices=prices), ensure_ascii=False, indent=2)
