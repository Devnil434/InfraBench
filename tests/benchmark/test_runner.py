
from pathlib import Path

import pytest

from benchmark.episode import EpisodeStatus
from benchmark.runner import ScenarioRunner, ScenarioRunnerError


SCENARIO_DIR = Path("scenarios/catalog")


def test_runner_prepares_pod_crash_episode():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "pod_crash_001.yaml"
    )

    assert episode.status == EpisodeStatus.READY
    assert episode.agent_payload is not None
    assert episode.scenario_id == "pod_crash_001"

    runner.cleanup_episode(episode)


def test_runner_prepares_network_latency_episode():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "network_latency_001.yaml"
    )

    assert episode.status == EpisodeStatus.READY
    assert episode.agent_payload is not None

    runner.cleanup_episode(episode)


def test_agent_payload_does_not_expose_hidden_data():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "pod_crash_001.yaml"
    )

    payload = episode.agent_payload.model_dump()

    assert "ground_truth" not in payload
    assert "faults" not in payload
    assert "success_probes" not in payload

    runner.cleanup_episode(episode)


def test_runner_marks_episode_running():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "pod_crash_001.yaml"
    )

    runner.mark_running(episode)

    assert episode.status == EpisodeStatus.RUNNING

    runner.cleanup_episode(episode)


def test_runner_completes_episode_after_cleanup():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "pod_crash_001.yaml"
    )

    runner.cleanup_episode(episode)

    assert episode.status == EpisodeStatus.COMPLETED
    assert episode.finished_at is not None


def test_runner_rejects_invalid_state_transition():
    runner = ScenarioRunner()

    episode = runner.prepare_from_file(
        SCENARIO_DIR / "pod_crash_001.yaml"
    )

    runner.cleanup_episode(episode)

    with pytest.raises(ScenarioRunnerError):
        runner.mark_running(episode)
