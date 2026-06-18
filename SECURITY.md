# Security and Privacy

TokenCause is local-first. It analyzes files already present on your machine and does not upload traces, source code, prompts, reports, or price configs to a hosted service.

## Local Data It May Read

Depending on the command, TokenCause may read:

- Codex session metadata and rollout files under `~/.codex`.
- Claude Code session JSONL files under `~/.claude/projects`.
- Trace files you pass explicitly, such as OpenTelemetry JSON/JSONL exports.
- Optional local price config files.

## Sensitive Outputs

Console output, JSON output, Markdown reports, and HTML reports can include:

- local file paths
- command names
- command output previews
- error messages
- repeated content previews
- session IDs and project names

`codex overview` may also write parsed diagnosis caches under `.tokencause-cache/codex`. These cache files can contain the same kinds of local paths, command previews, repeated-content previews, and session metadata as generated reports.

Treat generated reports and `.tokencause-cache` files as sensitive local artifacts. Do not paste full reports, cache files, rollout files, Claude session files, or source excerpts into public issues.

## Reporting Issues Safely

When filing an issue:

- Prefer a minimal synthetic trace that reproduces the problem.
- Redact file paths, session IDs, API keys, customer names, and proprietary code.
- If a bug needs real data shape context, include only the smallest relevant JSON object with sensitive values replaced.

Good:

```json
{"type":"response_item","payload":{"type":"function_call_output","output":"ERROR <redacted stack>"}}
```

Avoid:

```text
Full rollout JSONL, full terminal logs, private source files, or full Claude/Codex sessions.
```

## Network Behavior

TokenCause itself does not require network access for analysis. Package installation, GitHub Actions, or dependency installation may use the network, but the CLI does not send analyzed content anywhere.

## Supported Versions

TokenCause is currently alpha software. Security and privacy fixes should target the latest `main` branch until a formal release process exists.
