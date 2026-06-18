# Limitations

TokenCause is alpha software focused on AI coding session diagnosis. It is useful today, but the following boundaries are intentional.

## Estimated Waste Is Diagnostic

`estimated_waste_tokens` is not a bill. It is a capped sum of overlapping cost-driver impacts, used to highlight suspicious workflow patterns such as repeated context, long tool output, expensive files, retry loops, and session drift.

Use provider/model counters for billing-like totals:

- `model_billed_tokens`
- `model_input_tokens`
- `model_output_tokens`
- `cache_tokens`

Use `observable_transcript_tokens` and `estimated_waste_tokens` for diagnosis.

## Local Logs Vary

Codex and Claude Code local file formats may change. TokenCause parses the local session data it can see and skips sessions it cannot parse. Run:

```bash
tokencause doctor
```

to check local data availability.

## Codex Pricing Is User Supplied

TokenCause does not hard-code Codex prices because local rollout data may not include enough model detail and prices can change. Pass price flags or a local price config when you want dollar estimates.

## Claude and Codex Do Not Have Identical Detail Yet

Codex session reports currently have the richest session-level root-cause narrative and token attribution. Claude Code support includes local session diagnosis, cache-heavy context, tool result analysis, and overviews, but not every Codex-specific field has an exact Claude equivalent yet.

## Static Dashboard First

`tokencause serve` serves generated local HTML/JSON files. It is intentionally simple and dependency-free. It is not a hosted service and does not live-refresh sessions yet.

## Privacy

Generated reports can include local paths, command names, command output previews, repeated-content previews, session IDs, and project names. Treat `reports/` and `.tokencause-cache/` as sensitive local artifacts.
