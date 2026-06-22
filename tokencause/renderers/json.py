"""JSON renderers for analysis and canonical session traces."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from tokencause.constants import __version__, JSON_OUTPUT_SCHEMA_VERSION, JSON_TEXT_PREVIEW_LIMIT
from tokencause.core.casefile import build_session_case_file, session_case_file_to_json
from tokencause.core.diagnosis import build_session_trace_cost_drivers
from tokencause.core.models import Analysis, CostDriver, HumanDiagnosis, SessionTrace, TraceEvent
from tokencause.core.tokens import short_preview


def _top_items(mapping: dict[str, float | int], limit: int = 5) -> list[tuple[str, float | int]]:
    return sorted(mapping.items(), key=lambda row: row[1], reverse=True)[:limit]


def rounded_top_items(mapping: dict[str, float], digits: int = 6) -> dict[str, float]:
    return {key: round(float(value), digits) for key, value in _top_items(mapping)}


def trace_event_to_json(event: TraceEvent) -> dict[str, Any]:
    return {
        "index": event.index,
        "run_id": event.run_id,
        "step": event.step,
        "model": event.model,
        "tool": event.tool,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "total_tokens": event.total_tokens,
        "cost_usd": round(event.cost_usd, 6),
        "latency_ms": event.latency_ms,
        "status": event.status,
        "context_items": [short_preview(item, JSON_TEXT_PREVIEW_LIMIT) for item in event.context_items[:10]],
    }


def top_trace_events(events: list[TraceEvent], limit: int = 20) -> list[TraceEvent]:
    return sorted(events, key=lambda event: (event.cost_usd, event.total_tokens, event.latency_ms), reverse=True)[:limit]


def analysis_to_json_dict(
    analysis: Analysis,
    source_path: Path,
    budget_usd: float | None,
    adapter: str | None = None,
) -> dict[str, Any]:
    projected_cost = max(analysis.total_cost - analysis.estimated_savings_usd, 0.0)
    payload = {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "analysis",
        "source": str(source_path),
        "budget_usd": budget_usd,
        "summary": {
            "events": len(analysis.events),
            "total_cost_usd": round(analysis.total_cost, 6),
            "total_tokens": analysis.total_tokens,
            "total_latency_ms": analysis.total_latency_ms,
            "estimated_savings_usd": round(analysis.estimated_savings_usd, 6),
            "projected_cost_usd": round(projected_cost, 6),
        },
        "breakdowns": {
            "cost_by_model": rounded_top_items(analysis.cost_by_model),
            "cost_by_step": rounded_top_items(analysis.cost_by_step),
            "tokens_by_model": dict(_top_items(analysis.tokens_by_model)),
            "latency_by_step_ms": dict(_top_items(analysis.latency_by_step)),
            "repeated_context": dict(_top_items(analysis.repeated_context)),
            "repeated_items": dict(_top_items(analysis.repeated_items)),
        },
        "top_events": [trace_event_to_json(event) for event in top_trace_events(analysis.events)],
        "findings": [
            {
                "severity": finding.severity,
                "title": finding.title,
                "detail": finding.detail,
            }
            for finding in analysis.findings
        ],
        "recommendations": [
            {
                "title": recommendation.title,
                "detail": recommendation.detail,
                "estimated_savings_usd": round(recommendation.estimated_savings_usd, 6),
            }
            for recommendation in analysis.recommendations
        ],
        "failures": [
            {
                "index": event.index,
                "step": event.step,
                "model": event.model,
                "tool": event.tool,
                "status": event.status,
                "error": short_preview(event.error, JSON_TEXT_PREVIEW_LIMIT),
            }
            for event in analysis.failures[:50]
        ],
    }
    if adapter:
        payload["adapter"] = adapter
    return payload


def render_json(analysis: Analysis, source_path: Path, budget_usd: float | None, adapter: str | None = None) -> str:
    return json.dumps(analysis_to_json_dict(analysis, source_path, budget_usd, adapter=adapter), ensure_ascii=False, indent=2)


def cost_driver_to_json(driver: CostDriver, total_tokens: int) -> dict[str, Any]:
    total = total_tokens or 1
    return {
        "name": driver.name,
        "impact_tokens": driver.impact_tokens,
        "impact_share": round(driver.impact_tokens / total, 6),
        "summary": driver.summary,
        "evidence": driver.evidence,
    }


def human_diagnosis_to_json(diagnosis: HumanDiagnosis) -> dict[str, Any]:
    return {
        "root_cause": diagnosis.root_cause,
        "workflow_failure": diagnosis.workflow_failure,
        "workflow_pattern_label": diagnosis.workflow_pattern_label,
        "workflow_subtype": diagnosis.workflow_subtype,
        "evidence_metrics": diagnosis.evidence_metrics,
        "evidence": diagnosis.evidence,
        "next_actions": diagnosis.next_actions,
        "avoid_next_time": diagnosis.avoid_next_time,
        "billing_note": diagnosis.billing_note,
        "primary_driver": diagnosis.primary_driver,
        "actionable_driver": diagnosis.actionable_driver,
    }


def session_trace_summary_to_json(trace: SessionTrace) -> dict[str, Any]:
    return {
        "id": trace.id,
        "source": trace.source,
        "title": short_preview(trace.title, JSON_TEXT_PREVIEW_LIMIT),
        "cwd": trace.cwd,
        "events": len(trace.events),
        "observable_tokens": trace.observable_tokens,
        "model_total_tokens": trace.model_total_tokens,
        "model_input_tokens": trace.model_input_tokens,
        "cached_input_tokens": trace.cached_input_tokens,
        "model_output_tokens": trace.model_output_tokens,
    }


def session_trace_drivers_to_json(trace: SessionTrace) -> list[dict[str, Any]]:
    return [cost_driver_to_json(driver, trace.observable_tokens) for driver in build_session_trace_cost_drivers(trace)]


def aggregate_session_trace_driver_tokens(traces: list[SessionTrace]) -> Counter[str]:
    driver_tokens: Counter[str] = Counter()
    for trace in traces:
        for driver in build_session_trace_cost_drivers(trace):
            driver_tokens[driver.name] += driver.impact_tokens
    return driver_tokens


def session_trace_to_json_dict(trace: SessionTrace, analysis: Analysis, source_path: Path, budget_usd: float | None) -> dict[str, Any]:
    payload = analysis_to_json_dict(analysis, source_path, budget_usd, adapter=trace.source)
    drivers = build_session_trace_cost_drivers(trace)
    payload["session"] = session_trace_summary_to_json(trace)
    payload["cost_drivers"] = [cost_driver_to_json(driver, trace.observable_tokens) for driver in drivers]
    payload["case_file"] = session_case_file_to_json(build_session_case_file(trace, drivers))
    payload["diagnosis_scope"] = {
        "observable_tokens": "Estimated from visible session events such as messages, file reads, and tool output.",
        "model_total_tokens": "Provider/model counters when present in the trace, otherwise derived from event token fields.",
        "cost_drivers": "Heuristic diagnosis signals; overlapping driver impacts are not a billing total.",
    }
    return payload


def render_session_trace_json(trace: SessionTrace, analysis: Analysis, source_path: Path, budget_usd: float | None) -> str:
    return json.dumps(session_trace_to_json_dict(trace, analysis, source_path, budget_usd), ensure_ascii=False, indent=2)


def counter_breakdown(counter: Counter[str], total_tokens: int, limit: int = 10) -> list[dict[str, Any]]:
    total = total_tokens or 1
    return [
        {
            "name": name,
            "tokens": tokens,
            "share": round(tokens / total, 6),
        }
        for name, tokens in counter.most_common(limit)
    ]
