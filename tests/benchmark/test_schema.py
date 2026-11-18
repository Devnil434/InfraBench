"""Tests for scenario Pydantic v2 schemas."""

import pytest
from pydantic import ValidationError

from benchmark.schema import (
    AgentScenarioPayload,
    BudgetConstraint,
    Difficulty,
    EnvironmentInfo,
    GroundTruth,
    HealthProbe,
    HealthProbeParams,
    HttpProbe,
    HttpProbeParams,
    MetricProbe,
    MetricProbeParams,
    NetworkLatencyFault,
    NetworkLatencyParams,
    PodCrashFault,
    PodCrashParams,
    Scenario,
)


def sample_valid_scenario_data() -> dict:
    """Helper returning a complete, valid scenario dictionary."""
    return {
        "schema_version": "1.0",
        "id": "scenario_test_001",
        "name": "Test Scenario",
        "description": "A test scenario description.",
        "difficulty": "medium",
        "tags": ["test", "database"],
        "seed": 42,
        "environment": {
            "services": ["app", "db"],
            "endpoints": {"api": "http://localhost:8000"},
            "notes": "Testing environment notes",
        },
        "initial_symptoms": ["Health check failing"],
        "budget": {
            "time_limit_seconds": 120,
            "max_steps": 15,
            "cost_limit_dollars": 0.25,
        },
        "faults": [
            {
                "type": "pod_crash",
                "name": "crash_test",
                "description": "Crash test container",
                "params": {
                    "target_service": "db",
                    "signal": "SIGKILL",
                    "grace_period_seconds": 0,
                    "restart_policy": "Never",
                },
            }
        ],
        "ground_truth": {
            "root_cause": "The DB crashed due to SIGKILL.",
            "affected_services": ["db"],
            "expected_fix": "Restart DB container.",
            "symptoms": ["Connection refused"],
        },
        "success_probes": [
            {
                "type": "health_probe",
                "name": "health_check",
                "description": "Probe health endpoint",
                "params": {
                    "endpoint": "/health",
                    "expected_service_status": "UP",
                    "timeout_seconds": 5.0,
                },
            }
        ],
    }


def test_valid_scenario_parsing():
    """Verify that a fully specified scenario dictionary validates successfully."""
    data = sample_valid_scenario_data()
    scenario = Scenario.model_validate(data)

    assert scenario.id == "scenario_test_001"
    assert scenario.difficulty == Difficulty.MEDIUM
    assert scenario.seed == 42
    assert len(scenario.faults) == 1
    assert isinstance(scenario.faults[0], PodCrashFault)
    assert scenario.faults[0].params.target_service == "db"
    assert len(scenario.success_probes) == 1
    assert isinstance(scenario.success_probes[0], HealthProbe)


def test_valid_network_latency_scenario():
    """Verify that network_latency fault and metric probe validate properly."""
    data = sample_valid_scenario_data()
    data["faults"] = [
        {
            "type": "network_latency",
            "name": "add_latency",
            "description": "Introduce 150ms delay",
            "params": {
                "target_service": "cache",
                "latency_ms": 150,
                "jitter_ms": 20,
                "correlation_percent": 5.0,
                "duration_seconds": 90,
                "direction": "both",
            },
        }
    ]
    data["success_probes"] = [
        {
            "type": "metric_probe",
            "name": "latency_probe",
            "description": "Check p95 latency",
            "params": {
                "metric_name": "latency_ms.p95",
                "threshold": 100.0,
                "operator": "lte",
            },
        },
        {
            "type": "http_probe",
            "name": "http_probe",
            "description": "Check HTTP endpoint",
            "params": {
                "url": "http://localhost:8000/orders",
                "method": "GET",
                "expected_status": 200,
                "timeout_seconds": 3.0,
                "expected_json_subset": {"cached": True},
            },
        },
    ]

    scenario = Scenario.model_validate(data)
    assert isinstance(scenario.faults[0], NetworkLatencyFault)
    assert scenario.faults[0].params.latency_ms == 150
    assert len(scenario.success_probes) == 2
    assert isinstance(scenario.success_probes[0], MetricProbe)
    assert isinstance(scenario.success_probes[1], HttpProbe)


def test_strict_validation_rejects_extra_fields():
    """Verify that extra='forbid' strictly rejects unknown fields in models."""
    data = sample_valid_scenario_data()
    # 1. Extra field in top-level scenario
    data["unrecognized_top_field"] = "bad"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scenario.model_validate(data)

    del data["unrecognized_top_field"]

    # 2. Extra field in fault params
    data["faults"][0]["params"]["extra_param"] = 123
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scenario.model_validate(data)

    del data["faults"][0]["params"]["extra_param"]

    # 3. Extra field in budget
    data["budget"]["unsupported_budget"] = "unlimited"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scenario.model_validate(data)

    del data["budget"]["unsupported_budget"]

    # 4. Extra field in ground_truth
    data["ground_truth"]["secret_answer"] = 42
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Scenario.model_validate(data)


def test_difficulty_enum_validation():
    """Verify that only recognized difficulty levels are accepted."""
    data = sample_valid_scenario_data()
    data["difficulty"] = "insane"
    with pytest.raises(ValidationError, match="Input should be 'easy', 'medium', 'hard' or 'expert'"):
        Scenario.model_validate(data)


def test_budget_validation_constraints():
    """Verify positive boundary constraints on budget."""
    data = sample_valid_scenario_data()
    data["budget"]["time_limit_seconds"] = 0
    with pytest.raises(ValidationError, match="Input should be greater than 0"):
        Scenario.model_validate(data)

    data["budget"]["time_limit_seconds"] = -10
    with pytest.raises(ValidationError, match="Input should be greater than 0"):
        Scenario.model_validate(data)

    data["budget"]["time_limit_seconds"] = 60
    data["budget"]["max_steps"] = 0
    with pytest.raises(ValidationError, match="Input should be greater than 0"):
        Scenario.model_validate(data)

    data["budget"]["max_steps"] = 10
    data["budget"]["cost_limit_dollars"] = -0.5
    with pytest.raises(ValidationError, match="Input should be greater than or equal to 0"):
        Scenario.model_validate(data)


def test_deterministic_seed_validation():
    """Verify deterministic seed must be a non-negative integer."""
    data = sample_valid_scenario_data()
    data["seed"] = -1
    with pytest.raises(ValidationError, match="Input should be greater than or equal to 0"):
        Scenario.model_validate(data)


def test_fault_discriminated_union_rejection():
    """Verify that invalid fault types are rejected by the discriminated union."""
    data = sample_valid_scenario_data()
    data["faults"][0]["type"] = "unsupported_fault_type"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_probe_discriminated_union_rejection():
    """Verify that invalid probe types are rejected by the discriminated union."""
    data = sample_valid_scenario_data()
    data["success_probes"][0]["type"] = "invalid_probe"
    with pytest.raises(ValidationError):
        Scenario.model_validate(data)


def test_agent_payload_isolation():
    """Verify agent payload strictly excludes ground-truth, fault details, and success probes."""
    data = sample_valid_scenario_data()
    scenario = Scenario.model_validate(data)

    agent_payload = scenario.to_agent_payload()
    assert isinstance(agent_payload, AgentScenarioPayload)

    # Public agent-visible attributes
    assert agent_payload.scenario_id == scenario.id
    assert agent_payload.name == scenario.name
    assert agent_payload.description == scenario.description
    assert agent_payload.difficulty == scenario.difficulty
    assert agent_payload.budget.time_limit_seconds == scenario.budget.time_limit_seconds
    assert agent_payload.environment.services == scenario.environment.services
    assert agent_payload.initial_symptoms == scenario.initial_symptoms

    # Critical security checks: ensure evaluator-only attributes DO NOT EXIST on payload
    payload_dict = agent_payload.model_dump()
    assert "ground_truth" not in payload_dict
    assert "faults" not in payload_dict
    assert "success_probes" not in payload_dict
    assert "seed" not in payload_dict
    assert not hasattr(agent_payload, "ground_truth")
    assert not hasattr(agent_payload, "faults")
    assert not hasattr(agent_payload, "success_probes")
