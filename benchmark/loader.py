"""Scenario loading, catalog ingestion, and validation layer."""

from pathlib import Path
from typing import Dict, Optional, Union
import yaml
from pydantic import ValidationError

from benchmark.schema import AgentScenarioPayload, Scenario
from fault_engine.registry import FaultRegistry, default_registry


# ------------------------------------------------------------------------------
# Exception Hierarchy
# ------------------------------------------------------------------------------

class ScenarioError(Exception):
    """Base exception for scenario loading and validation."""
    pass


class ScenarioNotFoundError(ScenarioError, FileNotFoundError):
    """Raised when a scenario file or directory does not exist."""
    pass


class ScenarioParseError(ScenarioError, ValueError):
    """Raised when scenario YAML is malformed or not a valid dictionary."""
    pass


class ScenarioValidationError(ScenarioError, ValueError):
    """Raised when scenario content violates Pydantic schema validation."""
    pass


class DuplicateScenarioError(ScenarioError, ValueError):
    """Raised when multiple scenarios in a catalog share the same scenario ID."""
    pass


class UnknownFaultTypeError(ScenarioError, KeyError):
    """Raised when a scenario specifies a fault type not registered in FaultRegistry."""
    pass


# ------------------------------------------------------------------------------
# Loader Functions
# ------------------------------------------------------------------------------

def load_scenario(
    path: Union[str, Path],
    validate_registry: bool = True,
    registry: Optional[FaultRegistry] = None,
) -> Scenario:
    """Load, parse, and strictly validate a single scenario YAML file.

    Args:
        path: Path to scenario YAML file.
        validate_registry: If True, validates all fault types exist in FaultRegistry.
        registry: FaultRegistry instance to validate against (defaults to default_registry).

    Returns:
        Validated Scenario instance.

    Raises:
        ScenarioNotFoundError: If the file does not exist.
        ScenarioParseError: If the YAML syntax is invalid or not a dictionary.
        ScenarioValidationError: If the scenario fails Pydantic schema validation.
        UnknownFaultTypeError: If a fault type is not registered in FaultRegistry.
    """
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        raise ScenarioNotFoundError(f"Scenario file not found: '{file_path}'")

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise ScenarioParseError(f"Failed to read file '{file_path}': {exc}") from exc

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ScenarioParseError(f"Malformed YAML in '{file_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ScenarioParseError(
            f"Scenario file '{file_path}' must contain a top-level YAML mapping/dictionary, got {type(data).__name__}"
        )

    try:
        scenario = Scenario.model_validate(data)
    except ValidationError as exc:
        errors = exc.errors()
        error_details = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in errors
        )
        raise ScenarioValidationError(
            f"Schema validation failed for scenario in '{file_path}': {error_details}"
        ) from exc

    # Validate fault types against fault registry
    if validate_registry:
        reg = registry if registry is not None else default_registry
        for fault in scenario.faults:
            if fault.type not in reg:
                available = reg.list_faults()
                raise UnknownFaultTypeError(
                    f"Scenario '{scenario.id}' in '{file_path}' references unknown fault type '{fault.type}'. "
                    f"Available in registry: {available}"
                )

    return scenario


def load_scenario_catalog(
    directory: Union[str, Path],
    validate_registry: bool = True,
    registry: Optional[FaultRegistry] = None,
) -> Dict[str, Scenario]:
    """Scan a directory for scenario YAML files, returning a dictionary mapping ID -> Scenario.

    Args:
        directory: Directory path containing scenario YAML files.
        validate_registry: If True, validates all fault types exist in FaultRegistry.
        registry: FaultRegistry instance to validate against (defaults to default_registry).

    Returns:
        Dictionary mapping scenario ID to Scenario instance.

    Raises:
        ScenarioNotFoundError: If directory does not exist or is not a directory.
        DuplicateScenarioError: If two files declare identical scenario IDs.
        ScenarioParseError / ScenarioValidationError / UnknownFaultTypeError: On invalid files.
    """
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        raise ScenarioNotFoundError(f"Scenario catalog directory not found: '{dir_path}'")

    catalog: Dict[str, Scenario] = {}
    id_source: Dict[str, Path] = {}

    # Scan for .yaml and .yml files (sorted deterministically)
    yaml_files = sorted(
        [p for p in dir_path.glob("**/*") if p.is_file() and p.suffix.lower() in (".yaml", ".yml")]
    )

    for file_path in yaml_files:
        scenario = load_scenario(file_path, validate_registry=validate_registry, registry=registry)
        if scenario.id in catalog:
            original_file = id_source[scenario.id]
            raise DuplicateScenarioError(
                f"Duplicate scenario ID '{scenario.id}' found in '{file_path}' "
                f"(already loaded from '{original_file}')"
            )
        catalog[scenario.id] = scenario
        id_source[scenario.id] = file_path

    return catalog


def build_agent_payload(scenario: Scenario) -> AgentScenarioPayload:
    """Build the public agent-visible payload for a scenario, removing hidden evaluator data.

    Args:
        scenario: Full scenario definition.

    Returns:
        AgentScenarioPayload containing only agent-safe fields.
    """
    return scenario.to_agent_payload()
