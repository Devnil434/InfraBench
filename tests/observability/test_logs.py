"""Tests for log parser, circular buffer, ingestion, and query filtering."""

import datetime
from benchmark.environment import MockEnvironmentProvisioner
from observability.logs import LogCollector, LogParser, strip_ansi
from observability.schemas import LogEntry, LogLevel


def test_strip_ansi_sequences():
    raw = "\x1b[32mINFO:\x1b[0m \x1b[1mUvicorn running on http://0.0.0.0:8000\x1b[0m"
    clean = strip_ansi(raw)
    assert clean == "INFO: Uvicorn running on http://0.0.0.0:8000"


def test_log_parser_iso_timestamp_and_attributes():
    parser = LogParser()
    raw = '2026-11-19T14:30:15.500000Z INFO: 10.0.0.2 - "GET /health HTTP/1.1" 200 OK'
    entry = parser.parse_line(raw, service="app")

    assert entry.service == "app"
    assert entry.level == LogLevel.INFO
    assert entry.timestamp.year == 2026
    assert entry.timestamp.month == 11
    assert entry.timestamp.day == 19
    assert entry.timestamp.hour == 14
    assert entry.timestamp.tzinfo == datetime.timezone.utc
    assert entry.attributes.get("http_method") == "GET"
    assert entry.attributes.get("http_path") == "/health"
    assert entry.attributes.get("http_status") == 200


def test_log_parser_detects_various_levels():
    parser = LogParser()

    assert parser.parse_line("FATAL: Out of memory", service="app").level == LogLevel.CRITICAL
    assert parser.parse_line("CRITICAL - Redis connection lost", service="cache").level == LogLevel.CRITICAL
    assert parser.parse_line("ERROR: PostgreSQL port 5432 closed", service="db").level == LogLevel.ERROR
    assert parser.parse_line("WARNING: Cache hit ratio below 50%", service="cache").level == LogLevel.WARNING
    assert parser.parse_line("DEBUG: query executed in 1.2ms", service="db").level == LogLevel.DEBUG
    assert parser.parse_line("Regular message without keyword", service="app").level == LogLevel.INFO


def test_log_parser_multiline_traceback_collation():
    parser = LogParser()
    raw_traceback = """INFO: Request received
Traceback (most recent call last):
  File "app/main.py", line 42, in get_orders
    raise ConnectionRefusedError("Database down")
ConnectionRefusedError: Database down
INFO: Next request
"""
    entries = parser.parse_text(raw_traceback, service="app")
    assert len(entries) == 3

    assert entries[0].message == "INFO: Request received"
    assert entries[0].level == LogLevel.INFO

    # The traceback and its continuation lines must be collated into a single entry
    assert entries[1].level == LogLevel.ERROR
    assert "Traceback (most recent call last):" in entries[1].message
    assert 'raise ConnectionRefusedError("Database down")' in entries[1].message
    assert "ConnectionRefusedError: Database down" in entries[1].message

    assert entries[2].message == "INFO: Next request"
    assert entries[2].level == LogLevel.INFO


def test_log_collector_circular_buffer_eviction():
    collector = LogCollector(max_buffer_per_service=3)

    for i in range(5):
        collector.ingest(
            LogEntry(service="app", message=f"Message {i}")
        )

    assert collector.get_service_buffer_size("app") == 3
    entries = collector.query(service="app")
    messages = [e.message for e in entries]
    # Oldest entries 0 and 1 were evicted; 2, 3, 4 remain
    assert messages == ["Message 2", "Message 3", "Message 4"]


def test_log_collector_collect_from_provisioner():
    mock_prov = MockEnvironmentProvisioner(
        services={
            "app": {"running": True, "healthy": True, "logs": "INFO: app startup\nWARN: high latency\n"},
            "db": {"running": True, "healthy": True, "logs": "LOG: database ready\n"},
        }
    )
    collector = LogCollector(provisioner=mock_prov)

    entries = collector.collect_from_provisioner("app", tail=10)
    assert len(entries) == 2
    assert collector.get_service_buffer_size("app") == 2
    assert entries[0].level == LogLevel.INFO
    assert entries[1].level == LogLevel.WARNING
    assert entries[0].source == "docker"


def test_log_collector_query_filtering():
    collector = LogCollector()

    base_time = datetime.datetime(2026, 6, 1, 10, 0, 0, tzinfo=datetime.timezone.utc)
    t1 = base_time + datetime.timedelta(minutes=1)
    t2 = base_time + datetime.timedelta(minutes=2)
    t3 = base_time + datetime.timedelta(minutes=3)
    t4 = base_time + datetime.timedelta(minutes=4)

    collector.ingest(LogEntry(timestamp=t1, service="app", level=LogLevel.INFO, message="User logged in"))
    collector.ingest(LogEntry(timestamp=t2, service="app", level=LogLevel.WARNING, message="DB slow response"))
    collector.ingest(LogEntry(timestamp=t3, service="db", level=LogLevel.ERROR, message="Disk full on volume"))
    collector.ingest(LogEntry(timestamp=t4, service="cache", level=LogLevel.CRITICAL, message="OOM killer invoked"))

    # 1. Filter by service
    db_logs = collector.query(service="db")
    assert len(db_logs) == 1
    assert db_logs[0].message == "Disk full on volume"

    # 2. Filter by minimum level (WARNING -> should include WARNING, ERROR, CRITICAL)
    warn_and_above = collector.query(min_level=LogLevel.WARNING)
    assert len(warn_and_above) == 3
    assert all(e.level in {LogLevel.WARNING, LogLevel.ERROR, LogLevel.CRITICAL} for e in warn_and_above)

    # 3. Filter by error only
    err_and_above = collector.query(min_level="ERROR")
    assert len(err_and_above) == 2
    assert [e.service for e in err_and_above] == ["db", "cache"]

    # 4. Filter by text query
    slow_logs = collector.query(query="slow")
    assert len(slow_logs) == 1
    assert slow_logs[0].message == "DB slow response"

    # 5. Filter by time window
    time_window_logs = collector.query(since=t2, until=t3)
    assert len(time_window_logs) == 2
    assert [e.message for e in time_window_logs] == ["DB slow response", "Disk full on volume"]

    # 6. Limit constraint
    limited_logs = collector.query(limit=2)
    assert len(limited_logs) == 2


def test_log_collector_clear_buffers():
    collector = LogCollector()
    collector.ingest(LogEntry(service="app", message="Log 1"))
    collector.ingest(LogEntry(service="db", message="Log 2"))

    assert collector.get_service_buffer_size("app") == 1
    assert collector.get_service_buffer_size("db") == 1

    # Clear single service
    collector.clear(service="app")
    assert collector.get_service_buffer_size("app") == 0
    assert collector.get_service_buffer_size("db") == 1

    # Clear all
    collector.clear()
    assert collector.get_service_buffer_size("db") == 0
