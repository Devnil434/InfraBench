"""Tests for episode trajectory and outcome recorder."""

import datetime
from pathlib import Path
import pytest

from benchmark.episode import Episode, EpisodeStatus
from benchmark.recorder import EpisodeRecorder
from benchmark.schema import (
    AgentScenarioPayload,
    BudgetConstraint,
    Difficulty,
    EnvironmentInfo,
)


def test_episode_recorder_save_and_load(tmp_path: Path):
    recorder = EpisodeRecorder(records_dir=tmp_path)

    episode = Episode(scenario_id="scenario_rec_001")
    episode.transition_to(EpisodeStatus.PREPARING)
    episode.transition_to(EpisodeStatus.FAULTS_INJECTED)
    episode.transition_to(EpisodeStatus.READY)
    episode.transition_to(EpisodeStatus.RUNNING)

    # Record agent tool call
    episode.record_tool_call(
        tool_name="check_health",
        arguments={"service_name": "app"},
        result={"status": "DOWN"},
        duration_ms=12.5,
    )

    # Set evaluation
    episode.record_evaluation({"success": True, "score": 1.0})
    episode.transition_to(EpisodeStatus.COMPLETED)

    # Record payload
    episode.agent_payload = AgentScenarioPayload(
        scenario_id="scenario_rec_001",
        name="Test Rec",
        description="Testing recorder",
        difficulty=Difficulty.EASY,
        budget=BudgetConstraint(max_steps=5, time_limit_seconds=60),
        environment=EnvironmentInfo(services=["app", "db"]),
    )

    # Save
    saved_path = recorder.record(episode)
    assert saved_path.exists()
    assert saved_path.name == f"{episode.scenario_id}_{episode.episode_id}.json"

    # Load
    data = recorder.load(saved_path)
    assert data["episode_id"] == episode.episode_id
    assert data["scenario_id"] == "scenario_rec_001"
    assert data["status"] == "completed"
    assert data["step_count"] == 1
    assert len(data["tool_calls"]) == 1
    assert data["tool_calls"][0]["tool_name"] == "check_health"
    assert data["evaluation_results"]["score"] == 1.0
    assert data["agent_payload"]["name"] == "Test Rec"
    assert data["duration_seconds"] is not None
    assert data["duration_seconds"] >= 0.0


def test_episode_recorder_load_missing_file(tmp_path: Path):
    recorder = EpisodeRecorder(records_dir=tmp_path)
    with pytest.raises(FileNotFoundError):
        recorder.load(tmp_path / "non_existent.json")


def test_episode_recorder_list_records(tmp_path: Path):
    recorder = EpisodeRecorder(records_dir=tmp_path)
    assert recorder.list_records() == []

    ep1 = Episode(scenario_id="sc_1")
    ep2 = Episode(scenario_id="sc_2")

    p1 = recorder.record(ep1)
    p2 = recorder.record(ep2)

    records = recorder.list_records()
    assert len(records) == 2
    assert p1 in records
    assert p2 in records
