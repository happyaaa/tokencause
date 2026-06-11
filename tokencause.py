#!/usr/bin/env python3
"""Analyze agent run traces for cost, latency, and context waste."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


KNOWN_EXPENSIVE_MODEL_HINTS = (
    "opus",
    "fable",
    "mythos",
    "gpt-5",
    "o3",
    "reasoning",
)

CHEAP_STEP_HINTS = (
    "search",
    "grep",
    "glob",
    "list",
    "read",
    "summarize",
    "classify",
    "route",
    "plan",
)


@dataclass
class TraceEvent:
    raw: dict[str, Any]
    index: int
    run_id: str = "default"
    step: str = "unknown"
    model: str = "unknown"
    tool: str = "none"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""
    context_hash: str = ""
    context_items: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Finding:
    title: str
    detail: str
    severity: str = "info"


@dataclass
class Recommendation:
    title: str
    detail: str
    estimated_savings_usd: float = 0.0


@dataclass
class Analysis:
    events: list[TraceEvent]
    total_cost: float
    total_tokens: int
    total_latency_ms: int
    cost_by_model: dict[str, float]
    cost_by_step: dict[str, float]
    tokens_by_model: dict[str, int]
    latency_by_step: dict[str, int]
    failures: list[TraceEvent]
    repeated_context: dict[str, int]
    repeated_items: dict[str, int]
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    estimated_savings_usd: float = 0.0


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def first_present(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def get_path(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current if current not in (None, "") else default


def first_path(data: dict[str, Any], paths: tuple[tuple[str, ...], ...], default: Any = None) -> Any:
    for path in paths:
        value = get_path(data, path, None)
        if value not in (None, ""):
            return value
    return default


def normalize_context_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def infer_litellm_step(raw: dict[str, Any]) -> str:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    litellm_params = raw.get("litellm_params") if isinstance(raw.get("litellm_params"), dict) else {}
    messages = first_path(raw, (("messages",), ("request", "messages"), ("kwargs", "messages")), [])
    if isinstance(messages, list):
        joined = " ".join(
            str(message.get("content", ""))
            for message in messages
            if isinstance(message, dict) and message.get("role") in ("system", "user")
        ).lower()
    else:
        joined = ""

    explicit = first_present(
        {**metadata, **litellm_params, **raw},
        ("step", "step_name", "name", "route", "call_type", "endpoint"),
        "",
    )
    if explicit:
        return str(explicit)
    for hint in CHEAP_STEP_HINTS:
        if hint in joined:
            return hint
    return "llm_call"


def parse_litellm_event(raw: dict[str, Any], index: int) -> TraceEvent:
    usage = first_path(raw, (("usage",), ("response", "usage"), ("modelResponse", "usage")), {})
    if not isinstance(usage, dict):
        usage = {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    model = first_path(
        raw,
        (
            ("model",),
            ("model_name",),
            ("standard_logging_object", "model"),
            ("response", "model"),
            ("litellm_params", "model"),
        ),
        "unknown",
    )
    cost = first_path(
        raw,
        (
            ("response_cost",),
            ("cost",),
            ("cost_usd",),
            ("spend",),
            ("standard_logging_object", "response_cost"),
            ("standard_logging_object", "cost"),
        ),
        0.0,
    )
    latency = first_path(
        raw,
        (
            ("latency_ms",),
            ("duration_ms",),
            ("response_ms",),
            ("standard_logging_object", "response_ms"),
        ),
        0,
    )
    return TraceEvent(
        raw=raw,
        index=index,
        run_id=str(
            first_present(
                {**metadata, **raw},
                ("run_id", "trace_id", "session_id", "user_api_key", "user_id", "end_user"),
                "default",
            )
        ),
        step=infer_litellm_step(raw),
        model=str(model),
        tool=str(first_present({**metadata, **raw}, ("tool", "tool_name", "call_type"), "llm")),
        input_tokens=as_int(first_present(raw, ("input_tokens", "prompt_tokens"), usage.get("prompt_tokens", 0))),
        output_tokens=as_int(
            first_present(raw, ("output_tokens", "completion_tokens"), usage.get("completion_tokens", 0))
        ),
        cost_usd=as_float(cost),
        latency_ms=as_int(latency),
        status=str(first_present(raw, ("status", "outcome", "response_status"), "ok")).lower(),
        error=str(first_present(raw, ("error", "error_message", "exception", "failure_reason"), "")),
        context_hash=str(first_present({**metadata, **raw}, ("context_hash", "prompt_hash", "request_hash"), "")),
        context_items=normalize_context_items(first_present({**metadata, **raw}, ("context_items", "files", "documents"), None)),
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
        input_tokens=as_int(first_present(raw, ("input_tokens", "prompt_tokens"), usage.get("prompt_tokens", 0))),
        output_tokens=as_int(
            first_present(raw, ("output_tokens", "completion_tokens"), usage.get("completion_tokens", 0))
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
            if parser == "litellm":
                events.append(parse_litellm_event(raw, index))
            else:
                events.append(parse_event(raw, index))
    return events


def analyze(events: list[TraceEvent], budget_usd: float | None = None) -> Analysis:
    cost_by_model: dict[str, float] = defaultdict(float)
    cost_by_step: dict[str, float] = defaultdict(float)
    tokens_by_model: dict[str, int] = defaultdict(int)
    latency_by_step: dict[str, int] = defaultdict(int)
    context_counter: Counter[str] = Counter()
    item_counter: Counter[str] = Counter()
    failures: list[TraceEvent] = []

    for event in events:
        cost_by_model[event.model] += event.cost_usd
        cost_by_step[event.step] += event.cost_usd
        tokens_by_model[event.model] += event.total_tokens
        latency_by_step[event.step] += event.latency_ms
        if event.context_hash:
            context_counter[event.context_hash] += 1
        for item in event.context_items:
            item_counter[item] += 1
        if event.status not in ("ok", "success", "completed") or event.error:
            failures.append(event)

    analysis = Analysis(
        events=events,
        total_cost=sum(event.cost_usd for event in events),
        total_tokens=sum(event.total_tokens for event in events),
        total_latency_ms=sum(event.latency_ms for event in events),
        cost_by_model=dict(sorted(cost_by_model.items(), key=lambda row: row[1], reverse=True)),
        cost_by_step=dict(sorted(cost_by_step.items(), key=lambda row: row[1], reverse=True)),
        tokens_by_model=dict(sorted(tokens_by_model.items(), key=lambda row: row[1], reverse=True)),
        latency_by_step=dict(sorted(latency_by_step.items(), key=lambda row: row[1], reverse=True)),
        failures=failures,
        repeated_context={key: count for key, count in context_counter.items() if count > 1},
        repeated_items={key: count for key, count in item_counter.items() if count > 1},
    )
    analysis.findings = build_findings(analysis, budget_usd)
    analysis.recommendations = build_recommendations(analysis)
    cap_overlapping_savings(analysis)
    return analysis


def cap_overlapping_savings(analysis: Analysis) -> None:
    raw_savings = sum(item.estimated_savings_usd for item in analysis.recommendations)
    if raw_savings <= 0 or analysis.total_cost <= 0:
        analysis.estimated_savings_usd = 0.0
        return
    # Recommendations often overlap: repeated context can also be part of the most expensive step.
    # Cap the first-pass estimate to keep the report conservative.
    cap = analysis.total_cost * 0.75
    if raw_savings <= cap:
        analysis.estimated_savings_usd = raw_savings
        return
    scale = cap / raw_savings
    for recommendation in analysis.recommendations:
        recommendation.estimated_savings_usd *= scale
    analysis.estimated_savings_usd = cap


def build_findings(analysis: Analysis, budget_usd: float | None) -> list[Finding]:
    findings: list[Finding] = []
    events = analysis.events
    if not events:
        return [Finding("没有可分析事件", "输入 trace 为空。", "warning")]

    if budget_usd is not None and analysis.total_cost > budget_usd:
        findings.append(
            Finding(
                "超过预算",
                f"本次运行成本 ${analysis.total_cost:.4f}，超过预算 ${budget_usd:.4f}。",
                "warning",
            )
        )

    if analysis.total_cost > 0:
        top_step, top_cost = next(iter(analysis.cost_by_step.items()))
        share = top_cost / analysis.total_cost
        if share >= 0.5:
            findings.append(
                Finding(
                    "成本集中在单一步骤",
                    f"`{top_step}` 占总成本 {share:.0%}，优先检查这个步骤的模型选择和上下文大小。",
                    "warning",
                )
            )

    expensive_on_cheap = [
        event
        for event in events
        if any(hint in event.model.lower() for hint in KNOWN_EXPENSIVE_MODEL_HINTS)
        and any(hint in f"{event.step} {event.tool}".lower() for hint in CHEAP_STEP_HINTS)
    ]
    if expensive_on_cheap:
        sample = expensive_on_cheap[0]
        findings.append(
            Finding(
                "昂贵模型可能用于低价值步骤",
                f"例如第 {sample.index} 行 `{sample.step}` / `{sample.tool}` 使用 `{sample.model}`。搜索、路由、摘要类步骤通常可先尝试便宜模型。",
                "warning",
            )
        )

    if analysis.repeated_context:
        count = sum(value - 1 for value in analysis.repeated_context.values())
        findings.append(
            Finding(
                "发现重复上下文",
                f"有 {len(analysis.repeated_context)} 个 context_hash 被重复使用，额外重复出现 {count} 次。可以考虑缓存摘要或裁剪重复 context。",
                "info",
            )
        )

    if analysis.repeated_items:
        repeated = sorted(analysis.repeated_items.items(), key=lambda row: row[1], reverse=True)[:3]
        names = ", ".join(f"{name} x{count}" for name, count in repeated)
        findings.append(
            Finding(
                "文件/文档被反复塞入上下文",
                f"重复最多的是：{names}。检查这些内容是否应该压缩成稳定摘要。",
                "info",
            )
        )

    if analysis.failures:
        findings.append(
            Finding(
                "存在失败步骤",
                f"发现 {len(analysis.failures)} 个失败/异常事件。失败重试可能造成隐性成本。",
                "warning",
            )
        )

    latencies = [event.latency_ms for event in events if event.latency_ms > 0]
    if len(latencies) >= 3:
        median = statistics.median(latencies)
        slow = [event for event in events if event.latency_ms > median * 3 and event.latency_ms > 5_000]
        if slow:
            sample = slow[0]
            findings.append(
                Finding(
                    "存在明显慢步骤",
                    f"第 {sample.index} 行 `{sample.step}` 耗时 {sample.latency_ms / 1000:.1f}s，显著高于中位数 {median / 1000:.1f}s。",
                    "info",
                )
            )

    if not findings:
        findings.append(Finding("未发现明显浪费", "当前 trace 没有触发成本、延迟或重复上下文规则。", "info"))
    return findings


def is_expensive_model(model: str) -> bool:
    return any(hint in model.lower() for hint in KNOWN_EXPENSIVE_MODEL_HINTS)


def is_low_value_step(event: TraceEvent) -> bool:
    return any(hint in f"{event.step} {event.tool}".lower() for hint in CHEAP_STEP_HINTS)


def build_recommendations(analysis: Analysis) -> list[Recommendation]:
    recommendations: list[Recommendation] = []
    if not analysis.events or analysis.total_cost <= 0:
        return recommendations

    expensive_low_value = [
        event for event in analysis.events if event.cost_usd > 0 and is_expensive_model(event.model) and is_low_value_step(event)
    ]
    if expensive_low_value:
        cost = sum(event.cost_usd for event in expensive_low_value)
        steps = sorted({event.step for event in expensive_low_value})[:5]
        recommendations.append(
            Recommendation(
                "把低风险步骤降级到便宜模型",
                f"`{', '.join(steps)}` 这类步骤用了昂贵模型。优先把 search/read/route/summary 切到 mini/Haiku 级别模型，再保留主推理步骤使用强模型。",
                cost * 0.55,
            )
        )

    repeated_context_events = [
        event for event in analysis.events if event.context_hash and analysis.repeated_context.get(event.context_hash, 0) > 1
    ]
    if repeated_context_events:
        duplicate_cost = 0.0
        seen: set[str] = set()
        for event in repeated_context_events:
            if event.context_hash in seen:
                duplicate_cost += event.cost_usd
            else:
                seen.add(event.context_hash)
        if duplicate_cost > 0:
            recommendations.append(
                Recommendation(
                    "缓存重复上下文或稳定摘要",
                    "同一个 `context_hash` 在一次 run 中重复出现。可以缓存 context pack、文件摘要或 retrieval 结果，避免每轮重新塞完整上下文。",
                    duplicate_cost * 0.65,
                )
            )

    if analysis.failures:
        failure_cost = sum(event.cost_usd for event in analysis.failures)
        if failure_cost > 0:
            recommendations.append(
                Recommendation(
                    "给失败重试加预算护栏",
                    "失败事件已经产生真实成本。建议按 run 设置 max retries、per-step budget，并在连续失败后降级为人工确认或更小上下文重试。",
                    failure_cost * 0.8,
                )
            )

    if analysis.repeated_items:
        repeated_event_cost = sum(
            event.cost_usd
            for event in analysis.events
            if any(item in analysis.repeated_items for item in event.context_items)
        )
        if repeated_event_cost > 0:
            recommendations.append(
                Recommendation(
                    "把反复读取的文件压缩成 memo",
                    "有文件/文档被多次放入上下文。对 README、schema、配置文件这类稳定内容生成 memo，后续步骤引用 memo 而不是原文。",
                    repeated_event_cost * 0.25,
                )
            )

    top_steps = list(analysis.cost_by_step.items())[:1]
    if top_steps:
        step, cost = top_steps[0]
        if cost / analysis.total_cost >= 0.4:
            recommendations.append(
                Recommendation(
                    "先优化最贵步骤",
                    f"`{step}` 是当前最大成本来源。先对这个步骤做 prompt 裁剪、上下文上限和模型路由，收益会比平均优化所有步骤更高。",
                    float(cost) * 0.2,
                )
            )

    return sorted(recommendations, key=lambda item: item.estimated_savings_usd, reverse=True)[:5]


def money(value: float) -> str:
    return f"${value:.4f}"


def seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


def top_items(mapping: dict[str, float | int], limit: int = 5) -> list[tuple[str, float | int]]:
    return list(mapping.items())[:limit]


def render_markdown(analysis: Analysis, source_path: Path, budget_usd: float | None) -> str:
    projected_cost = max(analysis.total_cost - analysis.estimated_savings_usd, 0.0)
    lines = [
        "# TokenCause Report",
        "",
        f"- 输入文件：`{source_path}`",
        f"- 事件数：{len(analysis.events)}",
        f"- 总成本：{money(analysis.total_cost)}",
        f"- 总 token：{analysis.total_tokens}",
        f"- 总耗时：{seconds(analysis.total_latency_ms)}",
        f"- 粗略可省：{money(analysis.estimated_savings_usd)}",
        f"- 优化后估算：{money(projected_cost)}",
    ]
    if budget_usd is not None:
        lines.append(f"- 预算：{money(budget_usd)}")
    lines.append("")

    lines.extend(["## 主要发现", ""])
    for finding in analysis.findings:
        lines.append(f"- **[{finding.severity}] {finding.title}**：{finding.detail}")
    lines.append("")

    lines.extend(["## 优先降本动作", ""])
    if analysis.recommendations:
        for index, recommendation in enumerate(analysis.recommendations, start=1):
            lines.append(
                f"{index}. **{recommendation.title}**：{recommendation.detail} 预计节省 {money(recommendation.estimated_savings_usd)}。"
            )
    else:
        lines.append("- 暂无明确降本动作。")
    lines.append("")

    lines.extend(["## 成本按模型", ""])
    for model, cost in top_items(analysis.cost_by_model):
        tokens = analysis.tokens_by_model.get(model, 0)
        lines.append(f"- `{model}`：{money(float(cost))}，{tokens} tokens")
    lines.append("")

    lines.extend(["## 成本按步骤", ""])
    for step, cost in top_items(analysis.cost_by_step):
        lines.append(f"- `{step}`：{money(float(cost))}")
    lines.append("")

    lines.extend(["## 最慢步骤", ""])
    for step, latency in top_items(analysis.latency_by_step):
        lines.append(f"- `{step}`：{seconds(int(latency))}")
    lines.append("")

    if analysis.failures:
        lines.extend(["## 失败事件", ""])
        for event in analysis.failures[:10]:
            message = event.error or event.status
            lines.append(f"- 第 {event.index} 行 `{event.step}` / `{event.model}`：{message}")
        lines.append("")

    return "\n".join(lines)


def render_console(analysis: Analysis, source_path: Path, budget_usd: float | None) -> str:
    lines = [
        "TokenCause",
        f"input: {source_path}",
        f"events: {len(analysis.events)}",
        f"total cost: {money(analysis.total_cost)}",
        f"total tokens: {analysis.total_tokens}",
        f"total latency: {seconds(analysis.total_latency_ms)}",
        f"estimated savings: {money(analysis.estimated_savings_usd)}",
    ]
    if budget_usd is not None:
        lines.append(f"budget: {money(budget_usd)}")
    lines.append("")
    lines.append("findings:")
    for finding in analysis.findings:
        lines.append(f"- [{finding.severity}] {finding.title}: {finding.detail}")
    if analysis.recommendations:
        lines.append("")
        lines.append("recommended actions:")
        for recommendation in analysis.recommendations:
            lines.append(f"- {recommendation.title}: save about {money(recommendation.estimated_savings_usd)}")
    return "\n".join(lines)


def run_analysis_command(args: argparse.Namespace, parser_name: str) -> int:
    trace_path = Path(args.trace)
    try:
        events = load_jsonl(trace_path, parser=parser_name)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    analysis = analyze(events, args.budget)
    report = render_markdown(analysis, trace_path, args.budget)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")

    if args.markdown:
        print(report)
    else:
        print(render_console(analysis, trace_path, args.budget))
        if args.out:
            print(f"\nmarkdown report: {args.out}")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    return run_analysis_command(args, "generic")


def command_analyze_litellm(args: argparse.Namespace) -> int:
    return run_analysis_command(args, "litellm")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokencause",
        description="Analyze agent run traces for cost, latency, failures, and context waste.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a JSONL trace file.")
    analyze_parser.add_argument("trace", help="Path to a JSONL trace file.")
    analyze_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this run.")
    analyze_parser.add_argument("--out", help="Write a Markdown report to this path.")
    analyze_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    analyze_parser.set_defaults(func=command_analyze)

    litellm_parser = subparsers.add_parser("analyze-litellm", help="Analyze LiteLLM proxy/log JSONL.")
    litellm_parser.add_argument("trace", help="Path to a LiteLLM JSONL log file.")
    litellm_parser.add_argument("--budget", type=float, default=None, help="Optional budget in USD for this run.")
    litellm_parser.add_argument("--out", help="Write a Markdown report to this path.")
    litellm_parser.add_argument("--markdown", action="store_true", help="Print Markdown instead of console summary.")
    litellm_parser.set_defaults(func=command_analyze_litellm)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
