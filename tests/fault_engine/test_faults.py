"""Tests for real reversible fault injectors and independent validation."""

from benchmark.environment import MockEnvironmentProvisioner
from fault_engine.faults.network_latency import NetworkLatencyFault
from fault_engine.faults.pod_crash import PodCrashFault
from fault_engine.manager import FaultManager
from fault_engine.registry import FaultRegistry


def test_pod_crash_fault_lifecycle_with_provisioner():
    provisioner = MockEnvironmentProvisioner()
    fault = PodCrashFault(provisioner=provisioner)

    assert fault.injected is False
    assert fault.cleanup_required is False

    # Inject: stops app container
    res = fault.inject(
        params={"target_service": "app", "signal": "SIGKILL", "grace_period_seconds": 0},
        seed=42,
    )
    assert res["status"] == "injected"
    assert res["target_service"] == "app"
    assert fault.injected is True
    assert fault.cleanup_required is True

    # Independent validation confirms container is stopped
    assert provisioner.get_service_status("app")["running"] is False
    assert fault.validate() is True

    # Reset: restarts app container
    fault.reset()
    assert fault.injected is False
    assert fault.cleanup_required is False
    assert provisioner.get_service_status("app")["running"] is True


def test_pod_crash_validation_fails_if_container_still_running():
    provisioner = MockEnvironmentProvisioner()
    fault = PodCrashFault(provisioner=provisioner)

    fault.inject(params={"target_service": "app"})
    # Simulate container restarting unexpectedly outside injector control
    provisioner.services["app"]["running"] = True

    # Independent validation MUST detect that container is still running
    assert fault.validate() is False


def test_network_latency_fault_lifecycle_with_provisioner():
    provisioner = MockEnvironmentProvisioner()
    # Configure mock command output for tc validation
    provisioner.command_outputs["tc qdisc show dev eth0"] = (
        0,
        "qdisc netem 8001: dev eth0 root refcnt 2 limit 1000 delay 120.0ms 10.0ms",
        "",
    )

    fault = NetworkLatencyFault(provisioner=provisioner)

    # Inject
    res = fault.inject(
        params={"target_service": "db", "latency_ms": 120, "jitter_ms": 10},
        seed=99,
    )
    assert res["status"] == "injected"
    assert fault.injected is True
    assert fault.cleanup_required is True

    # Verify executed commands include tc netem add
    applied_commands = [cmd for srv, cmd in provisioner.command_history if srv == "db"]
    assert any("netem" in cmd and "120ms" in cmd for cmd in applied_commands)

    # Independent validation verifies netem output
    assert fault.validate() is True

    # Reset
    fault.reset()
    assert fault.injected is False
    assert fault.cleanup_required is False

    reset_commands = [cmd for srv, cmd in provisioner.command_history if srv == "db"]
    assert any("del" in cmd and "root" in cmd for cmd in reset_commands)


def test_fault_manager_passes_provisioner_to_registered_faults():
    registry = FaultRegistry()
    registry.register("pod_crash", PodCrashFault)

    provisioner = MockEnvironmentProvisioner()
    manager = FaultManager(registry=registry, provisioner=provisioner)

    from types import SimpleNamespace
    scenario = SimpleNamespace(
        seed=10,
        faults=[
            SimpleNamespace(
                name="crash_db",
                type="pod_crash",
                params=SimpleNamespace(
                    model_dump=lambda: {"target_service": "db", "grace_period_seconds": 0}
                ),
            )
        ],
    )

    results = manager.inject_all(scenario)
    assert len(results) == 1
    assert provisioner.get_service_status("db")["running"] is False

    validations = manager.validate_all()
    assert validations["crash_db"] is True

    manager.reset()
    assert provisioner.get_service_status("db")["running"] is True
