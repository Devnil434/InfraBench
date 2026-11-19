"""Log collection, parsing, and buffering pipeline for InfraBench.

Extracts, normalizes, and filters log streams from Docker containers, local processes,
and simulated services into structured, validated LogEntry objects.
"""

from __future__ import annotations

import collections
import datetime
import re
from typing import Any, Deque, Dict, List, Optional, Union

from benchmark.environment import BaseEnvironmentProvisioner
from observability.schemas import (
    LogEntry,
    LogLevel,
    ensure_utc_datetime,
    utc_now,
)

# Regex to remove terminal ANSI color/style escape codes
ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

# Regex to detect ISO-8601 or standard timestamps at start of log lines
TIMESTAMP_PREFIX_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\s+(.*)$"
)

# Regex to match HTTP access log patterns
HTTP_ACCESS_RE = re.compile(
    r'"(?P<method>[A-Z]+)\s+(?P<path>/[^\s]*)\s+HTTP/[0-9.]+"\s+(?P<status>\d{3})'
)

# Severity ordering map for min_level filtering
LEVEL_SEVERITY_ORDER: Dict[LogLevel, int] = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from log strings."""
    return ANSI_ESCAPE_RE.sub("", text)


class LogParser:
    """Parses raw text and container log streams into structured LogEntry models."""

    LEVEL_PATTERNS = [
        (re.compile(r"\b(FATAL|CRITICAL|PANIC)\b", re.IGNORECASE), LogLevel.CRITICAL),
        (re.compile(r"\b(ERROR|ERR)\b", re.IGNORECASE), LogLevel.ERROR),
        (re.compile(r"\b(WARNING|WARN)\b", re.IGNORECASE), LogLevel.WARNING),
        (re.compile(r"\b(DEBUG)\b", re.IGNORECASE), LogLevel.DEBUG),
        (re.compile(r"\b(INFO)\b", re.IGNORECASE), LogLevel.INFO),
    ]

    def parse_line(
        self,
        raw_line: str,
        service: str,
        source: str = "stdout",
        default_timestamp: Optional[datetime.datetime] = None,
    ) -> LogEntry:
        """Parse a single raw log line into a LogEntry."""
        cleaned = strip_ansi(raw_line).strip()
        if not cleaned:
            cleaned = "<empty>"

        timestamp = default_timestamp or utc_now()
        message = cleaned

        # Attempt to extract leading timestamp
        ts_match = TIMESTAMP_PREFIX_RE.match(cleaned)
        if ts_match:
            raw_ts = ts_match.group(1)
            message = ts_match.group(2).strip() or cleaned
            try:
                timestamp = ensure_utc_datetime(raw_ts)
            except Exception:
                timestamp = default_timestamp or utc_now()

        # Determine log level
        level = self._detect_level(message)

        # Extract structured context attributes
        attributes: Dict[str, Any] = {}
        http_match = HTTP_ACCESS_RE.search(message)
        if http_match:
            attributes["http_method"] = http_match.group("method")
            attributes["http_path"] = http_match.group("path")
            attributes["http_status"] = int(http_match.group("status"))

        return LogEntry(
            timestamp=timestamp,
            service=service,
            level=level,
            message=message,
            attributes=attributes,
            source=source,
        )

    def _detect_level(self, text: str) -> LogLevel:
        """Detect log level based on keywords or default to INFO."""
        # Prioritize error indicators
        if "traceback (most recent call last)" in text.lower():
            return LogLevel.ERROR

        for pattern, lvl in self.LEVEL_PATTERNS:
            if pattern.search(text):
                return lvl

        return LogLevel.INFO

    def parse_text(
        self,
        text: str,
        service: str,
        source: str = "stdout",
    ) -> List[LogEntry]:
        """Parse multiline log text, collating multiline stack traces into parent entries."""
        if not text:
            return []

        lines = text.splitlines()
        entries: List[LogEntry] = []
        in_traceback = False

        for line in lines:
            stripped = strip_ansi(line)
            if not stripped.strip():
                continue

            # Check if this line is continuation of an indented stack frame or exception closing
            is_indented = line.startswith(" ") or line.startswith("\t")
            is_exception_line = in_traceback and bool(
                re.match(r"^[A-Za-z0-9_.]*(?:Error|Exception|Fault|Warning):", stripped)
            )

            if in_traceback and (is_indented or is_exception_line):
                # Append to preceding entry message and ensure error level
                last_entry = entries[-1]
                updated_message = f"{last_entry.message}\n{stripped.rstrip()}"
                entries[-1] = LogEntry(
                    timestamp=last_entry.timestamp,
                    service=last_entry.service,
                    level=LogLevel.ERROR,
                    message=updated_message,
                    attributes=last_entry.attributes,
                    source=last_entry.source,
                )
                if is_exception_line:
                    # Exception line ends the traceback block
                    in_traceback = False
            else:
                entry = self.parse_line(stripped, service=service, source=source)
                entries.append(entry)
                in_traceback = "traceback (most recent call last)" in stripped.lower()

        return entries


class LogCollector:
    """Manages circular log buffers, ingestion from provisioners, and querying."""

    def __init__(
        self,
        provisioner: Optional[BaseEnvironmentProvisioner] = None,
        max_buffer_per_service: int = 1000,
        parser: Optional[LogParser] = None,
    ) -> None:
        self.provisioner = provisioner
        self.max_buffer_per_service = max_buffer_per_service
        self.parser = parser or LogParser()
        self._buffers: Dict[str, Deque[LogEntry]] = collections.defaultdict(
            lambda: collections.deque(maxlen=self.max_buffer_per_service)
        )

    def ingest(self, entry: LogEntry) -> None:
        """Add a single pre-parsed LogEntry into the service buffer."""
        self._buffers[entry.service].append(entry)

    def ingest_raw(
        self,
        text: str,
        service: str,
        source: str = "stdout",
    ) -> List[LogEntry]:
        """Parse and ingest multiline raw log text into the service buffer."""
        entries = self.parser.parse_text(text, service=service, source=source)
        for e in entries:
            self.ingest(e)
        return entries

    def collect_from_provisioner(
        self,
        service: str,
        tail: int = 100,
    ) -> List[LogEntry]:
        """Pull trailing logs from the environment provisioner and ingest into buffer."""
        if self.provisioner is None:
            return []

        raw_logs = self.provisioner.get_service_logs(service, tail=tail)
        return self.ingest_raw(raw_logs, service=service, source="docker")

    def query(
        self,
        service: Optional[str] = None,
        min_level: Optional[Union[LogLevel, str]] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        query: Optional[str] = None,
        limit: int = 100,
    ) -> List[LogEntry]:
        """Query buffered log entries across one or all services with filtering."""
        # Collect target entries
        candidate_entries: List[LogEntry] = []
        if service:
            if service in self._buffers:
                candidate_entries.extend(self._buffers[service])
        else:
            for buf in self._buffers.values():
                candidate_entries.extend(buf)

        # Sort chronologically
        candidate_entries.sort(key=lambda e: e.timestamp)

        # Parse min_level threshold
        min_rank = 0
        if min_level:
            level_enum = (
                min_level
                if isinstance(min_level, LogLevel)
                else LogLevel._missing_(min_level) or LogLevel.INFO
            )
            min_rank = LEVEL_SEVERITY_ORDER.get(level_enum, 0)

        # Parse time filters
        since_utc = ensure_utc_datetime(since) if since else None
        until_utc = ensure_utc_datetime(until) if until else None

        # Filter entries
        results: List[LogEntry] = []
        for entry in candidate_entries:
            # Level check
            if min_rank > 0:
                entry_rank = LEVEL_SEVERITY_ORDER.get(entry.level, 0)
                if entry_rank < min_rank:
                    continue

            # Time check
            if since_utc and entry.timestamp < since_utc:
                continue
            if until_utc and entry.timestamp > until_utc:
                continue

            # Query substring check
            if query and query.lower() not in entry.message.lower():
                continue

            results.append(entry)
            if len(results) >= limit:
                break

        return results

    def clear(self, service: Optional[str] = None) -> None:
        """Clear log buffers for a specific service or all services."""
        if service:
            if service in self._buffers:
                self._buffers[service].clear()
        else:
            self._buffers.clear()

    def get_service_buffer_size(self, service: str) -> int:
        """Return the current number of buffered entries for a service."""
        return len(self._buffers.get(service, []))
