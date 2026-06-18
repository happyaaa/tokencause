"""Small value-normalization helpers used by parsers."""

from __future__ import annotations

from typing import Any


def as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def first_present(data: dict[str, Any], keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def get_path(data: dict[str, Any], path: tuple[str, ...], default: Any = None) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current if current not in (None, "") else default


def first_path(data: dict[str, Any], paths: tuple[tuple[str, ...], ...], default: Any = None) -> Any:
    for path in paths:
        value = get_path(data, path, None)
        if value not in (None, ""):
            return value
    return default


def normalize_context_items(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def token_count_value(raw: dict[str, Any], usage: dict[str, Any], keys: tuple[str, ...]) -> int:
    usage_keys = keys + tuple(key.replace("_", "") for key in keys)
    return as_int(first_present(raw, keys, first_present(usage, usage_keys, 0)))
