"""Telemetry collector and middleware for CPU, memory, latency, and error-rate monitoring."""

import asyncio
import collections
import datetime
import math
import time
from typing import Dict, Any, List

import psutil
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.schemas.metrics import MetricsResponse, SystemMetrics, RequestMetrics, LatencyMetrics


class TelemetryCollector:
    """Collects and aggregates system and application metrics."""

    def __init__(self, max_latency_samples: int = 10000) -> None:
        self.start_time = time.time()
        self.total_requests = 0
        self.active_requests = 0
        self.status_2xx = 0
        self.status_3xx = 0
        self.status_4xx = 0
        self.status_5xx = 0
        self.latencies: collections.deque = collections.deque(maxlen=max_latency_samples)
        self._lock = asyncio.Lock()

        # Warm up psutil cpu measurement
        psutil.cpu_percent(interval=None)

    @property
    def uptime_seconds(self) -> float:
        """Calculate elapsed uptime in seconds."""
        return round(time.time() - self.start_time, 2)

    def record_request_start(self) -> None:
        """Record the beginning of a request."""
        self.active_requests += 1

    def record_request_end(self, status_code: int, duration_ms: float) -> None:
        """Record the completion of a request."""
        self.active_requests = max(0, self.active_requests - 1)
        self.total_requests += 1
        self.latencies.append(duration_ms)

        if 200 <= status_code < 300:
            self.status_2xx += 1
        elif 300 <= status_code < 400:
            self.status_3xx += 1
        elif 400 <= status_code < 500:
            self.status_4xx += 1
        else:
            self.status_5xx += 1

    def get_latency_stats(self) -> Dict[str, float]:
        """Calculate min, max, avg, and percentiles (p50, p95, p99) for latencies."""
        if not self.latencies:
            return {
                "avg": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
                "min": 0.0,
                "max": 0.0,
            }

        sorted_latencies: List[float] = sorted(self.latencies)
        count = len(sorted_latencies)

        def percentile(p: float) -> float:
            idx = int(math.ceil((p / 100.0) * count)) - 1
            return round(sorted_latencies[max(0, min(idx, count - 1))], 2)

        avg_latency = round(sum(sorted_latencies) / count, 2)
        min_latency = round(sorted_latencies[0], 2)
        max_latency = round(sorted_latencies[-1], 2)

        return {
            "avg": avg_latency,
            "p50": percentile(50.0),
            "p95": percentile(95.0),
            "p99": percentile(99.0),
            "min": min_latency,
            "max": max_latency,
        }

    def get_system_metrics(self) -> SystemMetrics:
        """Collect host or container CPU and memory metrics."""
        cpu_pct = round(psutil.cpu_percent(interval=None), 2)
        cpu_cnt = psutil.cpu_count(logical=True) or 1
        mem = psutil.virtual_memory()

        return SystemMetrics(
            cpu_percent=cpu_pct,
            cpu_count=cpu_cnt,
            memory_used_mb=round(mem.used / (1024 * 1024), 2),
            memory_total_mb=round(mem.total / (1024 * 1024), 2),
            memory_percent=round(mem.percent, 2),
        )

    def get_metrics_snapshot(self) -> MetricsResponse:
        """Generate a complete structured metrics snapshot."""
        system = self.get_system_metrics()
        latency_stats = self.get_latency_stats()

        error_count = self.status_4xx + self.status_5xx
        error_rate = (
            round((error_count / self.total_requests) * 100, 2)
            if self.total_requests > 0
            else 0.0
        )

        request_metrics = RequestMetrics(
            total=self.total_requests,
            active=self.active_requests,
            status_2xx=self.status_2xx,
            status_3xx=self.status_3xx,
            status_4xx=self.status_4xx,
            status_5xx=self.status_5xx,
            error_rate_percent=error_rate,
        )

        latency_metrics = LatencyMetrics(
            avg=latency_stats["avg"],
            p50=latency_stats["p50"],
            p95=latency_stats["p95"],
            p99=latency_stats["p99"],
            min=latency_stats["min"],
            max=latency_stats["max"],
        )

        return MetricsResponse(
            app_name=settings.app_name,
            environment=settings.app_env,
            uptime_seconds=self.uptime_seconds,
            timestamp=datetime.datetime.utcnow().isoformat(),
            system=system,
            http_requests=request_metrics,
            latency_ms=latency_metrics,
        )


# Global collector instance
telemetry_collector = TelemetryCollector()


class TelemetryMiddleware(BaseHTTPMiddleware):
    """Middleware to track request durations, active counts, and status codes."""

    async def dispatch(self, request: Request, call_next) -> Response:
        telemetry_collector.record_request_start()
        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            telemetry_collector.record_request_end(status_code, duration_ms)
