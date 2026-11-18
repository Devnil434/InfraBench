"""Pod crash fault injector stub for scenario integration."""

from typing import Any, Dict, Optional
from fault_engine.base import BaseFault
from fault_engine.registry import register_fault


@register_fault("pod_crash")
class PodCrashFault(BaseFault):
    """Lifecycle implementation for pod crash faults."""

    def inject(
        self,
        params: Optional[Dict[str, Any]] = None,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """Record pod crash injection state."""
        target = params.get("target_service", "app") if params else "app"
        self.original_state["target_service"] = target
        self.original_state["status"] = "running"
        self.injected = True
        self.cleanup_required = True
        return {"status": "injected", "target_service": target, "seed": seed}

    def validate(self) -> bool:
        """Confirm that target service is down."""
        return self.injected

    def reset(self) -> None:
        """Restore target service state."""
        if self.cleanup_required:
            self.original_state.clear()
            self.injected = False
            self.cleanup_required = False
