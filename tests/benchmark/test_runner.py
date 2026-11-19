"""Tests for ScenarioRunner lifecycle orchestration."""

from pathlib import Path
from types import SimpleNamespace
import pytest

from benchmark.episode import EpisodeStatus
from benchmark.runner import ScenarioRunner, ScenarioRunnerError, ScenarioSetupError
from fault_engine.base import BaseFault
from fault_engine.registry import FaultRegistry


SCENARIO_DIR = Path("scenarios/catalog")


def test_runner_prepares_pod_crash_episode():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    assert episode.status == EpisodeStatus.READY
    assert episode.agent_payload is not None
    assert episode.scenario_id == "pod_crash_001"

    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED


def test_runner_prepares_network_latency_episode():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "network_latency_001.yaml")

    assert episode.status == EpisodeStatus.READY
    assert episode.agent_payload is not None

    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED


def test_agent_payload_does_not_expose_hidden_data():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    payload = episode.agent_payload.model_dump()
    assert "ground_truth" not in payload
    assert "faults" not in payload
    assert "success_probes" not in payload

    runner.cleanup_episode(episode)


def test_runner_marks_episode_running():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    runner.mark_running(episode)
    assert episode.status == EpisodeStatus.RUNNING

    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED


def test_runner_completes_episode_after_cleanup():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED
    assert episode.finished_at is not None


def test_runner_rejects_invalid_state_transition():
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    runner.cleanup_episode(episode)
    with pytest.raises(ScenarioRunnerError):
        runner.mark_running(episode)


# ------------------------------------------------------------------------------
# Advanced Failure, Retry, and Manager Retention Tests
# ------------------------------------------------------------------------------

class MockFailingFault(BaseFault):
    """Configurable mock fault for failure and cleanup testing."""

    def inject(self, params=None, seed=42):
        params = params or {}
        self.original_state["fail_inject"] = params.get("fail_inject", False)
        self.original_state["fail_validate"] = params.get("fail_validate", False)
        self.original_state["fail_reset"] = params.get("fail_reset", False)

        if self.original_state["fail_inject"]:
            raise RuntimeError("Injection blew up")

        self.injected = True
        self.cleanup_required = True
        return {"status": "ok"}

    def validate(self) -> bool:
        return not self.original_state.get("fail_validate", False)

    def reset(self) -> None:
        if self.original_state.get("fail_reset"):
            # One-time failure so retry succeeds
            self.original_state["fail_reset"] = False
            raise RuntimeError("Cleanup blew up")

        self.injected = False
        self.cleanup_required = False
        self.original_state.clear()


def make_mock_scenario(scenario_id: str, fault_params: dict):
    return SimpleNamespace(
        id=scenario_id,
        name="Mock Scenario",
        seed=42,
        faults=[
            SimpleNamespace(
                name="test_fault",
                type="mock_failing",
                params=SimpleNamespace(model_dump=lambda: fault_params),
            )
        ],
        to_agent_payload=lambda: SimpleNamespace(model_dump=lambda: {}),
    )


def test_setup_failure_transitions_to_setup_failed():
    """Verify setup failure transitions strictly to SETUP_FAILED, never AGENT_FAILED."""
    registry = FaultRegistry()
    registry.register("mock_failing", MockFailingFault)
    runner = ScenarioRunner(registry=registry)

    # Fault validation fails
    scenario = make_mock_scenario("sc_setup_fail", {"fail_validate": True})
    episode = runner.prepare_scenario(scenario)

    assert episode.status == EpisodeStatus.SETUP_FAILED
    assert episode.setup_error is not None
    assert "Fault validation failed" in episode.setup_error
    assert episode.agent_error is None


def test_setup_failure_with_cleanup_failure_retains_manager_and_retries():
    """Verify setup failure that also fails cleanup transitions to CLEANUP_FAILED, retains manager, and recovers on retry."""
    registry = FaultRegistry()
    registry.register("mock_failing", MockFailingFault)
    runner = ScenarioRunner(registry=registry)

    # Validation fails AND reset fails on first try
    scenario = make_mock_scenario("sc_cleanup_fail", {"fail_validate": True, "fail_reset": True})
    episode = runner.prepare_scenario(scenario)

    # 1. Episode must enter CLEANUP_FAILED
    assert episode.status == EpisodeStatus.CLEANUP_FAILED
    assert episode.setup_error is not None
    assert episode.cleanup_error is not None

    # 2. Manager MUST be retained in runner._managers for retry
    assert runner.get_manager(episode.episode_id) is not None

    # 3. Retry cleanup: should succeed and resolve episode to SETUP_FAILED
    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.SETUP_FAILED
    assert runner.get_manager(episode.episode_id) is None


def test_fail_agent_transitions_to_agent_failed():
    """Verify fail_agent cleans up faults and marks episode AGENT_FAILED."""
    runner = ScenarioRunner()
    episode = runner.prepare_from_file(SCENARIO_DIR / "pod_crash_001.yaml")

    runner.mark_running(episode)
    runner.fail_agent(episode, reason="Agent exceeded step budget of 20")

    assert episode.status == EpisodeStatus.AGENT_FAILED
    assert episode.agent_error == "Agent exceeded step budget of 20"
    assert episode.setup_error is None
    assert runner.get_manager(episode.episode_id) is None


def test_fail_agent_with_cleanup_failure_retains_manager():
    """Verify agent failure with failing cleanup transitions to CLEANUP_FAILED, retains manager, and resolves on retry."""
    registry = FaultRegistry()
    registry.register("mock_failing", MockFailingFault)
    runner = ScenarioRunner(registry=registry)

    scenario = make_mock_scenario("sc_agent_cleanup_fail", {"fail_reset": True})
    episode = runner.prepare_scenario(scenario)
    assert episode.status == EpisodeStatus.READY

    runner.mark_running(episode)

    # Agent fails, and reset fails on first try
    runner.fail_agent(episode, reason="Tool timeout")
    assert episode.status == EpisodeStatus.CLEANUP_FAILED
    assert runner.get_manager(episode.episode_id) is not None

    # Retry cleanup
    runner.cleanup_episode(episode)
    assert episode.status == EpisodeStatus.AGENT_FAILED
    assert runner.get_manager(episode.episode_id) is None
