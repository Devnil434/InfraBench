"""Unit tests for Observability Foundation schemas and information barrier."""

import datetime
import json
import pytest
from pydantic import ValidationError

from observability.schemas import (
    AgentObservation,
    EventSeverity,
    IncidentEvent,
    InternalTelemetryRecord,
    LogEntry,
    LogLevel,
    MetricSnapshot,
    ensure_utc_datetime,
    utc_now,
)


def test_utc_now_and_ensure_utc_datetime():
    now = utc_now()
    assert now.tzinfo is not None
    assert now.tzinfo == datetime.timezone.utc

    # Naive datetime
    naive = datetime.datetime(2026, 1, 15, 12, 0, 0)
    aware = ensure_utc_datetime(naive)
    assert aware.tzinfo == datetime.timezone.utc
    assert aware.year == 2026

    # ISO string
    iso_str = "2026-06-01T10:30:00+00:00"
    aware_iso = ensure_utc_datetime(iso_str)
    assert aware_iso.tzinfo == datetime.timezone.utc

    # Float timestamp
    epoch = 1700000000.0
    aware_epoch = ensure_utc_datetime(epoch)
    assert aware_epoch.tzinfo == datetime.timezone.utc


def test_log_entry_valid_construction_and_defaults():
    entry = LogEntry(service="app", message="Service started successfully")

    assert entry.service == "app"
    assert entry.message == "Service started successfully"
    assert entry.level == LogLevel.INFO
    assert entry.source == "stdout"
    assert entry.attributes == {}
    assert entry.timestamp.tzinfo == datetime.timezone.utc


def test_log_entry_level_normalization_and_validation():
    # Case-insensitive normalization
    e1 = LogEntry(service="db", message="Query failed", level="error")
    assert e1.level == LogLevel.ERROR

    # Alias normalization: warn -> WARNING
    e2 = LogEntry(service="cache", message="High memory", level="warn")
    assert e2.level == LogLevel.WARNING

    # Alias normalization: fatal -> CRITICAL
    e3 = LogEntry(service="app", message="Crash", level="fatal")
    assert e3.level == LogLevel.CRITICAL

    # Invalid level raises ValidationError
    with pytest.raises(ValidationError) as exc_info:
        LogEntry(service="app", message="Test", level="NON_EXISTENT_LEVEL")
    assert "Invalid log level" in str(exc_info.value)


def test_log_entry_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        LogEntry(
            service="app",
            message="Test",
            unauthorized_field="leak",  # type: ignore[call-arg]
        )


def test_log_entry_json_serialization():
    entry = LogEntry(
        service="app",
        level=LogLevel.WARNING,
        message="Connection reset",
        attributes={"client_ip": "10.0.0.1", "retries": 3},
    )

    json_str = entry.model_dump_json()
    assert isinstance(json_str, str)

    parsed = json.loads(json_str)
    assert parsed["service"] == "app"
    assert parsed["level"] == "WARNING"
    assert parsed["attributes"]["retries"] == 3
    assert "timestamp" in parsed


def test_metric_snapshot_valid_construction_and_defaults():
    metric = MetricSnapshot(
        service="app",
        metrics={"cpu_percent": 24.5, "memory_used_mb": 512.0},
        labels={"environment": "benchmark"},
        unit="percent",
    )

    assert metric.service == "app"
    assert metric.metrics["cpu_percent"] == 24.5
    assert metric.labels["environment"] == "benchmark"
    assert metric.unit == "percent"
    assert metric.timestamp.tzinfo == datetime.timezone.utc

    # JSON serialization
    data = json.loads(metric.model_dump_json())
    assert data["metrics"]["cpu_percent"] == 24.5


def test_metric_snapshot_empty_defaults():
    metric = MetricSnapshot(service="cache")
    assert metric.metrics == {}
    assert metric.labels == {}
    assert metric.unit is None


def test_incident_event_valid_construction_and_defaults():
    event = IncidentEvent(
        source="health_probe",
        description="Database connection refused on port 5432",
        affected_service="db",
    )

    assert event.source == "health_probe"
    assert event.description == "Database connection refused on port 5432"
    assert event.affected_service == "db"
    assert event.severity == EventSeverity.INFO
    assert event.resolved is False
    assert len(event.event_id) > 0
    assert event.metadata == {}
    assert event.timestamp.tzinfo == datetime.timezone.utc


def test_incident_event_severity_normalization_and_validation():
    # Normalization
    ev_crit = IncidentEvent(source="monitor", description="OOM", severity="critical")
    assert ev_crit.severity == EventSeverity.CRITICAL

    ev_warn = IncidentEvent(source="monitor", description="Degraded", severity="warning")
    assert ev_warn.severity == EventSeverity.MEDIUM

    ev_err = IncidentEvent(source="monitor", description="Timeout", severity="error")
    assert ev_err.severity == EventSeverity.HIGH

    # Invalid severity raises ValidationError
    with pytest.raises(ValidationError) as exc_info:
        IncidentEvent(source="monitor", description="Test", severity="UNKNOWN_SEV")
    assert "Invalid event severity" in str(exc_info.value)


def test_agent_observation_valid_construction():
    log = LogEntry(service="app", message="HTTP 500 error logged")
    metric = MetricSnapshot(service="app", metrics={"http_5xx": 12.0})
    event = IncidentEvent(source="alerter", description="High 5xx error rate", severity=EventSeverity.HIGH)

    obs = AgentObservation(
        episode_id="ep_123",
        service="app",
        status="DEGRADED",
        logs=[log],
        metrics=[metric],
        events=[event],
        summary="Service experiencing elevated error rate",
        metadata={"region": "local"},
    )

    assert obs.episode_id == "ep_123"
    assert obs.status == "DEGRADED"
    assert len(obs.logs) == 1
    assert len(obs.metrics) == 1
    assert len(obs.events) == 1
    assert obs.timestamp.tzinfo == datetime.timezone.utc

    # Ensure JSON serializable
    dumped = obs.model_dump_json()
    parsed = json.loads(dumped)
    assert parsed["logs"][0]["message"] == "HTTP 500 error logged"
    assert parsed["events"][0]["severity"] == "HIGH"


def test_agent_observation_information_barrier_protection():
    # Attempting to leak internal secrets into metadata must raise ValidationError
    forbidden_test_cases = [
        {"ground_truth": "Database password or root cause"},
        {"faults": ["pod_crash"]},
        {"success_probes": [{"type": "health"}]},
        {"injection_params": {"signal": "SIGKILL"}},
        {"root_cause": "Abrupt kill"},
        {"expected_fix": "restart container"},
    ]

    for forbidden_dict in forbidden_test_cases:
        with pytest.raises(ValidationError) as exc_info:
            AgentObservation(
                service="app",
                metadata=forbidden_dict,
            )
        assert "Information barrier violation" in str(exc_info.value)


def test_agent_observation_extra_fields_forbidden():
    with pytest.raises(ValidationError):
        AgentObservation(
            service="app",
            ground_truth="leaked root cause",  # type: ignore[call-arg]
        )


def test_internal_telemetry_record_separation_and_conversion():
    record = InternalTelemetryRecord(
        scenario_id="pod_crash_001",
        episode_id="ep_001",
        raw_logs=[
            LogEntry(service="app", message="App ok"),
            LogEntry(service="db", message="DB crashed"),
        ],
        raw_metrics=[
            MetricSnapshot(service="app", metrics={"latency": 10.0}),
            MetricSnapshot(service="db", metrics={"connections": 0.0}),
        ],
        raw_events=[
            IncidentEvent(source="check", description="db down", affected_service="db"),
        ],
        ground_truth={"root_cause": "SIGKILL sent to postgres"},
        fault_parameters={"target": "db", "signal": "SIGKILL"},
        evaluator_probes=[{"endpoint": "/health", "expected": 200}],
    )

    # Convert to agent observation
    agent_obs = record.to_agent_observation(service="app", summary="Application view")

    # Verify agent observation contains only agent-safe data
    assert agent_obs.episode_id == "ep_001"
    assert agent_obs.service == "app"
    assert len(agent_obs.logs) == 1
    assert agent_obs.logs[0].service == "app"
    assert len(agent_obs.metrics) == 1
    assert agent_obs.metrics[0].service == "app"

    # Crucially verify ground-truth and internal evaluator data are not exposed
    assert not hasattr(agent_obs, "ground_truth")
    assert not hasattr(agent_obs, "fault_parameters")
    assert not hasattr(agent_obs, "evaluator_probes")
    assert "ground_truth" not in agent_obs.metadata
