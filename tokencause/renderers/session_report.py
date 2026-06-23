"""Public facade for source-agnostic session diagnosis reports."""

from __future__ import annotations

from tokencause.renderers.session_report_html import render_session_report_html
from tokencause.renderers.session_report_markdown import render_session_report_markdown
from tokencause.renderers.session_report_models import (
    SessionReportAppendix,
    SessionReportScope,
    SessionReportView,
    diagnostic_coverage_scope,
)

__all__ = [
    "SessionReportAppendix",
    "SessionReportScope",
    "SessionReportView",
    "diagnostic_coverage_scope",
    "render_session_report_html",
    "render_session_report_markdown",
]
