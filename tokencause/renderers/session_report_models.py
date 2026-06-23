"""Shared view models for source-agnostic session reports."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from tokencause.core.casefile import AttributionQuality, SessionCaseFile
from tokencause.core.models import CostDriver, SessionTrace


@dataclass
class SessionReportScope:
    model_billed_tokens: int
    observable_tokens: int
    classifiable_tokens: int
    actionable_diagnostic_tokens: int
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


def diagnostic_coverage_scope(
    trace: SessionTrace,
    drivers: list[CostDriver],
    estimated_cost_usd: float | None = None,
    attribution_quality: AttributionQuality | None = None,
) -> SessionReportScope:
    observable_tokens = trace.observable_tokens
    if attribution_quality is not None:
        unclassifiable_share = max(attribution_quality.unclassified_share, attribution_quality.assistant_or_other_share)
        classifiable_tokens = max(0, min(observable_tokens, int(observable_tokens * (1 - unclassifiable_share))))
    else:
        classifiable_tokens = observable_tokens
    actionable_driver_tokens = sum(driver.impact_tokens for driver in drivers if driver.name != "Cache-heavy context")
    diagnostic_coverage_tokens = min(actionable_driver_tokens, classifiable_tokens)
    return SessionReportScope(
        model_billed_tokens=trace.model_total_tokens,
        observable_tokens=observable_tokens,
        classifiable_tokens=classifiable_tokens,
        actionable_diagnostic_tokens=diagnostic_coverage_tokens,
        cache_tokens=trace.cached_input_tokens,
        model_output_tokens=trace.model_output_tokens,
        diagnostic_coverage_tokens=diagnostic_coverage_tokens,
        diagnostic_coverage_share=round(diagnostic_coverage_tokens / (observable_tokens or 1), 6),
        estimated_cost_usd=estimated_cost_usd,
    )
