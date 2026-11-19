
"""Coordinates fault injection, validation, and cleanup."""

from typing import Any, Dict, List, Tuple

from fault_engine.base import BaseFault
from fault_engine.registry import FaultRegistry, default_registry


class FaultManagerError(RuntimeError):
    """Raised when fault injection or validation fails."""


class FaultCleanupError(FaultManagerError):
    """Raised when one or more faults cannot be cleaned up."""


class FaultManager:
    """Manages the lifecycle of faults belonging to one scenario."""

    def __init__(
        self,
        registry: FaultRegistry | None = None,
    ) -> None:
        self.registry = registry or default_registry
        self._active_faults: List[Tuple[str, BaseFault]] = []

    @property
    def active_fault_names(self) -> List[str]:
        """Return names of currently tracked fault instances."""
        return [name for name, _ in self._active_faults]

    def inject_all(self, scenario: Any) -> List[Dict[str, Any]]:
        """Inject every fault defined in a validated scenario.

        If any injection fails, attempt cleanup in reverse order.
        """
        if self._active_faults:
            raise FaultManagerError(
                "This manager already has active faults. Reset it first."
            )

        results: List[Dict[str, Any]] = []

        try:
            for fault_definition in scenario.faults:
                fault_name = fault_definition.name

                fault_instance = self.registry.create(
                    fault_definition.type
                )

                # Track before injection so partial failures can be cleaned up.
                self._active_faults.append(
                    (fault_name, fault_instance)
                )

                result = fault_instance.inject(
                    params=fault_definition.params.model_dump(),
                    seed=scenario.seed,
                )

                results.append({
                    "fault_name": fault_name,
                    "fault_type": fault_definition.type,
                    "result": result,
                })

        except Exception as exc:
            try:
                self.reset()
            except FaultCleanupError as cleanup_exc:
                raise FaultManagerError(
                    f"Fault injection failed: {exc}. "
                    f"Cleanup also failed: {cleanup_exc}"
                ) from exc

            raise FaultManagerError(
                f"Fault injection failed: {exc}"
            ) from exc

        return results

    def validate_all(self) -> Dict[str, bool]:
        """Validate all currently tracked fault instances."""
        validation_results: Dict[str, bool] = {}

        for fault_name, fault_instance in self._active_faults:
            try:
                validation_results[fault_name] = (
                    fault_instance.validate()
                )
            except Exception as exc:
                raise FaultManagerError(
                    f"Validation failed for '{fault_name}': {exc}"
                ) from exc

        return validation_results

    def reset(self) -> None:
        """Clean up all tracked faults in reverse injection order."""
        cleanup_errors: List[str] = []

        for fault_name, fault_instance in reversed(
            self._active_faults
        ):
            try:
                fault_instance.reset()
            except Exception as exc:
                cleanup_errors.append(
                    f"{fault_name}: {exc}"
                )

        self._active_faults.clear()

        if cleanup_errors:
            raise FaultCleanupError(
                "One or more fault cleanups failed: "
                + "; ".join(cleanup_errors)
            )
