"""Network latency fault injector stub for scenario integration."""

from typing import Any, Dict, Optional
from fault_engine.base import BaseFault
from fault_engine.registry import register_fault


@register_fault("network_latency")
class NetworkLatencyFault(BaseFault):
    """Lifecycle implementation for network latency faults."""

    def inject(
        self,
        params: Optional[Dict[str, Any]] = None,
        seed: int = 42,
    ) -> Dict[str, Any]:
        """Record network latency injection state."""
        target = params.get("target_service", "db") if params else "db"
        latency = params.get("latency_ms", 100) if params else 100
        self.original_state["target_service"] = target
        self.original_state["added_latency_ms"] = latency
        self.injected = True
        self.cleanup_required = True
        return {"status": "injected", "target_service": target, "latency_ms": latency, "seed": seed}

    def validate(self) -> bool:
        """Confirm that target latency is active."""
        return self.injected

    def reset(self) -> None:
        """Restore network latency rules."""
        if self.cleanup_required:
            self.original_state.clear()
            self.injected = False
            self.cleanup_required = False
