# TokenCause

[![tests](https://github.com/happyaaa/tokencause/actions/workflows/test.yml/badge.svg)](https://github.com/happyaaa/tokencause/actions/workflows/test.yml)

[中文文档](README.zh-CN.md)

Diagnose whether an AI coding session was worth its tokens, and what to change next time.

TokenCause is a local-first diagnosis CLI for AI coding sessions. It explains not just where tokens went in Claude Code, Codex, and other coding-agent runs, but why the session became expensive, slow, risky, or hard to reason about: repeated context, long command output, expensive files, retry loops, broad exploration, session drift, and model mismatch.

Most usage tools tell you how much you spent. TokenCause tells you whether the session was worth its tokens, what made it expensive, and which workflow lesson to reuse next time.

![TokenCause demo dashboard](docs/assets/demo-dashboard.png)

## Why TokenCause

TokenCause focuses on the diagnostic layer that usage accounting tools usually do not answer:

- Why did this session get expensive?
- Which files, commands, retries, or repeated contexts drove the cost?
- Which tokens were necessary exploration, and which came from avoidable workflow shape?
- What lesson should this repo or workflow remember for the next AI coding session?

## What It Detects

- **Repeated context**: same file chunks, prompts, tool outputs, or errors repeatedly entering context.
- **Long tool output**: test logs, build logs, install logs, grep output, or command output dominating token spend.
- **Expensive files**: lockfiles, generated files, large JSON fixtures, snapshots, schemas, or minified assets.
- **Retry/failure cost**: failed patches, repeated tests, repeated commands, or retry loops.
- **Model mismatch**: expensive models used for search, read, route, summarize, or formatting-only work.
- **Session drift**: long sessions where later turns spend more tokens while making less progress.
- **Engineering process shape**: discovery-heavy, debug-heavy, implementation-without-verification, or review-light sessions.
- **Review risk signals**: weak verification, sensitive areas, large review surface, context pollution, retry loops, or generated artifacts.

## Quick Start

Fastest demo, with no local Codex or Claude Code history required:

Run from source:

```bash
git clone https://github.com/happyaaa/tokencause.git
cd tokencause
python3 tokencause.py serve --demo
```

Or install the local CLI first:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
tokencause serve --demo
```

Then point TokenCause at real local sessions:

```bash
tokencause doctor
tokencause serve
```

`serve` automatically uses recent local Codex sessions when available, otherwise recent Claude Code sessions. It writes a local dashboard site under `reports/tokencause-site` and serves it at `http://127.0.0.1:8787/` by default.

If you only want static files or JSON without running a server:

```bash
tokencause dashboard --session-reports
tokencause dashboard --json
```

`dashboard` writes a local HTML dashboard to `reports/tokencause-dashboard.html` by default.

The dashboard starts with a diagnosis, not just a table: the likely top cost driver, why it happened, the workflow pattern behind it, process shape, risk signals, the next action, and the reusable workflow lesson to carry into the next session.

Session reports separate token scopes: provider/model billed counters, observable transcript tokens, cache tokens, and TokenCause's estimated waste signal. Estimated waste is diagnostic, not a billing total.

Check what local sources TokenCause can see:

```bash
tokencause doctor
tokencause doctor --json
```

Check the installed CLI version:

```bash
tokencause --version
```

Analyze a standalone TokenCause session trace when you have one:

```bash
tokencause analyze examples/tokencause_trace.jsonl --budget 2
```

Print a Markdown report from a TokenCause session trace:

```bash
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --markdown
```

Print machine-readable JSON for scripts, CI, or your own dashboard:

```bash
tokencause dashboard --json
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --json
```

All JSON outputs include `schema_version` and `version` at the root so downstream tools can handle format changes separately from CLI releases. See [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md) for the output contract.

No server needed? Generate demo/static artifacts instead:

```bash
tokencause dashboard --demo
tokencause demo-site
```

More demo commands are in [docs/DEMO.md](docs/DEMO.md).

## Example Output

```text
TokenCause
input: examples/tokencause_trace.jsonl
events: 5
total cost: $2.5200
total tokens: 86100
total latency: 101.0s
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

The current reports are intentionally diagnosis-first. A dashboard can show trends, but the first useful question is usually: why did this session get expensive, was the exploration worth it, and what should change next time? TokenCause keeps the observability layer visible, then turns the largest cost driver into a workflow diagnosis and reusable lesson.

## Current Inputs

TokenCause currently supports:

- Codex Desktop/CLI local sessions.
- Claude Code local JSONL sessions.
- Claude Code OpenTelemetry JSON/JSONL export.
- TokenCause session trace JSONL for advanced integrations.

These examples are fake demo data for trying the CLI. In real usage, point TokenCause at your actual agent trace logs.

## Current Status

TokenCause is alpha but usable for local diagnosis. The strongest path today is Codex local session analysis, followed by Claude Code local sessions and Claude OpenTelemetry exports. TokenCause session trace JSONL is an advanced integration path, but it is only as diagnostic as the metadata it contains.

See [docs/LIMITATIONS.md](docs/LIMITATIONS.md) for current boundaries, including the difference between billed/model tokens, observable transcript tokens, cache tokens, and estimated waste.

## Privacy

TokenCause is local-first. It reads local session files, trace exports, and optional price config files from paths you pass in or from standard local Codex/Claude directories. It does not send conversation data, source code, traces, or reports to any hosted service.

Generated reports and `.tokencause-cache` files may contain local paths, command output previews, and session metadata. See [SECURITY.md](SECURITY.md) before sharing reports, caches, or filing issues with real traces.

## Codex Sessions

List recent local Codex sessions:

```bash
tokencause codex scan
tokencause codex scan --json
```

Explain the most recently updated session:

```bash
tokencause codex explain --last
tokencause codex explain --last --json
```

Explain a specific thread:

```bash
tokencause codex explain --thread-id 019eb90f
```

Estimate dollar cost with your own token prices:

```bash
tokencause codex explain --last \
  --input-price-per-mtok 2.00 \
  --cached-input-price-per-mtok 0.50 \
  --output-price-per-mtok 8.00
```

Or keep prices in a local JSON file:

```bash
cp examples/tokencause.prices.example.json tokencause.prices.json
tokencause codex overview --limit 20 --price-config tokencause.prices.json
```

Write a local HTML diagnosis report:

```bash
tokencause codex report --last --out reports/codex-report.html
open reports/codex-report.html
```

Write a local HTML overview across recent sessions:

```bash
tokencause codex overview --limit 20 --session-reports --out reports/codex-overview.html
open reports/codex-overview.html
tokencause codex overview --limit 20 --json
```

`codex overview` caches parsed sessions under `.tokencause-cache/codex` so repeated overview runs do not reparse every rollout file. It prints cache hit/miss counts and parse timing after each run. Use `--no-cache` to force a fresh parse. Cache files are local and gitignored, but they may contain sensitive local diagnosis metadata.

TokenCause does not hard-code Codex model prices because they change and local Codex rollout files may not include model names. Pass the price flags above, or `--price-config`, when you want an estimate. CLI price flags override the config file.

The Codex adapter reads `~/.codex/state_5.sqlite` to find session metadata and each session's rollout JSONL. It uses Codex token counters when present, then adds local transcript analysis for:

- observable transcript token breakdown
- command output categories: test, build, install, search, other, and error
- top files/artifacts
- repeated files/artifacts
- top commands
- repeated content chunks
- long tool outputs
- error-like outputs

The HTML report is the local observability panel. `codex report` shows one session's summary, root-cause narrative, token attribution, estimated cost drivers, recommendations, usage counters, token category breakdown, top files/artifacts, top commands, and repeated chunks. Top files are annotated when they look like lockfiles, generated files, schema artifacts, fixture data, snapshots, or minified assets. `codex overview` ranks recent sessions, groups their likely cost drivers, and includes cross-session recommendations; with `--session-reports`, each overview row links to a per-session diagnosis page.
Overview pages and JSON show the top 20 sessions as rows while aggregating cost-driver totals across every analyzed session.

This is local-only and does not upload conversation data.

## Claude Code Sessions

List recent local Claude Code sessions:

```bash
tokencause claude scan
tokencause claude scan --json
```

Explain the most recently updated Claude session:

```bash
tokencause claude explain --last
tokencause claude explain --last --json
```

Explain a specific JSONL file:

```bash
tokencause claude explain --session-file ~/.claude/projects/.../session.jsonl
```

Generate a local HTML diagnosis panel:

```bash
tokencause claude report --last --out reports/claude-report.html
open reports/claude-report.html
```

Generate a multi-session overview:

```bash
tokencause claude overview --limit 20 --session-reports --out reports/claude-overview.html
open reports/claude-overview.html
tokencause claude overview --limit 20 --json
```

Estimate Claude dollar cost with your own token prices:

```bash
cp examples/tokencause.prices.example.json tokencause.prices.json
tokencause claude overview --limit 20 --price-config tokencause.prices.json
```

Analyze a Claude Code OpenTelemetry JSON/JSONL export:

```bash
tokencause claude import-otel examples/claude_otel_sample.json --budget 1
```

Use `--json` with TokenCause traces or Claude OpenTelemetry imports when you want structured output for another tool:

```bash
tokencause claude import-otel examples/claude_otel_sample.json --budget 1 --json
```

The Claude adapter reads local `~/.claude/projects/*/*.jsonl` files. It reports Claude-specific cost drivers including cache-heavy context, repeated parent context, large tool results, repeated files/artifacts, and expensive models used on likely low-value steps. Use `--markdown` when you want the Markdown report instead of the diagnosis-first console output. `claude report` shows the same diagnosis as a local HTML page with summary, cost drivers, recommendations, usage counters, tool/model breakdowns, top files/artifacts, and repeated files/artifacts. Top files are annotated when they look like lockfiles, generated files, schema artifacts, fixture data, snapshots, or minified assets. `claude overview` ranks recent sessions by token volume, groups their likely cost drivers, and includes cross-session recommendations. TokenCause does not hard-code Claude prices; pass `--price-config` or the Claude price flags when you want dollar estimates. `claude import-otel` supports OTLP-style JSON/JSONL exports with `claude_code.token.usage`, `claude_code.cost.usage`, and Claude log records such as tool results; it also accepts flat JSONL records from simpler collectors.
Overview pages and JSON show the top 20 sessions as rows while aggregating cost-driver totals across every analyzed session.

## TokenCause Trace Format

Each line should be one JSON object:

```json
{"session_id":"abc","category":"tool_output","step":"test","model":"claude-sonnet-4","tool":"shell","command":"pytest tests/test_auth.py","tokens":2400,"input_tokens":1800,"output_tokens":600,"cost_usd":0.42,"latency_ms":18000,"content_hash":"failure-v1","file_refs":["tests/test_auth.py"],"preview":"pytest failed: timeout"}
```

Supported fields:

- `session_id`
- `category`
- `tokens`
- `preview`
- `command`
- `file_refs`
- `content_hash`
- `step`
- `model`
- `tool`
- `input_tokens`
- `output_tokens`
- `cost_usd`
- `latency_ms`
- `status`
- `error`

Common aliases are also supported, including `run_id`, `context_hash`, `context_items`, `files`, `prompt_tokens`, `completion_tokens`, `inputTokens`, `outputTokens`, `duration_ms`, and `model_name`. Token fields may also be nested under `usage`.

## Roadmap

Near-term work:

- deepen Codex and Claude Code session diagnosis
- promote repeated diagnosis into reusable workflow lessons
- make the dashboard easier to scan across projects
- improve source-level file carryover and drift evidence
- add more AI coding session sources when they expose enough trace metadata

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup and verification, and [CHANGELOG.md](CHANGELOG.md) for notable changes.

Run tests:

```bash
python3 -m unittest discover -s tests
```

Run example-only smoke commands:

```bash
python3 tokencause.py analyze examples/tokencause_trace.jsonl --budget 2 --out reports/sample_report.md
python3 tokencause.py claude import-otel examples/claude_otel_sample.json --budget 1
```

Generate local-session reports when you have Codex or Claude Code history on this machine:

```bash
python3 tokencause.py claude report --last --out reports/claude-report.html
python3 tokencause.py claude overview --limit 20 --session-reports --price-config examples/claude_prices.example.json --out reports/claude-overview.html
python3 tokencause.py codex report --last --out reports/codex-report.html
python3 tokencause.py codex overview --limit 20 --session-reports --out reports/codex-overview.html
```

## License

MIT
