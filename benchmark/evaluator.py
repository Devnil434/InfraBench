"""Independent success evaluator engine for InfraBench.

Executes declared success probes (HttpProbe, HealthProbe, MetricProbe) against target services
to independently verify whether infrastructure has recovered, without relying on agent claims.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from benchmark.schema import (
    HealthProbe,
    HttpProbe,
    MetricProbe,
    Scenario,
    SuccessProbe,
)

logger = logging.getLogger("infrabench.evaluator")


def _is_json_subset(expected: Any, actual: Any) -> bool:
    """Recursively check if `expected` is a subset of `actual`."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        for k, v in expected.items():
            if k not in actual:
                return False
            if not _is_json_subset(v, actual[k]):
                return False
        return True

    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) > len(actual):
            return False
        return all(
            any(_is_json_subset(exp_item, act_item) for act_item in actual)
            for exp_item in expected
        )

    return expected == actual


def _extract_metric_value(data: Dict[str, Any], path: str) -> Optional[float]:
    """Extract nested metric value using dot notation (e.g., 'latency_ms.p95')."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    try:
        return float(current)
    except (TypeError, ValueError):
        return None


def _compare_metric(observed: float, expected: float, operator: str) -> bool:
    """Evaluate comparator operator on metric values."""
    if operator == "lt":
        return observed < expected
    if operator == "lte":
        return observed <= expected
    if operator == "gt":
        return observed > expected
    if operator == "gte":
        return observed >= expected
    if operator == "eq":
        return math.isclose(observed, expected, rel_tol=1e-5, abs_tol=1e-5)
    return False


@dataclass
class ProbeResult:
    """Evaluation result for a single probe."""

    probe_name: str
    probe_type: str
    passed: bool
    observed_value: Any
    expected_value: Any
    latency_ms: float = 0.0
    error: Optional[str] = None
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationReport:
    """Aggregated evaluation report for a benchmark scenario."""

    success: bool
    score: float
    total_probes: int
    passed_probes: int
    failed_probes: int
    duration_seconds: float
    probe_results: List[ProbeResult] = field(default_factory=list)
    timestamp: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "score": self.score,
            "total_probes": self.total_probes,
            "passed_probes": self.passed_probes,
            "failed_probes": self.failed_probes,
            "duration_seconds": self.duration_seconds,
            "probe_results": [pr.to_dict() for pr in self.probe_results],
            "timestamp": self.timestamp,
        }


class SuccessEvaluator:
    """Evaluates success probes independently against the running environment."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        http_requester: Optional[
            Callable[[str, str, Optional[Dict[str, Any]], float], Tuple[int, Any, float]]
        ] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._http_requester = http_requester or self._default_http_request

    def _default_http_request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Tuple[int, Any, float]:
        """Perform an HTTP request and return (status_code, parsed_json_or_text, latency_ms)."""
        req_data = json.dumps(data).encode("utf-8") if data else None
        headers = {"User-Agent": "InfraBench-Evaluator"}
        if req_data:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        start_time = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                body = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                return resp.status, parsed, elapsed_ms
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return exc.code, parsed, elapsed_ms
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return 0, str(exc), elapsed_ms

    def evaluate_http_probe(self, probe: HttpProbe) -> ProbeResult:
        """Evaluate an HttpProbe."""
        raw_url = probe.params.url
        url = raw_url if raw_url.startswith("http") else f"{self.base_url}{raw_url}"
        status_code, body, latency_ms = self._http_requester(
            url,
            probe.params.method,
            None,
            probe.params.timeout_seconds,
        )

        expected_status = probe.params.expected_status
        status_ok = status_code == expected_status
        json_ok = True

        if status_ok and probe.params.expected_json_subset is not None:
            json_ok = _is_json_subset(probe.params.expected_json_subset, body)

        passed = status_ok and json_ok
        error_msg = None
        if not status_ok:
            error_msg = f"HTTP status mismatch: expected {expected_status}, got {status_code}"
        elif not json_ok:
            error_msg = f"JSON subset mismatch: expected subset {probe.params.expected_json_subset}"

        return ProbeResult(
            probe_name=probe.name,
            probe_type=probe.type,
            passed=passed,
            observed_value={"status_code": status_code, "body": body},
            expected_value={
                "status_code": expected_status,
                "expected_json_subset": probe.params.expected_json_subset,
            },
            latency_ms=latency_ms,
            error=error_msg,
        )

    def evaluate_health_probe(self, probe: HealthProbe) -> ProbeResult:
        """Evaluate a HealthProbe."""
        endpoint = probe.params.endpoint
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        status_code, body, latency_ms = self._http_requester(
            url,
            "GET",
            None,
            probe.params.timeout_seconds,
        )

        expected = probe.params.expected_service_status
        observed_status = body.get("status") if isinstance(body, dict) else None
        passed = (status_code == 200) and (observed_status == expected)
        error_msg = None
        if not passed:
            error_msg = (
                f"Health check failed: expected status '{expected}' (HTTP 200), "
                f"got '{observed_status}' (HTTP {status_code})"
            )

        return ProbeResult(
            probe_name=probe.name,
            probe_type=probe.type,
            passed=passed,
            observed_value=body,
            expected_value={"status": expected, "http_status": 200},
            latency_ms=latency_ms,
            error=error_msg,
        )

    def evaluate_metric_probe(self, probe: MetricProbe) -> ProbeResult:
        """Evaluate a MetricProbe."""
        url = f"{self.base_url}/metrics"
        status_code, body, latency_ms = self._http_requester(url, "GET", None, 5.0)

        if status_code != 200 or not isinstance(body, dict):
            return ProbeResult(
                probe_name=probe.name,
                probe_type=probe.type,
                passed=False,
                observed_value=None,
                expected_value=probe.params.threshold,
                latency_ms=latency_ms,
                error=f"Failed to fetch metrics: HTTP {status_code}",
            )

        observed = _extract_metric_value(body, probe.params.metric_name)
        if observed is None:
            return ProbeResult(
                probe_name=probe.name,
                probe_type=probe.type,
                passed=False,
                observed_value=None,
                expected_value=probe.params.threshold,
                latency_ms=latency_ms,
                error=f"Metric '{probe.params.metric_name}' not found in telemetry payload",
            )

        passed = _compare_metric(observed, probe.params.threshold, probe.params.operator)
        error_msg = None
        if not passed:
            error_msg = (
                f"Metric threshold breached: {probe.params.metric_name}={observed} "
                f"not {probe.params.operator} {probe.params.threshold}"
            )

        return ProbeResult(
            probe_name=probe.name,
            probe_type=probe.type,
            passed=passed,
            observed_value=observed,
            expected_value=f"{probe.params.operator} {probe.params.threshold}",
            latency_ms=latency_ms,
            error=error_msg,
        )

    def evaluate_probe(self, probe: SuccessProbe) -> ProbeResult:
        """Dispatch single probe evaluation based on polymorphic type."""
        if isinstance(probe, HttpProbe):
            return self.evaluate_http_probe(probe)
        if isinstance(probe, HealthProbe):
            return self.evaluate_health_probe(probe)
        if isinstance(probe, MetricProbe):
            return self.evaluate_metric_probe(probe)
        raise ValueError(f"Unknown probe type: {type(probe)}")

    def evaluate_scenario(self, scenario: Scenario) -> EvaluationReport:
        """Evaluate all success probes defined in a scenario and return comprehensive report."""
        start_time = time.perf_counter()
        probe_results: List[ProbeResult] = []

        for probe in scenario.success_probes:
            result = self.evaluate_probe(probe)
            probe_results.append(result)

        duration = time.perf_counter() - start_time
        total = len(probe_results)
        passed = sum(1 for pr in probe_results if pr.passed)
        failed = total - passed
        score = (passed / total) if total > 0 else 1.0
        success = (failed == 0)

        return EvaluationReport(
            success=success,
            score=score,
            total_probes=total,
            passed_probes=passed,
            failed_probes=failed,
            duration_seconds=duration,
            probe_results=probe_results,
        )
