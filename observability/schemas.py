"""Standardized Pydantic v2 schemas for InfraBench Observability.

Defines schemas for:
- LogEntry
- MetricSnapshot
- IncidentEvent
- AgentObservation

Enforces timezone-aware UTC timestamps, strict JSON serializability, log level
and severity validation, and strict separation between internal trusted benchmark
data and agent-visible observations.
"""

from __future__ import annotations

import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime.datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.datetime.now(datetime.timezone.utc)


def ensure_utc_datetime(v: Any) -> datetime.datetime:
    """Validate and coerce datetime into timezone-aware UTC."""
    if v is None:
        return utc_now()
    if isinstance(v, str):
        # Parse ISO string
        dt = datetime.datetime.fromisoformat(v)
    elif isinstance(v, (int, float)):
        # Epoch timestamp
        dt = datetime.datetime.fromtimestamp(v, tz=datetime.timezone.utc)
    elif isinstance(v, datetime.datetime):
        dt = v
    else:
        raise ValueError(f"Cannot parse datetime from: {v}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    else:
        dt = dt.astimezone(datetime.timezone.utc)
    return dt


class LogLevel(str, Enum):
    """Standardized logging severity levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @classmethod
    def _missing_(cls, value: object) -> Optional[LogLevel]:
        if isinstance(value, str):
            norm = value.strip().upper()
            if norm == "WARN":
                return cls.WARNING
            if norm == "FATAL":
                return cls.CRITICAL
            for member in cls:
                if member.value == norm:
                    return member
        return None


class EventSeverity(str, Enum):
    """Incident and alert severity classifications."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def _missing_(cls, value: object) -> Optional[EventSeverity]:
        if isinstance(value, str):
            norm = value.strip().upper()
            if norm in {"WARN", "WARNING"}:
                return cls.MEDIUM
            if norm in {"ERR", "ERROR"}:
                return cls.HIGH
            for member in cls:
                if member.value == norm:
                    return member
        return None


class LogEntry(BaseModel):
    """Structured representation of a single log line emitted by a service or container."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime.datetime = Field(
        default_factory=utc_now,
        description="Timezone-aware UTC timestamp of the log entry",
    )
    service: str = Field(min_length=1, description="Name of the originating service/container")
    level: LogLevel = Field(default=LogLevel.INFO, description="Log severity level")
    message: str = Field(min_length=1, description="Raw log text content")
    attributes: Dict[str, Any] = Field(
        default_factory=dict,
        description="Structured key-value context attributes",
    )
    source: Optional[str] = Field(
        default="stdout",
        description="Log stream source (e.g. stdout, stderr, docker)",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime.datetime:
        return ensure_utc_datetime(v)

    @field_validator("level", mode="before")
    @classmethod
    def validate_level(cls, v: Any) -> LogLevel:
        if isinstance(v, LogLevel):
            return v
        lvl = LogLevel._missing_(v)
        if lvl is not None:
            return lvl
        raise ValueError(
            f"Invalid log level '{v}'. Allowed levels: {[lvl.value for lvl in LogLevel]}"
        )


class MetricSnapshot(BaseModel):
    """Point-in-time telemetry metric observation."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime.datetime = Field(
        default_factory=utc_now,
        description="Timezone-aware UTC timestamp of metric collection",
    )
    service: str = Field(min_length=1, description="Target service the metrics belong to")
    metrics: Dict[str, float] = Field(
        default_factory=dict,
        description="Numerical metric measurements (e.g. cpu_percent, latency_ms)",
    )
    labels: Dict[str, str] = Field(
        default_factory=dict,
        description="Dimensional labels or tags (e.g. method=GET, status=503)",
    )
    unit: Optional[str] = Field(
        default=None,
        description="Optional measurement unit (e.g. ms, percent, count)",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime.datetime:
        return ensure_utc_datetime(v)


class IncidentEvent(BaseModel):
    """Discrete alert, state transition, or infrastructure incident event."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime.datetime = Field(
        default_factory=utc_now,
        description="Timezone-aware UTC timestamp of event generation",
    )
    event_id: str = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique event identifier",
    )
    severity: EventSeverity = Field(
        default=EventSeverity.INFO,
        description="Event severity level",
    )
    source: str = Field(
        min_length=1,
        description="Originating monitor or probe (e.g. healthcheck, alertmanager)",
    )
    description: str = Field(
        min_length=1,
        description="Human-readable event details",
    )
    affected_service: Optional[str] = Field(
        default=None,
        description="Primary service affected by the event",
    )
    resolved: bool = Field(
        default=False,
        description="Whether the event condition has been marked resolved",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Additional context metadata",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime.datetime:
        return ensure_utc_datetime(v)

    @field_validator("severity", mode="before")
    @classmethod
    def validate_severity(cls, v: Any) -> EventSeverity:
        if isinstance(v, EventSeverity):
            return v
        sev = EventSeverity._missing_(v)
        if sev is not None:
            return sev
        raise ValueError(
            f"Invalid event severity '{v}'. Allowed severities: {[s.value for s in EventSeverity]}"
        )


class AgentObservation(BaseModel):
    """Agent-visible observation payload.

    Strictly isolated: does NOT contain ground_truth, evaluator probe secrets,
    or internal fault injection parameters.
    """

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime.datetime = Field(
        default_factory=utc_now,
        description="Timezone-aware UTC timestamp when observation was compiled",
    )
    episode_id: Optional[str] = Field(
        default=None,
        description="Active episode ID if associated with a benchmark run",
    )
    service: Optional[str] = Field(
        default=None,
        description="Specific service scope of this observation, if applicable",
    )
    status: Optional[str] = Field(
        default=None,
        description="High-level health or operating status (e.g. UP, DEGRADED)",
    )
    logs: List[LogEntry] = Field(
        default_factory=list,
        description="Filtered log lines visible to the agent",
    )
    metrics: List[MetricSnapshot] = Field(
        default_factory=list,
        description="Telemetry metric snapshots visible to the agent",
    )
    events: List[IncidentEvent] = Field(
        default_factory=list,
        description="Active alerts and incident notifications visible to the agent",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Brief summary of observations",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict,
        description="Safe agent-facing metadata",
    )

    @field_validator("timestamp", mode="before")
    @classmethod
    def validate_timestamp(cls, v: Any) -> datetime.datetime:
        return ensure_utc_datetime(v)

    @model_validator(mode="after")
    def verify_information_barrier(self) -> AgentObservation:
        """Ensure no trusted evaluator or fault parameters are leaked into agent observations."""
        forbidden_keys = {
            "ground_truth",
            "faults",
            "success_probes",
            "injection_params",
            "root_cause",
            "expected_fix",
        }
        found_in_metadata = forbidden_keys.intersection(self.metadata.keys())
        if found_in_metadata:
            raise ValueError(
                f"Information barrier violation: metadata contains forbidden internal keys: {found_in_metadata}"
            )
        return self


class InternalTelemetryRecord(BaseModel):
    """Internal trusted telemetry container holding full diagnostic context including ground-truth."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime.datetime = Field(default_factory=utc_now)
    scenario_id: str
    episode_id: Optional[str] = None
    raw_logs: List[LogEntry] = Field(default_factory=list)
    raw_metrics: List[MetricSnapshot] = Field(default_factory=list)
    raw_events: List[IncidentEvent] = Field(default_factory=list)
    ground_truth: Optional[Dict[str, Any]] = None
    fault_parameters: Optional[Dict[str, Any]] = None
    evaluator_probes: Optional[List[Dict[str, Any]]] = None

    def to_agent_observation(
        self,
        service: Optional[str] = None,
        summary: Optional[str] = None,
    ) -> AgentObservation:
        """Strip internal secrets, ground-truth, and evaluator probes to generate an AgentObservation."""
        # Filter logs for requested service if specified
        filtered_logs = (
            [l for l in self.raw_logs if l.service == service]
            if service
            else list(self.raw_logs)
        )
        filtered_metrics = (
            [m for m in self.raw_metrics if m.service == service]
            if service
            else list(self.raw_metrics)
        )
        filtered_events = (
            [e for e in self.raw_events if e.affected_service == service or e.affected_service is None]
            if service
            else list(self.raw_events)
        )

        return AgentObservation(
            timestamp=utc_now(),
            episode_id=self.episode_id,
            service=service,
            logs=filtered_logs,
            metrics=filtered_metrics,
            events=filtered_events,
            summary=summary,
            metadata={"service_scope": service} if service else {},
        )
