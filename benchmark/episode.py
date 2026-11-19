
"""Episode state and lifecycle models for InfraBench."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from benchmark.schema import AgentScenarioPayload


class EpisodeStatus(str, Enum):
    CREATED = "created"
    PREPARING = "preparing"
    FAULTS_INJECTED = "faults_injected"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CLEANUP_FAILED = "cleanup_failed"


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


@dataclass
class Episode:
    """Tracks one execution of a benchmark scenario."""

    scenario_id: str
    episode_id: str = field(
        default_factory=lambda: str(uuid4())
    )

    status: EpisodeStatus = EpisodeStatus.CREATED

    created_at: datetime = field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    agent_payload: Optional[AgentScenarioPayload] = None

    fault_results: List[Dict[str, Any]] = field(
        default_factory=list
    )

    error: Optional[str] = None

    metadata: Dict[str, Any] = field(default_factory=dict)

    def transition_to(self, new_status: EpisodeStatus) -> None:
        """Update episode state and lifecycle timestamps."""

        if self.status in {
            EpisodeStatus.COMPLETED,
            EpisodeStatus.FAILED,
            EpisodeStatus.CLEANUP_FAILED,
        }:
            raise ValueError(
                f"Cannot transition terminal episode "
                f"from {self.status.value} to {new_status.value}"
            )

        self.status = new_status

        if new_status == EpisodeStatus.RUNNING:
            self.started_at = utc_now()

        if new_status in {
            EpisodeStatus.COMPLETED,
            EpisodeStatus.FAILED,
            EpisodeStatus.CLEANUP_FAILED,
        }:
            self.finished_at = utc_now()

    def record_error(self, message: str) -> None:
        """Record an episode error."""
        self.error = message
