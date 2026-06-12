# TokenCause Plan

TokenCause is a local-first cost root-cause tool for AI coding sessions.

The product should not compete with usage-accounting tools on "how much did I spend?" Its job is to explain why a session got expensive.

```text
ccusage    -> usage accounting
TokenCause -> cost root-cause analysis
```

## Current Status

Implemented:

- `tokencause analyze <trace.jsonl>`
- `tokencause analyze-litellm <log.jsonl>`
- `tokencause codex scan`
- `tokencause codex explain --last`
- `tokencause codex explain --thread-id <id-prefix>`
- English and Chinese README
- MIT license
- Python package / CLI entry point
- Unit tests

Repository:

- GitHub: `https://github.com/happyaaa/tokencause`
- Package name: `tokencause`
- CLI command: `tokencause`

## Is It Useful Yet?

Yes, as an early proof of concept.

It has been run against a real Codex Desktop session on this machine. The current output identified:

- tens of millions of Codex-reported total tokens
- observable transcript token breakdown
- long tool outputs
- repeated content chunks
- error-like outputs
- top commands
- top files/artifacts

The strongest real signal was that the session became expensive partly because we printed large rollout JSONL chunks into the conversation with commands like `sed` and `tail`. TokenCause surfaced that as long tool output. That is exactly the kind of root cause the product is supposed to find.

However, it is not production-quality yet.

Known issues:

- File/artifact extraction still has false positives, especially URLs and non-local references.
- Recommendations are too generic and can mention example-like file names instead of only evidence from the current session.
- Repeated context is currently hash-based and needs better grouping by artifact type.
- It does not yet estimate dollar cost for Codex sessions.
- It does not yet have a dashboard.
- It does not yet support Claude Code local logs or OpenTelemetry.

## Product Thesis

AI coding agents make token spend hard to understand because cost is hidden inside:

- repeated context
- long terminal output
- repeated test failures
- large files
- generated files
- retries
- tool-call loops
- expensive model routing

Most tools can answer:

> How much did I spend?

TokenCause should answer:

> Why did this session get expensive?

## First Real User Workflow

The first useful workflow should be:

```bash
tokencause codex scan
tokencause codex explain --last
```

Expected output:

```text
Session: Codex / project / timestamp
Total tokens: 186,420

Cost drivers:
1. Repeated context: 38%
2. Command output: 27%
3. File context: 19%
4. Retry/failure: 11%

Recommendations:
- Truncate long test logs.
- Summarize repeated files.
- Split or compact long sessions.
- Avoid rerunning identical commands without code changes.
```

## MVP Analyzers

Build these analyzers before adding more sources or UI:

### 1. RepeatedContextAnalyzer

Detect:

- same content hash repeated across turns
- same tool output repeated
- same error repeated
- same file reference appearing repeatedly

Output:

- repeated chunk count
- duplicate token estimate
- top repeated previews
- likely cause

### 2. LongToolOutputAnalyzer

Detect:

- large command output
- large test output
- build/install logs
- grep/search output dumps

Output:

- command
- token count
- category: test/build/install/search/other
- recommendation: truncate, tail, targeted command

### 3. ExpensiveFileAnalyzer

Detect:

- lockfiles
- generated files
- large JSON
- fixtures
- snapshots
- schemas
- minified files

Output:

- file path
- estimated token contribution
- why it is suspicious
- recommendation: ignore, summarize, inspect narrower region

### 4. RetryCostAnalyzer

Detect:

- repeated failed commands
- repeated identical errors
- patch/test loops
- tool outputs with no state change

Output:

- repeated command/error group
- estimated repeated token cost
- recommendation: dedupe error, change strategy before rerun

### 5. SessionDriftAnalyzer

Detect:

- later session turns getting larger
- high cached/input token growth
- fewer useful changes near the end

Output:

- drift point
- token growth trend
- recommendation: compact or start a new session

## Near-Term Milestones

### Milestone 1: Make `codex explain` credible

Goal: A user can run `tokencause codex explain --last` and trust the top 3 cost drivers.

Tasks:

- Fix file/artifact extraction so URLs and library names are not treated as local files.
- Replace generic recommendations with evidence-backed recommendations.
- Show top repeated chunks with previews and token impact.
- Separate command output into `test_log`, `build_log`, `install_log`, `search_output`, and `other_tool_output`.
- Add tests for repeated context, long tool output, expensive files, and retry loops.

### Milestone 2: Add local HTML report

Goal: Provide a simple local observability surface without building SaaS.

Command:

```bash
tokencause codex report --out reports/codex-dashboard.html
```

Dashboard sections:

- total tokens
- token breakdown
- top sessions
- top cost drivers
- top commands
- top files/artifacts
- repeated chunks
- recommendations

Keep it static HTML first.

### Milestone 3: Add Claude Code support

Goal: Support the other high-signal coding agent workflow.

Sources:

- Claude Code local logs/session files
- Claude Code OpenTelemetry export later

Commands:

```bash
tokencause claude scan
tokencause claude explain --last
```

### Milestone 4: Import from ccusage

Goal: Stand on top of ccusage instead of competing with it.

Command:

```bash
tokencause import --from-ccusage ccusage.json
```

Use ccusage for accounting and TokenCause for root-cause analysis.

## Positioning

Short English:

> TokenCause explains why your AI coding session got expensive.

Short Chinese:

> TokenCause 诊断你的 AI 编程会话为什么烧 token。

Long English:

> TokenCause is a local-first cost root-cause tool for AI coding sessions. It analyzes Codex, Claude Code, LiteLLM, and other agent traces to show which files, commands, repeated contexts, retries, and tool outputs drove token spend.

Long Chinese:

> TokenCause 是一个本地优先的 AI coding session 成本归因工具。它分析 Codex、Claude Code、LiteLLM 和其他 agent trace，告诉你 token 具体烧在了哪些文件、命令输出、重复上下文、失败重试和工具调用里。

## What Not To Build Yet

Do not build these before the analyzer is credible:

- hosted SaaS
- team dashboard
- auth
- billing
- complex charts
- broad provider support
- vague AI productivity coaching

The wedge is narrow:

> Run it locally on one real Codex/Claude Code session and immediately see why that session got expensive.

