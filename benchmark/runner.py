"""Scenario execution orchestration for InfraBench."""

from pathlib import Path
from typing import Any, Dict, Optional, Union

from benchmark.episode import Episode, EpisodeStatus
from benchmark.loader import build_agent_payload, load_scenario
from benchmark.schema import Scenario
from fault_engine.manager import (
    FaultCleanupError,
    FaultManager,
)
from fault_engine.registry import FaultRegistry, default_registry


class ScenarioRunnerError(RuntimeError):
    """Base exception for scenario runner failures."""
    pass


class ScenarioSetupError(ScenarioRunnerError):
    """Raised when a scenario cannot be loaded or prepared."""
    pass


class ScenarioRunner:
    """Prepares benchmark episodes and coordinates fault and environment lifecycles."""

    def __init__(
        self,
        registry: Optional[FaultRegistry] = None,
    ) -> None:
        self.registry = registry or default_registry
        # Tracks active FaultManager instances keyed by episode_id
        self._managers: Dict[str, FaultManager] = {}

    def get_manager(self, episode_id: str) -> Optional[FaultManager]:
        """Retrieve the FaultManager associated with an episode."""
        return self._managers.get(episode_id)

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
        """Inject and validate scenario faults before handing off to agent."""
        episode = Episode(scenario_id=scenario.id)
        episode.transition_to(EpisodeStatus.PREPARING)

        manager = FaultManager(registry=self.registry)

        try:
            # Step 1: Inject all scenario faults
            episode.fault_results = manager.inject_all(scenario)
            episode.transition_to(EpisodeStatus.FAULTS_INJECTED)

            # Step 2: Validate that every fault is active and verified
            validation_results = manager.validate_all()
            if not validation_results or not all(validation_results.values()):
                failed_faults = [
                    name for name, valid in validation_results.items() if not valid
                ]
                raise ScenarioSetupError(
                    f"Fault validation failed for: {failed_faults}"
                )

            # Step 3: Build agent-safe payload (strictly excludes ground-truth and probes)
            episode.agent_payload = build_agent_payload(scenario)
            episode.transition_to(EpisodeStatus.READY)

            # Retain manager for subsequent episode cleanup
            self._managers[episode.episode_id] = manager
            return episode

        except Exception as exc:
            episode.record_error(str(exc), error_type="setup")

            # Attempt rollback cleanup of any partially injected faults
            try:
                if manager.active_fault_names:
                    manager.reset()
            except FaultCleanupError as cleanup_exc:
                episode.record_error(
                    f"{exc}; cleanup failed: {cleanup_exc}",
                    error_type="cleanup",
                )
                # CRITICAL: Retain manager so cleanup can be retried later
                self._managers[episode.episode_id] = manager
                episode.transition_to(EpisodeStatus.CLEANUP_FAILED)
                return episode

            # If cleanup succeeded, classify strictly as SETUP_FAILED (never AGENT_FAILED)
            episode.transition_to(EpisodeStatus.SETUP_FAILED)
            return episode

    def mark_running(self, episode: Episode) -> None:
        """Mark an episode as active and handed off to an agent."""
        if episode.status != EpisodeStatus.READY:
            raise ScenarioRunnerError(
                f"Cannot start episode in state '{episode.status.value}'. "
                "Only READY episodes can transition to RUNNING."
            )
        episode.transition_to(EpisodeStatus.RUNNING)

    def fail_agent(self, episode: Episode, reason: str) -> None:
        """Mark an episode as failed due to an agent error, budget exhaustion, or crash."""
        if episode.status != EpisodeStatus.RUNNING:
            raise ScenarioRunnerError(
                f"Cannot fail agent in state '{episode.status.value}'. Episode must be RUNNING."
            )

        episode.record_error(reason, error_type="agent")

        # Attempt to clean up faults
        manager = self._managers.get(episode.episode_id)
        if manager and manager.active_fault_names:
            try:
                manager.reset()
                self._managers.pop(episode.episode_id, None)
            except FaultCleanupError as cleanup_exc:
                episode.record_error(
                    f"Agent failed: {reason}; cleanup also failed: {cleanup_exc}",
                    error_type="cleanup",
                )
                # Keep manager in _managers so cleanup can be retried
                episode.transition_to(EpisodeStatus.CLEANUP_FAILED)
                return

        episode.transition_to(EpisodeStatus.AGENT_FAILED)

    def complete_episode(
        self,
        episode: Episode,
        evaluation_results: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record evaluation outcomes, clean up faults, and mark episode COMPLETED."""
        if episode.status != EpisodeStatus.RUNNING:
            raise ScenarioRunnerError(
                f"Cannot complete episode in state '{episode.status.value}'. Episode must be RUNNING."
            )

        if evaluation_results is not None:
            episode.record_evaluation(evaluation_results)

        self.cleanup_episode(episode)

    def cleanup_episode(self, episode: Episode) -> None:
        """Clean up scenario faults and transition episode out of active or CLEANUP_FAILED states."""
        allowed_states = {
            EpisodeStatus.READY,
            EpisodeStatus.RUNNING,
            EpisodeStatus.CLEANUP_FAILED,
            EpisodeStatus.SETUP_FAILED,
            EpisodeStatus.AGENT_FAILED,
        }
        if episode.status not in allowed_states:
            raise ScenarioRunnerError(
                f"Cannot clean up episode in state '{episode.status.value}'. "
                f"Allowed states: {[s.value for s in allowed_states]}"
            )

        manager = self._managers.get(episode.episode_id)

        # If no active manager exists and not in CLEANUP_FAILED, nothing to reset
        if manager is None:
            if episode.status == EpisodeStatus.CLEANUP_FAILED:
                raise ScenarioRunnerError(
                    f"Episode {episode.episode_id} is CLEANUP_FAILED but has no tracked FaultManager to retry."
                )
            if episode.status == EpisodeStatus.RUNNING:
                episode.transition_to(EpisodeStatus.COMPLETED)
            return

        try:
            manager.reset()
        except FaultCleanupError as exc:
            episode.record_error(f"Cleanup failed: {exc}", error_type="cleanup")
            if episode.status != EpisodeStatus.CLEANUP_FAILED:
                episode.transition_to(EpisodeStatus.CLEANUP_FAILED)
            raise ScenarioRunnerError(f"Episode cleanup failed: {exc}") from exc

        # Cleanup succeeded: remove manager from tracking
        self._managers.pop(episode.episode_id, None)

        # Transition to appropriate terminal resolution
        if episode.status == EpisodeStatus.CLEANUP_FAILED:
            if episode.setup_error:
                episode.transition_to(EpisodeStatus.SETUP_FAILED)
            elif episode.agent_error:
                episode.transition_to(EpisodeStatus.AGENT_FAILED)
            else:
                episode.transition_to(EpisodeStatus.COMPLETED)
        elif episode.status in {EpisodeStatus.READY, EpisodeStatus.RUNNING}:
            episode.transition_to(EpisodeStatus.COMPLETED)
