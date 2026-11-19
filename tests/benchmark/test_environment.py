"""Unit and integration tests for benchmark environment provisioners."""

import json
import subprocess
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from benchmark.environment import (
    BaseEnvironmentProvisioner,
    DockerComposeProvisioner,
    MockEnvironmentProvisioner,
)
from benchmark.episode import EpisodeStatus
from benchmark.runner import ScenarioRunner, ScenarioSetupError
from benchmark.schema import (
    BudgetConstraint,
    Difficulty,
    EnvironmentInfo,
    GroundTruth,
    HealthProbe,
    HealthProbeParams,
    PodCrashFault,
    PodCrashParams,
    Scenario,
)
from fault_engine.base import BaseFault
from fault_engine.registry import FaultRegistry


class DummyHealthyFault(BaseFault):
    def inject(self, params: Optional[Dict[str, Any]] = None, seed: int = 42) -> Dict[str, Any]:
        self.injected = True
        self.cleanup_required = True
        return {"status": "injected"}

    def validate(self) -> bool:
        return True

    def reset(self) -> None:
        self.injected = False
        self.cleanup_required = False


def make_test_scenario() -> Scenario:
    return Scenario(
        id="test_env_scenario",
        name="Test Env Scenario",
        description="Scenario for environment testing",
        difficulty=Difficulty.EASY,
        budget=BudgetConstraint(max_steps=5, time_limit_seconds=60),
        environment=EnvironmentInfo(services=["app", "db", "cache"]),
        faults=[
            PodCrashFault(
                type="pod_crash",
                name="crash_app",
                params=PodCrashParams(target_service="app", signal="SIGKILL", grace_period_seconds=0),
            )
        ],
        success_probes=[
            HealthProbe(
                type="health_probe",
                name="check_app_health",
                params=HealthProbeParams(endpoint="/health", expected_service_status="UP"),
            )
        ],
        ground_truth=GroundTruth(
            root_cause="Application crash",
            affected_services=["app"],
            expected_fix="restart_service",
        ),
    )


def test_mock_provisioner_lifecycle():
    provisioner = MockEnvironmentProvisioner()

    assert provisioner.verify_health() is True
    status = provisioner.get_service_status("app")
    assert status["running"] is True
    assert status["healthy"] is True

    # Stop service
    assert provisioner.stop_service("app") is True
    assert provisioner.verify_health() is False
    assert provisioner.get_service_status("app")["running"] is False

    # Start service
    assert provisioner.start_service("app") is True
    assert provisioner.verify_health() is True

    # Restart service
    assert provisioner.restart_service("db") is True
    assert "db" in provisioner.restarted_services

    # Get logs
    logs = provisioner.get_service_logs("cache")
    assert "redis ready" in logs

    # Teardown
    provisioner.teardown()
    assert provisioner.verify_health() is False

    # Reset to baseline
    provisioner.reset_to_baseline()
    assert provisioner.baseline_reset_called is True
    assert provisioner.verify_health() is True


def test_mock_provisioner_execute_command():
    provisioner = MockEnvironmentProvisioner()
    provisioner.command_outputs["tc qdisc show dev eth0"] = (0, "qdisc prio 1:", "")

    code, out, err = provisioner.execute_command("app", ["tc", "qdisc", "show", "dev", "eth0"])
    assert code == 0
    assert "qdisc prio" in out
    assert provisioner.command_history == [("app", ["tc", "qdisc", "show", "dev", "eth0"])]

    # Unknown command defaults
    code, out, err = provisioner.execute_command("app", ["unknown", "cmd"])
    assert code == 0
    assert out == "ok"


def test_docker_compose_provisioner_command_building():
    # WSL enabled
    wsl_prov = DockerComposeProvisioner(wsl_distro="Ubuntu-22.04")
    cmd = wsl_prov._build_command(["ps", "-a"])
    assert cmd == ["wsl", "-d", "Ubuntu-22.04", "docker", "ps", "-a"]

    # Native docker (no WSL)
    native_prov = DockerComposeProvisioner(wsl_distro=None)
    cmd = native_prov._build_command(["ps", "-a"])
    assert cmd == ["docker", "ps", "-a"]


def test_docker_compose_provisioner_container_operations():
    executed_commands: List[List[str]] = []

    def fake_runner(cmd, **kwargs):
        executed_commands.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="success\n", stderr="")

    prov = DockerComposeProvisioner(wsl_distro=None, command_runner=fake_runner)

    # Start
    assert prov.start_service("app") is True
    assert executed_commands[-1] == ["docker", "start", "infrabench-app"]

    # Stop
    assert prov.stop_service("db", timeout=5) is True
    assert executed_commands[-1] == ["docker", "stop", "-t", "5", "infrabench-db"]

    # Restart
    assert prov.restart_service("cache") is True
    assert executed_commands[-1] == ["docker", "restart", "infrabench-cache"]

    # Logs
    logs = prov.get_service_logs("app", tail=42)
    assert logs == "success\n"
    assert executed_commands[-1] == ["docker", "logs", "--tail", "42", "infrabench-app"]

    # Execute
    code, out, err = prov.execute_command("app", ["ps", "aux"])
    assert code == 0
    assert out == "success\n"
    assert executed_commands[-1] == ["docker", "exec", "infrabench-app", "ps", "aux"]


def test_docker_compose_provisioner_get_service_status():
    inspect_data = {
        "Running": True,
        "Health": {"Status": "healthy"},
        "ExitCode": 0,
    }

    def fake_runner(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout=json.dumps(inspect_data),
            stderr="",
        )

    prov = DockerComposeProvisioner(wsl_distro=None, command_runner=fake_runner)
    status = prov.get_service_status("app")

    assert status["running"] is True
    assert status["healthy"] is True
    assert status["container"] == "infrabench-app"
    assert status["health_status"] == "healthy"


def test_docker_compose_provisioner_timeout_handling():
    def timeout_runner(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=5.0)

    prov = DockerComposeProvisioner(wsl_distro=None, command_runner=timeout_runner)
    code, out, err = prov._run_docker(["ps"], timeout=5.0)
    assert code == 124
    assert "timed out after 5.0s" in err


def test_docker_compose_verify_health_http():
    prov = DockerComposeProvisioner()

    # Success case
    mock_resp_data = {
        "status": "UP",
        "services": {
            "postgresql": {"status": "UP"},
            "redis": {"status": "UP"},
        },
    }

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(mock_resp_data).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert prov.verify_health() is True

    # Degraded case
    degraded_data = {
        "status": "DEGRADED",
        "services": {
            "postgresql": {"status": "DOWN"},
            "redis": {"status": "UP"},
        },
    }
    mock_resp.read.return_value = json.dumps(degraded_data).encode("utf-8")
    with patch("urllib.request.urlopen", return_value=mock_resp):
        assert prov.verify_health() is False

    # Connection error case
    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        assert prov.verify_health() is False


def test_scenario_runner_with_provisioner_healthy():
    registry = FaultRegistry()
    registry.register("pod_crash", DummyHealthyFault)

    mock_prov = MockEnvironmentProvisioner(health_status=True)
    runner = ScenarioRunner(registry=registry, provisioner=mock_prov)

    scenario = make_test_scenario()
    episode = runner.prepare_scenario(scenario)

    assert episode.status == EpisodeStatus.READY
    assert episode.setup_error is None

    # Cleanup resets environment baseline
    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED
    assert mock_prov.baseline_reset_called is True


def test_scenario_runner_with_provisioner_unhealthy_fails_setup():
    registry = FaultRegistry()
    registry.register("pod_crash", DummyHealthyFault)

    # Provisioner reports unhealthy baseline
    mock_prov = MockEnvironmentProvisioner(health_status=False)
    runner = ScenarioRunner(registry=registry, provisioner=mock_prov)

    scenario = make_test_scenario()
    episode = runner.prepare_scenario(scenario)

    # Setup must fail, never blame agent, and trigger baseline reset attempt
    assert episode.status == EpisodeStatus.SETUP_FAILED
    assert "Environment baseline health check failed" in episode.setup_error
    assert mock_prov.baseline_reset_called is True
