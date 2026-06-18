"""Cost-driver detection for normalized AI coding sessions.

This module is the public home for driver detection. The implementation still
delegates to diagnosis.py for this development-stage refactor so existing
adapter/cache imports keep working while the analysis layers get named clearly.
"""

from __future__ import annotations

from .diagnosis import (
    BILLING_ONLY_DRIVERS,
    build_broad_exploration,
    build_environment_issues,
    build_session_command_tokens,
    build_session_drift,
    build_session_file_tokens,
    build_session_repeated_artifacts,
    build_session_repeated_chunks,
    build_session_retry_loops,
    build_session_trace_cost_drivers,
    actionable_cost_drivers,
    environment_issue_kind,
    is_broad_exploration_command,
)
