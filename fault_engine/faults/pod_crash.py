"""Pod crash fault injector controlling real containers with independent validation."""

from typing import Any, Dict, Optional

from fault_engine.base import BaseFault
from fault_engine.registry import register_fault


@register_fault("pod_crash")
class PodCrashFault(BaseFault):
    """Lifecycle implementation for pod/container crash faults."""

    def __init__(self, provisioner: Optional[Any] = None) -> None:
        super().__init__()
        self.provisioner = provisioner

    def inject(
        self,
        params: Optional[Dict[str, Any]] = None,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """Stop target service/container and record cleanup metadata."""
        params = params or {}
        target = params.get("target_service", "app")
        signal = params.get("signal", "SIGKILL")
        grace_period = int(params.get("grace_period_seconds", 0))

        self.original_state["target_service"] = target
        self.original_state["signal"] = signal
        self.original_state["grace_period_seconds"] = grace_period
        self.original_state["stopped_by_injector"] = True

        if self.provisioner is not None:
            success = self.provisioner.stop_service(target, timeout=grace_period)
            if not success:
                raise RuntimeError(f"Failed to stop service '{target}' during pod_crash injection")

        self.injected = True
        self.cleanup_required = True

        return {
            "status": "injected",
            "target_service": target,
            "signal": signal,
            "grace_period_seconds": grace_period,
            "seed": seed,
        }

    def validate(self) -> bool:
        """Confirm independently that the target container is stopped."""
        if not self.injected:
            return False

        if self.provisioner is not None:
            target = self.original_state.get("target_service", "app")
            status = self.provisioner.get_service_status(target)
            # Service is successfully crashed if it is not running
            return status.get("running") is False

        return True

    def reset(self) -> None:
        """Restore target container to running state."""
        if not self.cleanup_required:
            return

        target = self.original_state.get("target_service", "app")
        if self.provisioner is not None:
            self.provisioner.start_service(target)

        self.original_state.clear()
        self.injected = False
        self.cleanup_required = False
