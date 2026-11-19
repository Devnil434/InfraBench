"""Tests for agent tool gateway, step/time budget enforcement, and auditing."""

import datetime
import pytest

from benchmark.agent_gateway import (
    AgentToolGateway,
    BudgetExceededError,
    ToolExecutionError,
)
from benchmark.environment import MockEnvironmentProvisioner
from benchmark.episode import Episode, EpisodeStatus
from benchmark.schema import BudgetConstraint


@pytest.fixture
def mock_provisioner():
    return MockEnvironmentProvisioner(
        services={
            "app": {"running": True, "healthy": True, "logs": "app line 1\napp line 2\n"},
            "db": {"running": True, "healthy": True, "logs": "postgres ready\n"},
            "cache": {"running": True, "healthy": True, "logs": "redis ready\n"},
        }
    )


@pytest.fixture
def mock_http():
    def _requester(url, method, data, timeout):
        if url.endswith("/db/health"):
            return 200, {"status": "UP", "service": "postgresql"}, 1.2
        if url.endswith("/redis/health"):
            return 200, {"status": "UP", "service": "redis"}, 1.0
        if url.endswith("/health"):
            return 200, {"status": "UP", "services": {"postgresql": {"status": "UP"}}}, 1.5
        if url.endswith("/metrics"):
            return 200, {"app_name": "InfraBench", "system": {"cpu_percent": 15.0}}, 2.0
        return 404, {"error": "Not Found"}, 0.5

    return _requester


def test_gateway_check_health_aggregate(mock_provisioner, mock_http):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
        http_requester=mock_http,
    )

    res = gateway.execute("check_health")
    assert res["status_code"] == 200
    assert res["response"]["status"] == "UP"
    assert res["queried_service"] == "aggregate"

    # Auditing check
    assert episode.step_count == 1
    assert len(episode.tool_calls) == 1
    assert episode.tool_calls[0]["tool_name"] == "check_health"
    assert episode.tool_calls[0]["error"] is None


def test_gateway_check_health_service_specific(mock_provisioner, mock_http):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
        http_requester=mock_http,
    )

    res = gateway.execute("check_health", service_name="db")
    assert res["status_code"] == 200
    assert res["response"]["service"] == "postgresql"
    assert res["queried_service"] == "db"


def test_gateway_read_metrics(mock_provisioner, mock_http):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
        http_requester=mock_http,
    )

    res = gateway.execute("read_metrics")
    assert res["status_code"] == 200
    assert res["metrics"]["app_name"] == "InfraBench"
    assert episode.step_count == 1


def test_gateway_read_service_logs(mock_provisioner):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
    )

    # Normal tail
    res = gateway.execute("read_service_logs", service="app", tail=50)
    assert res["service"] == "app"
    assert res["lines_requested"] == 50
    assert "app line 1" in res["logs"]

    # Bounded tail (e.g. 1000 requested -> capped at 500)
    res_bounded = gateway.execute("read_service_logs", service="db", tail=1000)
    assert res_bounded["lines_requested"] == 500

    # Unauthorized service
    with pytest.raises(ToolExecutionError) as exc_info:
        gateway.execute("read_service_logs", service="unauthorized_container")
    assert "Unauthorized service" in str(exc_info.value)

    # Tool call was still audited in trajectory with error
    assert episode.step_count == 3
    assert episode.tool_calls[-1]["error"] is not None


def test_gateway_restart_service(mock_provisioner):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
    )

    res = gateway.execute("restart_service", service="app")
    assert res["success"] is True
    assert "restarted successfully" in res["message"]
    assert "app" in mock_provisioner.restarted_services

    with pytest.raises(ToolExecutionError):
        gateway.execute("restart_service", service="attacker_svc")


def test_gateway_unknown_tool_rejection(mock_provisioner):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=5, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        gateway.execute("execute_bash_command", cmd="rm -rf /")

    assert "Unknown or unauthorized tool" in str(exc_info.value)
    assert episode.step_count == 1
    assert episode.tool_calls[0]["error"] is not None


def test_gateway_enforces_step_budget(mock_provisioner):
    episode = Episode(scenario_id="sc_001")
    budget = BudgetConstraint(max_steps=2, time_limit_seconds=60)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
    )

    # Step 1
    gateway.execute("restart_service", service="app")
    assert episode.step_count == 1

    # Step 2
    gateway.execute("restart_service", service="db")
    assert episode.step_count == 2

    # Step 3 (Exceeds budget!)
    with pytest.raises(BudgetExceededError) as exc_info:
        gateway.execute("restart_service", service="cache")

    assert "Step budget exceeded" in str(exc_info.value)
    # Step count is not incremented on budget refusal
    assert episode.step_count == 2


def test_gateway_enforces_time_budget(mock_provisioner):
    episode = Episode(scenario_id="sc_001")
    # Simulate episode started 100 seconds ago
    past_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=100)
    episode.started_at = past_time.isoformat()

    budget = BudgetConstraint(max_steps=10, time_limit_seconds=30)
    gateway = AgentToolGateway(
        episode=episode,
        budget=budget,
        provisioner=mock_provisioner,
    )

    with pytest.raises(BudgetExceededError) as exc_info:
        gateway.execute("restart_service", service="app")

    assert "Time budget exceeded" in str(exc_info.value)


def test_gateway_tool_definitions_schema():
    defs = AgentToolGateway.get_tool_definitions()
    assert isinstance(defs, list)
    assert len(defs) == 4

    names = [d["function"]["name"] for d in defs]
    assert "check_health" in names
    assert "read_metrics" in names
    assert "read_service_logs" in names
    assert "restart_service" in names

    for d in defs:
        assert d["type"] == "function"
        assert "description" in d["function"]
        assert "parameters" in d["function"]
