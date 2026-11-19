
from types import SimpleNamespace

import pytest

from fault_engine.base import BaseFault
from fault_engine.manager import (
    FaultCleanupError,
    FaultManager,
    FaultManagerError,
)
from fault_engine.registry import FaultRegistry


class RecordingFault(BaseFault):
    """Mock fault used to test manager lifecycle."""

    events = []

    def inject(self, params=None, seed=42):
        params = params or {}
        name = params.get("label", "unnamed")

        self.original_state["label"] = name
        self.original_state["fail_inject"] = params.get(
            "fail_inject", False
        )
        self.original_state["valid"] = params.get("valid", True)
        self.original_state["fail_reset_once"] = params.get(
            "fail_reset_once", False
        )

        self.events.append(f"inject:{name}")

        if self.original_state["fail_inject"]:
            raise RuntimeError("Simulated injection failure")

        self.injected = True
        self.cleanup_required = True

        return {"status": "injected", "label": name}

    def validate(self):
        return self.original_state.get("valid", False)


    def reset(self):
        name = self.original_state.get("label", "unnamed")
        self.events.append(f"reset:{name}")

        if self.original_state.get("fail_reset_once"):
            self.original_state["fail_reset_once"] = False
            raise RuntimeError("Simulated cleanup failure")

        self.injected = False
        self.cleanup_required = False
        self.original_state.clear()



def make_scenario(*faults):
    """Build a lightweight scenario without loading YAML."""

    definitions = []

    for label, params in faults:
        definitions.append(
            SimpleNamespace(
                name=label,
                type="recording",
                params=SimpleNamespace(
                    model_dump=lambda params=params: params
                ),
            )
        )

    return SimpleNamespace(
        seed=123,
        faults=definitions,
    )


@pytest.fixture
def manager():
    RecordingFault.events = []

    registry = FaultRegistry()
    registry.register("recording", RecordingFault)

    return FaultManager(registry=registry)


def test_inject_all_success(manager):
    scenario = make_scenario(
        ("fault_a", {"label": "fault_a"}),
        ("fault_b", {"label": "fault_b"}),
    )

    results = manager.inject_all(scenario)

    assert len(results) == 2
    assert manager.active_fault_names == ["fault_a", "fault_b"]

    assert RecordingFault.events == [
        "inject:fault_a",
        "inject:fault_b",
    ]


def test_validate_all(manager):
    scenario = make_scenario(
        ("healthy", {"label": "healthy", "valid": True}),
        ("unhealthy", {"label": "unhealthy", "valid": False}),
    )

    manager.inject_all(scenario)

    results = manager.validate_all()

    assert results == {
        "healthy": True,
        "unhealthy": False,
    }


def test_reset_uses_reverse_order(manager):
    scenario = make_scenario(
        ("fault_a", {"label": "fault_a"}),
        ("fault_b", {"label": "fault_b"}),
    )

    manager.inject_all(scenario)
    manager.reset()

    assert RecordingFault.events == [
        "inject:fault_a",
        "inject:fault_b",
        "reset:fault_b",
        "reset:fault_a",
    ]

    assert manager.active_fault_names == []


def test_injection_failure_rolls_back_previous_faults(manager):
    scenario = make_scenario(
        ("fault_a", {"label": "fault_a"}),
        (
            "fault_b",
            {"label": "fault_b", "fail_inject": True},
        ),
    )

    with pytest.raises(FaultManagerError):
        manager.inject_all(scenario)

    assert RecordingFault.events == [
        "inject:fault_a",
        "inject:fault_b",
        "reset:fault_b",
        "reset:fault_a",
    ]

    assert manager.active_fault_names == []


def test_manager_rejects_second_injection_without_reset(manager):
    scenario = make_scenario(
        ("fault_a", {"label": "fault_a"}),
    )

    manager.inject_all(scenario)

    with pytest.raises(FaultManagerError):
        manager.inject_all(scenario)

    manager.reset()


def test_failed_cleanup_retains_fault_for_retry(manager):
    scenario = make_scenario(
        (
            "fault_a",
            {
                "label": "fault_a",
                "fail_reset_once": True,
            },
        ),
    )

    manager.inject_all(scenario)

    with pytest.raises(FaultCleanupError):
        manager.reset()

    # Failed cleanup must not lose the fault instance.
    assert manager.active_fault_names == ["fault_a"]

    # The second attempt should succeed.
    manager.reset()

    assert manager.active_fault_names == []
    assert RecordingFault.events == [
        "inject:fault_a",
        "reset:fault_a",
        "reset:fault_a",
    ]


def test_reset_continues_after_one_fault_cleanup_fails(manager):
    scenario = make_scenario(
        ("fault_a", {"label": "fault_a"}),
        (
            "fault_b",
            {
                "label": "fault_b",
                "fail_reset_once": True,
            },
        ),
    )

    manager.inject_all(scenario)

    with pytest.raises(FaultCleanupError):
        manager.reset()

    # fault_a was cleaned successfully; only fault_b remains.
    assert manager.active_fault_names == ["fault_b"]

    assert RecordingFault.events == [
        "inject:fault_a",
        "inject:fault_b",
        "reset:fault_b",
        "reset:fault_a",
    ]

    manager.reset()

    assert manager.active_fault_names == []

    assert RecordingFault.events[-1] == "reset:fault_b"
