"""Shared TokenCause data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TraceEvent:
    raw: dict[str, Any]
    index: int
    run_id: str = "default"
    step: str = "unknown"
    model: str = "unknown"
    tool: str = "none"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""
    context_hash: str = ""
    context_items: tuple[str, ...] = ()

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class SessionEvent:
    category: str
    tokens: int
    preview: str
    index: int = 0
    timestamp: str = ""
    command: str = ""
    file_refs: tuple[str, ...] = ()
    content_hash: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    status: str = "ok"
    error: str = ""
    step: str = ""
    tool: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class SessionTrace:
    id: str
    source: str
    title: str = ""
    cwd: str = ""
    events: list[SessionEvent] = field(default_factory=list)
    usage_events: list[TokenUsage] = field(default_factory=list)
    repeated_chunks: list["RepeatedChunk"] = field(default_factory=list)
    repeated_artifacts: list["RepeatedArtifact"] = field(default_factory=list)
    long_tool_outputs: list[SessionEvent] = field(default_factory=list)
    retry_loops: list["RetryLoop"] = field(default_factory=list)
    session_drift: "SessionDrift | None" = None
    environment_issues: list["EnvironmentIssue"] = field(default_factory=list)
    broad_exploration: "BroadExploration | None" = None

    @property
    def observable_tokens(self) -> int:
        return sum(event.tokens for event in self.events)

    @property
    def model_total_tokens(self) -> int:
        total = sum(usage.total_tokens for usage in self.usage_events)
        if total:
            return total
        return sum(event.total_tokens for event in self.events)

    @property
    def model_input_tokens(self) -> int:
        return sum(usage.input_tokens for usage in self.usage_events) or sum(event.input_tokens for event in self.events)

    @property
    def cached_input_tokens(self) -> int:
        return sum(usage.cached_input_tokens for usage in self.usage_events)

    @property
    def model_output_tokens(self) -> int:
        return sum(usage.output_tokens for usage in self.usage_events) or sum(event.output_tokens for event in self.events)


@dataclass
class Finding:
    title: str
    detail: str
    severity: str = "info"


@dataclass
class Recommendation:
    title: str
    detail: str
    estimated_savings_usd: float = 0.0


@dataclass
class Analysis:
    events: list[TraceEvent]
    total_cost: float
    total_tokens: int
    total_latency_ms: int
    cost_by_model: dict[str, float]
    cost_by_step: dict[str, float]
    tokens_by_model: dict[str, int]
    latency_by_step: dict[str, int]
    failures: list[TraceEvent]
    repeated_context: dict[str, int]
    repeated_items: dict[str, int]
    findings: list[Finding] = field(default_factory=list)
    recommendations: list[Recommendation] = field(default_factory=list)
    estimated_savings_usd: float = 0.0


@dataclass
class CodexThread:
    id: str
    title: str
    rollout_path: Path
    cwd: str
    updated_at: int
    tokens_used: int = 0


@dataclass
class ClaudeSession:
    id: str
    path: Path
    project: str
    cwd: str
    updated_at: float
    messages: int = 0


@dataclass
class CodexContentEvent:
    category: str
    tokens: int
    preview: str
    timestamp: str = ""
    file_refs: tuple[str, ...] = ()
    command: str = ""
    content_hash: str = ""


@dataclass
class RepeatedChunk:
    content_hash: str
    count: int
    tokens_each: int
    duplicate_tokens: int
    category: str
    preview: str


@dataclass
class RepeatedArtifact:
    file_ref: str
    count: int
    tokens: int
    categories: tuple[str, ...]


@dataclass
class CostDriver:
    name: str
    impact_tokens: int
    summary: str
    evidence: str


@dataclass
class HumanDiagnosis:
    root_cause: str
    workflow_failure: str
    workflow_pattern_label: str = ""
    workflow_subtype: str = ""
    evidence_metrics: dict[str, Any] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    avoid_next_time: list[str] = field(default_factory=list)
    billing_note: str = ""
    primary_driver: str = ""
    actionable_driver: str = ""


@dataclass
class CodexPriceConfig:
    input_per_mtok: float = 0.0
    cached_input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @property
    def enabled(self) -> bool:
        return self.input_per_mtok > 0 or self.cached_input_per_mtok > 0 or self.output_per_mtok > 0


@dataclass
class ClaudePriceConfig:
    input_per_mtok: float = 0.0
    cache_write_per_mtok: float = 0.0
    cache_read_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @property
    def enabled(self) -> bool:
        return (
            self.input_per_mtok > 0
            or self.cache_write_per_mtok > 0
            or self.cache_read_per_mtok > 0
            or self.output_per_mtok > 0
        )


@dataclass
class RetryLoop:
    key: str
    count: int
    tokens: int
    command: str
    preview: str


@dataclass
class SessionDrift:
    early_avg_tokens: int
    late_avg_tokens: int
    ratio: float
    peak_tokens: int
    samples: int


@dataclass
class EnvironmentIssue:
    kind: str
    count: int
    tokens: int
    command: str
    preview: str


@dataclass
class BroadExploration:
    search_commands: int
    broad_commands: int
    unique_files: int
    search_tokens: int
    command_tokens: int
    examples: tuple[str, ...]


@dataclass
class CodexCacheResult:
    report: "CodexExplainReport"
    status: str


@dataclass
class CodexExplainReport:
    thread: CodexThread
    content_events: list[CodexContentEvent]
    usage_events: list[dict[str, int]]
    category_tokens: dict[str, int]
    file_tokens: dict[str, int]
    command_tokens: dict[str, int]
    repeated_hashes: dict[str, int]
    repeated_chunks: list[RepeatedChunk]
    repeated_artifacts: list[RepeatedArtifact]
    long_tool_outputs: list[CodexContentEvent]
    failure_events: list[CodexContentEvent]
    retry_loops: list[RetryLoop]
    session_drift: SessionDrift | None
    environment_issues: list[EnvironmentIssue] = field(default_factory=list)
    broad_exploration: BroadExploration | None = None

    @property
    def observable_tokens(self) -> int:
        return sum(event.tokens for event in self.content_events)

    @property
    def model_total_tokens(self) -> int:
        return sum(event.get("total_tokens", 0) for event in self.usage_events)

    @property
    def model_input_tokens(self) -> int:
        return sum(event.get("input_tokens", 0) for event in self.usage_events)

    @property
    def cached_input_tokens(self) -> int:
        return sum(event.get("cached_input_tokens", 0) for event in self.usage_events)

    @property
    def model_output_tokens(self) -> int:
        return sum(event.get("output_tokens", 0) for event in self.usage_events)
