# Demo

TokenCause is most useful with real local Codex or Claude Code sessions:

```bash
tokencause serve
tokencause codex explain --last
tokencause claude explain --last
```

If you do not have local coding-agent history on the current machine, generate a synthetic dashboard:

```bash
tokencause serve --demo
```

This serves a local dashboard and linked session reports from three fake Codex-style sessions, without reading `~/.codex` or `~/.claude`. The demo intentionally shows multiple cost patterns: long retry/test output, environment setup failure, and broad workspace exploration.

For static demo files instead of a server:

```bash
tokencause dashboard --demo
open reports/tokencause-dashboard.html
```

For a self-contained demo directory:

```bash
tokencause demo-site
open reports/tokencause-demo-site/index.html
```

You can also use the checked-in synthetic examples below to verify the CLI and see the diagnosis style.

## Generic Agent Trace

```bash
tokencause analyze examples/tokencause_trace.jsonl --budget 2
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --json | python -m json.tool
```

Expected signal:

- repeated context across planning, search, fix, and test steps
- expensive model usage on lower-value steps
- failed test step with retry-budget recommendations

## Claude OpenTelemetry Export

```bash
tokencause claude import-otel examples/claude_otel_sample.json --budget 1
tokencause claude import-otel examples/claude_otel_sample.json --budget 1 --json | python -m json.tool
```

Expected signal:

- Claude token and cost metrics
- tool result log records
- file references from OTLP attributes

## Local Dashboard

The real dashboard requires local Codex or Claude Code history:

```bash
tokencause doctor
tokencause serve
```

For a static artifact instead of a server:

```bash
tokencause dashboard --session-reports
```

Generated reports are intentionally gitignored because they may contain local paths, command previews, and session metadata.
