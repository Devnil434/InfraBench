"""Episode state and lifecycle models for InfraBench."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set
from uuid import uuid4

from benchmark.schema import AgentScenarioPayload


class EpisodeStatus(str, Enum):
    """Explicit lifecycle states for a benchmark episode."""

    CREATED = "created"
    PREPARING = "preparing"
    FAULTS_INJECTED = "faults_injected"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    SETUP_FAILED = "setup_failed"
    AGENT_FAILED = "agent_failed"
    CLEANUP_FAILED = "cleanup_failed"
    FAILED = "failed"  # Backward-compatible alias for generic failure


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal episode lifecycle state transition is attempted."""
    pass


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


# Allowed state transition graph
ALLOWED_TRANSITIONS: Dict[EpisodeStatus, Set[EpisodeStatus]] = {
    EpisodeStatus.CREATED: {
        EpisodeStatus.PREPARING,
    },
    EpisodeStatus.PREPARING: {
        EpisodeStatus.FAULTS_INJECTED,
        EpisodeStatus.SETUP_FAILED,
        EpisodeStatus.CLEANUP_FAILED,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.FAULTS_INJECTED: {
        EpisodeStatus.READY,
        EpisodeStatus.SETUP_FAILED,
        EpisodeStatus.CLEANUP_FAILED,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.READY: {
        EpisodeStatus.RUNNING,
        EpisodeStatus.SETUP_FAILED,
        EpisodeStatus.CLEANUP_FAILED,
        EpisodeStatus.COMPLETED,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.RUNNING: {
        EpisodeStatus.COMPLETED,
        EpisodeStatus.AGENT_FAILED,
        EpisodeStatus.CLEANUP_FAILED,
        EpisodeStatus.FAILED,
    },
    EpisodeStatus.CLEANUP_FAILED: {
        # Retryable: can retry cleanup and resolve to final outcome
        EpisodeStatus.COMPLETED,
        EpisodeStatus.SETUP_FAILED,
        EpisodeStatus.AGENT_FAILED,
        EpisodeStatus.CLEANUP_FAILED,
        EpisodeStatus.FAILED,
    },
    # Terminal states allow no further transitions
    EpisodeStatus.COMPLETED: set(),
    EpisodeStatus.SETUP_FAILED: set(),
    EpisodeStatus.AGENT_FAILED: set(),
    EpisodeStatus.FAILED: set(),
}


@dataclass
class Episode:
    """Tracks one execution and evaluation of a benchmark scenario."""

    scenario_id: str
    episode_id: str = field(default_factory=lambda: str(uuid4()))

    status: EpisodeStatus = EpisodeStatus.CREATED

    created_at: datetime = field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    agent_payload: Optional[AgentScenarioPayload] = None
    fault_results: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    step_count: int = 0
    evaluation_results: Optional[Dict[str, Any]] = None

    error: Optional[str] = None
    setup_error: Optional[str] = None
    agent_error: Optional[str] = None
    cleanup_error: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    def transition_to(self, new_status: EpisodeStatus) -> None:
        """Update episode state and record lifecycle timestamps according to allowed transitions."""
        allowed = ALLOWED_TRANSITIONS.get(self.status, set())
        if new_status not in allowed:
            raise InvalidStateTransitionError(
                f"Cannot transition episode from '{self.status.value}' to '{new_status.value}'. "
                f"Allowed transitions from '{self.status.value}': {[s.value for s in allowed]}"
            )

        self.status = new_status

        if new_status == EpisodeStatus.RUNNING and self.started_at is None:
            self.started_at = utc_now()

        if new_status in {
            EpisodeStatus.COMPLETED,
            EpisodeStatus.SETUP_FAILED,
            EpisodeStatus.AGENT_FAILED,
            EpisodeStatus.CLEANUP_FAILED,
            EpisodeStatus.FAILED,
        }:
            self.finished_at = utc_now()

    def record_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
        step: Optional[int] = None,
        duration_ms: float = 0.0,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record an auditable agent tool call into the episode trajectory."""
        self.step_count = step if step is not None else self.step_count + 1
        entry = {
            "step": self.step_count,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "duration_ms": duration_ms,
            "error": error,
            "timestamp": utc_now().isoformat(),
        }
        self.tool_calls.append(entry)
        return entry

    def record_evaluation(self, results: Dict[str, Any]) -> None:
        """Record independent success evaluation outcomes."""
        self.evaluation_results = results

    def record_error(
        self,
        message: str,
        error_type: Optional[str] = None,
    ) -> None:
        """Record an error and categorize as setup, agent, or cleanup failure."""
        self.error = message
        if error_type == "setup":
            self.setup_error = message
        elif error_type == "agent":
            self.agent_error = message
        elif error_type == "cleanup":
            self.cleanup_error = message
