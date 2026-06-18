"""Observable token attribution for normalized AI coding sessions."""

from __future__ import annotations

from dataclasses import dataclass

from .models import SessionTrace


@dataclass
class TokenAttribution:
    name: str
    tokens: int
    share: float
    source: str = "observable"


def token_share(tokens: int, total: int) -> float:
    return round(tokens / (total or 1), 6)


def build_token_attribution(trace: SessionTrace) -> list[TokenAttribution]:
    totals: dict[str, int] = {}
    for event in trace.events:
        totals[event.category] = totals.get(event.category, 0) + event.tokens
    total = trace.observable_tokens
    return [
        TokenAttribution(name=name, tokens=tokens, share=token_share(tokens, total))
        for name, tokens in sorted(totals.items(), key=lambda item: item[1], reverse=True)
        if tokens > 0
    ]
