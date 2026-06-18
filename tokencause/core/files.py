"""File-reference extraction and artifact classification."""

from __future__ import annotations

import re
from pathlib import Path


FILE_REF_RE = re.compile(
    r"(?:\./|\../|~/|/|[A-Za-z0-9_.-]+/)[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|jsonl|json|md|toml|yaml|yml|lock|txt|sql|css|html|sh)(?=$|[\s`'\"),.;:])"
)
BARE_FILE_RE = re.compile(
    r"\b(?:package-lock\.json|pnpm-lock\.yaml|yarn\.lock|poetry\.lock|requirements\.txt|pyproject\.toml|Cargo\.toml|Cargo\.lock)\b"
)

FILE_RISK_REASONS = (
    ("package-lock.json", "lockfile"),
    ("pnpm-lock.yaml", "lockfile"),
    ("yarn.lock", "lockfile"),
    ("poetry.lock", "lockfile"),
    ("Cargo.lock", "lockfile"),
    ("generated", "generated file"),
    ("fixture", "fixture data"),
    ("fixtures", "fixture data"),
    ("snapshot", "snapshot artifact"),
    ("schema", "schema artifact"),
    (".min.", "minified asset"),
)


def extract_file_refs(text: str) -> tuple[str, ...]:
    refs = {normalize_file_ref(match) for match in FILE_REF_RE.findall(text or "")}
    refs.update(BARE_FILE_RE.findall(text or ""))
    return tuple(sorted(ref for ref in refs if ref))


def file_risk_reason(file_ref: str) -> str:
    lower = file_ref.lower()
    for hint, reason in FILE_RISK_REASONS:
        if hint.lower() in lower:
            return reason
    if lower.endswith((".jsonl", ".json")):
        return "large structured data"
    return ""


def artifact_kind(file_ref: str) -> str:
    risk = file_risk_reason(file_ref)
    if risk:
        return risk
    suffix = Path(file_ref).suffix.lower().lstrip(".")
    if suffix in {"py", "ts", "tsx", "js", "jsx", "css", "html", "sql", "sh"}:
        return "source file"
    if suffix in {"md", "txt"}:
        return "documentation"
    if suffix in {"toml", "yaml", "yml"}:
        return "config file"
    if suffix:
        return f"{suffix} file"
    return "local artifact"


def normalize_file_ref(value: str) -> str:
    ref = value.strip().strip("`'\"),;:")
    if not ref:
        return ""
    lower = ref.lower()
    if lower.startswith(("http://", "https://", "ftp://", "//")):
        return ""
    if lower.startswith(("github.com/", "raw.githubusercontent.com/", "www.")):
        return ""
    if "://" in lower:
        return ""
    if lower.count(".") == 1 and "/" not in lower and not lower.startswith(("./", "../", "~/", "/")):
        return ""
    if ref.startswith("~/"):
        return ref
    if ref.startswith(("/", "./", "../")):
        return ref
    if "/" not in ref:
        return ""
    return ref


def resolve_file_ref(ref: str, cwd: str) -> Path | None:
    if not ref:
        return None
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.exists() or not resolved.is_file():
        return None
    return resolved


def should_count_file_ref(ref: str, cwd: str) -> bool:
    return resolve_file_ref(ref, cwd) is not None
