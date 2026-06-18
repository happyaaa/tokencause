"""Evidence extraction for TokenCause diagnosis case files."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .formatting import compact_number


@dataclass
class EvidenceItem:
    name: str
    value: str
    detail: str
    supports: str


def build_evidence_from_metrics(metrics: dict[str, Any]) -> list[EvidenceItem]:
    evidence: list[EvidenceItem] = []
    if "search_commands" in metrics or "file_refs" in metrics:
        searches = int(metrics.get("search_commands") or 0)
        file_refs = int(metrics.get("file_refs") or 0)
        evidence.append(
            EvidenceItem(
                name="Broad exploration",
                value=f"{searches} searches, {file_refs} file refs",
                detail="The session loaded broad workspace context before narrowing the working surface.",
                supports="Exploration or unreset long-session diagnosis",
            )
        )
    if "largest_output_tokens" in metrics:
        evidence.append(
            EvidenceItem(
                name="Largest tool output",
                value=f"{compact_number(int(metrics['largest_output_tokens']))} tokens",
                detail=str(metrics.get("largest_output_command") or "A tool result added a large chunk to the transcript."),
                supports="Output carryover diagnosis",
            )
        )
    if "repeated_artifact" in metrics:
        evidence.append(
            EvidenceItem(
                name="Repeated artifact",
                value=f"{metrics.get('repeated_artifact_count', '?')}x",
                detail=str(metrics.get("repeated_artifact") or ""),
                supports="Repeated artifact/context carryover diagnosis",
            )
        )
    if "retry_count" in metrics:
        evidence.append(
            EvidenceItem(
                name="Retry loop",
                value=f"{metrics['retry_count']} retries",
                detail=f"Repeated failures contributed about {compact_number(int(metrics.get('retry_tokens') or 0))} tokens.",
                supports="Retry/failure loop diagnosis",
            )
        )
    if "drift_ratio" in metrics:
        evidence.append(
            EvidenceItem(
                name="Session drift",
                value=f"{metrics['drift_ratio']}x",
                detail="Later turns were materially larger than early turns.",
                supports="Long-running context drift diagnosis",
            )
        )
    if "cached_input_tokens" in metrics:
        evidence.append(
            EvidenceItem(
                name="Cached input",
                value=f"{compact_number(int(metrics['cached_input_tokens']))} tokens",
                detail="This is a billing/accounting signal, not by itself a workflow root cause.",
                supports="Billing/cache signal",
            )
        )
    return evidence
