"""InfraBench Fault Engine package."""

from fault_engine.base import BaseFault
from fault_engine.registry import (
    FaultRegistry,
    default_registry,
    register_fault,
    get_fault,
    list_faults,
)

__all__ = [
    "BaseFault",
    "FaultRegistry",
    "default_registry",
    "register_fault",
    "get_fault",
    "list_faults",
]
