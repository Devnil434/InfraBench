
"""Scenario execution orchestration for InfraBench."""

from pathlib import Path
from typing import Dict, Optional, Union

from benchmark.episode import Episode, EpisodeStatus
from benchmark.loader import load_scenario, build_agent_payload
from benchmark.schema import Scenario
from fault_engine.manager import (
    FaultCleanupError,
    FaultManager,
)
from fault_engine.registry import FaultRegistry, default_registry


class ScenarioRunnerError(RuntimeError):
    """Base exception for scenario runner failures."""


class ScenarioSetupError(ScenarioRunnerError):
    """Raised when a scenario cannot be loaded or prepared."""


class ScenarioRunner:
    """Prepares benchmark episodes and manages their fault lifecycle."""

    def __init__(
        self,
        registry: Optional[FaultRegistry] = None,
    ) -> None:
        self.registry = registry or default_registry

        # Each episode gets its own FaultManager.
        self._managers: Dict[str, FaultManager] = {}

    def prepare_from_file(
        self,
        path: Union[str, Path],
    ) -> Episode:
        """Load a YAML scenario and prepare a benchmark episode."""

        scenario = load_scenario(
            path,
            validate_registry=True,
            registry=self.registry,
        )

        return self.prepare_scenario(scenario)

    def prepare_scenario(self, scenario: Scenario) -> Episode:
        """Inject and validate scenario faults before agent handoff."""

        episode = Episode(scenario_id=scenario.id)

        episode.transition_to(EpisodeStatus.PREPARING)

        manager = FaultManager(registry=self.registry)

        try:
            # Step 1: Inject all scenario faults.
            episode.fault_results = manager.inject_all(scenario)

            episode.transition_to(EpisodeStatus.FAULTS_INJECTED)

            # Step 2: Verify that every fault reports successful injection.
            validation_results = manager.validate_all()

            if not validation_results or not all(
                validation_results.values()
            ):
                failed_faults = [
                    name
                    for name, valid in validation_results.items()
                    if not valid
                ]

                raise ScenarioSetupError(
                    f"Fault validation failed for: {failed_faults}"
                )

            # Step 3: Build the agent-safe payload only after setup succeeds.
            episode.agent_payload = build_agent_payload(scenario)

            episode.transition_to(EpisodeStatus.READY)

            # Retain the manager so the episode can be cleaned up later.
            self._managers[episode.episode_id] = manager

            return episode

        except Exception as exc:
            episode.record_error(str(exc))

            try:
                if manager.active_fault_names:
                    manager.reset()

            except FaultCleanupError as cleanup_exc:
                episode.record_error(
                    f"{exc}; cleanup failed: {cleanup_exc}"
                )
                episode.transition_to(
                    EpisodeStatus.CLEANUP_FAILED
                )
                return episode

            episode.transition_to(EpisodeStatus.FAILED)
            return episode

    def mark_running(self, episode: Episode) -> None:
        """Mark an episode as handed off to an agent."""

        if episode.status != EpisodeStatus.READY:
            raise ScenarioRunnerError(
                "Only READY episodes can transition to RUNNING."
            )

        episode.transition_to(EpisodeStatus.RUNNING)

    def cleanup_episode(self, episode: Episode) -> None:
        """Reset episode faults and mark the episode completed."""

        if episode.status not in {
            EpisodeStatus.READY,
            EpisodeStatus.RUNNING,
        }:
            raise ScenarioRunnerError(
                f"Cannot clean up episode in state: "
                f"{episode.status.value}"
            )

        manager = self._managers.get(episode.episode_id)

        if manager is None:
            raise ScenarioRunnerError(
                f"No active FaultManager found for episode "
                f"{episode.episode_id}"
            )

        try:
            manager.reset()

        except FaultCleanupError as exc:
            episode.record_error(f"Cleanup failed: {exc}")
            episode.transition_to(
                EpisodeStatus.CLEANUP_FAILED
            )
            raise ScenarioRunnerError(
                f"Episode cleanup failed: {exc}"
            ) from exc

        self._managers.pop(episode.episode_id, None)

        episode.transition_to(EpisodeStatus.COMPLETED)
