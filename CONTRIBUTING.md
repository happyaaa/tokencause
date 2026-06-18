# Contributing

TokenCause is intentionally small and local-first. Keep changes focused on explaining why AI coding sessions get expensive.

## Local Setup

Use a virtual environment, especially on macOS/Homebrew Python where direct `pip install` may be blocked by PEP 668.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Verification

Run the unit test suite:

```bash
python -m unittest discover -s tests
```

Run smoke commands that only depend on checked-in examples:

```bash
tokencause --version
tokencause serve --help
tokencause serve --demo --help
tokencause dashboard --demo --out /tmp/tokencause-dashboard-demo.html
tokencause dashboard --demo --json | python -m json.tool
tokencause demo-site --out /tmp/tokencause-demo-site
tokencause doctor --project-root . --json | python -m json.tool
tokencause analyze examples/tokencause_trace.jsonl --budget 2
tokencause analyze examples/tokencause_trace.jsonl --budget 2 --json | python -m json.tool
tokencause claude import-otel examples/claude_otel_sample.json --budget 1 --json | python -m json.tool
```

When local Codex or Claude Code history exists, also check scan JSON:

```bash
tokencause codex scan --json | python -m json.tool
tokencause claude scan --json | python -m json.tool
```

Build the package before release-oriented changes:

```bash
python -m pip install build
python -m build
python -m pip install dist/*.whl
tokencause --version
```

For public demo commands that do not require local Codex or Claude Code history, see [docs/DEMO.md](docs/DEMO.md). For product boundaries and token-scope caveats, see [docs/LIMITATIONS.md](docs/LIMITATIONS.md).

## JSON Output Contract

See [docs/JSON_OUTPUT.md](docs/JSON_OUTPUT.md) for the current output shapes.

All machine-readable outputs should include:

- `schema_version`: increments when the JSON shape changes incompatibly.
- `version`: the TokenCause CLI version.
- `kind`: the output type, such as `codex_session`, `claude_overview`, or `doctor`.

Keep stdout as valid JSON whenever `--json` is set. Write progress, skipped-session notices, or diagnostics to stderr if needed.

## Git Hygiene

Do not commit generated reports, caches, build artifacts, or virtual environments. The repository ignores common local outputs such as:

- `.tokencause-cache/`
- `reports/`
- `build/`
- `dist/`
- `*.egg-info/`
- `.venv/`

Prefer small commits that keep behavior, tests, and docs together.
