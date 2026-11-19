"""End-to-End integration test covering the complete InfraBench Phase 2 lifecycle.

Verifies:
1. Scenario YAML loading & Pydantic validation
2. Environment provisioner baseline health verification
3. Reversible fault injection with independent validation
4. Agent isolation (zero leakage of ground-truth or probes)
5. Agent Tool Gateway diagnostics and remediation
6. Independent Success Probe evaluation
7. Fault and environment cleanup
8. Full trajectory recording to disk
"""

from pathlib import Path
import pytest

from benchmark.agent_gateway import AgentToolGateway
from benchmark.environment import MockEnvironmentProvisioner
from benchmark.episode import EpisodeStatus
from benchmark.evaluator import SuccessEvaluator
from benchmark.loader import load_scenario
from benchmark.recorder import EpisodeRecorder
from benchmark.runner import ScenarioRunner
from fault_engine.faults.pod_crash import PodCrashFault
from fault_engine.registry import FaultRegistry


def test_complete_e2e_pod_crash_lifecycle(tmp_path: Path):
    # Step 1: Load validated scenario from catalog
    scenario_path = Path("scenarios/catalog/pod_crash_001.yaml")
    assert scenario_path.exists(), "Scenario catalog file must exist"

    registry = FaultRegistry()
    registry.register("pod_crash", PodCrashFault)

    scenario = load_scenario(scenario_path, validate_registry=True, registry=registry)
    assert scenario.id == "pod_crash_001"
    assert scenario.ground_truth is not None

    # Step 2: Initialize environment provisioner
    provisioner = MockEnvironmentProvisioner(
        services={
            "app": {"running": True, "healthy": True, "logs": "Connected to database\n"},
            "db": {"running": True, "healthy": True, "logs": "FATAL: Process killed by SIGKILL\n"},
            "cache": {"running": True, "healthy": True, "logs": "Redis server ready\n"},
        }
    )

    runner = ScenarioRunner(registry=registry, provisioner=provisioner)

    # Step 3: Prepare scenario (baseline check -> fault injection -> independent validation -> READY)
    episode = runner.prepare_scenario(scenario)
    assert episode.status == EpisodeStatus.READY
    assert episode.setup_error is None
    # Fault stopped the target service 'db'
    assert provisioner.get_service_status("db")["running"] is False

    # Step 4: Verify agent payload strictly isolates ground-truth and success probes
    payload = episode.agent_payload
    assert payload is not None
    assert payload.scenario_id == "pod_crash_001"
    assert not hasattr(payload, "ground_truth")
    assert not hasattr(payload, "success_probes")
    assert not hasattr(payload, "faults")

    # Step 5: Start episode (RUNNING)
    runner.mark_running(episode)
    assert episode.status == EpisodeStatus.RUNNING

    # Step 6: Agent interacts through AgentToolGateway
    def fake_http(url, method, data, timeout):
        # When DB is stopped, /health and /db/health return 503 DEGRADED
        db_running = provisioner.get_service_status("db")["running"]
        if not db_running:
            if url.endswith("/db/health"):
                return 503, {"status": "DEGRADED", "service": "postgresql"}, 1.5
            if url.endswith("/health"):
                return 503, {"status": "DEGRADED", "services": {"postgresql": {"status": "DOWN"}}}, 2.0
        else:
            if url.endswith("/db/health"):
                return 200, {"status": "UP", "service": "postgresql"}, 1.0
            if url.endswith("/health"):
                return 200, {"status": "UP", "services": {"postgresql": {"status": "UP"}}}, 1.0
            if url.endswith("/orders"):
                return 200, {"orders": []}, 1.0
        return 404, {}, 1.0

    gateway = AgentToolGateway(
        episode=episode,
        budget=scenario.budget,
        provisioner=provisioner,
        http_requester=fake_http,
    )

    # 6a. Agent diagnoses aggregate health
    diag_health = gateway.execute("check_health")
    assert diag_health["status_code"] == 503
    assert diag_health["response"]["status"] == "DEGRADED"

    # 6b. Agent diagnoses dedicated db health
    diag_db = gateway.execute("check_health", service_name="db")
    assert diag_db["status_code"] == 503

    # 6c. Agent reads database service logs
    diag_logs = gateway.execute("read_service_logs", service="db", tail=100)
    assert "FATAL: Process killed by SIGKILL" in diag_logs["logs"]

    # 6d. Agent remediates by restarting the crashed database service
    remedy = gateway.execute("restart_service", service="db")
    assert remedy["success"] is True
    assert provisioner.get_service_status("db")["running"] is True

    # Step 7: Independent Evaluator verifies recovery
    evaluator = SuccessEvaluator(http_requester=fake_http)
    report = runner.evaluate(episode, scenario, evaluator=evaluator)

    assert report.success is True
    assert report.score == 1.0
    assert report.passed_probes == len(scenario.success_probes)
    assert episode.evaluation_results["success"] is True

    # Step 8: Complete episode and clean up
    runner.complete_episode(episode)
    assert episode.status == EpisodeStatus.COMPLETED
    assert episode.finished_at is not None

    # Step 9: Record episode trajectory to disk
    recorder = EpisodeRecorder(records_dir=tmp_path)
    record_file = recorder.record(episode)

    assert record_file.exists()
    record_data = recorder.load(record_file)

    assert record_data["scenario_id"] == "pod_crash_001"
    assert record_data["status"] == "completed"
    assert record_data["step_count"] == 4
    assert len(record_data["tool_calls"]) == 4
    assert record_data["tool_calls"][0]["tool_name"] == "check_health"
    assert record_data["tool_calls"][1]["tool_name"] == "check_health"
    assert record_data["tool_calls"][2]["tool_name"] == "read_service_logs"
    assert record_data["tool_calls"][3]["tool_name"] == "restart_service"
    assert record_data["evaluation_results"]["success"] is True
    assert record_data["duration_seconds"] is not None
