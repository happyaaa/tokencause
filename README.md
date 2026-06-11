# TokenCause

[中文文档](README.zh-CN.md)

Find why your AI coding session got expensive.

TokenCause is a local-first CLI for token cost root-cause analysis. It helps explain where tokens went in Claude Code, Codex, LiteLLM, and other AI coding or agent sessions: repeated context, long command output, expensive files, retry loops, and model mismatch.

Most usage tools tell you how much you spent. TokenCause tells you why.

## Why TokenCause

Tools like [ccusage](https://github.com/ryoppippi/ccusage) are excellent for local usage accounting across coding agent CLIs. They answer questions like:

- How many tokens did I use today?
- Which coding CLI used the most tokens?
- Which sessions, days, or projects were most expensive?

TokenCause focuses on the next layer:

- Why did this session get expensive?
- Which files, commands, retries, or repeated contexts drove the cost?
- What would I change in the workflow to avoid the same cost pattern next time?

In short:

```text
ccusage    -> usage accounting
TokenCause -> cost root-cause analysis
```

## What It Detects

- **Repeated context**: same file chunks, prompts, tool outputs, or errors repeatedly entering context.
- **Long tool output**: test logs, build logs, install logs, grep output, or command output dominating token spend.
- **Expensive files**: lockfiles, generated files, large JSON fixtures, snapshots, schemas, or minified assets.
- **Retry/failure cost**: failed patches, repeated tests, repeated commands, or retry loops.
- **Model mismatch**: expensive models used for search, read, route, summarize, or formatting-only work.
- **Session drift**: long sessions where later turns spend more tokens while making less progress.

## Quick Start

Run from source:

```bash
git clone https://github.com/happyaaa/tokencause.git
cd tokencause
python3 tokencause.py analyze examples/sample_trace.jsonl --budget 2
```

Or install the local CLI:

```bash
python3 -m pip install -e .
tokencause analyze examples/sample_trace.jsonl --budget 2
```

Analyze a LiteLLM JSONL log:

```bash
tokencause analyze-litellm examples/litellm_sample.jsonl --budget 2 --out reports/litellm_report.md
```

Print a Markdown report:

```bash
tokencause analyze examples/sample_trace.jsonl --budget 2 --markdown
```

## Example Output

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
- [warning] Budget exceeded
- [warning] Expensive model may be used for low-value steps
- [info] Repeated context detected
- [warning] Failed steps detected

recommended actions:
- Downgrade low-risk steps to cheaper models
- Cache repeated context or stable summaries
- Compress repeatedly-read files into memos
- Add budget guards to retries
```

The current reports are intentionally diagnosis-first. A dashboard can show trends, but the first useful question is usually: why did this session get expensive?

## Current Inputs

TokenCause currently supports:

- Generic JSONL traces.
- LiteLLM proxy/log JSONL.

These examples are fake demo data for trying the CLI. In real usage, point TokenCause at your actual LiteLLM or agent trace logs.

## Generic Trace Format

Each line should be one JSON object:

```json
{"run_id":"abc","step":"plan","model":"claude-sonnet-4","tool":"none","input_tokens":12000,"output_tokens":900,"cost_usd":0.42,"latency_ms":18000,"context_hash":"repo-v1","context_items":["README.md","src/auth.py"]}
```

Supported fields:

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

Common aliases are also supported, including `prompt_tokens`, `completion_tokens`, `duration_ms`, and `model_name`.

## LiteLLM Logs

Use:

```bash
tokencause analyze-litellm path/to/litellm.jsonl --budget 10
```

The LiteLLM adapter reads:

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

If your LiteLLM logs do not include `metadata.step`, `metadata.context_hash`, or `metadata.context_items`, TokenCause can still analyze total spend. To diagnose token waste by agent workflow step, pass those values into LiteLLM metadata when making calls.

## Roadmap

Milestone 1 is Codex Session Doctor:

```bash
tokencause codex scan
tokencause codex explain --last
tokencause codex report --out dashboard.html
```

Planned analyzers:

- `RepeatedContextAnalyzer`
- `LongToolOutputAnalyzer`
- `ExpensiveFileAnalyzer`
- `RetryCostAnalyzer`
- `ModelMismatchAnalyzer`
- `SessionDriftAnalyzer`

Future sources:

- Codex session JSONL.
- Claude Code local logs.
- Claude Code OpenTelemetry export.
- LangSmith export.
- ccusage JSON output import.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Generate example reports:

```bash
python3 tokencause.py analyze examples/sample_trace.jsonl --budget 2 --out reports/sample_report.md
python3 tokencause.py analyze-litellm examples/litellm_sample.jsonl --budget 2 --out reports/litellm_report.md
```

## License

MIT
