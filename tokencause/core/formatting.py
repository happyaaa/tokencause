"""Small formatting helpers shared by CLI and renderers."""


def money(value: float) -> str:
    return f"${value:.4f}"


def compact_number(value: int | float) -> str:
    absolute = abs(value)
    units = ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K"))
    for divisor, suffix in units:
        if absolute >= divisor:
            compact = value / divisor
            rendered = f"{compact:.1f}".rstrip("0").rstrip(".")
            return f"{rendered}{suffix}"
    return f"{value:g}" if isinstance(value, float) else str(value)


def seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


def top_items(mapping: dict[str, float | int], limit: int = 5) -> list[tuple[str, float | int]]:
    return list(mapping.items())[:limit]
