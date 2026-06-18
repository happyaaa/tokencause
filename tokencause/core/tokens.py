"""Token and text normalization helpers."""

from __future__ import annotations

import hashlib
import re


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    # Cheap local estimate. Exact model tokenizers are intentionally not required for local-first use.
    return max(1, len(text) // 4)


def short_preview(text: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def content_hash(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return ""
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]
