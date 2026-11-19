"""Metrics collection, telemetry snapshots, and time-series query engine for InfraBench.

Extracts, flattens, and buffers numerical metrics from service /metrics endpoints,
providing point-in-time snapshots, delta calculations, and time-range queries.
"""

from __future__ import annotations

import collections
import datetime
import json
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

from observability.schemas import (
    MetricSnapshot,
    ensure_utc_datetime,
    utc_now,
)


def flatten_metrics(data: Dict[str, Any], prefix: str = "") -> Dict[str, float]:
    """Recursively flatten nested telemetry dictionaries into dot-notated float values.

    Example:
        {"system": {"cpu_percent": 15.2}} -> {"system.cpu_percent": 15.2}
    """
    flat: Dict[str, float] = {}
    target = data

    # If wrapped under a top-level "metrics" dict without a prefix, unwrap it
    if not prefix and "metrics" in data and isinstance(data["metrics"], dict):
        target = data["metrics"]

    for k, v in target.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            flat.update(flatten_metrics(v, prefix=key))
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            flat[key] = float(v)

    return flat


def default_http_request(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    timeout: float = 5.0,
) -> Tuple[int, Any, float]:
    """Standard HTTP request helper with latency measurement."""
    start = time.perf_counter()
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode("utf-8") if data is not None else None,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method.upper(),
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            latency_ms = (time.perf_counter() - start) * 1000.0
            raw_body = response.read().decode("utf-8")
            try:
                body = json.loads(raw_body)
            except Exception:
                body = raw_body
            return response.status, body, latency_ms
    except urllib.error.HTTPError as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        raw_body = exc.read().decode("utf-8")
        try:
            body = json.loads(raw_body)
        except Exception:
            body = raw_body
        return exc.code, body, latency_ms
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return 0, {"error": str(exc)}, latency_ms


class MetricsCollector:
    """Manages periodic or on-demand telemetry collection, buffering, and trend analysis."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        max_buffer_per_service: int = 100,
        http_requester: Optional[
            Callable[[str, str, Optional[Dict[str, Any]], float], Tuple[int, Any, float]]
        ] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_buffer_per_service = max_buffer_per_service
        self._http_requester = http_requester or default_http_request
        self._buffers: Dict[str, Deque[MetricSnapshot]] = collections.defaultdict(
            lambda: collections.deque(maxlen=self.max_buffer_per_service)
        )

    def snapshot_from_response(
        self,
        response_body: Dict[str, Any],
        service: str = "app",
        timestamp: Optional[datetime.datetime] = None,
        labels: Optional[Dict[str, str]] = None,
        unit: Optional[str] = None,
    ) -> MetricSnapshot:
        """Parse, flatten, and validate an HTTP telemetry response into a MetricSnapshot."""
        # Determine timestamp
        if timestamp is not None:
            ts = ensure_utc_datetime(timestamp)
        elif "timestamp" in response_body:
            try:
                ts = ensure_utc_datetime(response_body["timestamp"])
            except Exception:
                ts = utc_now()
        else:
            ts = utc_now()

        # Build labels
        final_labels: Dict[str, str] = {"service": service}
        if "environment" in response_body and isinstance(response_body["environment"], str):
            final_labels["environment"] = response_body["environment"]
        if "app_name" in response_body and isinstance(response_body["app_name"], str):
            final_labels["app_name"] = response_body["app_name"]
        if labels:
            final_labels.update(labels)

        # Flatten metrics
        flat_metrics = flatten_metrics(response_body)

        return MetricSnapshot(
            timestamp=ts,
            service=service,
            metrics=flat_metrics,
            labels=final_labels,
            unit=unit,
        )

    def ingest(self, snapshot: MetricSnapshot) -> None:
        """Add a MetricSnapshot to the per-service circular buffer."""
        self._buffers[snapshot.service].append(snapshot)

    def collect(
        self,
        service: str = "app",
        endpoint: str = "/metrics",
    ) -> Optional[MetricSnapshot]:
        """Perform an HTTP GET request to pull telemetry and buffer the snapshot.

        Returns None if request fails or receives a non-200 status code.
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        status_code, body, _ = self._http_requester(url, "GET", None, 5.0)

        if status_code == 200 and isinstance(body, dict):
            snapshot = self.snapshot_from_response(body, service=service)
            self.ingest(snapshot)
            return snapshot

        return None

    def latest(self, service: str = "app") -> Optional[MetricSnapshot]:
        """Return the most recently collected snapshot for a service."""
        buf = self._buffers.get(service)
        if buf and len(buf) > 0:
            return buf[-1]
        return None

    def delta(self, metric_path: str, service: str = "app") -> Optional[float]:
        """Calculate the change (latest - previous) for a specific metric path.

        Returns None if fewer than 2 snapshots exist or metric is missing in either.
        """
        buf = self._buffers.get(service)
        if not buf or len(buf) < 2:
            return None

        current = buf[-1]
        previous = buf[-2]

        if metric_path not in current.metrics or metric_path not in previous.metrics:
            return None

        return round(current.metrics[metric_path] - previous.metrics[metric_path], 4)

    def query(
        self,
        service: Optional[str] = None,
        metric_name: Optional[str] = None,
        since: Optional[datetime.datetime] = None,
        until: Optional[datetime.datetime] = None,
        limit: int = 50,
    ) -> List[MetricSnapshot]:
        """Query buffered metric snapshots with service, metric existence, and time filtering."""
        candidates: List[MetricSnapshot] = []
        if service:
            if service in self._buffers:
                candidates.extend(self._buffers[service])
        else:
            for buf in self._buffers.values():
                candidates.extend(buf)

        candidates.sort(key=lambda s: s.timestamp)

        since_utc = ensure_utc_datetime(since) if since else None
        until_utc = ensure_utc_datetime(until) if until else None

        results: List[MetricSnapshot] = []
        for snapshot in candidates:
            if since_utc and snapshot.timestamp < since_utc:
                continue
            if until_utc and snapshot.timestamp > until_utc:
                continue
            if metric_name and metric_name not in snapshot.metrics:
                continue

            results.append(snapshot)
            if len(results) >= limit:
                break

        return results

    def timeseries(
        self,
        metric_name: str,
        service: str = "app",
    ) -> List[Tuple[datetime.datetime, float]]:
        """Return a chronological sequence of (timestamp, value) tuples for a metric."""
        buf = self._buffers.get(service, [])
        return [
            (snapshot.timestamp, snapshot.metrics[metric_name])
            for snapshot in buf
            if metric_name in snapshot.metrics
        ]

    def clear(self, service: Optional[str] = None) -> None:
        """Clear buffered snapshots for a specific service or all services."""
        if service:
            if service in self._buffers:
                self._buffers[service].clear()
        else:
            self._buffers.clear()

    def get_service_buffer_size(self, service: str) -> int:
        """Return the current number of buffered snapshots for a service."""
        return len(self._buffers.get(service, []))
