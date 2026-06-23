"""HTML rendering for session diagnosis reports."""

from __future__ import annotations

import html

from tokencause.core.formatting import compact_number
from tokencause.core.tokens import short_preview
from tokencause.renderers.html import HTML_BAR_CSS, HTML_FOOTER_CSS, html_bar_rows, html_footer, html_rows
from tokencause.renderers.redaction import compact_session_id, redact_text
from tokencause.renderers.session_report_models import SessionReportView
from tokencause.renderers.session_report_rows import (
    clean_driver_evidence,
    observable_source_group_rows,
    report_actionable_driver_rows,
    report_attribution_quality_rows,
    report_attribution_rows,
    report_billing_rows,
    report_drift_timeline_rows,
    report_file_carryover_rows,
    report_metric_cards,
    report_process_rows,
    report_project_source_carryover_rows,
    report_risk_rows,
)


def html_actionable_driver_cards(rows: list[tuple[str, int, int, str]]) -> str:
    cards: list[str] = []
    for label, tokens, total, detail in rows:
        share = tokens / (total or 1)
        score = max(0, min(100, round(share * 100)))
        percent = f"{score} score"
        width = score
        cards.append(
            '<article class="driver-card">'
            '<div class="driver-card-top">'
            f'<strong class="driver-name">{html.escape(label)}</strong>'
            f'<span class="driver-percent">{html.escape(percent)}</span>'
            "</div>"
            f'<p class="driver-detail">{html.escape(detail)}</p>'
            '<div class="bar-track">'
            f'<div class="bar-fill" style="width: {width}%;"></div>'
            "</div>"
            "</article>"
        )
    return "\n".join(cards)


def html_directional_signal_cards(rows: list[tuple[str, int, int, str]]) -> str:
    cards: list[str] = []
    for label, _tokens, _total, detail in rows:
        cards.append(
            '<article class="driver-card">'
            '<div class="driver-card-top">'
            f'<strong class="driver-name">{html.escape(label)}</strong>'
            '<span class="driver-percent">signal</span>'
            "</div>"
            f'<p class="driver-detail">{html.escape(detail)}</p>'
            "</article>"
        )
    return "\n".join(cards)

def render_session_report_html(view: SessionReportView) -> str:
    def clean(value: str) -> str:
        return redact_text(value, view.trace.cwd)

    def clean_rows(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return [(clean(left), clean(right)) for left, right in rows]

    likely_cause = view.case_file.likely_causes[0] if view.case_file.likely_causes else None
    likely_cause_title = clean((likely_cause.name if likely_cause else "") or "No likely cause identified")
    likely_cause_confidence = (likely_cause.confidence if likely_cause else "") or ""
    likely_cause_why = clean((likely_cause.why if likely_cause else "") or "TokenCause did not find enough evidence for a clear workflow cause.")
    low_attribution = view.case_file.attribution_quality.level == "low"
    if view.case_file.attribution_quality.level == "low":
        likely_cause_title = "Limited diagnosis: most tokens are not source-attributable"
    diagnosis_kicker = (
        "Diagnosis limited - low attribution quality"
        if view.case_file.attribution_quality.level == "low"
        else f"Likely cause{f' - {likely_cause_confidence} confidence' if likely_cause_confidence else ''}"
    )
    case_evidence_items = [
            f"<li><strong>{html.escape(clean(item.name))}:</strong> {html.escape(clean(item.value))}"
        + (f'<div class="muted">{html.escape(clean(short_preview(item.detail, 180)))}</div>' if item.detail else "")
        + "</li>"
        for item in view.case_file.evidence
        if item.supports != "Billing/cache signal"
        and not (low_attribution and item.name in {"Repeated artifact", "Repeated file/artifact context"})
    ]
    if not case_evidence_items:
        case_evidence_items = [
            f"<li><strong>{html.escape(clean(driver.name))}:</strong> {html.escape(clean(clean_driver_evidence(driver)))}</li>"
            for driver in view.drivers
            if driver.name != "Cache-heavy context"
            and not (low_attribution and driver.name == "Repeated file/artifact context")
        ][:4]
    case_recommendations = view.case_file.next_run_plan[:5] or view.case_file.recommendations[:4] or ["Inspect the largest commands, files, and repeated context."]
    workflow_lessons = view.case_file.workflow_lessons
    if low_attribution:
        workflow_lessons = [
            lesson
            for lesson in workflow_lessons
            if lesson.title != "Promote repeated context into a checkpoint memo"
        ]
    workflow_lessons = workflow_lessons[:4]
    workflow_lessons_html = (
        "".join(
            "<li>"
            f"<strong>{html.escape(clean(lesson.title))}:</strong> {html.escape(clean(lesson.lesson))}"
            f'<div class="muted">Trigger: {html.escape(clean(short_preview(lesson.trigger, 180)))}</div>'
            "</li>"
            for lesson in workflow_lessons
        )
        or "<li>No reusable workflow lesson detected.</li>"
    )
    observed_rows = [
        (clean(fact.name), clean(f"{fact.value} - {fact.detail}" if fact.detail else fact.value))
        for fact in view.case_file.observed_facts
    ]
    category_rows = [
        (item.name, item.tokens, view.trace.observable_tokens or 1, f"{compact_number(item.tokens)} tokens")
        for item in view.case_file.token_attribution[:10]
    ]
    source_group_rows = observable_source_group_rows(view.category_tokens)
    actionable_driver_rows = [
        (clean(label), tokens, total, clean(detail))
        for label, tokens, total, detail in report_actionable_driver_rows(view)
    ]
    metric_cards = report_metric_cards(view)
    session_rows = [
        (
            clean(label),
            compact_session_id(clean(value))
            if label == "Session"
            else short_preview(clean(value), 48)
            if label == "Title"
            else clean(value),
        )
        for label, value in view.session_rows
    ]
    driver_title = (
        'Directional Signals <span class="muted">(low attribution quality; signals are not ranked as actionable causes)</span>'
        if low_attribution
        else 'Actionable Drivers <span class="muted">(relative impact score; not token share; cache is separated below)</span>'
    )
    driver_cards_html = (
        html_directional_signal_cards(actionable_driver_rows)
        if low_attribution
        else html_actionable_driver_cards(actionable_driver_rows)
    )
    attribution_quality_rows = clean_rows(report_attribution_quality_rows(view))
    process_rows = clean_rows(report_process_rows(view))
    risk_rows = clean_rows(report_risk_rows(view))
    if view.case_file.attribution_quality.level == "low":
        project_source_rows = [("Suppressed", "Source attribution is too coarse to make reliable project-source carryover claims.")]
        file_carryover_rows = [("Suppressed", "Source attribution is too coarse to make reliable file/artifact carryover claims.")]
    else:
        project_source_rows = report_project_source_carryover_rows(view)
        file_carryover_rows = report_file_carryover_rows(view)
    drift_timeline_rows = clean_rows(report_drift_timeline_rows(view))

    appendix_html = "".join(
        '<details class="appendix-details">'
        f"<summary>Appendix: {html.escape(section.title)}</summary>\n"
        f"<table>{html_rows(clean_rows(section.rows)) if section.rows else '<tr><td>None detected.</td><td></td></tr>'}</table>\n"
        "</details>\n"
        for section in view.appendix_sections
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(view.heading)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3f5f8;
      --surface: #ffffff;
      --surface-soft: #f8fafc;
      --ink: #131925;
      --muted: #667085;
      --line: #dfe5ee;
      --line-soft: #e9eef5;
      --accent: #2563eb;
      --accent-soft: #eef4ff;
      --shadow: 0 18px 48px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(180deg, #f8fafc 0%, var(--bg) 280px),
        var(--bg);
      font-size: 14px;
      line-height: 1.45;
    }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px 24px 48px; }}
    h1, h2 {{ margin: 0; }}
    h1 {{ font-size: 24px; line-height: 1.05; letter-spacing: 0; }}
    h2 {{ color: #202939; font-size: 13px; font-weight: 760; letter-spacing: 0; margin: 26px 0 10px; }}
    .report-hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.45fr) minmax(300px, 0.8fr);
      gap: 16px;
      align-items: stretch;
      margin-bottom: 18px;
    }}
    .diagnosis-panel, .metric-panel, .card, .section-table {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
    }}
    .diagnosis-panel {{ padding: 18px; box-shadow: var(--shadow); }}
    .report-kicker {{ color: var(--muted); font-size: 12px; font-weight: 700; margin-top: 10px; }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px 14px;
      margin-top: 16px;
      padding-top: 14px;
      border-top: 1px solid var(--line-soft);
      color: #475467;
      font-size: 12px;
    }}
    .meta div {{ min-width: 0; overflow-wrap: anywhere; }}
    .meta strong {{ color: #1f2937; font-weight: 700; }}
    .casefile {{ margin-top: 16px; border-left: 3px solid var(--accent); padding-left: 14px; }}
    .casefile-title {{ display: block; margin-top: 7px; font-size: 28px; line-height: 1.05; font-weight: 780; color: #0f172a; letter-spacing: 0; }}
    .casefile-copy {{ max-width: 78ch; margin: 10px 0 0; color: #435066; font-size: 14px; }}
    .metric-panel {{ padding: 10px; }}
    .grid {{ display: grid; grid-template-columns: 1fr; gap: 8px; }}
    .metric {{
      background: var(--surface-soft);
      border: 1px solid var(--line-soft);
      border-radius: 7px;
      padding: 12px;
      color: var(--muted);
      font-size: 11px;
      text-transform: uppercase;
      font-weight: 720;
      letter-spacing: 0;
    }}
    .metric strong {{ display: block; color: #101828; font-size: 22px; line-height: 1.05; margin-top: 6px; text-transform: none; letter-spacing: 0; }}
    .card {{ padding: 14px 16px; margin: 0; }}
    .card ul {{ margin: 0; padding-left: 18px; }}
    .card li + li {{ margin-top: 5px; }}
    .report-grid {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 14px; align-items: start; }}
    .wide {{ grid-column: 1 / -1; }}
    .driver-list {{ display: grid; grid-template-columns: 1fr; gap: 10px; }}
    .driver-card {{ background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 14px 16px; min-width: 0; }}
    .driver-card-top {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }}
    .driver-name {{ color: #1f2937; font-size: 15px; line-height: 1.25; }}
    .driver-percent {{ color: #334155; font-weight: 760; white-space: nowrap; }}
    .driver-detail {{ color: #475569; margin: 8px 0 0; line-height: 1.35; overflow-wrap: anywhere; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
      box-shadow: 0 1px 2px rgba(16, 24, 40, 0.03);
    }}
    td, th {{ border-bottom: 1px solid var(--line-soft); padding: 9px 11px; vertical-align: top; text-align: left; }}
    td:first-child {{ width: 34%; color: #344054; font-weight: 620; overflow-wrap: anywhere; }}
    td:last-child {{ color: #475467; overflow-wrap: anywhere; }}
    tr:last-child td {{ border-bottom: 0; }}
    ol, ul {{ margin: 8px 0 0 22px; }}
    code {{ background: var(--accent-soft); color: #1d4ed8; padding: 1px 4px; border-radius: 4px; }}
    .muted {{ color: var(--muted); }}
    .appendix {{ margin-top: 26px; padding-top: 4px; }}
    .appendix-details {{
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 8px;
      margin-top: 10px;
      overflow: hidden;
    }}
    .appendix-details summary {{
      cursor: pointer;
      padding: 11px 13px;
      color: #344054;
      font-weight: 720;
      list-style-position: inside;
    }}
    .appendix-details table {{ border: 0; border-top: 1px solid var(--line-soft); border-radius: 0; box-shadow: none; }}
    @media (max-width: 900px) {{
      main {{ padding: 18px 12px 36px; }}
      .report-hero, .report-grid {{ grid-template-columns: 1fr; }}
      .meta {{ grid-template-columns: 1fr; }}
      .casefile-title {{ font-size: 23px; }}
    }}
{HTML_BAR_CSS}
{HTML_FOOTER_CSS}
  </style>
</head>
<body>
<main>
  <section class="report-hero">
    <div class="diagnosis-panel">
      <h1>{html.escape(view.heading)}</h1>
      <div class="report-kicker">{html.escape(diagnosis_kicker)}</div>
      <section class="casefile">
        <span class="casefile-title">{html.escape(likely_cause_title)}</span>
        <p class="casefile-copy">{html.escape(likely_cause_why)}</p>
      </section>
      <section class="meta">{''.join(f'<div><strong>{html.escape(label)}:</strong> {html.escape(value)}</div>' for label, value in session_rows)}</section>
    </div>
    <aside class="metric-panel">
      <section class="grid">{''.join(f'<div class="metric">{html.escape(label)}<strong>{html.escape(value)}</strong></div>' for label, value in metric_cards)}</section>
    </aside>
  </section>

  <section class="report-grid">
    <div>
      <h2>Evidence</h2>
      <section class="card"><ul>{''.join(case_evidence_items) if case_evidence_items else '<li>No high-signal evidence detected.</li>'}</ul></section>
    </div>
    <div>
      <h2>Attribution Quality & Value</h2>
      <table>{html_rows(attribution_quality_rows)}</table>
    </div>

    <div class="wide">
      <h2>Next Run Plan</h2>
      <section class="card"><ol>{''.join(f'<li>{html.escape(item)}</li>' for item in case_recommendations)}</ol></section>
    </div>

    <div>
      <h2>Risk Signals</h2>
      <table>{html_rows(risk_rows) if risk_rows else '<tr><td>No high-signal risk detected.</td><td></td></tr>'}</table>
    </div>

    <div>
      <h2>Engineering Process</h2>
      <table>{html_rows(process_rows)}</table>
    </div>

    <div class="wide">
      <h2>Reusable Workflow Lessons</h2>
      <section class="card"><ul>{workflow_lessons_html}</ul></section>
    </div>

    <div class="wide">
      <h2>Diagnostic Trace</h2>
      <section class="card">{html.escape(clean(view.case_file.cause_sentence))}</section>
    </div>

    <div>
      <h2>Project Source Carryover</h2>
      <table>{html_rows(project_source_rows) if project_source_rows else '<tr><td>No repeated project source files detected.</td><td></td></tr>'}</table>
    </div>

    <div>
      <h2>Drift Timeline</h2>
      <table>{html_rows(drift_timeline_rows) if drift_timeline_rows else '<tr><td>No clear context drift timeline detected.</td><td></td></tr>'}</table>
    </div>

    <div class="wide">
      <h2>All File / Artifact Carryover</h2>
      <table>{html_rows(file_carryover_rows) if file_carryover_rows else '<tr><td>No repeated file/artifact carryover detected.</td><td></td></tr>'}</table>
    </div>
  </section>

  <section class="report-grid">
    <div>
      <h2>Token Attribution</h2>
      <table>{html_rows(clean_rows(report_attribution_rows(view)))}</table>
    </div>
    <div>
      <h2>Billing / Accounting</h2>
      <table>{html_rows(clean_rows(report_billing_rows(view)))}</table>
    </div>
  </section>

  <h2>{driver_title}</h2>
  <section class="driver-list">{driver_cards_html if actionable_driver_rows else '<article class="driver-card">No high-signal drivers detected.</article>'}</section>

  <section class="report-grid">
    <div>
      <h2>Observed Facts</h2>
      <table>{html_rows(observed_rows)}</table>
    </div>
    <div>
      <h2>Limits</h2>
      <section class="card"><ul>{''.join(f'<li>{html.escape(clean(item))}</li>' for item in view.case_file.limits)}</ul></section>
    </div>
  </section>

  <section class="appendix">
    <h2>Appendix</h2>
    <details class="appendix-details">
      <summary>Appendix: Observable Token Sources</summary>
      <table>{html_bar_rows(source_group_rows) if source_group_rows else '<tr><td>None detected.</td><td></td></tr>'}</table>
    </details>

    <details class="appendix-details">
      <summary>Appendix: Raw Token Categories</summary>
      <table>{html_bar_rows(category_rows)}</table>
    </details>

    {appendix_html}
  </section>
  {html_footer()}
</main>
</body>
</html>
"""
