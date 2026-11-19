"""Tests for independent success evaluator engine and probe verification."""

import json
from benchmark.evaluator import (
    EvaluationReport,
    ProbeResult,
    SuccessEvaluator,
    _compare_metric,
    _extract_metric_value,
    _is_json_subset,
)
from benchmark.episode import Episode, EpisodeStatus
from benchmark.runner import ScenarioRunner
from benchmark.schema import (
    BudgetConstraint,
    Difficulty,
    EnvironmentInfo,
    GroundTruth,
    HealthProbe,
    HealthProbeParams,
    HttpProbe,
    HttpProbeParams,
    MetricProbe,
    MetricProbeParams,
    PodCrashFault,
    PodCrashParams,
    Scenario,
)


def test_is_json_subset_logic():
    # Identical
    assert _is_json_subset({"a": 1}, {"a": 1}) is True

    # Nested subset
    assert _is_json_subset({"a": {"b": 2}}, {"a": {"b": 2, "c": 3}, "d": 4}) is True

    # Missing key
    assert _is_json_subset({"a": 1, "b": 2}, {"a": 1}) is False

    # Value mismatch
    assert _is_json_subset({"a": 1}, {"a": 2}) is False

    # List subset
    assert _is_json_subset([{"id": 1}], [{"id": 1, "name": "foo"}, {"id": 2}]) is True


def test_extract_metric_value_dot_notation():
    data = {
        "app_name": "InfraBench",
        "system": {"cpu_percent": 12.5, "memory": {"used_mb": 512}},
        "latency_ms": {"p95": 42.0},
    }

    assert _extract_metric_value(data, "system.cpu_percent") == 12.5
    assert _extract_metric_value(data, "system.memory.used_mb") == 512.0
    assert _extract_metric_value(data, "latency_ms.p95") == 42.0
    assert _extract_metric_value(data, "non_existent") is None
    assert _extract_metric_value(data, "system.non_existent") is None


def test_compare_metric_operators():
    # lt
    assert _compare_metric(5.0, 10.0, "lt") is True
    assert _compare_metric(10.0, 10.0, "lt") is False

    # lte
    assert _compare_metric(10.0, 10.0, "lte") is True
    assert _compare_metric(10.1, 10.0, "lte") is False

    # gt
    assert _compare_metric(15.0, 10.0, "gt") is True
    assert _compare_metric(10.0, 10.0, "gt") is False

    # gte
    assert _compare_metric(10.0, 10.0, "gte") is True
    assert _compare_metric(9.9, 10.0, "gte") is False

    # eq
    assert _compare_metric(10.0, 10.0, "eq") is True
    assert _compare_metric(10.000001, 10.0, "eq") is True
    assert _compare_metric(10.1, 10.0, "eq") is False


def test_evaluate_http_probe_success():
    def mock_http(url, method, data, timeout):
        return 200, {"status": "ok", "items": [1, 2, 3]}, 15.2

    evaluator = SuccessEvaluator(http_requester=mock_http)
    probe = HttpProbe(
        type="http_probe",
        name="test_get_items",
        params=HttpProbeParams(
            url="/items",
            method="GET",
            expected_status=200,
            expected_json_subset={"status": "ok"},
        ),
    )

    result = evaluator.evaluate_http_probe(probe)
    assert result.passed is True
    assert result.probe_name == "test_get_items"
    assert result.latency_ms == 15.2
    assert result.error is None


def test_evaluate_http_probe_status_mismatch():
    def mock_http(url, method, data, timeout):
        return 500, {"error": "internal failure"}, 5.0

    evaluator = SuccessEvaluator(http_requester=mock_http)
    probe = HttpProbe(
        type="http_probe",
        name="test_probe_fail",
        params=HttpProbeParams(url="/test", expected_status=200),
    )

    result = evaluator.evaluate_http_probe(probe)
    assert result.passed is False
    assert "expected 200, got 500" in result.error


def test_evaluate_http_probe_json_mismatch():
    def mock_http(url, method, data, timeout):
        return 200, {"status": "degraded"}, 5.0

    evaluator = SuccessEvaluator(http_requester=mock_http)
    probe = HttpProbe(
        type="http_probe",
        name="test_probe_json",
        params=HttpProbeParams(
            url="/test",
            expected_status=200,
            expected_json_subset={"status": "healthy"},
        ),
    )

    result = evaluator.evaluate_http_probe(probe)
    assert result.passed is False
    assert "JSON subset mismatch" in result.error


def test_evaluate_health_probe():
    # UP case
    def mock_healthy(url, method, data, timeout):
        return 200, {"status": "UP", "services": {"db": "UP"}}, 2.1

    evaluator = SuccessEvaluator(http_requester=mock_healthy)
    probe = HealthProbe(
        type="health_probe",
        name="health_check",
        params=HealthProbeParams(endpoint="/health", expected_service_status="UP"),
    )

    result = evaluator.evaluate_health_probe(probe)
    assert result.passed is True
    assert result.error is None

    # DOWN case
    def mock_unhealthy(url, method, data, timeout):
        return 503, {"status": "DEGRADED", "services": {"db": "DOWN"}}, 2.5

    evaluator_unhealthy = SuccessEvaluator(http_requester=mock_unhealthy)
    result_down = evaluator_unhealthy.evaluate_health_probe(probe)
    assert result_down.passed is False
    assert "Health check failed" in result_down.error


def test_evaluate_metric_probe():
    telemetry_data = {
        "latency_ms": {"p95": 14.5},
        "http_requests": {"error_rate_percent": 0.02},
    }

    def mock_metrics(url, method, data, timeout):
        return 200, telemetry_data, 3.0

    evaluator = SuccessEvaluator(http_requester=mock_metrics)

    # Passing probe (p95 <= 50.0)
    passing_probe = MetricProbe(
        type="metric_probe",
        name="p95_latency",
        params=MetricProbeParams(metric_name="latency_ms.p95", threshold=50.0, operator="lte"),
    )
    result = evaluator.evaluate_metric_probe(passing_probe)
    assert result.passed is True
    assert result.observed_value == 14.5

    # Failing probe (error_rate_percent < 0.01)
    failing_probe = MetricProbe(
        type="metric_probe",
        name="error_rate",
        params=MetricProbeParams(metric_name="http_requests.error_rate_percent", threshold=0.01, operator="lt"),
    )
    result_fail = evaluator.evaluate_metric_probe(failing_probe)
    assert result_fail.passed is False
    assert "Metric threshold breached" in result_fail.error


def test_evaluate_scenario_aggregated_report():
    def mock_http(url, method, data, timeout):
        if url.endswith("/health"):
            return 200, {"status": "UP"}, 1.0
        if url.endswith("/metrics"):
            return 200, {"latency_ms": {"p95": 10.0}}, 1.0
        if url.endswith("/orders"):
            return 200, {"orders": []}, 1.0
        return 404, {}, 1.0

    evaluator = SuccessEvaluator(http_requester=mock_http)

    scenario = Scenario(
        id="scenario_eval_test",
        name="Eval Test",
        description="Eval testing scenario",
        difficulty=Difficulty.EASY,
        budget=BudgetConstraint(max_steps=5, time_limit_seconds=60),
        environment=EnvironmentInfo(services=["app", "db", "cache"]),
        faults=[
            PodCrashFault(
                type="pod_crash",
                name="crash",
                params=PodCrashParams(target_service="app"),
            )
        ],
        success_probes=[
            HealthProbe(
                type="health_probe",
                name="probe_health",
                params=HealthProbeParams(endpoint="/health", expected_service_status="UP"),
            ),
            MetricProbe(
                type="metric_probe",
                name="probe_metric",
                params=MetricProbeParams(metric_name="latency_ms.p95", threshold=20.0, operator="lte"),
            ),
            HttpProbe(
                type="http_probe",
                name="probe_orders",
                params=HttpProbeParams(url="/orders", expected_status=200),
            ),
        ],
        ground_truth=GroundTruth(
            root_cause="test",
            affected_services=["app"],
            expected_fix="restart",
        ),
    )

    report = evaluator.evaluate_scenario(scenario)

    assert report.success is True
    assert report.score == 1.0
    assert report.total_probes == 3
    assert report.passed_probes == 3
    assert report.failed_probes == 0
    assert len(report.probe_results) == 3

    # Check serialization
    report_dict = report.to_dict()
    assert report_dict["success"] is True
    assert report_dict["score"] == 1.0
    assert len(report_dict["probe_results"]) == 3


def test_scenario_runner_evaluate_integration():
    def mock_http(url, method, data, timeout):
        return 200, {"status": "UP"}, 1.0

    evaluator = SuccessEvaluator(http_requester=mock_http)
    runner = ScenarioRunner()

    scenario = Scenario(
        id="scenario_runner_eval",
        name="Runner Eval",
        description="Runner eval scenario",
        difficulty=Difficulty.EASY,
        budget=BudgetConstraint(max_steps=5, time_limit_seconds=60),
        environment=EnvironmentInfo(services=["app", "db"]),
        faults=[
            PodCrashFault(
                type="pod_crash",
                name="crash",
                params=PodCrashParams(target_service="app"),
            )
        ],
        success_probes=[
            HealthProbe(
                type="health_probe",
                name="health",
                params=HealthProbeParams(endpoint="/health", expected_service_status="UP"),
            )
        ],
        ground_truth=GroundTruth(
            root_cause="crash",
            affected_services=["app"],
            expected_fix="restart",
        ),
    )

    episode = runner.prepare_scenario(scenario)
    runner.mark_running(episode)

    report = runner.evaluate(episode, scenario, evaluator=evaluator)
    assert report.success is True
    assert episode.evaluation_results is not None
    assert episode.evaluation_results["success"] is True

    runner.complete_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED
