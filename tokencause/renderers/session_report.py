"""Source-agnostic session diagnosis report renderers."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any
import html

from tokencause.core.casefile import SessionCaseFile
from tokencause.core.formatting import compact_number, money
from tokencause.core.models import CostDriver, SessionTrace
from tokencause.core.tokens import short_preview
from tokencause.renderers.html import HTML_BAR_CSS, HTML_FOOTER_CSS, html_bar_rows, html_footer, html_rows


@dataclass
class SessionReportScope:
    model_billed_tokens: int
    observable_tokens: int
    cache_tokens: int
    model_output_tokens: int
    diagnostic_coverage_tokens: int
    diagnostic_coverage_share: float
    estimated_cost_usd: float | None = None


@dataclass
class SessionReportAppendix:
    title: str
    rows: list[tuple[str, str]]


@dataclass
class SessionReportView:
    heading: str
    session_rows: list[tuple[str, str]]
    metric_cards: list[tuple[str, str]]
    case_file: SessionCaseFile
    trace: SessionTrace
    drivers: list[CostDriver]
    scope: SessionReportScope
    category_tokens: Counter[str]
    appendix_sections: list[SessionReportAppendix] = field(default_factory=list)


OBSERVABLE_SOURCE_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Discovery / search", ("search_output",)),
    ("Tool results", ("other_tool_output", "tool_output", "build_log", "install_log")),
    ("Debug / verification", ("test_log", "error_log")),
    ("Conversation", ("assistant_message", "user_message")),
    ("Tool calls", ("tool_call",)),
)


def diagnostic_coverage_scope(
    trace: SessionTrace,
    drivers: list[CostDriver],
    estimated_cost_usd: float | None = None,
) -> SessionReportScope:
    observable_tokens = trace.observable_tokens
    diagnostic_coverage_tokens = min(sum(driver.impact_tokens for driver in drivers), observable_tokens)
    return SessionReportScope(
        model_billed_tokens=trace.model_total_tokens,
        observable_tokens=observable_tokens,
        cache_tokens=trace.cached_input_tokens,
        model_output_tokens=trace.model_output_tokens,
        diagnostic_coverage_tokens=diagnostic_coverage_tokens,
        diagnostic_coverage_share=round(diagnostic_coverage_tokens / (observable_tokens or 1), 6),
        estimated_cost_usd=estimated_cost_usd,
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


def html_actionable_driver_cards(rows: list[tuple[str, int, int, str]]) -> str:
    cards: list[str] = []
    for label, tokens, total, detail in rows:
        share = tokens / (total or 1)
        percent = f"{share:.0%}"
        width = min(100, max(1 if tokens > 0 else 0, round(share * 100)))
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


def report_actionable_driver_rows(view: SessionReportView, limit: int = 6) -> list[tuple[str, int, int, str]]:
    actionable_drivers = [driver for driver in view.drivers if driver.name != "Cache-heavy context"]
    total = view.trace.observable_tokens or 1
    return [
        (
            f"{index}. {driver.name}",
            driver.impact_tokens,
            total,
            clean_driver_evidence(driver),
        )
        for index, driver in enumerate(actionable_drivers[:limit], start=1)
    ]


def report_billing_rows(view: SessionReportView) -> list[tuple[str, str]]:
    rows = [
        ("model billed tokens", compact_number(view.scope.model_billed_tokens)),
        ("cached input tokens", compact_number(view.scope.cache_tokens)),
        ("model output tokens", compact_number(view.scope.model_output_tokens)),
        ("observable transcript tokens", compact_number(view.scope.observable_tokens)),
    ]
    cache_driver = next((driver for driver in view.drivers if driver.name == "Cache-heavy context"), None)
    if cache_driver is not None:
        rows.append(("billing signal", f"{cache_driver.name}: {cache_driver.summary}"))
    if view.scope.estimated_cost_usd is not None:
        rows.append(("estimated cost", money(view.scope.estimated_cost_usd)))
    return rows


def report_attribution_rows(view: SessionReportView) -> list[tuple[str, str]]:
    return [
        ("actionable observable tokens", compact_number(view.scope.observable_tokens)),
        ("billing/cache tokens", compact_number(view.scope.cache_tokens)),
        ("model output tokens", compact_number(view.scope.model_output_tokens)),
        (
            "diagnostic coverage",
            f"{compact_number(view.scope.diagnostic_coverage_tokens)} observable tokens matched one or more drivers ({view.scope.diagnostic_coverage_share:.0%})",
        ),
        ("scope note", "Diagnostic coverage is not waste. Driver categories can overlap and this is not a billing total."),
    ]


def display_file_ref(file_ref: str, cwd: str) -> str:
    if cwd and file_ref.startswith(cwd.rstrip("/") + "/"):
        return file_ref[len(cwd.rstrip("/")) + 1 :]
    return file_ref


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
        detail = (
            f"{item.appearances}x, {compact_number(item.tokens)} estimated tokens, "
            f"{compact_number(item.repeated_tokens)} after first appearance, "
            f"events {item.first_event_index}-{item.last_event_index}"
        )
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
        detail = (
            f"{item.appearances}x, {compact_number(item.repeated_tokens)} after first appearance, "
            f"events {item.first_event_index}-{item.last_event_index}"
        )
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


def render_session_report_markdown(view: SessionReportView) -> str:
    likely_cause = view.case_file.likely_causes[0] if view.case_file.likely_causes else None
    lines = [f"# {view.heading}", ""]
    for label, value in view.session_rows:
        lines.append(f"- **{label}:** `{value}`" if "/" in value or value.startswith(".") else f"- **{label}:** {value}")
    if view.scope.estimated_cost_usd is not None:
        lines.append(f"- **Estimated cost:** {money(view.scope.estimated_cost_usd)}")

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

    lines.extend(["", "## Cause Sentence", view.case_file.cause_sentence])

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

    lines.extend(["", "## Next Run Plan"])
    if view.case_file.recommendations:
        lines.extend(f"- {item}" for item in view.case_file.recommendations[:4])
    else:
        lines.append("- Inspect the largest commands, files, and repeated context.")

    lines.extend(
        [
            "",
            "## Token Attribution",
            f"- **Actionable observable tokens:** {compact_number(view.scope.observable_tokens)}",
            f"- **Billing/cache tokens:** {compact_number(view.scope.cache_tokens)}",
            f"- **Model output tokens:** {compact_number(view.scope.model_output_tokens)}",
            f"- **Diagnostic coverage:** {compact_number(view.scope.diagnostic_coverage_tokens)} observable tokens matched one or more drivers ({view.scope.diagnostic_coverage_share:.0%} of observable)",
            "",
            "## Billing / Accounting",
            f"- **Model billed tokens:** {compact_number(view.scope.model_billed_tokens)}",
            f"- **Cached input tokens:** {compact_number(view.scope.cache_tokens)}",
            f"- **Observable transcript tokens:** {compact_number(view.scope.observable_tokens)}",
            "- Diagnostic coverage is not waste. Driver categories can overlap and this is not a billing total.",
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


def render_session_report_html(view: SessionReportView) -> str:
    likely_cause = view.case_file.likely_causes[0] if view.case_file.likely_causes else None
    likely_cause_title = (likely_cause.name if likely_cause else "") or "No likely cause identified"
    likely_cause_confidence = (likely_cause.confidence if likely_cause else "") or ""
    likely_cause_why = (likely_cause.why if likely_cause else "") or "TokenCause did not find enough evidence for a clear workflow cause."
    case_evidence_items = [
        f"<li><strong>{html.escape(item.name)}:</strong> {html.escape(item.value)}"
        + (f'<div class="muted">{html.escape(short_preview(item.detail, 180))}</div>' if item.detail else "")
        + "</li>"
        for item in view.case_file.evidence
        if item.supports != "Billing/cache signal"
    ]
    if not case_evidence_items:
        case_evidence_items = [
            f"<li><strong>{html.escape(driver.name)}:</strong> {html.escape(clean_driver_evidence(driver))}</li>"
            for driver in view.drivers
            if driver.name != "Cache-heavy context"
        ][:4]
    case_recommendations = view.case_file.recommendations[:4] or ["Inspect the largest commands, files, and repeated context."]
    observed_rows = [
        (fact.name, f"{fact.value} - {fact.detail}" if fact.detail else fact.value)
        for fact in view.case_file.observed_facts
    ]
    category_rows = [
        (item.name, item.tokens, view.trace.observable_tokens or 1, f"{compact_number(item.tokens)} tokens")
        for item in view.case_file.token_attribution[:10]
    ]
    source_group_rows = observable_source_group_rows(view.category_tokens)
    actionable_driver_rows = report_actionable_driver_rows(view)
    project_source_rows = report_project_source_carryover_rows(view)
    file_carryover_rows = report_file_carryover_rows(view)
    drift_timeline_rows = report_drift_timeline_rows(view)

    appendix_html = "".join(
        f"<h2>Appendix: {html.escape(section.title)}</h2>\n"
        f"<table>{html_rows(section.rows) if section.rows else '<tr><td>None detected.</td><td></td></tr>'}</table>\n"
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
      <div class="report-kicker">Likely cause{f' - {html.escape(likely_cause_confidence)} confidence' if likely_cause_confidence else ''}</div>
      <section class="casefile">
        <span class="casefile-title">{html.escape(likely_cause_title)}</span>
        <p class="casefile-copy">{html.escape(likely_cause_why)}</p>
      </section>
      <section class="meta">{''.join(f'<div><strong>{html.escape(label)}:</strong> {html.escape(value)}</div>' for label, value in view.session_rows)}</section>
    </div>
    <aside class="metric-panel">
      <section class="grid">{''.join(f'<div class="metric">{html.escape(label)}<strong>{html.escape(value)}</strong></div>' for label, value in view.metric_cards)}</section>
    </aside>
  </section>

  <section class="report-grid">
    <div>
      <h2>Evidence</h2>
      <section class="card"><ul>{''.join(case_evidence_items) if case_evidence_items else '<li>No high-signal evidence detected.</li>'}</ul></section>
    </div>
    <div>
      <h2>Next Run Plan</h2>
      <section class="card"><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in case_recommendations)}</ul></section>
    </div>

    <div class="wide">
      <h2>Cause Sentence</h2>
      <section class="card">{html.escape(view.case_file.cause_sentence)}</section>
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
      <table>{html_rows(report_attribution_rows(view))}</table>
    </div>
    <div>
      <h2>Billing / Accounting</h2>
      <table>{html_rows(report_billing_rows(view))}</table>
    </div>
  </section>

  <h2>Actionable Drivers <span class="muted">(diagnostic impact; cache is separated below)</span></h2>
  <section class="driver-list">{html_actionable_driver_cards(actionable_driver_rows) if actionable_driver_rows else '<article class="driver-card">No high-signal actionable drivers detected.</article>'}</section>

  <section class="report-grid">
    <div>
      <h2>Observed Facts</h2>
      <table>{html_rows(observed_rows)}</table>
    </div>
    <div>
      <h2>Limits</h2>
      <section class="card"><ul>{''.join(f'<li>{html.escape(item)}</li>' for item in view.case_file.limits)}</ul></section>
    </div>
  </section>

  <section class="appendix">
    <h2>Appendix: Observable Token Sources</h2>
    <table>{html_bar_rows(source_group_rows) if source_group_rows else '<tr><td>None detected.</td><td></td></tr>'}</table>

    <h2>Appendix: Raw Token Categories</h2>
    <table>{html_bar_rows(category_rows)}</table>

    {appendix_html}
  </section>
  {html_footer()}
</main>
</body>
</html>
"""
