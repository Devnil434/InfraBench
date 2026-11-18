"""Tests for scenario loader and catalog ingestion."""

from pathlib import Path
import pytest

from benchmark.loader import (
    DuplicateScenarioError,
    ScenarioNotFoundError,
    ScenarioParseError,
    ScenarioValidationError,
    UnknownFaultTypeError,
    build_agent_payload,
    load_scenario,
    load_scenario_catalog,
)
from benchmark.schema import AgentScenarioPayload, Scenario
from fault_engine.base import BaseFault
from fault_engine.registry import FaultRegistry, default_registry


CATALOG_DIR = Path("scenarios/catalog")


def test_load_valid_pod_crash_scenario():
    """Verify loading and validating pod_crash_001.yaml from catalog."""
    file_path = CATALOG_DIR / "pod_crash_001.yaml"
    scenario = load_scenario(file_path)

    assert isinstance(scenario, Scenario)
    assert scenario.id == "pod_crash_001"
    assert scenario.difficulty.value == "easy"
    assert scenario.faults[0].type == "pod_crash"
    assert scenario.faults[0].params.target_service == "db"
    assert scenario.budget.time_limit_seconds == 300
    assert len(scenario.success_probes) == 2


def test_load_valid_network_latency_scenario():
    """Verify loading and validating network_latency_001.yaml from catalog."""
    file_path = CATALOG_DIR / "network_latency_001.yaml"
    scenario = load_scenario(file_path)

    assert isinstance(scenario, Scenario)
    assert scenario.id == "network_latency_001"
    assert scenario.difficulty.value == "medium"
    assert scenario.faults[0].type == "network_latency"
    assert scenario.faults[0].params.latency_ms == 250
    assert scenario.faults[0].params.target_service == "cache"
    assert scenario.budget.time_limit_seconds == 400


def test_missing_file_handling():
    """Verify ScenarioNotFoundError is raised when target file does not exist."""
    with pytest.raises(ScenarioNotFoundError, match="Scenario file not found"):
        load_scenario("scenarios/catalog/non_existent_file.yaml")


def test_invalid_yaml_syntax(tmp_path: Path):
    """Verify ScenarioParseError is raised when YAML syntax is corrupted."""
    bad_yaml = tmp_path / "bad.yaml"
    bad_yaml.write_text("id: 'test': invalid: [syntax: unclosed", encoding="utf-8")

    with pytest.raises(ScenarioParseError, match="Malformed YAML"):
        load_scenario(bad_yaml)


def test_non_mapping_yaml(tmp_path: Path):
    """Verify ScenarioParseError is raised when YAML is a scalar or list instead of mapping."""
    list_yaml = tmp_path / "list.yaml"
    list_yaml.write_text("- item1\n- item2\n", encoding="utf-8")

    with pytest.raises(ScenarioParseError, match="must contain a top-level YAML mapping"):
        load_scenario(list_yaml)


def test_invalid_schema_validation(tmp_path: Path):
    """Verify ScenarioValidationError is raised when required schema fields are missing."""
    invalid_schema = tmp_path / "missing_fields.yaml"
    invalid_schema.write_text("id: 'incomplete_001'\nname: 'Incomplete'\n", encoding="utf-8")

    with pytest.raises(ScenarioValidationError, match="Schema validation failed"):
        load_scenario(invalid_schema)


def test_unknown_fault_registry_detection(tmp_path: Path):
    """Verify UnknownFaultTypeError is raised when a scenario specifies an unregistered fault type."""
    # Create empty registry with no faults registered
    custom_registry = FaultRegistry()

    # Even though schema accepts pod_crash, registry does not have it registered
    file_path = CATALOG_DIR / "pod_crash_001.yaml"
    with pytest.raises(UnknownFaultTypeError, match="references unknown fault type 'pod_crash'"):
        load_scenario(file_path, validate_registry=True, registry=custom_registry)

    # When validate_registry=False, it should parse without checking registry
    scenario = load_scenario(file_path, validate_registry=False, registry=custom_registry)
    assert scenario.id == "pod_crash_001"


def test_load_scenario_catalog():
    """Verify load_scenario_catalog successfully loads all scenarios in catalog."""
    catalog = load_scenario_catalog(CATALOG_DIR)

    assert "pod_crash_001" in catalog
    assert "network_latency_001" in catalog
    assert isinstance(catalog["pod_crash_001"], Scenario)
    assert isinstance(catalog["network_latency_001"], Scenario)


def test_load_catalog_missing_directory():
    """Verify ScenarioNotFoundError is raised when catalog directory does not exist."""
    with pytest.raises(ScenarioNotFoundError, match="Scenario catalog directory not found"):
        load_scenario_catalog("non_existent_catalog_path")


def test_load_catalog_duplicate_ids(tmp_path: Path):
    """Verify DuplicateScenarioError is raised when two files define identical scenario IDs."""
    source_content = (CATALOG_DIR / "pod_crash_001.yaml").read_text(encoding="utf-8")

    file1 = tmp_path / "scenario_a.yaml"
    file2 = tmp_path / "scenario_b.yaml"

    file1.write_text(source_content, encoding="utf-8")
    file2.write_text(source_content, encoding="utf-8")

    with pytest.raises(DuplicateScenarioError, match="Duplicate scenario ID 'pod_crash_001' found"):
        load_scenario_catalog(tmp_path)


def test_build_agent_payload():
    """Verify build_agent_payload strips evaluator ground-truth and internal probes."""
    file_path = CATALOG_DIR / "pod_crash_001.yaml"
    scenario = load_scenario(file_path)

    payload = build_agent_payload(scenario)
    assert isinstance(payload, AgentScenarioPayload)

    # Public fields present
    assert payload.scenario_id == "pod_crash_001"
    assert payload.name == scenario.name
    assert payload.difficulty == scenario.difficulty
    assert payload.budget.time_limit_seconds == 300
    assert payload.initial_symptoms == scenario.initial_symptoms

    # Evaluator-only fields are NOT on the payload model
    payload_dict = payload.model_dump()
    assert "ground_truth" not in payload_dict
    assert "faults" not in payload_dict
    assert "success_probes" not in payload_dict


def test_registry_resolution_integration():
    """Verify that scenario fault types resolve directly to registered BaseFault classes."""
    catalog = load_scenario_catalog(CATALOG_DIR)

    for scenario_id, scenario in catalog.items():
        for fault_def in scenario.faults:
            fault_cls = default_registry.get(fault_def.type)
            assert issubclass(fault_cls, BaseFault)

            # Verify instantiation works
            instance = fault_cls()
            assert instance.injected is False
            assert instance.cleanup_required is False
            assert instance.original_state == {}
