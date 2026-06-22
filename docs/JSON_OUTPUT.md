# JSON Output

TokenCause supports machine-readable output for local integrations, CI checks, and future dashboards.

Use `--json` when available:

```bash
tokencause doctor --json
tokencause dashboard --json
tokencause dashboard --demo --json
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --json
tokencause codex scan --json
tokencause codex explain --last --json
tokencause codex overview --limit 20 --json
tokencause claude scan --json
tokencause claude explain --last --json
tokencause claude overview --limit 20 --json
tokencause claude import-otel examples/claude_otel_sample.json --budget 1 --json
```

When `--json` is set, stdout should be valid JSON. Operational warnings should go to stderr.

## Root Fields

All JSON outputs include:

- `schema_version`: integer data-shape version. Increment this for incompatible JSON changes.
- `version`: TokenCause CLI version.
- `kind`: output type.

Current `kind` values:

- `doctor`
- `dashboard`
- `analysis`
- `codex_scan`
- `claude_scan`
- `codex_session`
- `claude_session`
- `codex_overview`
- `claude_overview`

## Doctor Output

Command:

```bash
tokencause doctor --json
```

Shape:

- `ok`: true when all non-optional checks pass.
- `statuses`: checks with `name`, `ok`, `optional`, and `detail`.
- `next_commands`: suggested commands to run next.

Optional checks, such as local Codex/Claude history or `Price config`, can be missing without making `ok` false.

## Dashboard Output

Command:

```bash
tokencause dashboard --json
tokencause dashboard --demo --json
```

Shape:

- `source`: selected local adapter, `codex` or `claude`.
- `summary`: adapter-neutral dashboard diagnosis with `sessions_analyzed`, `top_session`, `top_driver`, `why`, `workflow_pattern`, `next_action`, `avoid_next_time`, reusable `workflow_lessons`, `process_shape`, `risk_signals`, `attribution_quality`, `value_evidence`, and `recommendations`.
- `overview`: the selected adapter's overview payload.

The dashboard command auto-selects Codex when local Codex sessions are visible, otherwise Claude Code. Use `--source codex` or `--source claude` to force an adapter.

`dashboard --demo --json` uses synthetic Codex-style session data and does not read local history. In JSON mode, `report_link` is `null` unless `--session-reports` is also set, matching the real dashboard behavior.

## Analysis Output

Commands:

```bash
tokencause analyze TRACE.jsonl --json
tokencause claude import-otel otel.json --json
```

Shape:

- `source`: input file path.
- `adapter`: parser/import adapter, such as `tokencause_trace` or `claude_otel`.
- `budget_usd`: optional budget.
- `summary`: event count, total cost, total tokens, latency, estimated savings.
- `breakdowns`: cost/tokens/latency/repetition maps.
- `top_events`: bounded list of the highest-cost/highest-token events with step, model, tool, token counts, cost, status, and bounded context items.
- `findings`: severity, title, detail.
- `recommendations`: title, detail, estimated savings.
- `failures`: bounded list of failed events.

For `tokencause analyze` on a TokenCause session trace, analysis output also includes:

- `session`: canonical session metadata and token counters.
- `cost_drivers`: ranked diagnosis drivers with `name`, `impact_tokens`, `impact_share`, `summary`, and `evidence`.
- `diagnosis_scope`: notes explaining observable tokens, model counters, and overlapping driver impact.

## Scan Output

Commands:

```bash
tokencause codex scan --json
tokencause claude scan --json
```

Shape:

- `sessions`: recent local sessions in newest-first order.
- Codex sessions include `id`, `title`, `rollout_path`, `cwd`, `updated_at`, and `tokens_used`.
- Claude sessions include `id`, `project`, `cwd`, `path`, `updated_at`, and `messages`.

## Session Diagnosis Output

Commands:

```bash
tokencause codex explain --last --json
tokencause claude explain --last --json
```

Shape:

- `session`: local session metadata.
- `summary`: headline token/cost counters and natural-language summary items.
- `cost_drivers`: ranked drivers with `name`, `impact_tokens`, `impact_share`, `summary`, and `evidence`.
- `canonical_trace`: adapter output normalized to TokenCause `SessionTrace` counters.
- `root_cause_narrative`: Codex session root-cause narrative items with `driver`, `impact_tokens`, `impact_share`, `cause`, `evidence`, and `next_action`.
- `token_attribution`: Codex token scope split: `model_billed_tokens`, `model_input_tokens`, `model_output_tokens`, `cache_tokens`, `observable_transcript_tokens`, `estimated_waste_tokens`, `estimated_waste_share_of_observable`, and `scope_notes`.
- `case_file`: structured diagnosis record with observed facts, evidence, likely causes, file carryover, drift timeline, `process_summary`, `risk_signals`, `attribution_quality`, `value_evidence`, `next_run_plan`, recommendations, reusable `workflow_lessons`, and limits.
- `recommendations`: concrete next actions.
- `observability`: raw breakdowns used to explain the diagnosis.

## Overview Output

Commands:

```bash
tokencause codex overview --limit 20 --json
tokencause claude overview --limit 20 --json
```

Shape:

- `adapter`: local session adapter, `codex` or `claude`.
- `summary`: aggregate sessions and token/cost totals.
- `sessions`: top 20 sessions ranked by token volume.
- `cost_drivers`: cross-session driver totals and shares across all analyzed sessions.
- `canonical_trace`: top sessions normalized to TokenCause `SessionTrace` counters.
- `recommendations`: cross-session actions.
- Codex overview includes `token_breakdown`.
- Claude overview includes `token_breakdown_by_tool`.

## Compatibility Rules

- Additive fields are allowed without a schema bump.
- Removing or renaming fields requires incrementing `schema_version`.
- Keep `--json` stdout parseable with `python -m json.tool`.
- Keep text/progress messages out of stdout in JSON mode.
