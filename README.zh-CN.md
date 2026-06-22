# TokenCause

[![tests](https://github.com/happyaaa/tokencause/actions/workflows/test.yml/badge.svg)](https://github.com/happyaaa/tokencause/actions/workflows/test.yml)

[English](README.md)

诊断一次 AI 编程会话的 token 花得值不值，以及下次该怎么改。

TokenCause 是一个本地优先的 AI coding session diagnosis CLI。它不是只告诉你“花了多少”，而是解释 Claude Code、Codex 或其他 coding-agent session 为什么变贵、变慢、变危险、变得难以理解：重复上下文、超长命令输出、大文件、失败重试、宽泛探索、session drift、贵模型误用。

大多数 usage 工具告诉你花了多少。TokenCause 告诉你这次 session 的 token 花得值不值，为什么贵，以及下次应该沉淀成什么 workflow lesson。

![TokenCause demo dashboard](docs/assets/demo-dashboard.png)

## 为什么是 TokenCause

TokenCause 关注 usage accounting 工具通常回答不了的诊断层：

- 为什么这次 session 这么贵？
- 是哪些文件、命令、重试、重复上下文导致成本上升？
- 哪些 token 是必要探索，哪些来自可避免的 workflow 形状？
- 这个 repo 或 workflow 下一次应该记住什么 lesson？

## 它会检测什么

- **Repeated context**：同样的文件片段、prompt、工具输出、错误日志反复进入上下文。
- **Long tool output**：测试日志、构建日志、安装日志、grep 结果、命令输出占据大量 token。
- **Expensive files**：lockfile、generated file、大 JSON、fixture、snapshot、schema、minified asset。
- **Retry/failure cost**：失败 patch、重复测试、重复命令、retry loop。
- **Model mismatch**：search、read、route、summary、纯格式化任务用了昂贵模型。
- **Session drift**：会话后半段 token 越来越多，但有效进展变少。
- **Engineering process shape**：discovery-heavy、debug-heavy、implementation-without-verification、review-light 这类工程过程形状。
- **Review risk signals**：弱验证、敏感区域、大 review surface、上下文污染、retry loop、generated artifact 等风险信号。

## 快速开始

最快 demo，不需要本机已有 Codex 或 Claude Code 历史：

直接从源码运行：

```bash
git clone https://github.com/happyaaa/tokencause.git
cd tokencause
python3 tokencause.py serve --demo
```

或者先安装本地 CLI：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tokencause serve --demo
```

然后再分析真实本地 sessions：

```bash
tokencause doctor
tokencause serve
```

`serve` 会自动优先使用最近的本地 Codex sessions；如果没有 Codex session，就使用最近的 Claude Code sessions。它会把本地 dashboard site 写到 `reports/tokencause-site`，并默认在 `http://127.0.0.1:8787/` 启动。

如果你只想生成静态文件或 JSON，不想启动 server：

```bash
tokencause dashboard --session-reports
tokencause dashboard --json
```

`dashboard` 默认会写出本地 HTML dashboard：`reports/tokencause-dashboard.html`。

dashboard 开头会先给诊断，而不只是表格：最可能的 top cost driver、为什么发生、背后的 workflow pattern、process shape、risk signals、下一步动作，以及下一次应该复用的 workflow lesson。

单个 session report 会明确区分 token 口径：provider/model billed counters、observable transcript tokens、cache tokens，以及 TokenCause 的 estimated waste signal。estimated waste 是诊断信号，不是账单总额。

检查 TokenCause 能看到哪些本地数据源：

```bash
tokencause doctor
tokencause doctor --json
```

检查已安装 CLI 版本：

```bash
tokencause --version
```

如果你手上已经有单独的 JSONL trace，可以这样分析：

```bash
tokencause analyze examples/tokencause_trace.jsonl --budget 2
```

从通用 trace 直接输出 Markdown 报告：

```bash
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --markdown
```

输出机器可读 JSON，方便接脚本、CI 或你自己的 dashboard：

```bash
tokencause dashboard --json
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --json
```

所有 JSON 输出的根对象都会包含 `schema_version` 和 `version`，方便下游工具把数据结构变更和 CLI 版本区分开。输出契约见 [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md)。

不想启动 server，只想生成 demo/static artifacts：

```bash
tokencause dashboard --demo
tokencause demo-site
```

更多 demo 命令见 [docs/DEMO.md](docs/DEMO.md)。

## 示例输出

```text
TokenCause
input: examples/tokencause_trace.jsonl
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

当前报告是 diagnosis-first。dashboard 可以展示趋势，但最先有价值的问题通常是：为什么这次 session 变贵、这次探索值不值、下次应该改变什么？TokenCause 会保留 observability 数据层，然后把最大的 cost driver 翻译成 workflow diagnosis 和可复用 lesson。

## 当前输入

TokenCause 当前支持：

- Codex Desktop/CLI 本地会话。
- Claude Code 本地 JSONL 会话。
- Claude Code OpenTelemetry JSON/JSONL export。
- 通用 JSONL trace。

`examples/` 里的文件是假数据，只用于快速试跑 CLI。真实使用时应该指向你自己的 agent trace 日志。

## 当前状态

TokenCause 目前是 alpha，但已经可以用于本地诊断。现在最强的路径是 Codex local session analysis，其次是 Claude Code local sessions 和 Claude OpenTelemetry exports。Generic JSONL 是高级集成路径，但诊断深度取决于这些日志里是否包含 workflow metadata。

当前边界见 [docs/LIMITATIONS.md](docs/LIMITATIONS.md)，尤其是 billed/model tokens、observable transcript tokens、cache tokens、estimated waste 之间的区别。

## 隐私

TokenCause 是 local-first 工具。它只读取你传入路径里的本地 session 文件、trace export、可选价格配置文件，或标准 Codex/Claude 本地目录。它不会把对话数据、源码、trace 或报告上传到任何托管服务。

生成的报告和 `.tokencause-cache` 文件可能包含本地路径、命令输出片段和 session metadata。分享报告、缓存或用真实 trace 提 issue 前，请先看 [SECURITY.md](SECURITY.md)。

## Codex 会话

列出最近的本地 Codex 会话：

```bash
tokencause codex scan
tokencause codex scan --json
```

解释最近更新的会话：

```bash
tokencause codex explain --last
tokencause codex explain --last --json
```

解释指定 thread：

```bash
tokencause codex explain --thread-id 019eb90f
```

用你自己填写的 token 单价估算美元成本：

```bash
tokencause codex explain --last \
  --input-price-per-mtok 2.00 \
  --cached-input-price-per-mtok 0.50 \
  --output-price-per-mtok 8.00
```

也可以把价格放在本地 JSON 文件里：

```bash
cp examples/tokencause.prices.example.json tokencause.prices.json
tokencause codex overview --limit 20 --price-config tokencause.prices.json
```

生成本地 HTML 诊断报告：

```bash
tokencause codex report --last --out reports/codex-report.html
open reports/codex-report.html
```

生成最近多个 sessions 的本地 HTML 总览：

```bash
tokencause codex overview --limit 20 --session-reports --out reports/codex-overview.html
open reports/codex-overview.html
tokencause codex overview --limit 20 --json
```

`codex overview` 会把解析后的 session 缓存在 `.tokencause-cache/codex`，重复生成 overview 时不用每次重读所有 rollout 文件。每次运行后会打印 cache hit/miss 数量和解析耗时；需要强制重新解析时可以加 `--no-cache`。缓存文件只保存在本地并已被 gitignore，但仍可能包含敏感的本地诊断 metadata。

TokenCause 不会硬编码 Codex 模型价格，因为价格会变，而且本地 Codex rollout 不一定包含模型名。需要估算成本时，用上面的 price flags 或 `--price-config` 自己传入单价。CLI price flags 会覆盖配置文件。

Codex adapter 会读取 `~/.codex/state_5.sqlite` 找到 session metadata 和每个 session 的 rollout JSONL。它优先使用 Codex 自带的 token counters，然后做本地 transcript 分析：

- observable transcript token breakdown
- command output categories：test、build、install、search、other、error
- top files/artifacts
- repeated files/artifacts
- top commands
- repeated content chunks
- long tool outputs
- error-like outputs

HTML report 是本地 observability panel。`codex report` 看单个 session 的 summary、root-cause narrative、token attribution、估算 cost drivers、recommendations、usage counters、token category breakdown、top files/artifacts、top commands、repeated chunks。Top files 如果像 lockfile、generated file、schema artifact、fixture data、snapshot 或 minified asset，会直接标注风险原因。`codex overview` 会把最近多个 sessions 排序，聚合它们的主要 cost drivers，并给出跨 session recommendations；加上 `--session-reports` 后，每一行都能点进对应 session 的诊断页。
Overview 页面和 JSON 只展示 token 最高的前 20 个 sessions，但 cost-driver 聚合会覆盖所有已分析 sessions。

整个过程只在本地运行，不上传对话数据。

## Claude Code 会话

列出最近的本地 Claude Code 会话：

```bash
tokencause claude scan
tokencause claude scan --json
```

解释最近更新的 Claude 会话：

```bash
tokencause claude explain --last
tokencause claude explain --last --json
```

解释指定 JSONL 文件：

```bash
tokencause claude explain --session-file ~/.claude/projects/.../session.jsonl
```

生成本地 HTML 诊断面板：

```bash
tokencause claude report --last --out reports/claude-report.html
open reports/claude-report.html
```

生成多会话 overview：

```bash
tokencause claude overview --limit 20 --session-reports --out reports/claude-overview.html
open reports/claude-overview.html
tokencause claude overview --limit 20 --json
```

用你自己填写的 Claude token 单价估算美元成本：

```bash
cp examples/tokencause.prices.example.json tokencause.prices.json
tokencause claude overview --limit 20 --price-config tokencause.prices.json
```

分析 Claude Code OpenTelemetry JSON/JSONL export：

```bash
tokencause claude import-otel examples/claude_otel_sample.json --budget 1
```

如果要把通用 trace 或 Claude OpenTelemetry import 接到其他工具里，可以加 `--json` 输出结构化结果：

```bash
tokencause claude import-otel examples/claude_otel_sample.json --budget 1 --json
```

Claude adapter 会读取本地 `~/.claude/projects/*/*.jsonl` 文件。它会输出 Claude-specific cost drivers，包括 cache-heavy context、repeated parent context、大 tool results、repeated files/artifacts、以及贵模型用在疑似低价值步骤。需要通用 Markdown 报告时可以加 `--markdown`。`claude report` 会把同样的诊断做成本地 HTML 页面，包括 summary、cost drivers、recommendations、usage counters、tool/model breakdowns、top files/artifacts、repeated files/artifacts。Top files 如果像 lockfile、generated file、schema artifact、fixture data、snapshot 或 minified asset，会直接标注风险原因。`claude overview` 会按 token 量排序最近 sessions，聚合它们的主要 cost drivers，并给出跨 session recommendations。TokenCause 不会硬编码 Claude 价格；需要美元估算时，传 `--price-config` 或 Claude price flags。`claude import-otel` 支持 OTLP-style JSON/JSONL export，读取 `claude_code.token.usage`、`claude_code.cost.usage`，以及 tool result 这类 Claude log records；也支持更简单 collector 生成的 flat JSONL records。
Overview 页面和 JSON 只展示 token 最高的前 20 个 sessions，但 cost-driver 聚合会覆盖所有已分析 sessions。

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

也兼容一些常见别名，例如 `prompt_tokens`、`completion_tokens`、`inputTokens`、`outputTokens`、`duration_ms`、`model_name`。Token 字段也可以嵌在 `usage` 里。

## Roadmap

近期重点：

- 继续加深 Codex 和 Claude Code session diagnosis
- 把重复出现的诊断沉淀成 reusable workflow lessons
- 让 dashboard 更容易按项目扫描
- 改进文件 carryover 和 session drift 的证据表达
- 只在数据源能提供足够 trace metadata 时，再接入更多 AI coding session 来源

## 开发

本地 setup 和验证命令见 [CONTRIBUTING.md](CONTRIBUTING.md)，主要变化见 [CHANGELOG.md](CHANGELOG.md)。

运行测试：

```bash
python3 -m unittest discover -s tests
```

运行只依赖 `examples/` 的 smoke commands：

```bash
python3 tokencause.py analyze examples/tokencause_trace.jsonl --budget 2 --out reports/sample_report.md
python3 tokencause.py claude import-otel examples/claude_otel_sample.json --budget 1
```

如果这台机器上有 Codex 或 Claude Code 历史，再生成本地 session 报告：

```bash
python3 tokencause.py claude report --last --out reports/claude-report.html
python3 tokencause.py claude overview --limit 20 --session-reports --price-config examples/claude_prices.example.json --out reports/claude-overview.html
python3 tokencause.py codex report --last --out reports/codex-report.html
python3 tokencause.py codex overview --limit 20 --session-reports --out reports/codex-overview.html
```

## License

MIT
