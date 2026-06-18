"""Generic usage accounting and budget recommendations."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict

from .models import Analysis, Finding, Recommendation, TraceEvent


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
    "format",
    "formatter",
)


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
    # Recommendations can overlap, so keep the first-pass savings estimate conservative.
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
