"""Tests for Episode state and lifecycle tracking."""

import pytest

from benchmark.episode import (
    Episode,
    EpisodeStatus,
    InvalidStateTransitionError,
)


def test_episode_has_unique_ids():
    """Verify episodes receive unique UUIDs upon creation."""
    first = Episode(scenario_id="pod_crash_001")
    second = Episode(scenario_id="pod_crash_001")
    assert first.episode_id != second.episode_id


def test_episode_initial_status():
    """Verify newly created episode defaults to CREATED with empty timestamps."""
    episode = Episode(scenario_id="pod_crash_001")
    assert episode.status == EpisodeStatus.CREATED
    assert episode.started_at is None
    assert episode.finished_at is None
    assert episode.tool_calls == []
    assert episode.evaluation_results is None


def test_episode_transition_records_start_time():
    """Verify transitioning to RUNNING records started_at timestamp."""
    episode = Episode(scenario_id="pod_crash_001")
    episode.transition_to(EpisodeStatus.PREPARING)
    episode.transition_to(EpisodeStatus.FAULTS_INJECTED)
    episode.transition_to(EpisodeStatus.READY)
    episode.transition_to(EpisodeStatus.RUNNING)

    assert episode.status == EpisodeStatus.RUNNING
    assert episode.started_at is not None


def test_episode_terminal_transition_records_finish_time():
    """Verify terminal transitions record finished_at timestamp."""
    episode = Episode(scenario_id="pod_crash_001")
    episode.transition_to(EpisodeStatus.PREPARING)
    episode.transition_to(EpisodeStatus.SETUP_FAILED)

    assert episode.status == EpisodeStatus.SETUP_FAILED
    assert episode.finished_at is not None


def test_terminal_episode_cannot_transition():
    """Verify terminal states (COMPLETED, SETUP_FAILED, AGENT_FAILED) reject transitions."""
    episode = Episode(scenario_id="pod_crash_001")
    episode.transition_to(EpisodeStatus.PREPARING)
    episode.transition_to(EpisodeStatus.FAULTS_INJECTED)
    episode.transition_to(EpisodeStatus.READY)
    episode.transition_to(EpisodeStatus.RUNNING)
    episode.transition_to(EpisodeStatus.COMPLETED)

    with pytest.raises(ValueError):
        episode.transition_to(EpisodeStatus.RUNNING)


def test_illegal_state_transition_raises():
    """Verify illegal jumps in the state graph are rejected."""
    episode = Episode(scenario_id="pod_crash_001")

    # Cannot jump directly from CREATED to RUNNING
    with pytest.raises(InvalidStateTransitionError, match="Cannot transition episode from 'created' to 'running'"):
        episode.transition_to(EpisodeStatus.RUNNING)


def test_setup_failed_versus_agent_failed_distinction():
    """Verify SETUP_FAILED and AGENT_FAILED are separate, distinct states."""
    ep_setup = Episode(scenario_id="scenario_001")
    ep_setup.transition_to(EpisodeStatus.PREPARING)
    ep_setup.transition_to(EpisodeStatus.SETUP_FAILED)
    assert ep_setup.status == EpisodeStatus.SETUP_FAILED
    assert ep_setup.status != EpisodeStatus.AGENT_FAILED

    ep_agent = Episode(scenario_id="scenario_001")
    ep_agent.transition_to(EpisodeStatus.PREPARING)
    ep_agent.transition_to(EpisodeStatus.FAULTS_INJECTED)
    ep_agent.transition_to(EpisodeStatus.READY)
    ep_agent.transition_to(EpisodeStatus.RUNNING)
    ep_agent.transition_to(EpisodeStatus.AGENT_FAILED)
    assert ep_agent.status == EpisodeStatus.AGENT_FAILED


def test_tool_call_trajectory_recording():
    """Verify tool calls are appended to the episode trajectory with step and timestamp."""
    episode = Episode(scenario_id="pod_crash_001")

    call1 = episode.record_tool_call("curl_health", {"url": "http://localhost:8000/health"}, {"status": 503})
    assert call1["step"] == 1
    assert call1["tool_name"] == "curl_health"
    assert len(episode.tool_calls) == 1

    call2 = episode.record_tool_call("docker_restart", {"container": "infrabench-db"}, {"status": "ok"})
    assert call2["step"] == 2
    assert len(episode.tool_calls) == 2


def test_record_evaluation_and_categorized_errors():
    """Verify recording evaluation outcomes and categorizing errors."""
    episode = Episode(scenario_id="pod_crash_001")

    episode.record_error("Port 5432 unreachable", error_type="setup")
    assert episode.setup_error == "Port 5432 unreachable"
    assert episode.error == "Port 5432 unreachable"

    episode.record_error("Agent exceeded 30 steps", error_type="agent")
    assert episode.agent_error == "Agent exceeded 30 steps"

    episode.record_error("Docker cleanup timeout", error_type="cleanup")
    assert episode.cleanup_error == "Docker cleanup timeout"

    eval_data = {"passed": True, "probes_successful": 2, "total_probes": 2}
    episode.record_evaluation(eval_data)
    assert episode.evaluation_results == eval_data
