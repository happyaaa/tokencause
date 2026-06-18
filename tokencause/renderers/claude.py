"""Claude Code session renderers."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
import html
import json

from tokencause.constants import __version__, JSON_OUTPUT_SCHEMA_VERSION
from tokencause.core.accounting import analyze
from tokencause.core.casefile import build_session_case_file, session_case_file_to_json
from tokencause.core.diagnosis import build_human_diagnosis, build_session_trace_cost_drivers
from tokencause.core.files import file_risk_reason
from tokencause.core.formatting import compact_number, money, top_items
from tokencause.core.models import Analysis, ClaudePriceConfig, ClaudeSession, CostDriver, TraceEvent
from tokencause.core.schema import trace_events_to_session_trace
from tokencause.core.tokens import short_preview
from tokencause.core.values import as_int
from tokencause.renderers.html import (
    HTML_BAR_CSS,
    HTML_FOOTER_CSS,
    html_bar_rows,
    html_footer,
    html_rows,
    html_table_rows,
    overview_recommendations,
)
from tokencause.renderers.json import (
    aggregate_session_trace_driver_tokens,
    cost_driver_to_json,
    counter_breakdown,
    human_diagnosis_to_json,
    session_trace_summary_to_json,
)
from tokencause.renderers.session_report import (
    SessionReportAppendix,
    SessionReportScope,
    SessionReportView,
    diagnostic_coverage_scope,
    render_session_report_html,
)

def render_claude_scan(sessions: list[ClaudeSession]) -> str:
    if not sessions:
        return "No Claude sessions found."
    lines = ["TokenCause Claude sessions", ""]
    for session in sessions:
        updated = datetime.fromtimestamp(session.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- {session.id[:8]}  {updated}  {session.messages} records  {session.project}")
    return "\n".join(lines)


def render_claude_scan_json(sessions: list[ClaudeSession]) -> str:
    return json.dumps(
        {
            "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
            "version": __version__,
            "kind": "claude_scan",
            "sessions": [
                {
                    "id": session.id,
                    "project": session.project,
                    "cwd": session.cwd,
                    "path": str(session.path),
                    "updated_at": session.updated_at,
                    "messages": session.messages,
                }
                for session in sessions
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


def claude_usage_tokens(event: TraceEvent, key: str) -> int:
    message = event.raw.get("message") if isinstance(event.raw.get("message"), dict) else {}
    usage = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return as_int(usage.get(key))


def claude_estimated_cost(events: list[TraceEvent], prices: ClaudePriceConfig | None) -> float | None:
    if prices is None or not prices.enabled:
        return None
    input_tokens = sum(event.input_tokens for event in events)
    output_tokens = sum(event.output_tokens for event in events)
    cache_write_tokens = sum(claude_usage_tokens(event, "cache_creation_input_tokens") for event in events)
    cache_read_tokens = sum(claude_usage_tokens(event, "cache_read_input_tokens") for event in events)
    uncached_input_tokens = max(input_tokens - cache_write_tokens - cache_read_tokens, 0)
    return (
        (uncached_input_tokens / 1_000_000) * prices.input_per_mtok
        + (cache_write_tokens / 1_000_000) * prices.cache_write_per_mtok
        + (cache_read_tokens / 1_000_000) * prices.cache_read_per_mtok
        + (output_tokens / 1_000_000) * prices.output_per_mtok
    )


def build_claude_cost_drivers(events: list[TraceEvent], analysis: Analysis) -> list[CostDriver]:
    session_id = events[0].run_id if events else "claude"
    trace = trace_events_to_session_trace(events, session_id=session_id, source="claude")
    return build_session_trace_cost_drivers(trace)


def build_claude_human_diagnosis(events: list[TraceEvent], session: ClaudeSession | None, drivers: list[CostDriver]):
    session_id = session.id if session else events[0].run_id if events else "claude"
    title = session.project if session else ""
    cwd = session.cwd if session else ""
    trace = trace_events_to_session_trace(events, session_id=session_id, source="claude", title=title, cwd=cwd)
    return build_human_diagnosis(trace, drivers)


def build_claude_session_trace(session: ClaudeSession, events: list[TraceEvent]):
    return trace_events_to_session_trace(
        events,
        session_id=session.id,
        source="claude",
        title=session.project,
        cwd=session.cwd,
    )


def render_claude_explain(session: ClaudeSession, events: list[TraceEvent], budget_usd: float | None) -> str:
    analysis = analyze(events, budget_usd)
    drivers = build_claude_cost_drivers(events, analysis)
    human_diagnosis = build_claude_human_diagnosis(events, session, drivers)
    total = analysis.total_tokens or 1
    lines = [
        "TokenCause Claude Diagnosis",
        f"session: {session.id}",
        f"project: {session.project}",
        f"path: {session.path}",
        f"events: {len(events)}",
        f"total tokens: {analysis.total_tokens}",
        f"total cost: {money(analysis.total_cost)}",
        "",
        "Human Diagnosis:",
    ]
    lines.extend(
        [
            f"- root cause: {human_diagnosis.root_cause}",
            f"- workflow failure: {human_diagnosis.workflow_failure}",
            f"- primary driver: {human_diagnosis.primary_driver}",
            f"- actionable driver: {human_diagnosis.actionable_driver}",
        ]
    )
    if human_diagnosis.billing_note:
        lines.append(f"- billing note: {human_diagnosis.billing_note}")

    lines.extend(["", "Evidence:"])
    if human_diagnosis.evidence:
        lines.extend(f"- {item}" for item in human_diagnosis.evidence)
    else:
        lines.append("- No high-signal evidence detected.")

    lines.extend(["", "What to do next:"])
    if human_diagnosis.next_actions:
        lines.extend(f"- {item}" for item in human_diagnosis.next_actions)
    else:
        lines.append("- Inspect the largest commands, files, and repeated context.")

    lines.extend(["", "Driver Summary:"])
    if drivers:
        for index, driver in enumerate(drivers[:4], start=1):
            lines.append(f"{index}. {driver.evidence}")
    else:
        lines.append("1. No obvious dominant Claude cost driver was detected from local JSONL.")

    lines.extend(["", "Cost drivers (estimated impact; categories can overlap):"])
    if drivers:
        for index, driver in enumerate(drivers[:6], start=1):
            lines.append(f"{index}. {driver.name} — {driver.impact_tokens / total:.0%}")
            lines.append(f"   {driver.evidence}")
    else:
        lines.append("- No obvious high-signal cost drivers detected.")

    lines.extend(["", "Recommendations:"])
    recommendations = claude_recommendations(analysis, drivers)
    if recommendations:
        lines.extend(f"- {item}" for item in recommendations[:5])
    else:
        lines.append("- No specific recommendation yet.")

    lines.extend(["", "Raw observability:", ""])
    lines.append("token breakdown:")
    by_tool: dict[str, int] = defaultdict(int)
    for event in events:
        by_tool[event.tool] += event.total_tokens
    for tool, tokens in sorted(by_tool.items(), key=lambda row: row[1], reverse=True):
        lines.append(f"- {tool}: {tokens} tokens ({tokens / total:.0%})")
    lines.append("")
    lines.append("top repeated files/artifacts:")
    for item, count in top_items(analysis.repeated_items, 5):
        lines.append(f"- {item}: repeated {count}x")
    if not analysis.repeated_items:
        lines.append("- none detected")
    return "\n".join(lines)


def claude_recommendations(analysis: Analysis, drivers: list[CostDriver]) -> list[str]:
    recommendations = [recommendation.detail for recommendation in analysis.recommendations[:4]]
    for driver in drivers[:4]:
        if driver.name == "Long tool output":
            recommendations.append("Truncate or summarize large Claude tool results before they dominate the session.")
        elif driver.name == "Cache-heavy context":
            recommendations.append("Compact or restart sessions when cache-read context grows without adding useful progress.")
        elif driver.name == "Repeated context":
            recommendations.append("Start a fresh session or compact repeated parent context into a short memo.")
        elif driver.name == "Repeated file/artifact context":
            recommendations.append("Summarize repeatedly referenced files or inspect narrower sections.")
        elif driver.name == "Error/test log noise":
            recommendations.append("Carry forward only the first failure summary, then rerun a narrower command.")
        elif driver.name == "Retry/failure loop":
            recommendations.append("Stop repeated failing reruns and change the diagnostic strategy.")
        elif driver.name == "Broad exploration":
            recommendations.append("Narrow the investigation before running more search or read commands.")
        elif driver.name == "Environment issue":
            recommendations.append("Fix setup blockers outside the long agent loop, then resume with a short error summary.")
    return list(dict.fromkeys(recommendations))


def render_claude_html_report(
    session: ClaudeSession,
    events: list[TraceEvent],
    budget_usd: float | None = None,
    prices: ClaudePriceConfig | None = None,
) -> str:
    return render_session_report_html(build_claude_session_report_view(session, events, budget_usd=budget_usd, prices=prices))


def claude_overview_evidence_summary(drivers: list[CostDriver], human_diagnosis: Any) -> str:
    metrics = human_diagnosis.evidence_metrics if isinstance(human_diagnosis.evidence_metrics, dict) else {}
    parts: list[str] = []
    if human_diagnosis.workflow_subtype:
        parts.append(str(human_diagnosis.workflow_subtype))
    if "file_refs" in metrics:
        parts.append(f"{metrics['file_refs']} file refs")
    if "largest_output_tokens" in metrics:
        parts.append(f"{compact_number(int(metrics['largest_output_tokens']))} largest output")
    if "repeated_artifact_count" in metrics:
        parts.append(f"{metrics['repeated_artifact_count']}x repeated artifact")
    if "retry_count" in metrics:
        parts.append(f"{metrics['retry_count']} retries")
    if len(parts) < 2:
        for driver in drivers:
            if driver.name == "Cache-heavy context":
                continue
            parts.append(driver.name)
            if len(parts) >= 2:
                break
    return "; ".join(parts[:4]) or human_diagnosis.workflow_pattern_label or "No high-signal evidence detected"


def build_claude_session_report_view(
    session: ClaudeSession,
    events: list[TraceEvent],
    budget_usd: float | None = None,
    prices: ClaudePriceConfig | None = None,
) -> SessionReportView:
    analysis = analyze(events, budget_usd)
    drivers = build_claude_cost_drivers(events, analysis)
    trace = build_claude_session_trace(session, events)
    case_file = build_session_case_file(trace, drivers)
    cache_creation_tokens = sum(claude_usage_tokens(event, "cache_creation_input_tokens") for event in events)
    cache_read_tokens = sum(claude_usage_tokens(event, "cache_read_input_tokens") for event in events)
    input_tokens = sum(event.input_tokens for event in events)
    output_tokens = sum(event.output_tokens for event in events)
    estimated_cost = claude_estimated_cost(events, prices)
    by_model: Counter[str] = Counter()
    by_file: Counter[str] = Counter()
    for event in events:
        by_model[event.model] += event.total_tokens
        for file_ref in event.context_items:
            by_file[file_ref] += event.total_tokens
    model_rows = [
        (model, f"{compact_number(tokens)} tokens")
        for model, tokens in by_model.most_common(10)
    ]
    repeated_rows = [
        (item, f"repeated {count}x")
        for item, count in top_items(analysis.repeated_items, 10)
    ]
    file_rows = [
        (
            file_ref,
            f"{tokens} tokens" + (f" — {file_risk_reason(file_ref)}" if file_risk_reason(file_ref) else ""),
        )
        for file_ref, tokens in by_file.most_common(10)
    ]
    updated = datetime.fromtimestamp(session.updated_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    category_tokens = Counter()
    for event in trace.events:
        category_tokens[event.category] += event.tokens
    scope = diagnostic_coverage_scope(trace, drivers, estimated_cost_usd=estimated_cost)
    # Claude exposes cache writes separately; keep the report's cache field aligned with billing impact.
    scope = SessionReportScope(
        model_billed_tokens=scope.model_billed_tokens,
        observable_tokens=scope.observable_tokens,
        cache_tokens=cache_creation_tokens + cache_read_tokens,
        model_output_tokens=scope.model_output_tokens,
        diagnostic_coverage_tokens=scope.diagnostic_coverage_tokens,
        diagnostic_coverage_share=scope.diagnostic_coverage_share,
        estimated_cost_usd=scope.estimated_cost_usd,
    )
    metric_cards = [
        ("Events", str(len(events))),
        ("Observable tokens", compact_number(trace.observable_tokens)),
        ("Input tokens", compact_number(input_tokens)),
        ("Estimated cost" if estimated_cost is not None else "Output tokens", money(estimated_cost) if estimated_cost is not None else compact_number(output_tokens)),
    ]
    return SessionReportView(
        heading="TokenCause Diagnosis",
        session_rows=[
            ("Session", session.id),
            ("Project", session.project),
            ("CWD", session.cwd),
            ("Updated", updated),
            ("Source", str(session.path)),
        ],
        metric_cards=metric_cards,
        case_file=case_file,
        trace=trace,
        drivers=drivers,
        scope=scope,
        category_tokens=category_tokens,
        appendix_sections=[
            SessionReportAppendix(
                "Claude Usage Counters",
                [
                    ("input tokens", compact_number(input_tokens)),
                    ("output tokens", compact_number(output_tokens)),
                    ("cache creation input tokens", compact_number(cache_creation_tokens)),
                    ("cache read input tokens", compact_number(cache_read_tokens)),
                    ("estimated cost", money(estimated_cost) if estimated_cost is not None else money(analysis.total_cost)),
                ],
            ),
            SessionReportAppendix("Token Breakdown By Model", model_rows),
            SessionReportAppendix("Top Files / Artifacts", file_rows),
            SessionReportAppendix("Top Repeated Files / Artifacts", repeated_rows),
        ],
    )


def render_claude_overview_html(
    reports: list[tuple[ClaudeSession, list[TraceEvent]]],
    report_links: dict[str, str] | None = None,
    prices: ClaudePriceConfig | None = None,
) -> str:
    analyzed = [
        (session, events, analyze(events, None))
        for session, events in reports
    ]
    analyzed.sort(key=lambda item: item[2].total_tokens, reverse=True)
    total_tokens = sum(analysis.total_tokens for _, _, analysis in analyzed)
    show_cost = prices is not None and prices.enabled
    total_estimated_cost = sum(claude_estimated_cost(events, prices) or 0.0 for _, events, _ in analyzed)
    driver_tokens: Counter[str] = Counter()
    tool_tokens: Counter[str] = Counter()
    rows: list[str] = []
    all_drivers: dict[str, list[CostDriver]] = {}
    for session, events, analysis in analyzed:
        drivers = build_claude_cost_drivers(events, analysis)
        all_drivers[session.id] = drivers
        for driver in drivers:
            driver_tokens[driver.name] += driver.impact_tokens
        for event in events:
            tool_tokens[event.tool] += event.total_tokens
    for session, events, analysis in analyzed[:20]:
        drivers = all_drivers.get(session.id, [])
        human_diagnosis = build_claude_human_diagnosis(events, session, drivers)
        top_driver = human_diagnosis.actionable_driver or (drivers[0].name if drivers else "None detected")
        evidence = claude_overview_evidence_summary(drivers, human_diagnosis)
        updated = datetime.fromtimestamp(session.updated_at, tz=timezone.utc).strftime("%Y-%m-%d")
        percent = analysis.total_tokens / (total_tokens or 1)
        estimated_cost = claude_estimated_cost(events, prices)
        session_label = session.id
        href = report_links.get(session.id) if report_links else None
        session_cell = (
            f'<a href="{html.escape(href)}">{html.escape(session_label)}</a>'
            if href
            else html.escape(session_label)
        )
        cells = [
            session_cell,
            html.escape(updated),
            html.escape(session.project),
            html.escape(f"{analysis.total_tokens} ({percent:.0%})"),
        ]
        if show_cost:
            cells.append(html.escape(money(estimated_cost or 0.0)))
        cells.extend(
            [
                html.escape(top_driver),
                html.escape(evidence),
            ]
        )
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    driver_rows = [
        (driver, tokens, total_tokens, f"{tokens} tokens")
        for driver, tokens in driver_tokens.most_common(10)
    ]
    tool_rows = [
        (tool, tokens, total_tokens, f"{tokens} tokens")
        for tool, tokens in tool_tokens.most_common(10)
    ]
    recommendations = overview_recommendations(driver_tokens)
    session_header = (
        "<tr><th>Session</th><th>Updated</th><th>Project</th><th>Tokens</th><th>Est. Cost</th><th>Top Driver</th><th>Evidence</th></tr>"
        if show_cost
        else "<tr><th>Session</th><th>Updated</th><th>Project</th><th>Tokens</th><th>Top Driver</th><th>Evidence</th></tr>"
    )
    session_rows = "\n".join(rows) or (
        "<tr><td>No sessions found.</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>"
        if show_cost
        else "<tr><td>No sessions found.</td><td></td><td></td><td></td><td></td><td></td></tr>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TokenCause Claude Overview</title>
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
  <h1>TokenCause Claude Overview</h1>
  <p class="muted">Recent Claude Code sessions, ranked by token volume and grouped by likely cost drivers.</p>

  <section class="grid">
    <div class="metric">Sessions analyzed<strong>{len(analyzed)}</strong></div>
    <div class="metric">Total tokens<strong>{total_tokens}</strong></div>
    <div class="metric">{'Estimated cost' if show_cost else 'Top driver'}<strong>{money(total_estimated_cost) if show_cost else html.escape(driver_tokens.most_common(1)[0][0]) if driver_tokens else "None detected"}</strong></div>
  </section>

  <h2>Sessions</h2>
  <table>
    {session_header}
    {session_rows}
  </table>

  <h2>Cost Drivers <span class="muted">(estimated impact; categories can overlap)</span></h2>
  <table>{html_bar_rows(driver_rows) if driver_rows else '<tr><td>None detected.</td><td></td></tr>'}</table>

  <h2>Recommendations</h2>
  <table>{html_table_rows([[item] for item in recommendations]) if recommendations else '<tr><td>No specific recommendation yet.</td></tr>'}</table>

  <h2>Token Breakdown By Tool</h2>
  <table>{html_bar_rows(tool_rows) if tool_rows else '<tr><td>None detected.</td><td></td></tr>'}</table>
  {html_footer()}
</main>
</body>
</html>
"""


def claude_explain_to_json_dict(
    session: ClaudeSession,
    events: list[TraceEvent],
    budget_usd: float | None,
) -> dict[str, Any]:
    analysis = analyze(events, budget_usd)
    drivers = build_claude_cost_drivers(events, analysis)
    trace = trace_events_to_session_trace(
        events,
        session_id=session.id,
        source="claude",
        title=session.project,
        cwd=session.cwd,
    )
    by_tool: dict[str, int] = defaultdict(int)
    for event in events:
        by_tool[event.tool] += event.total_tokens
    human_diagnosis = build_human_diagnosis(trace, drivers)
    case_file = build_session_case_file(trace, drivers)
    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "claude_session",
        "session": {
            "id": session.id,
            "project": session.project,
            "cwd": session.cwd,
            "path": str(session.path),
            "updated_at": session.updated_at,
            "messages": session.messages,
        },
        "summary": {
            "events": len(events),
            "total_tokens": analysis.total_tokens,
            "total_cost_usd": round(analysis.total_cost, 6),
            "estimated_savings_usd": round(analysis.estimated_savings_usd, 6),
            "budget_usd": budget_usd,
            "items": [driver.evidence for driver in drivers[:4]]
            or ["No obvious dominant Claude cost driver was detected from local JSONL."],
        },
        "human_diagnosis": human_diagnosis_to_json(human_diagnosis),
        "case_file": session_case_file_to_json(case_file),
        "cost_drivers": [cost_driver_to_json(driver, analysis.total_tokens) for driver in drivers],
        "canonical_trace": session_trace_summary_to_json(trace),
        "recommendations": claude_recommendations(analysis, drivers),
        "observability": {
            "tokens_by_tool": dict(top_items(by_tool)),
            "tokens_by_model": dict(top_items(analysis.tokens_by_model)),
            "repeated_context": dict(top_items(analysis.repeated_context)),
            "repeated_items": dict(top_items(analysis.repeated_items, 20)),
            "failures": [
                {
                    "index": event.index,
                    "step": event.step,
                    "model": event.model,
                    "tool": event.tool,
                    "status": event.status,
                    "error": event.error,
                }
                for event in analysis.failures[:20]
            ],
        },
    }


def render_claude_explain_json(session: ClaudeSession, events: list[TraceEvent], budget_usd: float | None) -> str:
    return json.dumps(claude_explain_to_json_dict(session, events, budget_usd), ensure_ascii=False, indent=2)


def claude_overview_to_json_dict(
    reports: list[tuple[ClaudeSession, list[TraceEvent]]],
    report_links: dict[str, str] | None = None,
    prices: ClaudePriceConfig | None = None,
) -> dict[str, Any]:
    analyzed = [(session, events, analyze(events, None)) for session, events in reports]
    analyzed.sort(key=lambda item: item[2].total_tokens, reverse=True)
    total_tokens = sum(analysis.total_tokens for _, _, analysis in analyzed)
    total_estimated_cost = sum(claude_estimated_cost(events, prices) or 0.0 for _, events, _ in analyzed)
    traces = [
        trace_events_to_session_trace(events, session_id=session.id, source="claude", title=session.project, cwd=session.cwd)
        for session, events, _ in analyzed
    ]
    driver_tokens = aggregate_session_trace_driver_tokens(traces)
    tool_tokens: Counter[str] = Counter()
    sessions: list[dict[str, Any]] = []
    all_drivers: dict[str, list[CostDriver]] = {}
    for session, events, analysis in analyzed:
        drivers = build_claude_cost_drivers(events, analysis)
        all_drivers[session.id] = drivers
        for event in events:
            tool_tokens[event.tool] += event.total_tokens
    for session, events, analysis in analyzed[:20]:
        drivers = all_drivers.get(session.id, [])
        human_diagnosis = build_claude_human_diagnosis(events, session, drivers)
        estimated_cost = claude_estimated_cost(events, prices)
        top_driver = drivers[0] if drivers else None
        sessions.append(
            {
                "id": session.id,
                "project": session.project,
                "cwd": session.cwd,
                "path": str(session.path),
                "updated_at": session.updated_at,
                "messages": session.messages,
                "total_tokens": analysis.total_tokens,
                "token_share": round(analysis.total_tokens / (total_tokens or 1), 6),
                "estimated_cost_usd": round(estimated_cost, 6) if estimated_cost is not None else None,
                "top_driver": top_driver.name if top_driver else "None detected",
                "actionable_driver": human_diagnosis.actionable_driver,
                "top_driver_evidence": top_driver.evidence if top_driver else "",
                "human_diagnosis": human_diagnosis_to_json(human_diagnosis),
                "report_link": report_links.get(session.id) if report_links else None,
            }
        )
    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "claude_overview",
        "adapter": "claude",
        "summary": {
            "sessions_analyzed": len(analyzed),
            "total_tokens": total_tokens,
            "estimated_cost_usd": round(total_estimated_cost, 6) if prices and prices.enabled else None,
        },
        "sessions": sessions,
        "cost_drivers": counter_breakdown(driver_tokens, total_tokens),
        "canonical_trace": {
            "schema": "SessionTrace",
            "sessions": [session_trace_summary_to_json(trace) for trace in traces[:20]],
        },
        "token_breakdown_by_tool": counter_breakdown(tool_tokens, total_tokens),
        "recommendations": overview_recommendations(driver_tokens),
    }


def render_claude_overview_json(
    reports: list[tuple[ClaudeSession, list[TraceEvent]]],
    report_links: dict[str, str] | None = None,
    prices: ClaudePriceConfig | None = None,
) -> str:
    return json.dumps(claude_overview_to_json_dict(reports, report_links=report_links, prices=prices), ensure_ascii=False, indent=2)
