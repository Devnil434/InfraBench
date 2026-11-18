"""Abstract base interface for fault injection in InfraBench."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseFault(ABC):
    """Abstract base class for all InfraBench faults.

    Each fault instance maintains its own internal cleanup state to prevent
    accidental cleanup of resources that the injector did not create.

    Responsibilities:
    - inject(): Apply the controlled fault.
    - validate(): Confirm expected symptoms.
    - reset(): Restore original state.

    Note: This interface handles only fault lifecycle; it does not contain
    reward calculations, agent decisions, or benchmark scoring.
    """

    def __init__(self) -> None:
        self.injected: bool = False
        self.cleanup_required: bool = False
        self.original_state: Dict[str, Any] = {}

    @abstractmethod
    def inject(
        self,
        params: Optional[Dict[str, Any]] = None,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """Apply the controlled fault to the target infrastructure.

        Args:
            params: Fault configuration parameters.
            seed: Deterministic random seed.

        Returns:
            Dictionary describing the injection result or telemetry.
        """
        ...

    @abstractmethod
    def validate(self) -> bool:
        """Confirm that the intended fault symptoms are active.

        Returns:
            True if fault symptoms are verified, False otherwise.
        """
        ...

    @abstractmethod
    def reset(self) -> None:
        """Restore original infrastructure state using internal cleanup tracking."""
        ...
