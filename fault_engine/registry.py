"""Fault registry for discovering, registering, and creating fault instances."""

from typing import Callable, Dict, List, Optional, Type
from fault_engine.base import BaseFault


class FaultRegistry:
    """Registry maintaining available fault classes."""

    def __init__(self) -> None:
        self._registry: Dict[str, Type[BaseFault]] = {}

    def register(
        self, name: str, fault_cls: Optional[Type[BaseFault]] = None
    ) -> Callable[[Type[BaseFault]], Type[BaseFault]] | Type[BaseFault]:
        """Register a fault class under a given name.

        Can be used as a decorator:
            @registry.register("my_fault")
            class MyFault(BaseFault): ...

        Or directly:
            registry.register("my_fault", MyFault)
        """
        def decorator(cls: Type[BaseFault]) -> Type[BaseFault]:
            if not issubclass(cls, BaseFault):
                raise TypeError(f"Registered class {cls.__name__} must inherit from BaseFault")
            if name in self._registry:
                raise ValueError(f"Fault '{name}' is already registered")
            self._registry[name] = cls
            return cls

        if fault_cls is not None:
            return decorator(fault_cls)
        return decorator

    def get(self, name: str) -> Type[BaseFault]:
        """Retrieve a registered fault class by name."""
        if name not in self._registry:
            raise KeyError(f"Fault '{name}' not found in registry. Available: {self.list_faults()}")
        return self._registry[name]

    def create(self, name: str, *args, **kwargs) -> BaseFault:
        """Instantiate a registered fault."""
        fault_cls = self.get(name)
        return fault_cls(*args, **kwargs)

    def list_faults(self) -> List[str]:
        """Return a sorted list of registered fault names."""
        return sorted(self._registry.keys())

    def unregister(self, name: str) -> None:
        """Remove a fault from the registry."""
        if name in self._registry:
            del self._registry[name]

    def clear(self) -> None:
        """Clear all registered faults."""
        self._registry.clear()

    def __contains__(self, name: str) -> bool:
        """Check if a fault is registered."""
        return name in self._registry


# Global default registry
default_registry = FaultRegistry()
register_fault = default_registry.register
get_fault = default_registry.get
list_faults = default_registry.list_faults
