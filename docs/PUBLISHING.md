# Publishing

TokenCause is a Python CLI package. The recommended public install path is PyPI plus `uvx`/`uv tool install`/`pipx`.

## User Install Commands

After publishing to PyPI:

```bash
uvx tokencause open
uv tool install tokencause
pipx install tokencause
```

Before the first PyPI release:

```bash
pipx install git+https://github.com/happyaaa/tokencause
```

## PyPI Trusted Publishing

Use PyPI Trusted Publishing instead of a long-lived PyPI token.

Configure a Trusted Publisher on PyPI with:

- owner: `happyaaa`
- repository: `tokencause`
- workflow: `publish.yml`
- environment: `pypi`

The workflow builds from a GitHub Release and publishes with `pypa/gh-action-pypi-publish`.

## Release Checklist

1. Verify the package name `tokencause` is available or owned on PyPI.
2. Update `pyproject.toml` version.
3. Run the local checks:

   ```bash
   python -m build
   python -m unittest discover -s tests
   ```

4. Create a GitHub Release for the same version tag, for example `v0.1.1`.
5. Confirm the `publish` workflow passes.
6. Test the public install:

   ```bash
   uvx tokencause --version
   uvx tokencause open
   ```

## Why Not npm First?

TokenCause's core value is local AI coding session diagnosis: parsing local session files, normalizing traces, attributing tokens, extracting evidence, and rendering reports. That is already implemented as a Python package with a console script.

An npm wrapper can be added later if `npx tokencause` becomes important for distribution, but the first release should keep the working Python core and publish through PyPI.
