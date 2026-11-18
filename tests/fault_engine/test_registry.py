"""Tests for BaseFault interface and FaultRegistry."""

import pytest
from fault_engine.base import BaseFault
from fault_engine.registry import FaultRegistry, default_registry


def test_base_fault_is_abstract():
    """Verify that BaseFault cannot be instantiated directly."""
    try:
        BaseFault()
        assert False
    except TypeError:
        assert True


class DummyFault(BaseFault):
    """A minimal concrete implementation of BaseFault for testing."""

    def inject(self, params=None, seed=42):
        self.original_state["connections"] = 100
        self.injected = True
        self.cleanup_required = True
        return {"status": "injected", "seed": seed}

    def validate(self) -> bool:
        return self.injected

    def reset(self) -> None:
        if self.cleanup_required:
            self.original_state.clear()
            self.injected = False
            self.cleanup_required = False


def test_fault_cleanup_state_initialization():
    """Verify that each fault instance maintains its own internal cleanup state."""
    fault = DummyFault()
    assert fault.injected is False
    assert fault.cleanup_required is False
    assert fault.original_state == {}


def test_fault_lifecycle():
    """Verify inject, validate, and reset lifecycle with state tracking."""
    fault = DummyFault()

    # Step 1: Inject
    result = fault.inject(params={"limit": 50}, seed=10)
    assert result["status"] == "injected"
    assert result["seed"] == 10
    assert fault.injected is True
    assert fault.cleanup_required is True
    assert fault.original_state["connections"] == 100

    # Step 2: Validate
    assert fault.validate() is True

    # Step 3: Reset
    fault.reset()
    assert fault.injected is False
    assert fault.cleanup_required is False
    assert fault.original_state == {}


def test_registry_registration_and_retrieval():
    """Verify registration, listing, and creation of faults via FaultRegistry."""
    registry = FaultRegistry()

    # Test direct registration
    registry.register("dummy_fault", DummyFault)
    assert "dummy_fault" in registry
    assert registry.list_faults() == ["dummy_fault"]

    # Test retrieval
    cls = registry.get("dummy_fault")
    assert cls is DummyFault

    # Test instantiation
    instance = registry.create("dummy_fault")
    assert isinstance(instance, DummyFault)
    assert instance.injected is False


def test_registry_decorator_registration():
    """Verify registration using decorator syntax."""
    registry = FaultRegistry()

    @registry.register("decorated_fault")
    class AnotherFault(BaseFault):
        def inject(self, params=None, seed=42):
            self.injected = True
            return {}

        def validate(self):
            return True

        def reset(self):
            self.injected = False

    assert "decorated_fault" in registry
    assert isinstance(registry.create("decorated_fault"), AnotherFault)


def test_registry_rejects_non_base_fault():
    """Verify that registry rejects classes not inheriting from BaseFault."""
    registry = FaultRegistry()

    class NotAFault:
        pass

    with pytest.raises(TypeError, match="must inherit from BaseFault"):
        registry.register("invalid", NotAFault)


def test_registry_unknown_fault():
    """Verify that registry raises KeyError for unknown faults."""
    registry = FaultRegistry()
    with pytest.raises(KeyError, match="not found in registry"):
        registry.get("non_existent_fault")
