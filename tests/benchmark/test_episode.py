
import pytest

from benchmark.episode import Episode, EpisodeStatus


def test_episode_has_unique_ids():
    first = Episode(scenario_id="pod_crash_001")
    second = Episode(scenario_id="pod_crash_001")

    assert first.episode_id != second.episode_id


def test_episode_initial_status():
    episode = Episode(scenario_id="pod_crash_001")

    assert episode.status == EpisodeStatus.CREATED
    assert episode.started_at is None
    assert episode.finished_at is None


def test_episode_transition_records_start_time():
    episode = Episode(scenario_id="pod_crash_001")

    episode.transition_to(EpisodeStatus.PREPARING)
    episode.transition_to(EpisodeStatus.RUNNING)

    assert episode.status == EpisodeStatus.RUNNING
    assert episode.started_at is not None


def test_episode_terminal_transition_records_finish_time():
    episode = Episode(scenario_id="pod_crash_001")

    episode.transition_to(EpisodeStatus.FAILED)

    assert episode.status == EpisodeStatus.FAILED
    assert episode.finished_at is not None


def test_terminal_episode_cannot_transition():
    episode = Episode(scenario_id="pod_crash_001")

    episode.transition_to(EpisodeStatus.COMPLETED)

    with pytest.raises(ValueError):
        episode.transition_to(EpisodeStatus.RUNNING)
