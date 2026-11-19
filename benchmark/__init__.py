"""InfraBench Benchmark and Scenario Management package."""

from benchmark.schema import (
    AgentScenarioPayload,
    BudgetConstraint,
    Difficulty,
    EnvironmentInfo,
    FaultDefinition,
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
    SuccessProbe,
)
from benchmark.agent_gateway import (
    AgentToolGateway,
    BudgetExceededError,
    GatewayError,
    ToolExecutionError,
)
from benchmark.environment import (
    BaseEnvironmentProvisioner,
    DockerComposeProvisioner,
    EnvironmentError,
    EnvironmentHealthCheckError,
    EnvironmentProvisionError,
    MockEnvironmentProvisioner,
)
from benchmark.evaluator import (
    EvaluationReport,
    ProbeResult,
    SuccessEvaluator,
)
from benchmark.recorder import EpisodeRecorder
from benchmark.loader import (
    DuplicateScenarioError,
    ScenarioError,
    ScenarioNotFoundError,
    ScenarioParseError,
    ScenarioValidationError,
    UnknownFaultTypeError,
    build_agent_payload,
    load_scenario,
    load_scenario_catalog,
)

__all__ = [
    # Schema
    "Difficulty",
    "BudgetConstraint",
    "PodCrashParams",
    "PodCrashFault",
    "NetworkLatencyParams",
    "NetworkLatencyFault",
    "FaultDefinition",
    "HttpProbeParams",
    "HttpProbe",
    "HealthProbeParams",
    "HealthProbe",
    "MetricProbeParams",
    "MetricProbe",
    "SuccessProbe",
    "GroundTruth",
    "EnvironmentInfo",
    "Scenario",
    "AgentScenarioPayload",
    # Agent Gateway
    "AgentToolGateway",
    "BudgetExceededError",
    "GatewayError",
    "ToolExecutionError",
    # Environment
    "BaseEnvironmentProvisioner",
    "DockerComposeProvisioner",
    "MockEnvironmentProvisioner",
    "EnvironmentError",
    "EnvironmentHealthCheckError",
    "EnvironmentProvisionError",
    # Evaluator
    "EvaluationReport",
    "ProbeResult",
    "SuccessEvaluator",
    # Recorder
    "EpisodeRecorder",
    # Loader
    "ScenarioError",
    "ScenarioNotFoundError",
    "ScenarioParseError",
    "ScenarioValidationError",
    "DuplicateScenarioError",
    "UnknownFaultTypeError",
    "load_scenario",
    "load_scenario_catalog",
    "build_agent_payload",
]
