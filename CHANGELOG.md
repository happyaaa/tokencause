# Changelog

All notable TokenCause changes are tracked here.

## Unreleased

- Added diagnosis-first Codex and Claude local session analysis.
- Added a top-level `tokencause dashboard` command that auto-selects local Codex or Claude Code sessions.
- Added `tokencause serve` for a localhost dashboard site with linked session diagnosis reports.
- Added `tokencause demo-site` for a synthetic dashboard that does not require local Codex or Claude history.
- Added `tokencause serve --demo` and `tokencause dashboard --demo` so users can try the dashboard without local Codex or Claude history.
- Added dashboard-level workflow diagnosis with why, workflow pattern, next action, and avoid-next-time guidance.
- Added Codex session token attribution that separates billed/model tokens, observable transcript tokens, cache tokens, and estimated waste signal.
- Added local HTML reports and multi-session overviews with drill-down session reports.
- Added machine-readable JSON output for analysis, scan, session diagnosis, overview, and doctor commands.
- Added JSON schema versioning and adapter identifiers for generic and Claude OpenTelemetry analysis outputs.
- Added bounded `top_events` to analysis JSON so dashboards and scripts can identify the highest-cost/highest-token records.
- Added adapter identifiers to Codex and Claude overview JSON outputs.
- Added source-by-source usage recipes.
- Added local-first security and privacy guidance.
- Added configurable Codex and Claude token price estimates.
- Added Claude OpenTelemetry import paths.
- Hardened parsing for generic trace token aliases and Claude OpenTelemetry token type aliases.
- Hardened Codex JSON outputs to bound session-title previews instead of emitting full prompt-like titles.
- Hardened analysis JSON outputs to bound top-event context items and failure errors.
- Improved `tokencause doctor` so local Codex/Claude history is optional and next commands point to diagnosis workflows.
