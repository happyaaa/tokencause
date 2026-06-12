# TokenCause

[English](README.md)

诊断你的 AI 编程会话为什么烧 token。

TokenCause 是一个本地优先的 token 成本 root-cause analysis CLI。它不是只告诉你“花了多少”，而是解释 Claude Code、Codex、LiteLLM 或其他 AI coding / agent session 里的 token 到底花在了哪里：重复上下文、超长命令输出、大文件、失败重试、贵模型误用。

大多数 usage 工具告诉你花了多少。TokenCause 告诉你为什么。

## 为什么是 TokenCause

[ccusage](https://github.com/ryoppippi/ccusage) 这类工具很适合做本地 coding agent CLI 的 usage accounting。它回答的是：

- 今天用了多少 token？
- 哪个 coding CLI 用得最多？
- 哪些 session、日期、项目最贵？

TokenCause 关注下一层：

- 为什么这次 session 这么贵？
- 是哪些文件、命令、重试、重复上下文导致成本上升？
- 下一次应该怎么改 workflow，避免同样的 token 浪费？

简单说：

```text
ccusage    -> usage accounting
TokenCause -> cost root-cause analysis
```

## 它会检测什么

- **Repeated context**：同样的文件片段、prompt、工具输出、错误日志反复进入上下文。
- **Long tool output**：测试日志、构建日志、安装日志、grep 结果、命令输出占据大量 token。
- **Expensive files**：lockfile、generated file、大 JSON、fixture、snapshot、schema、minified asset。
- **Retry/failure cost**：失败 patch、重复测试、重复命令、retry loop。
- **Model mismatch**：search、read、route、summary、纯格式化任务用了昂贵模型。
- **Session drift**：会话后半段 token 越来越多，但有效进展变少。

## 快速开始

直接从源码运行：

```bash
git clone https://github.com/happyaaa/tokencause.git
cd tokencause
python3 tokencause.py analyze examples/sample_trace.jsonl --budget 2
```

或者安装本地 CLI：

```bash
python3 -m pip install -e .
tokencause analyze examples/sample_trace.jsonl --budget 2
```

分析 LiteLLM JSONL 日志：

```bash
tokencause analyze-litellm examples/litellm_sample.jsonl --budget 2 --out reports/litellm_report.md
```

直接输出 Markdown 报告：

```bash
tokencause analyze examples/sample_trace.jsonl --budget 2 --markdown
```

## 示例输出

```text
TokenCause
input: examples/litellm_sample.jsonl
events: 5
total cost: $2.5200
total tokens: 67600
total latency: 86.0s
estimated savings: $1.8900
budget: $2.0000

findings:
- [warning] 超过预算
- [warning] 昂贵模型可能用于低价值步骤
- [info] 发现重复上下文
- [warning] 存在失败步骤

recommended actions:
- 把低风险步骤降级到便宜模型
- 缓存重复上下文或稳定摘要
- 把反复读取的文件压缩成 memo
- 给失败重试加预算护栏
```

当前报告是 diagnosis-first。dashboard 可以展示趋势，但最先有价值的问题通常是：为什么这次 session 变贵？

## 当前输入

TokenCause 当前支持：

- Codex Desktop/CLI 本地会话。
- 通用 JSONL trace。
- LiteLLM proxy/log JSONL。

`examples/` 里的文件是假数据，只用于快速试跑 CLI。真实使用时应该指向你自己的 LiteLLM 或 agent trace 日志。

## Codex 会话

列出最近的本地 Codex 会话：

```bash
tokencause codex scan
```

解释最近更新的会话：

```bash
tokencause codex explain --last
```

解释指定 thread：

```bash
tokencause codex explain --thread-id 019eb90f
```

Codex adapter 会读取 `~/.codex/state_5.sqlite` 找到 session metadata 和每个 session 的 rollout JSONL。它优先使用 Codex 自带的 token counters，然后做本地 transcript 分析：

- observable transcript token breakdown
- top files/artifacts
- top commands
- repeated content chunks
- long tool outputs
- error-like outputs

整个过程只在本地运行，不上传对话数据。

## 通用 Trace 格式

每行一个 JSON object：

```json
{"run_id":"abc","step":"plan","model":"claude-sonnet-4","tool":"none","input_tokens":12000,"output_tokens":900,"cost_usd":0.42,"latency_ms":18000,"context_hash":"repo-v1","context_items":["README.md","src/auth.py"]}
```

支持字段：

- `run_id`
- `step`
- `model`
- `tool`
- `input_tokens`
- `output_tokens`
- `cost_usd`
- `latency_ms`
- `status`
- `error`
- `context_hash`
- `context_items`
- `files`

也兼容一些常见别名，例如 `prompt_tokens`、`completion_tokens`、`duration_ms`、`model_name`。

## LiteLLM 日志

使用：

```bash
tokencause analyze-litellm path/to/litellm.jsonl --budget 10
```

LiteLLM adapter 会读取：

- `model` / `model_name`
- `response_cost` / `cost` / `spend`
- `usage.prompt_tokens`
- `usage.completion_tokens`
- `duration_ms` / `latency_ms`
- `metadata.run_id`
- `metadata.step`
- `metadata.tool`
- `metadata.context_hash`
- `metadata.context_items`
- `status`
- `error_message`

如果你的 LiteLLM 日志里还没有 `metadata.step`、`metadata.context_hash` 或 `metadata.context_items`，TokenCause 仍然可以分析总花费。但如果要定位“哪个 agent 步骤浪费 token”，建议在调用 LiteLLM 时把这些字段写进 metadata。

## Roadmap

当前产品计划和 milestone 拆解见 [docs/PLAN.md](docs/PLAN.md)。

计划中的 analyzers：

- `RepeatedContextAnalyzer`
- `LongToolOutputAnalyzer`
- `ExpensiveFileAnalyzer`
- `RetryCostAnalyzer`
- `ModelMismatchAnalyzer`
- `SessionDriftAnalyzer`

后续数据源：

- Claude Code local logs。
- Claude Code OpenTelemetry export。
- LangSmith export。
- ccusage JSON output import。

## 开发

运行测试：

```bash
python3 -m unittest discover -s tests
```

生成示例报告：

```bash
python3 tokencause.py analyze examples/sample_trace.jsonl --budget 2 --out reports/sample_report.md
python3 tokencause.py analyze-litellm examples/litellm_sample.jsonl --budget 2 --out reports/litellm_report.md
```

## License

MIT
