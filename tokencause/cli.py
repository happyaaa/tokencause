#!/usr/bin/env python3
"""Analyze agent run traces for cost, latency, and context waste."""

from __future__ import annotations

import argparse
import functools
import html
import json
import sqlite3
import sys
import time
import webbrowser
from datetime import datetime, timezone
from collections import Counter, defaultdict
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .constants import __version__, JSON_OUTPUT_SCHEMA_VERSION, JSON_TEXT_PREVIEW_LIMIT
from .core.accounting import (
    analyze,
    build_findings,
    build_recommendations,
    cap_overlapping_savings,
    is_expensive_model,
    is_low_value_step,
)
from .core.files import (
    artifact_kind,
    extract_file_refs,
    file_risk_reason,
    should_count_file_ref,
)
from .core.values import (
    as_float,
    as_int,
    first_path,
    first_present,
    get_path,
    normalize_context_items,
    token_count_value,
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

from .core.formatting import money, seconds, top_items
from .core.tokens import content_hash, estimate_tokens, short_preview
from .core.diagnosis import (
    build_broad_exploration,
    build_environment_issues,
    build_human_diagnosis,
    build_session_trace_cost_drivers,
    environment_issue_kind,
    is_broad_exploration_command,
)
from .core.schema import (
    codex_report_to_session_trace,
    load_session_trace_jsonl,
    parse_session_event,
    session_trace_to_trace_events,
    trace_events_to_session_trace,
)
from .core.models import (
    Analysis,
    BroadExploration,
    ClaudePriceConfig,
    ClaudeSession,
    CodexCacheResult,
    CodexContentEvent,
    CodexExplainReport,
    CodexPriceConfig,
    CodexThread,
    CostDriver,
    EnvironmentIssue,
    Finding,
    HumanDiagnosis,
    Recommendation,
    RepeatedArtifact,
    RepeatedChunk,
    RetryLoop,
    SessionDrift,
    SessionEvent,
    SessionTrace,
    TokenUsage,
    TraceEvent,
)
from .adapters.codex import (
    build_repeated_artifacts,
    build_repeated_chunks,
    build_retry_loops,
    build_session_drift,
    classify_codex_event,
    codex_tool_output_category,
    command_exit_code,
    command_from_arguments,
    is_error_output,
    is_failed_test_output,
    load_codex_threads,
    parse_codex_rollout,
    parse_codex_rollout_cached,
    pick_codex_thread,
)
from .adapters.claude import (
    load_claude_otel,
    load_claude_sessions,
    parse_claude_jsonl,
    pick_claude_session,
)
from .renderers.json import (
    aggregate_session_trace_driver_tokens,
    analysis_to_json_dict,
    cost_driver_to_json,
    counter_breakdown,
    render_json,
    render_session_trace_json,
    session_trace_summary_to_json,
)
from .renderers.dashboard import (
    dashboard_payload,
    dashboard_summary_from_overview,
    render_dashboard_html,
    render_dashboard_summary_html,
)
from .renderers.console import render_console, render_markdown
from .renderers.codex import (
    build_codex_cost_drivers,
    build_codex_root_cause_narrative,
    build_codex_summary,
    build_codex_token_attribution,
    codex_driver_cause,
    codex_driver_next_action,
    codex_estimated_cost,
    codex_explain_to_json_dict,
    codex_overview_to_json_dict,
    codex_recommendations,
    html_overview_session_rows,
    render_codex_explain,
    render_codex_explain_json,
    render_codex_explain_markdown,
    render_codex_html_report,
    render_codex_overview_html,
    render_codex_overview_json,
    render_codex_scan,
    render_codex_scan_json,
)
from .renderers.claude import (
    build_claude_cost_drivers,
    claude_estimated_cost,
    claude_explain_to_json_dict,
    claude_overview_to_json_dict,
    claude_recommendations,
    claude_usage_tokens,
    render_claude_explain,
    render_claude_explain_json,
    render_claude_html_report,
    render_claude_overview_html,
    render_claude_overview_json,
    render_claude_scan,
    render_claude_scan_json,
)
from .renderers.html import (
    HTML_BAR_CSS,
    HTML_FOOTER_CSS,
    html_bar_rows,
    html_footer,
    html_rows,
    html_table_rows,
    overview_recommendations,
)
from .storage.cache import (
    broad_exploration_to_json,
    codex_content_event_to_json,
    codex_report_cache_key,
    codex_report_from_json,
    codex_report_to_json,
    codex_thread_to_json,
    session_drift_to_json,
)


def parse_event(raw: dict[str, Any], index: int) -> TraceEvent:
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
    return TraceEvent(
        raw=raw,
        index=index,
        run_id=str(first_present(raw, ("run_id", "runId", "session_id", "trace_id"), "default")),
        step=str(first_present(raw, ("step", "name", "span_name", "operation"), "unknown")),
        model=str(first_present(raw, ("model", "model_name", "modelName"), "unknown")),
        tool=str(first_present(raw, ("tool", "tool_name", "toolName"), "none")),
        input_tokens=token_count_value(raw, usage, ("input_tokens", "inputTokens", "prompt_tokens", "promptTokens")),
        output_tokens=token_count_value(
            raw,
            usage,
            ("output_tokens", "outputTokens", "completion_tokens", "completionTokens"),
        ),
        cost_usd=as_float(first_present(raw, ("cost_usd", "cost", "spend"), 0.0)),
        latency_ms=as_int(first_present(raw, ("latency_ms", "duration_ms", "elapsed_ms"), 0)),
        status=str(first_present(raw, ("status", "outcome"), "ok")).lower(),
        error=str(first_present(raw, ("error", "error_message", "exception"), "")),
        context_hash=str(first_present(raw, ("context_hash", "prompt_hash", "contextHash"), "")),
        context_items=normalize_context_items(first_present(raw, ("context_items", "files", "documents"), None)),
    )


def load_jsonl(path: Path, parser: str = "generic") -> list[TraceEvent]:
    events: list[TraceEvent] = []
    with path.open("r", encoding="utf-8") as file:
        for index, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            try:
                raw = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{index}: invalid JSON: {exc}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"{path}:{index}: expected a JSON object")
            events.append(parse_event(raw, index))
    return events
































































def status_line(name: str, ok: bool, detail: str) -> str:
    return f"- {name}: {'available' if ok else 'missing'} — {detail}"


def status_ok(statuses: list[dict[str, Any]], name: str) -> bool:
    return any(status["name"] == name and status["ok"] for status in statuses)


def doctor_next_commands(statuses: list[dict[str, Any]]) -> list[str]:
    commands: list[str] = []
    has_local_sessions = status_ok(statuses, "Codex sessions") or status_ok(statuses, "Claude sessions")
    has_examples = status_ok(statuses, "Example traces")
    if has_local_sessions:
        commands.extend(
            [
                "tokencause open",
                "tokencause report --last --open",
                "tokencause overview --session-reports --open",
                "tokencause serve",
                "tokencause dashboard --session-reports",
                "tokencause dashboard --json",
            ]
        )
    elif has_examples:
        commands.append("tokencause open")
    if status_ok(statuses, "Codex sessions"):
        commands.extend(
            [
                "tokencause codex scan",
                "tokencause codex explain --last",
            ]
        )
    if status_ok(statuses, "Claude sessions"):
        commands.extend(
            [
                "tokencause claude scan",
                "tokencause claude explain --last",
            ]
        )
    if has_examples:
        if has_local_sessions:
            commands.append("tokencause serve --demo")
        commands.append("tokencause dashboard --demo")
        commands.append("tokencause demo-site")
        commands.extend(
            [
                "tokencause analyze examples/tokencause_trace.jsonl --budget 2",
                "tokencause claude import-otel examples/claude_otel_sample.json --budget 1",
            ]
        )
    if not commands:
        commands.append("tokencause open")
    return commands


def doctor_status_report(
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    project_root: Path | None = None,
    price_config: Path | None = None,
) -> dict[str, Any]:
    codex_root = codex_home or Path.home() / ".codex"
    claude_root = claude_home or Path.home() / ".claude"
    root = project_root or Path.cwd()
    statuses: list[dict[str, Any]] = []

    def add_status(name: str, ok: bool, detail: str, optional: bool = False) -> None:
        statuses.append({"name": name, "ok": ok, "optional": optional, "detail": detail})

    try:
        codex_sessions = load_codex_threads(codex_root, limit=1)
        add_status(
            "Codex sessions",
            bool(codex_sessions),
            f"{len(codex_sessions)} recent session(s) visible in {codex_root}",
            optional=True,
        )
    except (OSError, sqlite3.Error) as exc:
        add_status("Codex sessions", False, f"{codex_root}: {exc}", optional=True)

    try:
        claude_sessions = load_claude_sessions(claude_root, limit=1)
        add_status(
            "Claude sessions",
            bool(claude_sessions),
            f"{len(claude_sessions)} recent session(s) visible in {claude_root}",
            optional=True,
        )
    except OSError as exc:
        add_status("Claude sessions", False, f"{claude_root}: {exc}", optional=True)

    examples_dir = root / "examples"
    required_examples = [
        "tokencause_trace.jsonl",
        "claude_otel_sample.json",
    ]
    present_examples = [name for name in required_examples if (examples_dir / name).exists()]
    add_status(
        "Example traces",
        len(present_examples) == len(required_examples),
        f"{len(present_examples)}/{len(required_examples)} found under {examples_dir}",
    )

    price_examples = [
        "tokencause.prices.example.json",
        "codex_prices.example.json",
        "claude_prices.example.json",
    ]
    present_price_examples = [name for name in price_examples if (examples_dir / name).exists()]
    missing_price_examples = [name for name in price_examples if name not in present_price_examples]
    price_detail = (
        f"{len(present_price_examples)}/{len(price_examples)} found under {examples_dir}"
        if not missing_price_examples
        else f"missing {', '.join(missing_price_examples)} under {examples_dir}"
    )
    add_status(
        "Price examples",
        len(present_price_examples) == len(price_examples),
        price_detail,
    )

    config_path = price_config or root / "tokencause.prices.json"
    add_status(
        "Price config",
        config_path.exists(),
        str(config_path) if config_path.exists() else f"optional; copy examples/tokencause.prices.example.json to {config_path}",
        optional=True,
    )

    return {
        "schema_version": JSON_OUTPUT_SCHEMA_VERSION,
        "version": __version__,
        "kind": "doctor",
        "statuses": statuses,
        "ok": all(status["ok"] for status in statuses if not status["optional"]),
        "next_commands": doctor_next_commands(statuses),
    }


def render_doctor(
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    project_root: Path | None = None,
    price_config: Path | None = None,
) -> str:
    report = doctor_status_report(
        codex_home=codex_home,
        claude_home=claude_home,
        project_root=project_root,
        price_config=price_config,
    )
    lines = ["TokenCause Doctor", ""]
    lines.extend(status_line(str(status["name"]), bool(status["ok"]), str(status["detail"])) for status in report["statuses"])
    lines.extend(["", "Next commands:"])
    lines.extend(f"- {command}" for command in report["next_commands"])
    return "\n".join(lines)


def render_doctor_json(
    codex_home: Path | None = None,
    claude_home: Path | None = None,
    project_root: Path | None = None,
    price_config: Path | None = None,
) -> str:
    return json.dumps(
        doctor_status_report(
            codex_home=codex_home,
            claude_home=claude_home,
            project_root=project_root,
            price_config=price_config,
        ),
        ensure_ascii=False,
        indent=2,
    )


def command_doctor(args: argparse.Namespace) -> int:
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
    project_root = Path(args.project_root).expanduser() if args.project_root else None
    price_config = Path(args.price_config).expanduser() if args.price_config else None
    if args.json:
        print(render_doctor_json(codex_home=codex_home, claude_home=claude_home, project_root=project_root, price_config=price_config))
    else:
        print(render_doctor(codex_home=codex_home, claude_home=claude_home, project_root=project_root, price_config=price_config))
    return 0


def run_analysis_command(args: argparse.Namespace, parser_name: str) -> int:
    trace_path = Path(args.trace)
    try:
        events = load_jsonl(trace_path, parser=parser_name)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    analysis = analyze(events, args.budget)
    report = (
        render_json(analysis, trace_path, args.budget, adapter=parser_name)
        if args.json
        else render_markdown(analysis, trace_path, args.budget)
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

    if args.json:
        print(report)
    elif args.markdown:
        print(report)
    else:
        print(render_console(analysis, trace_path, args.budget))
        if args.out:
            print(f"\nmarkdown report: {args.out}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    trace_path = Path(args.trace)
    try:
        trace = load_session_trace_jsonl(trace_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    events = session_trace_to_trace_events(trace)
    analysis = analyze(events, args.budget)
    report = (
        render_session_trace_json(trace, analysis, trace_path, args.budget)
        if args.json
        else render_markdown(analysis, trace_path, args.budget, trace=trace)
    )

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

    if args.json:
        print(report)
    elif args.markdown:
        print(report)
    else:
        print(render_console(analysis, trace_path, args.budget))
        if args.out:
            print(f"\nmarkdown report: {args.out}")
    return 0


def command_claude_scan(args: argparse.Namespace) -> int:
    try:
        claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
        sessions = filtered_claude_sessions(args, claude_home)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_claude_scan_json(sessions) if args.json else render_claude_scan(sessions))
    return 0


def _project_filters_from_args(args: argparse.Namespace) -> list[str]:
    values = []
    cwd = getattr(args, "cwd", None)
    project = getattr(args, "project", None)
    if cwd:
        values.append(str(Path(cwd).expanduser().resolve(strict=False)))
    if project:
        values.append(str(project))
        values.append(str(Path(project).expanduser().resolve(strict=False)))
    return [value.rstrip("/") for value in values if value]


def _session_matches_project_filter(cwd: str, project: str = "", filters: list[str] | None = None) -> bool:
    if not filters:
        return True
    candidates = {cwd.rstrip("/"), project.rstrip("/")}
    if cwd:
        candidates.add(str(Path(cwd).expanduser().resolve(strict=False)).rstrip("/"))
    return any(
        candidate == item or candidate.startswith(item + "/") or item in candidate
        for candidate in candidates
        for item in filters
        if candidate and item
    )


def _prefilter_limit(args: argparse.Namespace) -> int:
    filters = _project_filters_from_args(args)
    limit = int(getattr(args, "limit", 20) or 20)
    return max(limit * 20, 200) if filters else limit


def filtered_codex_threads(args: argparse.Namespace, codex_home: Path | None = None) -> list[CodexThread]:
    filters = _project_filters_from_args(args)
    threads = load_codex_threads(codex_home, limit=_prefilter_limit(args))
    if filters:
        threads = [thread for thread in threads if _session_matches_project_filter(thread.cwd, filters=filters)]
    return threads[: int(getattr(args, "limit", 20) or 20)]


def filtered_claude_sessions(args: argparse.Namespace, claude_home: Path | None = None) -> list[ClaudeSession]:
    filters = _project_filters_from_args(args)
    sessions = load_claude_sessions(claude_home, limit=_prefilter_limit(args))
    if filters:
        sessions = [
            session
            for session in sessions
            if _session_matches_project_filter(session.cwd, project=session.project, filters=filters)
        ]
    return sessions[: int(getattr(args, "limit", 20) or 20)]


def command_claude_explain(args: argparse.Namespace) -> int:
    try:
        claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
        session = pick_claude_session(
            claude_home,
            last=args.last,
            session_id=args.session_id,
            session_file=args.session_file,
        )
        events = parse_claude_jsonl(session)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    analysis = analyze(events, args.budget)
    markdown_report = render_markdown(analysis, session.path, args.budget)
    json_report = render_claude_explain_json(session, events, args.budget) if args.json else ""
    console_report = "" if args.json or args.markdown else render_claude_explain(session, events, args.budget)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_report if args.json else markdown_report if args.markdown else console_report, encoding="utf-8")
    if args.json:
        print(json_report)
    elif args.markdown:
        print(markdown_report)
    else:
        print(console_report)
        if args.out:
            print(f"\nreport: {args.out}")
    return 0


def command_claude_report(args: argparse.Namespace) -> int:
    try:
        claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
        session = pick_claude_session(
            claude_home,
            last=args.last,
            session_id=args.session_id,
            session_file=args.session_file,
        )
        events = parse_claude_jsonl(session)
        prices = claude_prices_from_args(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_claude_html_report(session, events, budget_usd=args.budget, prices=prices), encoding="utf-8")
    print(f"html report: {out_path}")
    estimated_cost = claude_estimated_cost(events, prices)
    if estimated_cost is not None:
        print(f"estimated cost: {money(estimated_cost)}")
    return 0


def command_claude_overview(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    try:
        claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
        prices = claude_prices_from_args(args)
        sessions = filtered_claude_sessions(args, claude_home)
        reports: list[tuple[ClaudeSession, list[TraceEvent]]] = []
        skipped = 0
        for session in sessions:
            try:
                reports.append((session, parse_claude_jsonl(session)))
            except (OSError, ValueError):
                skipped += 1
                continue
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out or "reports/claude-overview.html")
    if args.out or not args.json:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    report_links: dict[str, str] = {}
    if args.session_reports:
        report_dir = out_path.parent / "claude-sessions" if args.out or not args.json else Path("reports/claude-sessions")
        report_dir.mkdir(parents=True, exist_ok=True)
        for session, events in reports:
            report_name = f"{session.id[:8]}.html"
            (report_dir / report_name).write_text(render_claude_html_report(session, events, prices=prices), encoding="utf-8")
            report_links[session.id] = f"claude-sessions/{report_name}"

    if args.json:
        output = render_claude_overview_json(reports, report_links=report_links, prices=prices)
        if args.out:
            out_path.write_text(output + "\n", encoding="utf-8")
        print(output)
    else:
        out_path.write_text(render_claude_overview_html(reports, report_links=report_links, prices=prices), encoding="utf-8")
        print(f"html overview: {out_path}")
        if args.session_reports:
            print(f"session reports: {out_path.parent / 'claude-sessions'}")
    elapsed = time.perf_counter() - started
    if not args.json:
        print(f"parsed: {len(reports)}/{len(sessions)} sessions in {elapsed:.2f}s")
    if skipped:
        print(f"skipped: {skipped}", file=sys.stderr if args.json else sys.stdout)
    if prices.enabled and not args.json:
        total_estimated_cost = sum(claude_estimated_cost(events, prices) or 0.0 for _, events in reports)
        print(f"estimated cost: {money(total_estimated_cost)}")
    return 0


def command_claude_import_otel(args: argparse.Namespace) -> int:
    trace_path = Path(args.trace)
    try:
        events = load_claude_otel(trace_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    analysis = analyze(events, args.budget)
    report = (
        render_json(analysis, trace_path, args.budget, adapter="claude_otel")
        if args.json
        else render_markdown(analysis, trace_path, args.budget)
    )
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    if args.json:
        print(report)
    elif args.markdown:
        print(report)
    else:
        print(render_console(analysis, trace_path, args.budget))
        if args.out:
            print(f"\nmarkdown report: {args.out}")
    return 0


def dashboard_source_from_args(args: argparse.Namespace) -> str:
    source = args.source
    if source != "auto":
        return source
    codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
    claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
    try:
        if filtered_codex_threads(args, codex_home):
            return "codex"
    except (OSError, sqlite3.Error):
        pass
    try:
        if filtered_claude_sessions(args, claude_home):
            return "claude"
    except OSError:
        pass
    raise ValueError("No local Codex or Claude Code sessions found. Run `tokencause doctor` for setup details.")


def command_dashboard(args: argparse.Namespace) -> int:
    if getattr(args, "demo", False):
        return command_dashboard_demo(args)

    try:
        source = dashboard_source_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out or "reports/tokencause-dashboard.html")
    report_links: dict[str, str] = {}
    try:
        if source == "codex":
            codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
            prices = load_codex_price_config(Path(args.price_config).expanduser()) if args.price_config else CodexPriceConfig()
            threads = filtered_codex_threads(args, codex_home)
            reports: list[CodexExplainReport] = []
            cache_dir = None if args.no_cache else Path(args.cache_dir)
            for thread in threads:
                try:
                    reports.append(parse_codex_rollout_cached(thread, cache_dir).report)
                except (OSError, ValueError):
                    continue
            if args.session_reports:
                report_dir = out_path.parent / "codex-sessions" if args.out or not args.json else Path("reports/codex-sessions")
                report_dir.mkdir(parents=True, exist_ok=True)
                for report in reports:
                    report_name = f"{report.thread.id[:8]}.html"
                    (report_dir / report_name).write_text(render_codex_html_report(report, prices=prices), encoding="utf-8")
                    report_links[report.thread.id] = f"codex-sessions/{report_name}"
            overview = codex_overview_to_json_dict(reports, report_links=report_links, prices=prices)
            if args.json:
                output = json.dumps(dashboard_payload("codex", overview), ensure_ascii=False, indent=2)
                if args.out:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(output + "\n", encoding="utf-8")
                print(output)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    render_dashboard_html(
                        "codex",
                        render_codex_overview_html(reports, report_links=report_links, prices=prices),
                        overview,
                    ),
                    encoding="utf-8",
                )
                print(f"dashboard: {out_path}")
                if args.session_reports:
                    print(f"session reports: {out_path.parent / 'codex-sessions'}")
        else:
            claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
            prices = load_claude_price_config(Path(args.price_config).expanduser()) if args.price_config else ClaudePriceConfig()
            sessions = filtered_claude_sessions(args, claude_home)
            reports: list[tuple[ClaudeSession, list[TraceEvent]]] = []
            for session in sessions:
                try:
                    reports.append((session, parse_claude_jsonl(session)))
                except (OSError, ValueError):
                    continue
            if args.session_reports:
                report_dir = out_path.parent / "claude-sessions" if args.out or not args.json else Path("reports/claude-sessions")
                report_dir.mkdir(parents=True, exist_ok=True)
                for session, events in reports:
                    report_name = f"{session.id[:8]}.html"
                    (report_dir / report_name).write_text(render_claude_html_report(session, events, prices=prices), encoding="utf-8")
                    report_links[session.id] = f"claude-sessions/{report_name}"
            overview = claude_overview_to_json_dict(reports, report_links=report_links, prices=prices)
            if args.json:
                output = json.dumps(dashboard_payload("claude", overview), ensure_ascii=False, indent=2)
                if args.out:
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    out_path.write_text(output + "\n", encoding="utf-8")
                print(output)
            else:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(
                    render_dashboard_html(
                        "claude",
                        render_claude_overview_html(reports, report_links=report_links, prices=prices),
                        overview,
                    ),
                    encoding="utf-8",
                )
                print(f"dashboard: {out_path}")
                if args.session_reports:
                    print(f"session reports: {out_path.parent / 'claude-sessions'}")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def open_html_path(path: Path) -> None:
    webbrowser.open(path.expanduser().resolve(strict=False).as_uri())


def command_report(args: argparse.Namespace) -> int:
    try:
        if args.source != "auto":
            source = args.source
        elif args.thread_id:
            source = "codex"
        elif args.session_id or args.session_file:
            source = "claude"
        else:
            source = dashboard_source_from_args(args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out or "reports/tokencause-report.html")
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if source == "codex":
            codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
            prices = load_codex_price_config(Path(args.price_config).expanduser()) if args.price_config else CodexPriceConfig()
            thread = pick_codex_thread(codex_home, last=True, thread_id=args.thread_id)
            report = parse_codex_rollout(thread)
            out_path.write_text(render_codex_html_report(report, prices=prices), encoding="utf-8")
        else:
            claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
            prices = load_claude_price_config(Path(args.price_config).expanduser()) if args.price_config else ClaudePriceConfig()
            session = pick_claude_session(
                claude_home,
                last=True,
                session_id=args.session_id,
                session_file=args.session_file,
            )
            events = parse_claude_jsonl(session)
            out_path.write_text(render_claude_html_report(session, events, prices=prices), encoding="utf-8")
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"source: {source}")
    print(f"html report: {out_path}")
    if args.open:
        open_html_path(out_path)
        print(f"opened: {out_path}")
    return 0


def command_overview(args: argparse.Namespace) -> int:
    if not args.out and not args.json:
        args.out = "reports/tokencause-overview.html"
    result = command_dashboard(args)
    if result == 0 and args.open and not args.json:
        open_html_path(Path(args.out or "reports/tokencause-overview.html"))
        print(f"opened: {args.out or 'reports/tokencause-overview.html'}")
    return result


def command_open(args: argparse.Namespace) -> int:
    try:
        source = dashboard_source_from_args(args)
    except ValueError as exc:
        if args.source != "auto":
            print(f"error: {exc}", file=sys.stderr)
            return 1
        demo_dir = Path(args.out or "reports/tokencause-demo-site")
        try:
            site = write_demo_site(demo_dir)
        except OSError as write_exc:
            print(f"error: {write_exc}", file=sys.stderr)
            return 1
        print("mode: synthetic demo")
        print("note: demo uses bundled fake data, not your local sessions.")
        print(f"demo dashboard: {site['index']}")
        print(f"demo json: {site['json']}")
        if not args.no_open:
            open_html_path(Path(site["index"]))
            print(f"opened: {site['index']}")
        return 0

    report_args = argparse.Namespace(
        source=source,
        last=True,
        codex_home=args.codex_home,
        claude_home=args.claude_home,
        thread_id=None,
        session_id=None,
        session_file=None,
        cwd=args.cwd,
        project=args.project,
        limit=args.limit,
        out=args.out or "reports/tokencause-report.html",
        open=not args.no_open,
        price_config=args.price_config,
    )
    print("mode: local session report")
    return command_report(report_args)


def command_dashboard_demo(args: argparse.Namespace) -> int:
    out_path = Path(args.out or "reports/tokencause-dashboard.html")
    artifacts = demo_dashboard_artifacts(include_report_link=not args.json or args.session_reports)
    output = json.dumps(artifacts["payload"], ensure_ascii=False, indent=2)
    try:
        if args.json:
            if args.session_reports:
                report_dir = out_path.parent / "codex-sessions" if args.out else Path("reports/codex-sessions")
                report_dir.mkdir(parents=True, exist_ok=True)
                for report_name, session_html in artifacts["session_html_by_name"].items():
                    (report_dir / str(report_name)).write_text(str(session_html), encoding="utf-8")
            if args.out:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_text(output + "\n", encoding="utf-8")
            print(output)
            if args.session_reports:
                print(f"session reports: {report_dir}", file=sys.stderr)
            return 0

        out_path.parent.mkdir(parents=True, exist_ok=True)
        report_dir = out_path.parent / "codex-sessions"
        report_dir.mkdir(parents=True, exist_ok=True)
        for report_name, session_html in artifacts["session_html_by_name"].items():
            (report_dir / str(report_name)).write_text(str(session_html), encoding="utf-8")
        out_path.write_text(str(artifacts["index_html"]), encoding="utf-8")
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"dashboard: {out_path}")
    print(f"session reports: {report_dir}")
    return 0


def write_dashboard_site(args: argparse.Namespace, site_dir: Path) -> dict[str, Any]:
    source = dashboard_source_from_args(args)
    site_dir.mkdir(parents=True, exist_ok=True)
    index_path = site_dir / "index.html"
    payload_path = site_dir / "dashboard.json"
    report_links: dict[str, str] = {}

    if source == "codex":
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
        prices = load_codex_price_config(Path(args.price_config).expanduser()) if args.price_config else CodexPriceConfig()
        threads = filtered_codex_threads(args, codex_home)
        reports: list[CodexExplainReport] = []
        cache_dir = None if args.no_cache else Path(args.cache_dir)
        for thread in threads:
            try:
                reports.append(parse_codex_rollout_cached(thread, cache_dir).report)
            except (OSError, ValueError):
                continue
        report_dir = site_dir / "codex-sessions"
        report_dir.mkdir(parents=True, exist_ok=True)
        for report in reports:
            report_name = f"{report.thread.id[:8]}.html"
            (report_dir / report_name).write_text(render_codex_html_report(report, prices=prices), encoding="utf-8")
            report_links[report.thread.id] = f"codex-sessions/{report_name}"
        overview = codex_overview_to_json_dict(reports, report_links=report_links, prices=prices)
        index_path.write_text(
            render_dashboard_html(
                "codex",
                render_codex_overview_html(reports, report_links=report_links, prices=prices),
                overview,
            ),
            encoding="utf-8",
        )
        payload_path.write_text(json.dumps(dashboard_payload("codex", overview), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"source": "codex", "index": index_path, "json": payload_path, "sessions": len(reports)}

    claude_home = Path(args.claude_home).expanduser() if args.claude_home else None
    prices = load_claude_price_config(Path(args.price_config).expanduser()) if args.price_config else ClaudePriceConfig()
    sessions = filtered_claude_sessions(args, claude_home)
    reports: list[tuple[ClaudeSession, list[TraceEvent]]] = []
    for session in sessions:
        try:
            reports.append((session, parse_claude_jsonl(session)))
        except (OSError, ValueError):
            continue
    report_dir = site_dir / "claude-sessions"
    report_dir.mkdir(parents=True, exist_ok=True)
    for session, events in reports:
        report_name = f"{session.id[:8]}.html"
        (report_dir / report_name).write_text(render_claude_html_report(session, events, prices=prices), encoding="utf-8")
        report_links[session.id] = f"claude-sessions/{report_name}"
    overview = claude_overview_to_json_dict(reports, report_links=report_links, prices=prices)
    index_path.write_text(
        render_dashboard_html(
            "claude",
            render_claude_overview_html(reports, report_links=report_links, prices=prices),
            overview,
        ),
        encoding="utf-8",
    )
    payload_path.write_text(json.dumps(dashboard_payload("claude", overview), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"source": "claude", "index": index_path, "json": payload_path, "sessions": len(reports)}


def demo_dashboard_artifacts(include_report_link: bool = True) -> dict[str, Any]:
    reports = demo_codex_reports()
    report = reports[0]
    report_names = {item.thread.id: f"{item.thread.id[:8]}.html" for item in reports}
    report_name = report_names[report.thread.id]
    report_links = (
        {item.thread.id: f"codex-sessions/{report_names[item.thread.id]}" for item in reports}
        if include_report_link
        else {}
    )
    overview = codex_overview_to_json_dict(reports, report_links=report_links)
    session_html_by_name = {
        report_names[item.thread.id]: render_codex_html_report(item)
        for item in reports
    }
    return {
        "report": report,
        "reports": reports,
        "report_name": report_name,
        "report_names": report_names,
        "overview": overview,
        "payload": dashboard_payload("codex", overview),
        "index_html": render_dashboard_html("codex", render_codex_overview_html(reports, report_links=report_links), overview),
        "session_html": session_html_by_name[report_name],
        "session_html_by_name": session_html_by_name,
    }


def demo_codex_reports() -> list[CodexExplainReport]:
    return [
        demo_codex_report(),
        demo_environment_report(),
        demo_broad_exploration_report(),
    ]


def demo_codex_report() -> CodexExplainReport:
    repeated_failure = (
        "ERROR tests/test_checkout.py::test_total\n"
        "AssertionError: expected 42.00, got 39.00\n"
        + ("stack frame from checkout calculation\n" * 80)
    )
    lockfile_output = "package-lock.json\n" + ("dependency tree entry\n" * 180)
    tool_call = CodexContentEvent(
        category="tool_call",
        tokens=12,
        preview="pytest tests/test_checkout.py",
        timestamp="2026-06-16T12:00:00Z",
        command="pytest tests/test_checkout.py",
        content_hash=content_hash("tool_call pytest tests/test_checkout.py"),
    )
    failure_one = CodexContentEvent(
        category="test_log",
        tokens=estimate_tokens(repeated_failure),
        preview=repeated_failure,
        timestamp="2026-06-16T12:00:01Z",
        file_refs=("tests/test_checkout.py",),
        command="pytest tests/test_checkout.py",
        content_hash=content_hash(repeated_failure),
    )
    failure_two = CodexContentEvent(
        category="test_log",
        tokens=estimate_tokens(repeated_failure),
        preview=repeated_failure,
        timestamp="2026-06-16T12:02:01Z",
        file_refs=("tests/test_checkout.py",),
        command="pytest tests/test_checkout.py",
        content_hash=content_hash(repeated_failure),
    )
    lockfile = CodexContentEvent(
        category="other_tool_output",
        tokens=estimate_tokens(lockfile_output),
        preview=lockfile_output,
        timestamp="2026-06-16T12:03:00Z",
        file_refs=("package-lock.json",),
        command="cat package-lock.json",
        content_hash=content_hash(lockfile_output),
    )
    assistant_context = CodexContentEvent(
        category="assistant_message",
        tokens=820,
        preview="I will inspect the failing checkout path, summarize the repeated failure, and avoid rereading the full lockfile.",
        timestamp="2026-06-16T12:04:00Z",
        content_hash=content_hash("assistant demo context"),
    )
    content_events = [tool_call, failure_one, failure_two, lockfile, assistant_context]
    repeated_chunks = [
        RepeatedChunk(
            content_hash=failure_one.content_hash,
            count=2,
            tokens_each=failure_one.tokens,
            duplicate_tokens=failure_one.tokens,
            category="test_log",
            preview=failure_one.preview,
        )
    ]
    retry_loops = [
        RetryLoop(
            key="pytest tests/test_checkout.py",
            count=2,
            tokens=failure_one.tokens + failure_two.tokens,
            command="pytest tests/test_checkout.py",
            preview=repeated_failure,
        )
    ]
    return CodexExplainReport(
        thread=CodexThread(
            id="demo-codex-session",
            title="Demo: diagnose an expensive AI coding session",
            rollout_path=Path("examples/demo-codex-rollout.jsonl"),
            cwd="/demo/checkout-service",
            updated_at=1781625600,
            tokens_used=42000,
        ),
        content_events=content_events,
        usage_events=[
            {
                "input_tokens": 28000,
                "cached_input_tokens": 0,
                "output_tokens": 3500,
                "total_tokens": 31500,
            }
        ],
        category_tokens=dict(
            sorted(
                {
                    category: sum(event.tokens for event in content_events if event.category == category)
                    for category in {event.category for event in content_events}
                }.items(),
                key=lambda row: row[1],
                reverse=True,
            )
        ),
        file_tokens={
            "tests/test_checkout.py": failure_one.tokens + failure_two.tokens,
            "package-lock.json": lockfile.tokens,
        },
        command_tokens={
            "pytest tests/test_checkout.py": tool_call.tokens + failure_one.tokens + failure_two.tokens,
            "cat package-lock.json": lockfile.tokens,
        },
        repeated_hashes={failure_one.content_hash: 2},
        repeated_chunks=repeated_chunks,
        repeated_artifacts=[
            RepeatedArtifact(
                file_ref="tests/test_checkout.py",
                count=2,
                tokens=failure_one.tokens + failure_two.tokens,
                categories=("test_log",),
            )
        ],
        long_tool_outputs=[failure_one, failure_two, lockfile],
        failure_events=[failure_one, failure_two],
        retry_loops=retry_loops,
        session_drift=SessionDrift(
            early_avg_tokens=9000,
            late_avg_tokens=31500,
            ratio=3.5,
            peak_tokens=31500,
            samples=4,
        ),
    )


def demo_environment_report() -> CodexExplainReport:
    install_error = (
        "npm ERR! code EACCES\n"
        "npm ERR! permission denied while installing sharp\n"
        + ("node-gyp rebuild failed because the local toolchain is missing\n" * 60)
    )
    retry_output = (
        "pip install -r requirements.txt\n"
        "ERROR: Could not find a version that satisfies the requirement internal-sdk\n"
        + ("network retry exhausted while resolving package index\n" * 40)
    )
    install_event = CodexContentEvent(
        category="error_log",
        tokens=estimate_tokens(install_error),
        preview=install_error,
        timestamp="2026-06-16T11:00:00Z",
        file_refs=("package-lock.json",),
        command="npm install",
        content_hash=content_hash(install_error),
    )
    retry_event = CodexContentEvent(
        category="error_log",
        tokens=estimate_tokens(retry_output),
        preview=retry_output,
        timestamp="2026-06-16T11:04:00Z",
        file_refs=("requirements.txt",),
        command="pip install -r requirements.txt",
        content_hash=content_hash(retry_output),
    )
    assistant_event = CodexContentEvent(
        category="assistant_message",
        tokens=520,
        preview="The session is blocked on local dependency setup; fix npm permissions and package index access before retrying.",
        timestamp="2026-06-16T11:06:00Z",
        content_hash=content_hash("assistant demo environment"),
    )
    content_events = [install_event, retry_event, assistant_event]
    return CodexExplainReport(
        thread=CodexThread(
            id="demo-env-session",
            title="Demo: environment setup burned the session",
            rollout_path=Path("examples/demo-env-rollout.jsonl"),
            cwd="/demo/mobile-app",
            updated_at=1781622000,
            tokens_used=28000,
        ),
        content_events=content_events,
        usage_events=[
            {
                "input_tokens": 18000,
                "cached_input_tokens": 0,
                "output_tokens": 2100,
                "total_tokens": 20100,
            }
        ],
        category_tokens=_demo_category_tokens(content_events),
        file_tokens={
            "package-lock.json": install_event.tokens,
            "requirements.txt": retry_event.tokens,
        },
        command_tokens={
            "npm install": install_event.tokens,
            "pip install -r requirements.txt": retry_event.tokens,
        },
        repeated_hashes={},
        repeated_chunks=[],
        repeated_artifacts=[],
        long_tool_outputs=[install_event, retry_event],
        failure_events=[install_event, retry_event],
        retry_loops=[
            RetryLoop(
                key="dependency setup",
                count=2,
                tokens=install_event.tokens + retry_event.tokens,
                command="npm install / pip install -r requirements.txt",
                preview=install_error,
            )
        ],
        session_drift=None,
        environment_issues=[
            EnvironmentIssue(
                kind="permission",
                count=1,
                tokens=install_event.tokens,
                command="npm install",
                preview=install_error,
            ),
            EnvironmentIssue(
                kind="network",
                count=1,
                tokens=retry_event.tokens,
                command="pip install -r requirements.txt",
                preview=retry_output,
            ),
        ],
    )


def demo_broad_exploration_report() -> CodexExplainReport:
    search_output = (
        "src/auth/login.ts\nsrc/auth/session.ts\nsrc/billing/invoices.ts\nsrc/generated/api_schema.json\n"
        + ("src/generated/api_schema.json\n" * 120)
    )
    schema_output = "src/generated/api_schema.json\n" + ("OpenAPI schema field definition\n" * 150)
    search_event = CodexContentEvent(
        category="search_output",
        tokens=estimate_tokens(search_output),
        preview=search_output,
        timestamp="2026-06-16T10:00:00Z",
        file_refs=("src/auth/login.ts", "src/auth/session.ts", "src/billing/invoices.ts", "src/generated/api_schema.json"),
        command="rg -n TODO src",
        content_hash=content_hash(search_output),
    )
    schema_event = CodexContentEvent(
        category="other_tool_output",
        tokens=estimate_tokens(schema_output),
        preview=schema_output,
        timestamp="2026-06-16T10:03:00Z",
        file_refs=("src/generated/api_schema.json",),
        command="cat src/generated/api_schema.json",
        content_hash=content_hash(schema_output),
    )
    assistant_event = CodexContentEvent(
        category="assistant_message",
        tokens=430,
        preview="The task should narrow to auth/session.ts before loading generated schema artifacts.",
        timestamp="2026-06-16T10:05:00Z",
        content_hash=content_hash("assistant demo broad exploration"),
    )
    content_events = [search_event, schema_event, assistant_event]
    return CodexExplainReport(
        thread=CodexThread(
            id="demo-broad-session",
            title="Demo: broad repository exploration before narrowing",
            rollout_path=Path("examples/demo-broad-rollout.jsonl"),
            cwd="/demo/saas-platform",
            updated_at=1781618400,
            tokens_used=19000,
        ),
        content_events=content_events,
        usage_events=[
            {
                "input_tokens": 12000,
                "cached_input_tokens": 0,
                "output_tokens": 1200,
                "total_tokens": 13200,
            }
        ],
        category_tokens=_demo_category_tokens(content_events),
        file_tokens={
            "src/auth/login.ts": search_event.tokens // 4,
            "src/auth/session.ts": search_event.tokens // 4,
            "src/billing/invoices.ts": search_event.tokens // 4,
            "src/generated/api_schema.json": schema_event.tokens + search_event.tokens // 4,
        },
        command_tokens={
            "rg -n TODO src": search_event.tokens,
            "cat src/generated/api_schema.json": schema_event.tokens,
        },
        repeated_hashes={},
        repeated_chunks=[],
        repeated_artifacts=[
            RepeatedArtifact(
                file_ref="src/generated/api_schema.json",
                count=2,
                tokens=schema_event.tokens + search_event.tokens // 4,
                categories=("search_output", "other_tool_output"),
            )
        ],
        long_tool_outputs=[search_event, schema_event],
        failure_events=[],
        retry_loops=[],
        session_drift=None,
        broad_exploration=BroadExploration(
            search_commands=1,
            broad_commands=2,
            unique_files=4,
            search_tokens=search_event.tokens,
            command_tokens=search_event.tokens + schema_event.tokens,
            examples=("rg -n TODO src", "cat src/generated/api_schema.json"),
        ),
    )


def _demo_category_tokens(content_events: list[CodexContentEvent]) -> dict[str, int]:
    return dict(
        sorted(
            {
                category: sum(event.tokens for event in content_events if event.category == category)
                for category in {event.category for event in content_events}
            }.items(),
            key=lambda row: row[1],
            reverse=True,
        )
    )


def write_demo_site(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = demo_dashboard_artifacts(include_report_link=True)
    report_dir = out_dir / "codex-sessions"
    report_dir.mkdir(parents=True, exist_ok=True)
    for report_name, session_html in artifacts["session_html_by_name"].items():
        (report_dir / str(report_name)).write_text(str(session_html), encoding="utf-8")
    index_path = out_dir / "index.html"
    json_path = out_dir / "dashboard.json"
    index_path.write_text(str(artifacts["index_html"]), encoding="utf-8")
    json_path.write_text(json.dumps(artifacts["payload"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"source": "demo", "index": index_path, "json": json_path, "sessions": len(artifacts["reports"])}


def command_demo_site(args: argparse.Namespace) -> int:
    try:
        site = write_demo_site(Path(args.out))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"demo dashboard: {site['index']}")
    print(f"demo json: {site['json']}")
    print(f"session reports: {Path(args.out) / 'codex-sessions'}")
    return 0


class TokenCauseStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def write_serve_site(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "demo", False):
        return write_demo_site(Path(args.site_dir))
    return write_dashboard_site(args, Path(args.site_dir))


def command_serve(args: argparse.Namespace) -> int:
    try:
        site = write_serve_site(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    handler = functools.partial(TokenCauseStaticHandler, directory=str(Path(args.site_dir)))
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/"
    print(f"TokenCause dashboard: {url}")
    print(f"source: {site['source']}")
    print(f"sessions: {site['sessions']}")
    print(f"site dir: {Path(args.site_dir)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


def command_codex_scan(args: argparse.Namespace) -> int:
    try:
        threads = filtered_codex_threads(args, Path(args.codex_home).expanduser() if args.codex_home else None)
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(render_codex_scan_json(threads) if args.json else render_codex_scan(threads))
    return 0


def codex_prices_from_args(args: argparse.Namespace) -> CodexPriceConfig:
    config_path = getattr(args, "price_config", None)
    prices = load_codex_price_config(Path(config_path).expanduser()) if config_path else CodexPriceConfig()
    input_price = getattr(args, "input_price_per_mtok", None)
    cached_input_price = getattr(args, "cached_input_price_per_mtok", None)
    output_price = getattr(args, "output_price_per_mtok", None)
    if input_price is not None:
        prices.input_per_mtok = float(input_price)
    if cached_input_price is not None:
        prices.cached_input_per_mtok = float(cached_input_price)
    if output_price is not None:
        prices.output_per_mtok = float(output_price)
    return prices


def claude_prices_from_args(args: argparse.Namespace) -> ClaudePriceConfig:
    config_path = getattr(args, "price_config", None)
    prices = load_claude_price_config(Path(config_path).expanduser()) if config_path else ClaudePriceConfig()
    input_price = getattr(args, "input_price_per_mtok", None)
    cache_write_price = getattr(args, "cache_write_price_per_mtok", None)
    cache_read_price = getattr(args, "cache_read_price_per_mtok", None)
    output_price = getattr(args, "output_price_per_mtok", None)
    if input_price is not None:
        prices.input_per_mtok = float(input_price)
    if cache_write_price is not None:
        prices.cache_write_per_mtok = float(cache_write_price)
    if cache_read_price is not None:
        prices.cache_read_per_mtok = float(cache_read_price)
    if output_price is not None:
        prices.output_per_mtok = float(output_price)
    return prices


def load_codex_price_config(path: Path) -> CodexPriceConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read price config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Price config must be a JSON object: {path}")
    data = raw.get("codex") if isinstance(raw.get("codex"), dict) else raw
    return CodexPriceConfig(
        input_per_mtok=as_float(data.get("input_price_per_mtok")),
        cached_input_per_mtok=as_float(data.get("cached_input_price_per_mtok")),
        output_per_mtok=as_float(data.get("output_price_per_mtok")),
    )


def load_claude_price_config(path: Path) -> ClaudePriceConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read price config {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Price config must be a JSON object: {path}")
    data = raw.get("claude") if isinstance(raw.get("claude"), dict) else raw
    return ClaudePriceConfig(
        input_per_mtok=as_float(data.get("input_price_per_mtok")),
        cache_write_per_mtok=as_float(data.get("cache_write_price_per_mtok")),
        cache_read_per_mtok=as_float(data.get("cache_read_price_per_mtok")),
        output_per_mtok=as_float(data.get("output_price_per_mtok")),
    )


def command_codex_explain(args: argparse.Namespace) -> int:
    try:
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
        thread = pick_codex_thread(codex_home, last=args.last, thread_id=args.thread_id)
        report = parse_codex_rollout(thread)
        prices = codex_prices_from_args(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        output = render_codex_explain_json(report, prices=prices)
    elif args.markdown:
        output = render_codex_explain_markdown(report, prices=prices)
    else:
        output = render_codex_explain(report, prices=prices)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output + "\n", encoding="utf-8")
    print(output)
    if args.out and not args.json:
        print(f"\nreport: {args.out}")
    return 0


def command_codex_report(args: argparse.Namespace) -> int:
    try:
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
        thread = pick_codex_thread(codex_home, last=args.last, thread_id=args.thread_id)
        report = parse_codex_rollout(thread)
        prices = codex_prices_from_args(args)
    except (OSError, sqlite3.Error, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_codex_html_report(report, prices=prices), encoding="utf-8")
    print(f"html report: {out_path}")
    return 0


def command_codex_overview(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    try:
        codex_home = Path(args.codex_home).expanduser() if args.codex_home else None
        prices = codex_prices_from_args(args)
        threads = filtered_codex_threads(args, codex_home)
        reports = []
        cache_statuses: Counter[str] = Counter()
        cache_dir = None if args.no_cache else Path(args.cache_dir)
        for thread in threads:
            try:
                result = parse_codex_rollout_cached(thread, cache_dir)
                reports.append(result.report)
                cache_statuses[result.status] += 1
            except (OSError, ValueError):
                cache_statuses["skipped"] += 1
                continue
    except (OSError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out or "reports/codex-overview.html")
    if args.out or not args.json:
        out_path.parent.mkdir(parents=True, exist_ok=True)
    report_links: dict[str, str] = {}
    if args.session_reports:
        report_dir = out_path.parent / "codex-sessions" if args.out or not args.json else Path("reports/codex-sessions")
        report_dir.mkdir(parents=True, exist_ok=True)
        for report in reports:
            report_name = f"{report.thread.id[:8]}.html"
            (report_dir / report_name).write_text(render_codex_html_report(report, prices=prices), encoding="utf-8")
            report_links[report.thread.id] = f"codex-sessions/{report_name}"

    if args.json:
        output = render_codex_overview_json(reports, report_links=report_links, prices=prices)
        if args.out:
            out_path.write_text(output + "\n", encoding="utf-8")
        print(output)
    else:
        out_path.write_text(render_codex_overview_html(reports, report_links=report_links, prices=prices), encoding="utf-8")
        print(f"html overview: {out_path}")
        if args.session_reports:
            print(f"session reports: {out_path.parent / 'codex-sessions'}")
    elapsed = time.perf_counter() - started
    status_text = ", ".join(f"{status}={count}" for status, count in sorted(cache_statuses.items()))
    if not args.json:
        print(f"cache: {status_text or 'none'}")
        print(f"parsed: {len(reports)}/{len(threads)} sessions in {elapsed:.2f}s")
    if prices.enabled and not args.json:
        total_estimated_cost = sum(codex_estimated_cost(report, prices) or 0.0 for report in reports)
        print(f"estimated cost: {money(total_estimated_cost)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokencause",
        description="Analyze agent run traces for cost, latency, failures, and context waste.",
    )
    parser.add_argument("--version", action="version", version=f"tokencause {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_codex_price_args(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--price-config",
            help="JSON file with Codex token prices. CLI price flags override this file.",
        )
        target.add_argument(
            "--input-price-per-mtok",
            type=float,
            default=None,
            help="Estimated uncached input price in USD per 1M tokens.",
        )
        target.add_argument(
            "--cached-input-price-per-mtok",
            type=float,
            default=None,
            help="Estimated cached input price in USD per 1M tokens.",
        )
        target.add_argument(
            "--output-price-per-mtok",
            type=float,
            default=None,
            help="Estimated output price in USD per 1M tokens.",
        )

    def add_claude_price_args(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--price-config",
            help="JSON file with Claude token prices. CLI price flags override this file.",
        )
        target.add_argument(
            "--input-price-per-mtok",
            type=float,
            default=None,
            help="Estimated uncached input price in USD per 1M tokens.",
        )
        target.add_argument(
            "--cache-write-price-per-mtok",
            type=float,
            default=None,
            help="Estimated Claude cache creation/write price in USD per 1M tokens.",
        )
        target.add_argument(
            "--cache-read-price-per-mtok",
            type=float,
            default=None,
            help="Estimated Claude cache read price in USD per 1M tokens.",
        )
        target.add_argument(
            "--output-price-per-mtok",
            type=float,
            default=None,
            help="Estimated output price in USD per 1M tokens.",
        )

    doctor_parser = subparsers.add_parser("doctor", help="Check local TokenCause data-source availability.")
    doctor_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    doctor_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    doctor_parser.add_argument("--project-root", help="Project root to check for examples and local config. Defaults to cwd.")
    doctor_parser.add_argument("--price-config", help="Optional price config path to check.")
    doctor_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    doctor_parser.set_defaults(func=command_doctor)

    open_parser = subparsers.add_parser(
        "open",
        help="Open the best available TokenCause report, or a synthetic demo when no local sessions exist.",
    )
    open_parser.add_argument(
        "--source",
        choices=("auto", "codex", "claude"),
        default="auto",
        help="Local session source to analyze. Defaults to auto.",
    )
    open_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    open_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    open_parser.add_argument("--cwd", help="Only consider sessions whose working directory matches this project path.")
    open_parser.add_argument("--project", help="Only consider sessions matching this project name or path.")
    open_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to inspect when auto-selecting a source.")
    open_parser.add_argument(
        "--out",
        help="Output path. For local sessions this is an HTML report path; for demo fallback this is a site directory.",
    )
    open_parser.add_argument("--no-open", action="store_true", help="Write the report without opening a browser.")
    open_parser.add_argument("--price-config", help="Optional Codex or Claude price config JSON file.")
    open_parser.set_defaults(func=command_open)

    dashboard_parser = subparsers.add_parser("dashboard", help="Open the local AI coding session dashboard.")
    dashboard_parser.add_argument(
        "--source",
        choices=("auto", "codex", "claude"),
        default="auto",
        help="Local session source to analyze. Defaults to auto.",
    )
    dashboard_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    dashboard_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    dashboard_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to analyze.")
    dashboard_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    dashboard_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    dashboard_parser.add_argument(
        "--cache-dir",
        default=".tokencause-cache/codex",
        help="Directory for parsed Codex session cache. Defaults to .tokencause-cache/codex.",
    )
    dashboard_parser.add_argument("--no-cache", action="store_true", help="Disable parsed Codex session cache.")
    dashboard_parser.add_argument(
        "--session-reports",
        action="store_true",
        help="Also write linked per-session HTML reports next to the dashboard.",
    )
    dashboard_parser.add_argument(
        "--out",
        help="Write the dashboard to this path. Defaults to reports/tokencause-dashboard.html for HTML; JSON prints to stdout unless --out is set.",
    )
    dashboard_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of writing HTML by default.")
    dashboard_parser.add_argument("--price-config", help="Optional Codex or Claude price config JSON file.")
    dashboard_parser.add_argument("--demo", action="store_true", help="Write a synthetic demo dashboard without reading local sessions.")
    dashboard_parser.set_defaults(func=command_dashboard)

    report_parser = subparsers.add_parser("report", help="Write a local HTML diagnosis report for the latest AI coding session.")
    report_parser.add_argument(
        "--source",
        choices=("auto", "codex", "claude"),
        default="auto",
        help="Local session source to analyze. Defaults to auto.",
    )
    report_parser.add_argument("--last", action="store_true", help="Report on the latest available session. This is the default.")
    report_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    report_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    report_parser.add_argument("--thread-id", help="Report on a specific Codex thread id or id prefix.")
    report_parser.add_argument("--session-id", help="Report on a specific Claude session id or id prefix.")
    report_parser.add_argument("--session-file", help="Report on a specific Claude JSONL session file.")
    report_parser.add_argument("--cwd", help="Only consider sessions whose working directory matches this project path.")
    report_parser.add_argument("--project", help="Only consider sessions matching this project name or path.")
    report_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to inspect when auto-selecting a source.")
    report_parser.add_argument("--out", help="Write the HTML report to this path. Defaults to reports/tokencause-report.html.")
    report_parser.add_argument("--open", action="store_true", help="Open the generated HTML report in the default browser.")
    report_parser.add_argument("--price-config", help="Optional Codex or Claude price config JSON file.")
    report_parser.set_defaults(func=command_report)

    overview_parser = subparsers.add_parser("overview", help="Write a local HTML overview across recent AI coding sessions.")
    overview_parser.add_argument(
        "--source",
        choices=("auto", "codex", "claude"),
        default="auto",
        help="Local session source to analyze. Defaults to auto.",
    )
    overview_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    overview_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    overview_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to analyze.")
    overview_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    overview_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    overview_parser.add_argument(
        "--cache-dir",
        default=".tokencause-cache/codex",
        help="Directory for parsed Codex session cache. Defaults to .tokencause-cache/codex.",
    )
    overview_parser.add_argument("--no-cache", action="store_true", help="Disable parsed Codex session cache.")
    overview_parser.add_argument(
        "--session-reports",
        action="store_true",
        help="Also write linked per-session HTML reports next to the overview.",
    )
    overview_parser.add_argument(
        "--out",
        help="Write the overview to this path. Defaults to reports/tokencause-overview.html for HTML; JSON prints to stdout unless --out is set.",
    )
    overview_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of writing HTML by default.")
    overview_parser.add_argument("--open", action="store_true", help="Open the generated HTML overview in the default browser.")
    overview_parser.add_argument("--price-config", help="Optional Codex or Claude price config JSON file.")
    overview_parser.set_defaults(func=command_overview)

    serve_parser = subparsers.add_parser("serve", help="Serve the local AI coding session dashboard on localhost.")
    serve_parser.add_argument(
        "--source",
        choices=("auto", "codex", "claude"),
        default="auto",
        help="Local session source to serve. Defaults to auto.",
    )
    serve_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    serve_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    serve_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to analyze.")
    serve_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    serve_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    serve_parser.add_argument(
        "--cache-dir",
        default=".tokencause-cache/codex",
        help="Directory for parsed Codex session cache. Defaults to .tokencause-cache/codex.",
    )
    serve_parser.add_argument("--no-cache", action="store_true", help="Disable parsed Codex session cache.")
    serve_parser.add_argument("--price-config", help="Optional Codex or Claude price config JSON file.")
    serve_parser.add_argument("--site-dir", default="reports/tokencause-site", help="Directory for generated dashboard site files.")
    serve_parser.add_argument("--host", default="127.0.0.1", help="Host interface for the local dashboard server.")
    serve_parser.add_argument("--port", type=int, default=8787, help="Port for the local dashboard server. Use 0 to pick a free port.")
    serve_parser.add_argument("--demo", action="store_true", help="Serve a synthetic demo dashboard without reading local sessions.")
    serve_parser.set_defaults(func=command_serve)

    demo_site_parser = subparsers.add_parser("demo-site", help="Write a synthetic demo dashboard without reading local sessions.")
    demo_site_parser.add_argument("--out", default="reports/tokencause-demo-site", help="Directory for generated demo dashboard files.")
    demo_site_parser.set_defaults(func=command_demo_site)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a TokenCause session trace JSONL file.")
    analyze_parser.add_argument("trace", help="Path to a TokenCause session trace JSONL file.")
    analyze_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this run.")
    analyze_parser.add_argument("--out", help="Write the report to this path. Uses JSON when --json is set, otherwise Markdown.")
    analyze_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    analyze_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of console/Markdown output.")
    analyze_parser.set_defaults(func=command_analyze)

    claude_parser = subparsers.add_parser("claude", help="Analyze local Claude Code sessions.")
    claude_subparsers = claude_parser.add_subparsers(dest="claude_command", required=True)

    claude_scan_parser = claude_subparsers.add_parser("scan", help="List recent Claude Code sessions.")
    claude_scan_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    claude_scan_parser.add_argument("--limit", type=int, default=10, help="Number of sessions to list.")
    claude_scan_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    claude_scan_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    claude_scan_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    claude_scan_parser.set_defaults(func=command_claude_scan)

    claude_explain_parser = claude_subparsers.add_parser("explain", help="Explain a Claude Code session from local JSONL.")
    claude_explain_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    claude_explain_parser.add_argument("--last", action="store_true", help="Explain the most recently updated Claude session.")
    claude_explain_parser.add_argument("--session-id", help="Explain a specific Claude session id or id prefix.")
    claude_explain_parser.add_argument("--session-file", help="Explain a specific Claude JSONL session file.")
    claude_explain_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this session.")
    claude_explain_parser.add_argument("--out", help="Write the explanation to this path. Uses JSON when --json is set.")
    claude_explain_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    claude_explain_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of console/Markdown output.")
    claude_explain_parser.set_defaults(func=command_claude_explain)

    claude_report_parser = claude_subparsers.add_parser("report", help="Write a Claude Code session HTML diagnosis report.")
    claude_report_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    claude_report_parser.add_argument("--last", action="store_true", help="Report on the most recently updated Claude session.")
    claude_report_parser.add_argument("--session-id", help="Report on a specific Claude session id or id prefix.")
    claude_report_parser.add_argument("--session-file", help="Report on a specific Claude JSONL session file.")
    claude_report_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this session.")
    claude_report_parser.add_argument(
        "--out",
        default="reports/claude-report.html",
        help="Write the HTML report to this path. Defaults to reports/claude-report.html.",
    )
    add_claude_price_args(claude_report_parser)
    claude_report_parser.set_defaults(func=command_claude_report)

    claude_overview_parser = claude_subparsers.add_parser("overview", help="Write a multi-session Claude Code HTML overview.")
    claude_overview_parser.add_argument("--claude-home", help="Claude home directory. Defaults to ~/.claude.")
    claude_overview_parser.add_argument("--limit", type=int, default=20, help="Number of sessions to include.")
    claude_overview_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    claude_overview_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    claude_overview_parser.add_argument(
        "--out",
        help="Write the overview to this path. Defaults to reports/claude-overview.html for HTML; JSON prints to stdout unless --out is set.",
    )
    claude_overview_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of writing HTML by default.")
    claude_overview_parser.add_argument(
        "--session-reports",
        action="store_true",
        help="Also write per-session Claude HTML reports and link to them from the overview.",
    )
    add_claude_price_args(claude_overview_parser)
    claude_overview_parser.set_defaults(func=command_claude_overview)

    claude_otel_parser = claude_subparsers.add_parser("import-otel", help="Analyze a Claude Code OpenTelemetry JSON/JSONL export.")
    claude_otel_parser.add_argument("trace", help="Path to a Claude Code OpenTelemetry JSON or JSONL export.")
    claude_otel_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this import.")
    claude_otel_parser.add_argument("--out", help="Write the report to this path. Uses JSON when --json is set, otherwise Markdown.")
    claude_otel_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    claude_otel_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of console/Markdown output.")
    claude_otel_parser.set_defaults(func=command_claude_import_otel)

    codex_parser = subparsers.add_parser("codex", help="Analyze local Codex Desktop/CLI sessions.")
    codex_subparsers = codex_parser.add_subparsers(dest="codex_command", required=True)

    codex_scan_parser = codex_subparsers.add_parser("scan", help="List recent Codex sessions.")
    codex_scan_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_scan_parser.add_argument("--limit", type=int, default=10, help="Number of sessions to list.")
    codex_scan_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    codex_scan_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    codex_scan_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of text.")
    codex_scan_parser.set_defaults(func=command_codex_scan)

    codex_explain_parser = codex_subparsers.add_parser("explain", help="Explain why a Codex session used tokens.")
    codex_explain_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_explain_parser.add_argument("--last", action="store_true", help="Explain the most recently updated Codex session.")
    codex_explain_parser.add_argument("--thread-id", help="Explain a specific Codex thread id or id prefix.")
    codex_explain_parser.add_argument("--out", help="Write the explanation to this path. Uses JSON when --json is set.")
    codex_explain_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of console output.")
    codex_explain_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console output.")
    add_codex_price_args(codex_explain_parser)
    codex_explain_parser.set_defaults(func=command_codex_explain)

    codex_report_parser = codex_subparsers.add_parser("report", help="Write a local HTML diagnosis report.")
    codex_report_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_report_parser.add_argument("--last", action="store_true", help="Report on the most recently updated Codex session.")
    codex_report_parser.add_argument("--thread-id", help="Report on a specific Codex thread id or id prefix.")
    codex_report_parser.add_argument(
        "--out",
        default="reports/codex-report.html",
        help="Write the HTML report to this path. Defaults to reports/codex-report.html.",
    )
    add_codex_price_args(codex_report_parser)
    codex_report_parser.set_defaults(func=command_codex_report)

    codex_overview_parser = codex_subparsers.add_parser("overview", help="Write a local HTML overview across recent sessions.")
    codex_overview_parser.add_argument("--codex-home", help="Codex home directory. Defaults to ~/.codex.")
    codex_overview_parser.add_argument("--limit", type=int, default=20, help="Number of recent sessions to analyze.")
    codex_overview_parser.add_argument("--cwd", help="Only include sessions whose working directory matches this project path.")
    codex_overview_parser.add_argument("--project", help="Only include sessions matching this project name or path.")
    codex_overview_parser.add_argument(
        "--cache-dir",
        default=".tokencause-cache/codex",
        help="Directory for parsed session cache. Defaults to .tokencause-cache/codex.",
    )
    codex_overview_parser.add_argument("--no-cache", action="store_true", help="Disable parsed session cache.")
    codex_overview_parser.add_argument(
        "--session-reports",
        action="store_true",
        help="Also write linked per-session HTML reports next to the overview.",
    )
    codex_overview_parser.add_argument(
        "--out",
        help="Write the overview to this path. Defaults to reports/codex-overview.html for HTML; JSON prints to stdout unless --out is set.",
    )
    codex_overview_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON instead of writing HTML by default.")
    add_codex_price_args(codex_overview_parser)
    codex_overview_parser.set_defaults(func=command_codex_overview)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
