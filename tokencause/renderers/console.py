"""Console and Markdown renderers for generic trace analysis."""

from pathlib import Path

from tokencause.core.casefile import build_session_case_file
from tokencause.core.formatting import money, seconds, top_items
from tokencause.core.models import Analysis, SessionTrace


def render_markdown(analysis: Analysis, source_path: Path, budget_usd: float | None, trace: SessionTrace | None = None) -> str:
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

    if trace is not None:
        case_file = build_session_case_file(trace)
        lines.extend(["## Engineering Process", ""])
        lines.append(f"- **Shape:** {case_file.process_summary.shape}")
        lines.append(f"- **Narrative:** {case_file.process_summary.narrative}")
        for phase in case_file.process_summary.phases:
            if phase.tokens <= 0 and phase.events <= 0:
                continue
            lines.append(f"- **{phase.name.replace('_', ' ').title()}:** {phase.tokens} tokens ({phase.share:.0%}), {phase.events} event(s)")
        lines.append("")

        lines.extend(["## Risk Signals", ""])
        if case_file.risk_signals:
            for risk in case_file.risk_signals:
                lines.append(f"- **{risk.name} ({risk.severity})**：{risk.why}")
                for item in risk.evidence[:2]:
                    lines.append(f"  - {item}")
        else:
            lines.append("- No high-signal risk detected.")
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
