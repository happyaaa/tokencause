"""Dashboard summary and HTML rendering."""

from __future__ import annotations

import html
import re
from typing import Any

from tokencause.constants import __version__, JSON_OUTPUT_SCHEMA_VERSION
from tokencause.core.formatting import compact_number
from tokencause.core.tokens import short_preview


DASHBOARD_CSS = """
    body { background: #f6f7f9 !important; color: #1d2430 !important; }
    main { max-width: 1180px !important; }
    h1 { letter-spacing: -0.02em; margin-bottom: 6px !important; }
    h2 { letter-spacing: -0.01em; color: #1d2430; }
    table { border: 1px solid #dfe4ec !important; border-radius: 6px !important; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03); }
    th { background: #eef2f7 !important; color: #596579 !important; font-size: 12px !important; text-transform: uppercase; letter-spacing: 0; }
    td { color: #2d3748; }
    .bar-track { height: 6px !important; background: #e5eaf1 !important; }
    .bar-fill { background: #2563eb !important; }
    .bar-share { color: #1d4ed8 !important; }
    .dashboard-hero { background: #ffffff; border: 1px solid #d9e0ea; border-radius: 8px; padding: 18px; margin: 20px 0 16px; box-shadow: 0 12px 36px rgba(31, 41, 55, 0.06); }
    .dashboard-hero h2 { margin: 0; font-size: 22px; color: #121826; }
    .dashboard-hero .muted { color: #657386; }
    .dashboard-intro { display: grid; grid-template-columns: minmax(0, 1.4fr) minmax(280px, 0.8fr); gap: 18px; align-items: stretch; }
    .dashboard-diagnosis { border-left: 4px solid #2563eb; padding-left: 14px; }
    .dashboard-diagnosis-title { display: block; margin-top: 10px; font-size: 24px; line-height: 1.08; letter-spacing: -0.02em; font-weight: 750; color: #0f172a; }
    .dashboard-diagnosis-copy { margin: 10px 0 0; color: #435066; max-width: 78ch; }
    .dashboard-evidence { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 12px; }
    .dashboard-evidence span { background: #eef4ff; border: 1px solid #d6e4ff; border-radius: 999px; color: #1d4ed8; font-size: 12px; font-weight: 700; padding: 4px 8px; }
    .dashboard-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }
    .dashboard-card { background: #f8fafc; border: 1px solid #e3e8f0; border-radius: 6px; padding: 12px; }
    .dashboard-card a { color: #1d4ed8; }
    .dashboard-label { color: #6b778a; display: block; font-size: 11px; text-transform: uppercase; letter-spacing: 0; font-weight: 700; }
    .dashboard-value { display: block; font-size: 18px; font-weight: 760; margin-top: 6px; color: #131b2a; line-height: 1.12; }
    .dashboard-detail { color: #66758a; margin-top: 6px; font-size: 12px; line-height: 1.35; }
    .dashboard-action { background: #ffffff; color: #1f2937; border: 1px solid #d9e0ea; border-radius: 8px; padding: 16px 18px; margin: 0 0 24px; box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03); }
    .dashboard-action h2 { margin-top: 0; }
    .dashboard-action-grid { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 0.9fr); gap: 24px; }
    .dashboard-action ul { margin: 8px 0 0; padding-left: 18px; }
    .dashboard-action a { font-weight: 700; }
    @media (max-width: 820px) { .dashboard-intro, .dashboard-action-grid { grid-template-columns: 1fr; } .dashboard-grid { grid-template-columns: 1fr; } }
"""


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def dashboard_metric_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.0%}" if 0 <= value <= 1 else f"{value:.2f}"
    if isinstance(value, int):
        return compact_number(value)
    if value is None:
        return "n/a"
    return str(value)


def dashboard_root_cause_copy(diagnosis: dict[str, Any], workflow_label: str) -> str:
    billing_driver = str(diagnosis.get("billing_driver") or "")
    if workflow_label == "One long session mixed discovery, coding, and verification":
        sentences = [
            "The agent did broad discovery, then kept coding and verifying in the same context.",
            "Large search/test outputs and repeated artifacts stayed in the session.",
        ]
        if billing_driver:
            sentences.append("Cache-heavy billing amplified the effect.")
        return " ".join(sentences)
    why = str(diagnosis.get("why", ""))
    if billing_driver and "cache" not in why.lower():
        return f"{why} Cache-heavy billing amplified the effect."
    return why


def dashboard_evidence_chips(metrics: dict[str, Any]) -> str:
    chips: list[str] = []
    if "search_commands" in metrics:
        chips.append(f"{metrics['search_commands']} searches")
    if "file_refs" in metrics:
        chips.append(f"{metrics['file_refs']} file refs")
    if "largest_output_tokens" in metrics:
        chips.append(f"largest output {compact_number(_as_int(metrics['largest_output_tokens']))}")
    if "repeated_artifact_count" in metrics:
        chips.append(f"artifact repeated {metrics['repeated_artifact_count']}x")
    if "retry_count" in metrics:
        chips.append(f"{metrics['retry_count']} retries")
    if "drift_ratio" in metrics:
        chips.append(f"{metrics['drift_ratio']}x drift")
    if not chips:
        return ""
    return '<div class="dashboard-evidence">' + "".join(f"<span>{html.escape(str(chip))}</span>" for chip in chips[:4]) + "</div>"


def dashboard_case_evidence_chips(evidence: list[Any]) -> str:
    chips: list[str] = []
    for item in evidence:
        if not isinstance(item, dict) or item.get("supports") == "Billing/cache signal":
            continue
        name = str(item.get("name") or "")
        value = str(item.get("value") or "")
        if name and value:
            chips.append(f"{name}: {value}")
    if not chips:
        return ""
    return '<div class="dashboard-evidence">' + "".join(f"<span>{html.escape(chip)}</span>" for chip in chips[:5]) + "</div>"


def dashboard_subtype_html(subtype: str) -> str:
    if not subtype:
        return ""
    return f'<div class="dashboard-evidence"><span>{html.escape(subtype)}</span></div>'


def dashboard_top_session_label(session: dict[str, Any] | None) -> str:
    if not session:
        return "No session"
    title = session.get("title") or session.get("project") or session.get("id") or "Untitled session"
    return short_preview(str(title), 70)


def render_dashboard_summary_html(source_label: str, diagnosis: dict[str, Any], overview: dict[str, Any]) -> str:
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    top_session = diagnosis.get("top_session") if isinstance(diagnosis.get("top_session"), dict) else None
    sessions_analyzed = _as_int(diagnosis.get("sessions_analyzed"))
    actionable_tokens = summary.get("total_observable_tokens", summary.get("total_tokens"))
    billing_tokens = summary.get("total_thread_tokens", summary.get("total_tokens"))
    report_link = top_session.get("report_link") if top_session else None
    avoid_next_time = diagnosis.get("avoid_next_time") if isinstance(diagnosis.get("avoid_next_time"), list) else []
    avoid_items = "".join(f"<li>{html.escape(str(item))}</li>" for item in avoid_next_time[:3])
    workflow_lessons = diagnosis.get("workflow_lessons") if isinstance(diagnosis.get("workflow_lessons"), list) else []
    lesson_items = "".join(
        "<li>"
        f"<strong>{html.escape(str(item.get('title', 'Workflow lesson')))}:</strong> "
        f"{html.escape(str(item.get('lesson', '')))}"
        "</li>"
        for item in workflow_lessons[:2]
        if isinstance(item, dict)
    )
    billing_note = str(diagnosis.get("billing_note") or "")
    billing_note_html = f'<p class="muted">{html.escape(billing_note)}</p>' if billing_note else ""
    billing_driver = str(diagnosis.get("billing_driver") or "")
    billing_driver_html = f"<br>Billing driver: {html.escape(billing_driver)}" if billing_driver else ""
    evidence_metrics = diagnosis.get("evidence_metrics") if isinstance(diagnosis.get("evidence_metrics"), dict) else {}
    case_evidence = diagnosis.get("case_evidence") if isinstance(diagnosis.get("case_evidence"), list) else []
    subtype = str(diagnosis.get("workflow_subtype") or "")
    evidence_html = dashboard_case_evidence_chips(case_evidence) or (dashboard_subtype_html(subtype) + dashboard_evidence_chips(evidence_metrics))
    workflow_label = str(diagnosis.get("workflow_pattern_label") or diagnosis.get("top_driver") or "None detected")
    process_shape = str(diagnosis.get("process_shape") or "")
    attribution_quality = diagnosis.get("attribution_quality") if isinstance(diagnosis.get("attribution_quality"), dict) else {}
    value_evidence = diagnosis.get("value_evidence") if isinstance(diagnosis.get("value_evidence"), dict) else {}
    risk_signals = diagnosis.get("risk_signals") if isinstance(diagnosis.get("risk_signals"), list) else []
    risk_names = [
        str(item.get("name"))
        for item in risk_signals
        if isinstance(item, dict) and item.get("name")
    ][:3]
    process_risk_html = ""
    if process_shape or risk_names:
        process_risk_html = (
            '<p class="dashboard-detail">'
            + (f"Process: {html.escape(process_shape)}" if process_shape else "")
            + (f"<br>Risk: {html.escape(' + '.join(risk_names))}" if risk_names else "")
            + (f"<br>Attribution: {html.escape(str(attribution_quality.get('level')))}" if attribution_quality.get("level") else "")
            + (f"<br>Value evidence: {html.escape(str(value_evidence.get('level')))}" if value_evidence.get("level") else "")
            + "</p>"
        )
    confidence = str(diagnosis.get("confidence") or "")
    confidence_html = f'<p class="dashboard-detail">{html.escape(confidence)} confidence</p>' if confidence else ""
    drilldown = (
        f'<p class="dashboard-detail"><a href="{html.escape(str(report_link))}">Open session diagnosis</a></p>'
        if report_link
        else '<p class="dashboard-detail">Run with <code>--session-reports</code> to add drill-down pages.</p>'
    )
    root_cause_copy = dashboard_root_cause_copy(diagnosis, workflow_label)
    return f"""
  <section class="dashboard-hero">
    <div class="dashboard-intro">
      <div class="dashboard-diagnosis">
        <h2>TokenCause Diagnosis</h2>
        <span class="dashboard-diagnosis-title">{html.escape(workflow_label)}</span>
        {confidence_html}
        <p class="dashboard-diagnosis-copy">{html.escape(root_cause_copy)}</p>
        {evidence_html}
      </div>
      <div class="dashboard-grid">
        <div class="dashboard-card">
          <span class="dashboard-label">Sessions</span>
          <span class="dashboard-value">{dashboard_metric_value(sessions_analyzed)}</span>
          <div class="dashboard-detail">recent sessions analyzed</div>
        </div>
        <div class="dashboard-card">
          <span class="dashboard-label">Actionable tokens</span>
          <span class="dashboard-value">{dashboard_metric_value(actionable_tokens)}</span>
          <div class="dashboard-detail">observable workflow tokens</div>
        </div>
        <div class="dashboard-card">
          <span class="dashboard-label">Billing/cache signal</span>
          <span class="dashboard-value">{dashboard_metric_value(billing_tokens)}</span>
          <div class="dashboard-detail">{billing_driver_html[4:] if billing_driver_html else "No separate billing driver detected."}</div>
          {drilldown}
        </div>
      </div>
    </div>
  </section>

  <section class="dashboard-action">
    <h2>Recommended Next Move</h2>
    <div class="dashboard-action-grid">
      <div>
        <p><strong>{html.escape(str(diagnosis.get("next_action", "Inspect the top session.")))}</strong></p>
        <p class="muted">{html.escape(str(diagnosis.get("workflow_pattern", "")))}</p>
        {process_risk_html}
        {billing_note_html}
      </div>
      <div>
        <strong>Remember next time</strong>
        <ul>{lesson_items or avoid_items or "<li>Inspect the top session before changing workflow.</li>"}</ul>
      </div>
    </div>
  </section>
"""


def render_dashboard_html(source: str, overview_html: str, overview: dict[str, Any]) -> str:
    source_label = "Codex" if source == "codex" else "Claude Code"
    source_description = (
        "Local Codex AI coding sessions, ranked by token cost and grouped by likely cost drivers."
        if source == "codex"
        else "Local Claude Code AI coding sessions, ranked by token volume and grouped by likely cost drivers."
    )
    diagnosis = dashboard_summary_from_overview(overview)
    rendered = re.sub(r"<title>TokenCause (?:Codex|Claude) Overview</title>", "<title>TokenCause Dashboard</title>", overview_html)
    rendered = re.sub(r"<h1>TokenCause (?:Overview|Claude Overview)</h1>", "<h1>TokenCause Dashboard</h1>", rendered)
    rendered = rendered.replace("</style>", DASHBOARD_CSS + "\n  </style>", 1)
    rendered = re.sub(
        r'<p class="muted">Recent (?:Codex|Claude Code) sessions, ranked by [^<]+</p>',
        f'<p class="muted">Source: {source_label}. {source_description}</p>',
        rendered,
    )
    rendered = re.sub(r'\n  <section class="grid">\n.*?\n  </section>\n', "\n", rendered, count=1, flags=re.S)
    summary_html = render_dashboard_summary_html(source_label, diagnosis, overview)
    rendered = rendered.replace("  <h2>Most Expensive Sessions</h2>", "  <h2>Sessions Needing Attention</h2>", 1)
    return rendered.replace("  <h2>Sessions Needing Attention</h2>", summary_html + "\n  <h2>Sessions Needing Attention</h2>", 1)


def dashboard_summary_from_overview(overview: dict[str, Any]) -> dict[str, Any]:
    sessions = overview.get("sessions") if isinstance(overview.get("sessions"), list) else []
    top_session = sessions[0] if sessions and isinstance(sessions[0], dict) else None
    cost_drivers = overview.get("cost_drivers") if isinstance(overview.get("cost_drivers"), list) else []
    top_driver = cost_drivers[0].get("name") if cost_drivers and isinstance(cost_drivers[0], dict) else "None detected"
    summary = overview.get("summary") if isinstance(overview.get("summary"), dict) else {}
    recommendations = overview.get("recommendations") if isinstance(overview.get("recommendations"), list) else []
    human_diagnosis = top_session.get("human_diagnosis") if isinstance(top_session, dict) else None
    case_file = top_session.get("case_file") if isinstance(top_session, dict) else None
    if isinstance(case_file, dict):
        likely_causes = case_file.get("likely_causes") if isinstance(case_file.get("likely_causes"), list) else []
        cause = likely_causes[0] if likely_causes and isinstance(likely_causes[0], dict) else {}
        recommendations_from_case = case_file.get("recommendations") if isinstance(case_file.get("recommendations"), list) else []
        next_run_plan = case_file.get("next_run_plan") if isinstance(case_file.get("next_run_plan"), list) else []
        workflow_lessons = case_file.get("workflow_lessons") if isinstance(case_file.get("workflow_lessons"), list) else []
        process_summary = case_file.get("process_summary") if isinstance(case_file.get("process_summary"), dict) else {}
        risk_signals = case_file.get("risk_signals") if isinstance(case_file.get("risk_signals"), list) else []
        attribution_quality = case_file.get("attribution_quality") if isinstance(case_file.get("attribution_quality"), dict) else {}
        value_evidence = case_file.get("value_evidence") if isinstance(case_file.get("value_evidence"), dict) else {}
        evidence = case_file.get("evidence") if isinstance(case_file.get("evidence"), list) else []
        return {
            "sessions_analyzed": _as_int(summary.get("sessions_analyzed")),
            "top_session": top_session,
            "top_driver": str(top_session.get("actionable_driver") or top_driver),
            "billing_driver": "Cache-heavy context" if any(isinstance(item, dict) and item.get("name") == "Cached input" for item in evidence) else "",
            "workflow_pattern_label": str(cause.get("name") or "No likely cause"),
            "workflow_subtype": "",
            "confidence": str(cause.get("confidence") or ""),
            "case_evidence": evidence,
            "evidence_metrics": {},
            "why": str(cause.get("why") or "No likely workflow cause was detected yet."),
            "workflow_pattern": "",
            "next_action": str(next_run_plan[0]) if next_run_plan else str(recommendations_from_case[0]) if recommendations_from_case else str(recommendations[0]) if recommendations else "Inspect the top session.",
            "avoid_next_time": [str(item) for item in recommendations_from_case[1:3]],
            "workflow_lessons": workflow_lessons,
            "process_shape": str(process_summary.get("shape") or ""),
            "risk_signals": risk_signals,
            "attribution_quality": attribution_quality,
            "value_evidence": value_evidence,
            "recommendations": [str(item) for item in recommendations_from_case] or recommendations,
            "billing_note": "",
        }
    if isinstance(human_diagnosis, dict):
        next_actions = human_diagnosis.get("next_actions") if isinstance(human_diagnosis.get("next_actions"), list) else []
        avoid_next_time = (
            human_diagnosis.get("avoid_next_time") if isinstance(human_diagnosis.get("avoid_next_time"), list) else []
        )
        actionable_driver = human_diagnosis.get("actionable_driver") or top_session.get("actionable_driver") or top_driver
        primary_driver = str(human_diagnosis.get("primary_driver") or "")
        return {
            "sessions_analyzed": _as_int(summary.get("sessions_analyzed")),
            "top_session": top_session,
            "top_driver": str(actionable_driver),
            "billing_driver": primary_driver if primary_driver == "Cache-heavy context" else "",
            "workflow_pattern_label": str(
                human_diagnosis.get("workflow_pattern_label") or top_session.get("workflow_pattern_label") or actionable_driver
            ),
            "workflow_subtype": str(human_diagnosis.get("workflow_subtype") or top_session.get("workflow_subtype") or ""),
            "evidence_metrics": human_diagnosis.get("evidence_metrics") if isinstance(human_diagnosis.get("evidence_metrics"), dict) else {},
            "why": str(human_diagnosis.get("root_cause") or "No dominant workflow-level cost driver was detected yet."),
            "workflow_pattern": f"Workflow pattern: {human_diagnosis.get('workflow_failure') or ''}",
            "next_action": str(next_actions[0]) if next_actions else str(recommendations[0]) if recommendations else "Inspect the top session.",
            "avoid_next_time": [str(item) for item in avoid_next_time],
            "workflow_lessons": [],
            "process_shape": "",
            "risk_signals": [],
            "attribution_quality": {},
            "value_evidence": {},
            "recommendations": list(dict.fromkeys([str(item) for item in next_actions] + [str(item) for item in recommendations])),
            "billing_note": str(human_diagnosis.get("billing_note") or ""),
        }
    diagnosis_by_driver = {
        "Long tool output": {
            "why": "This got expensive mainly because long tool output entered the session context.",
            "workflow_pattern": "Workflow pattern: broad commands produced large logs that the agent kept carrying forward.",
            "next_action": "Shorten the next command output with a scoped command, `tail -100`, or a short summary before continuing.",
            "avoid_next_time": ["Use scoped commands first, and pass only `tail -100` or a summarized failure when logs are long."],
        },
        "Error/test log noise": {
            "why": "This got expensive mainly because repeated test or error output entered the session context.",
            "workflow_pattern": "Workflow pattern: failures were rerun or re-shown without first reducing the error surface.",
            "next_action": "Run a narrower test and carry forward only the first failure summary.",
            "avoid_next_time": ["Summarize the first failure, then rerun a narrower test or command."],
        },
        "Cache-heavy context": {
            "why": "This got expensive mainly because cached context dominated token usage.",
            "workflow_pattern": "Workflow pattern: the session accumulated a large stable context and continued operating inside it.",
            "next_action": "Compact or restart the session after saving a short working summary.",
            "avoid_next_time": ["Compact or restart after large context stabilizes, then carry forward only a short working summary."],
        },
        "Repeated context": {
            "why": "This got expensive mainly because the same context appeared repeatedly.",
            "workflow_pattern": "Workflow pattern: the agent reloaded or restated stable context instead of referencing a compact memo.",
            "next_action": "Create a concise memo for the repeated context and reference that memo instead of raw content.",
            "avoid_next_time": ["Create a concise memo for stable context and refer back to it instead of reloading raw content."],
        },
        "Repeated file/artifact context": {
            "why": "This got expensive mainly because the same files or artifacts were loaded repeatedly.",
            "workflow_pattern": "Workflow pattern: the agent kept rereading files or generated artifacts across turns.",
            "next_action": "Inspect narrower file ranges and summarize stable files once.",
            "avoid_next_time": ["Inspect narrower file ranges and summarize stable files once."],
        },
        "Expensive file context": {
            "why": "This got expensive mainly because costly generated, lockfile, fixture, schema, or snapshot content entered context.",
            "workflow_pattern": "Workflow pattern: bulky low-signal artifacts were treated like source context.",
            "next_action": "Ignore or summarize generated files, lockfiles, fixtures, schemas, snapshots, and minified assets.",
            "avoid_next_time": ["Ignore or summarize generated files, lockfiles, fixtures, schemas, snapshots, and minified assets."],
        },
        "Retry/failure loop": {
            "why": "This got expensive mainly because repeated failures or reruns accumulated token cost.",
            "workflow_pattern": "Workflow pattern: the same failing path was retried without changing strategy.",
            "next_action": "Stop the loop, summarize the blocker, and choose a different diagnostic step.",
            "avoid_next_time": ["Stop after repeated failures, summarize the blocker, and choose a different diagnostic step."],
        },
        "Session drift": {
            "why": "This got expensive mainly because later turns became much larger than early turns.",
            "workflow_pattern": "Workflow pattern: a long-running session drifted into carrying too much history.",
            "next_action": "Start a fresh session from a checkpoint summary before continuing.",
            "avoid_next_time": ["Split the task or start a fresh session after a stable checkpoint."],
        },
        "Environment issue": {
            "why": "This got expensive mainly because setup or environment failures consumed the session.",
            "workflow_pattern": "Workflow pattern: dependency, permission, network, config, or version blockers were debugged inside the agent loop.",
            "next_action": "Fix the setup blocker outside the long loop, then resume with a short error summary and one validation command.",
            "avoid_next_time": ["Check dependencies, permissions, env vars, network access, and runtime versions before asking the agent to keep retrying."],
        },
        "Broad exploration": {
            "why": "This got expensive mainly because the agent explored too much workspace context before narrowing the hypothesis.",
            "workflow_pattern": "Workflow pattern: broad search/read commands pulled in more files and output than the next decision needed.",
            "next_action": "Narrow the task to one subsystem, file range, or explicit hypothesis before continuing.",
            "avoid_next_time": ["Start with a focused hypothesis and cap search/read output before loading broad workspace context."],
        },
    }
    diagnosis = diagnosis_by_driver.get(
        top_driver,
        {
            "why": "No dominant workflow-level cost driver was detected yet.",
            "workflow_pattern": "Workflow pattern: not enough signal yet to identify a specific cost pattern.",
            "next_action": "Inspect the top session and compare its largest commands, files, and repeated context.",
            "avoid_next_time": ["Inspect the top session and compare its largest commands, files, and repeated context."],
        },
    )
    return {
        "sessions_analyzed": _as_int(summary.get("sessions_analyzed")),
        "top_session": top_session,
        "top_driver": top_driver,
        "billing_driver": top_driver if top_driver == "Cache-heavy context" else "",
        "workflow_pattern_label": top_driver,
        "workflow_subtype": "",
        "evidence_metrics": {},
        "why": str(diagnosis["why"]),
        "workflow_pattern": str(diagnosis["workflow_pattern"]),
        "next_action": str(recommendations[0]) if recommendations else str(diagnosis["next_action"]),
        "avoid_next_time": list(diagnosis["avoid_next_time"]),
        "workflow_lessons": [],
        "process_shape": "",
        "risk_signals": [],
        "attribution_quality": {},
        "value_evidence": {},
        "recommendations": recommendations,
    }


def dashboard_payload(source: str, overview: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "dashboard",
        "source": source,
        "summary": dashboard_summary_from_overview(overview),
        "overview": overview,
    }
