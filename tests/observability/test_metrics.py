"""Tests for metrics collection, response flattening, snapshot buffering, delta calculation, and querying."""

import datetime
from typing import Any, Dict, Optional, Tuple

import pytest

from observability.metrics import MetricsCollector, flatten_metrics
from observability.schemas import MetricSnapshot


SAMPLE_METRICS_RESPONSE = {
    "app_name": "InfraBench",
    "environment": "test",
    "uptime_seconds": 120.5,
    "timestamp": "2026-11-19T14:30:00Z",
    "system": {
        "cpu_percent": 24.5,
        "cpu_count": 8,
        "memory_used_mb": 1024.0,
        "memory_total_mb": 16384.0,
        "memory_percent": 6.25,
    },
    "http_requests": {
        "total": 500,
        "active": 3,
        "status_2xx": 480,
        "status_3xx": 5,
        "status_4xx": 10,
        "status_5xx": 5,
        "error_rate_percent": 3.0,
    },
    "latency_ms": {
        "avg": 15.2,
        "p50": 12.0,
        "p95": 45.0,
        "p99": 85.0,
        "min": 1.5,
        "max": 120.0,
    },
}


def test_flatten_metrics_nested_and_types():
    data = {
        "status": "UP",  # non-numeric, should be ignored
        "is_healthy": True,  # boolean, should not be converted to float
        "uptime": 60,  # integer converted to float
        "system": {
            "cpu_percent": 12.5,
            "nested": {"core_0": 5.0},
        },
    }
    flat = flatten_metrics(data)
    assert flat["uptime"] == 60.0
    assert flat["system.cpu_percent"] == 12.5
    assert flat["system.nested.core_0"] == 5.0
    assert "status" not in flat
    assert "is_healthy" not in flat


def test_flatten_metrics_unwraps_top_level_metrics():
    wrapped = {
        "status_code": 200,
        "metrics": {
            "cpu_percent": 45.0,
            "latency_ms": {"p95": 25.5},
        },
    }
    flat = flatten_metrics(wrapped)
    assert flat["cpu_percent"] == 45.0
    assert flat["latency_ms.p95"] == 25.5


def test_snapshot_from_response():
    collector = MetricsCollector()
    snapshot = collector.snapshot_from_response(SAMPLE_METRICS_RESPONSE, service="app")

    assert snapshot.service == "app"
    assert snapshot.timestamp.year == 2026
    assert snapshot.timestamp.month == 11
    assert snapshot.timestamp.day == 19
    assert snapshot.timestamp.hour == 14
    assert snapshot.timestamp.tzinfo == datetime.timezone.utc

    assert snapshot.labels["service"] == "app"
    assert snapshot.labels["environment"] == "test"
    assert snapshot.labels["app_name"] == "InfraBench"

    assert snapshot.metrics["system.cpu_percent"] == 24.5
    assert snapshot.metrics["system.cpu_count"] == 8.0
    assert snapshot.metrics["http_requests.total"] == 500.0
    assert snapshot.metrics["http_requests.error_rate_percent"] == 3.0
    assert snapshot.metrics["latency_ms.p95"] == 45.0
    assert snapshot.metrics["latency_ms.max"] == 120.0


def test_collector_collect_success():
    def mock_requester(url: str, method: str, data: Optional[Dict], timeout: float) -> Tuple[int, Any, float]:
        assert "/metrics" in url
        return 200, SAMPLE_METRICS_RESPONSE, 12.5

    collector = MetricsCollector(http_requester=mock_requester)
    snapshot = collector.collect(service="app")

    assert snapshot is not None
    assert snapshot.service == "app"
    assert collector.get_service_buffer_size("app") == 1
    assert collector.latest("app") == snapshot


def test_collector_collect_failure_graceful():
    def failing_requester(url: str, method: str, data: Optional[Dict], timeout: float) -> Tuple[int, Any, float]:
        return 503, {"error": "Service Unavailable"}, 5.0

    collector = MetricsCollector(http_requester=failing_requester)
    snapshot = collector.collect(service="app")

    assert snapshot is None
    assert collector.get_service_buffer_size("app") == 0
    assert collector.latest("app") is None


def test_circular_buffer_eviction():
    collector = MetricsCollector(max_buffer_per_service=3)

    for i in range(5):
        collector.ingest(
            MetricSnapshot(
                service="app",
                metrics={"metric_val": float(i)},
            )
        )

    assert collector.get_service_buffer_size("app") == 3
    snapshots = collector.query(service="app")
    values = [s.metrics["metric_val"] for s in snapshots]
    # Oldest 0, 1 evicted; 2, 3, 4 retained
    assert values == [2.0, 3.0, 4.0]


def test_collector_delta():
    collector = MetricsCollector()

    # No snapshots
    assert collector.delta("latency_ms.p95", service="app") is None

    # Only 1 snapshot
    collector.ingest(
        MetricSnapshot(
            service="app",
            metrics={"latency_ms.p95": 20.0, "cpu": 10.0},
        )
    )
    assert collector.delta("latency_ms.p95", service="app") is None

    # 2 snapshots
    collector.ingest(
        MetricSnapshot(
            service="app",
            metrics={"latency_ms.p95": 35.5, "cpu": 12.0},
        )
    )
    # 35.5 - 20.0 = 15.5
    assert collector.delta("latency_ms.p95", service="app") == 15.5
    assert collector.delta("cpu", service="app") == 2.0
    # Missing metric in delta
    assert collector.delta("nonexistent_metric", service="app") is None


def test_collector_query_filtering():
    collector = MetricsCollector()

    base_time = datetime.datetime(2026, 11, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)

    # Ingest snapshots across two services at different timestamps
    for i in range(4):
        collector.ingest(
            MetricSnapshot(
                timestamp=base_time + datetime.timedelta(minutes=i * 10),
                service="app",
                metrics={"cpu": float(10 + i), "shared": 1.0},
            )
        )

    for i in range(2):
        collector.ingest(
            MetricSnapshot(
                timestamp=base_time + datetime.timedelta(minutes=i * 10),
                service="db",
                metrics={"db_connections": float(5 + i), "shared": 2.0},
            )
        )

    # 1. Query by service
    app_snaps = collector.query(service="app")
    assert len(app_snaps) == 4
    db_snaps = collector.query(service="db")
    assert len(db_snaps) == 2

    # 2. Query across all services
    all_snaps = collector.query()
    assert len(all_snaps) == 6

    # 3. Query by metric_name existence
    db_conn_snaps = collector.query(metric_name="db_connections")
    assert len(db_conn_snaps) == 2

    # 4. Time filtering
    since = base_time + datetime.timedelta(minutes=15)
    until = base_time + datetime.timedelta(minutes=25)
    windowed = collector.query(service="app", since=since, until=until)
    # Should include minutes=20 (i=2)
    assert len(windowed) == 1
    assert windowed[0].metrics["cpu"] == 12.0

    # 5. Limit filtering
    limited = collector.query(service="app", limit=2)
    assert len(limited) == 2


def test_collector_timeseries():
    collector = MetricsCollector()
    t0 = datetime.datetime(2026, 11, 19, 10, 0, tzinfo=datetime.timezone.utc)
    t1 = datetime.datetime(2026, 11, 19, 10, 5, tzinfo=datetime.timezone.utc)
    t2 = datetime.datetime(2026, 11, 19, 10, 10, tzinfo=datetime.timezone.utc)

    collector.ingest(MetricSnapshot(timestamp=t0, service="app", metrics={"cpu": 10.0}))
    collector.ingest(MetricSnapshot(timestamp=t1, service="app", metrics={"other": 5.0}))  # no cpu
    collector.ingest(MetricSnapshot(timestamp=t2, service="app", metrics={"cpu": 25.0}))

    series = collector.timeseries("cpu", service="app")
    assert len(series) == 2
    assert series[0] == (t0, 10.0)
    assert series[1] == (t2, 25.0)


def test_collector_clear():
    collector = MetricsCollector()
    collector.ingest(MetricSnapshot(service="app", metrics={"cpu": 10.0}))
    collector.ingest(MetricSnapshot(service="db", metrics={"conn": 5.0}))

    # Clear app only
    collector.clear(service="app")
    assert collector.get_service_buffer_size("app") == 0
    assert collector.get_service_buffer_size("db") == 1

    # Clear all
    collector.clear()
    assert collector.get_service_buffer_size("db") == 0
