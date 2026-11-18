"""Pydantic v2 schemas for InfraBench benchmark scenarios."""

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, ConfigDict, Field


class Difficulty(str, Enum):
    """Scenario difficulty levels."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    EXPERT = "expert"


class BudgetConstraint(BaseModel):
    """Resource and time budgets for the agent resolving the scenario."""

    model_config = ConfigDict(extra="forbid")

    time_limit_seconds: int = Field(gt=0, description="Maximum execution time in seconds")
    max_steps: int = Field(default=30, gt=0, description="Maximum allowable agent tool calls/steps")
    cost_limit_dollars: Optional[float] = Field(
        default=None, ge=0.0, description="Maximum allowable LLM/API cost in dollars"
    )


# ------------------------------------------------------------------------------
# Fault Parameters & Definitions (Discriminated Union)
# ------------------------------------------------------------------------------

class PodCrashParams(BaseModel):
    """Parameters for pod_crash fault."""

    model_config = ConfigDict(extra="forbid")

    target_service: str = Field(min_length=1, description="Target service/container to crash")
    signal: str = Field(default="SIGKILL", description="Termination signal to issue")
    grace_period_seconds: int = Field(default=0, ge=0, description="Grace period before killing")
    restart_policy: str = Field(
        default="Never", description="Restart policy to enforce on crash (e.g. Never, Always)"
    )


class PodCrashFault(BaseModel):
    """Pod crash fault specification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["pod_crash"] = "pod_crash"
    name: str = Field(min_length=1, description="Human-readable identifier for the fault")
    description: Optional[str] = Field(default=None, description="Detailed explanation of the fault")
    params: PodCrashParams


class NetworkLatencyParams(BaseModel):
    """Parameters for network_latency fault."""

    model_config = ConfigDict(extra="forbid")

    target_service: str = Field(min_length=1, description="Target service to inject latency into")
    latency_ms: int = Field(gt=0, description="Added packet latency in milliseconds")
    jitter_ms: int = Field(default=0, ge=0, description="Latency jitter variation in milliseconds")
    correlation_percent: float = Field(
        default=0.0, ge=0.0, le=100.0, description="Correlation to previous packet latency (0-100)"
    )
    duration_seconds: int = Field(default=60, gt=0, description="Duration in seconds for the latency rule")
    direction: Literal["ingress", "egress", "both"] = Field(
        default="both", description="Network traffic direction affected"
    )


class NetworkLatencyFault(BaseModel):
    """Network latency fault specification."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["network_latency"] = "network_latency"
    name: str = Field(min_length=1, description="Human-readable identifier for the fault")
    description: Optional[str] = Field(default=None, description="Detailed explanation of the fault")
    params: NetworkLatencyParams


FaultDefinition = Annotated[
    Union[PodCrashFault, NetworkLatencyFault],
    Field(discriminator="type", description="Polymorphic fault definition"),
]


# ------------------------------------------------------------------------------
# Success Probes (Discriminated Union)
# ------------------------------------------------------------------------------

class HttpProbeParams(BaseModel):
    """Parameters for HTTP success probe."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, description="Target URL endpoint to probe")
    method: Literal["GET", "POST", "PUT", "DELETE", "HEAD"] = Field(
        default="GET", description="HTTP method"
    )
    expected_status: int = Field(default=200, ge=100, le=599, description="Expected HTTP status code")
    timeout_seconds: float = Field(default=5.0, gt=0.0, description="Probe request timeout in seconds")
    expected_json_subset: Optional[Dict[str, Any]] = Field(
        default=None, description="Key-value pairs required in the JSON response"
    )


class HttpProbe(BaseModel):
    """HTTP validation probe."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["http_probe"] = "http_probe"
    name: str = Field(min_length=1, description="Probe identifier")
    description: Optional[str] = Field(default=None, description="Probe purpose")
    params: HttpProbeParams


class HealthProbeParams(BaseModel):
    """Parameters for service health probe."""

    model_config = ConfigDict(extra="forbid")

    endpoint: str = Field(default="/health", description="Health check endpoint path")
    expected_service_status: str = Field(default="UP", description="Expected overall or service status")
    timeout_seconds: float = Field(default=5.0, gt=0.0, description="Probe timeout in seconds")


class HealthProbe(BaseModel):
    """Health check endpoint probe."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["health_probe"] = "health_probe"
    name: str = Field(min_length=1, description="Probe identifier")
    description: Optional[str] = Field(default=None, description="Probe purpose")
    params: HealthProbeParams


class MetricProbeParams(BaseModel):
    """Parameters for telemetry metric threshold probe."""

    model_config = ConfigDict(extra="forbid")

    metric_name: str = Field(min_length=1, description="Name of the telemetry metric to evaluate")
    threshold: float = Field(description="Numerical threshold value")
    operator: Literal["lt", "lte", "gt", "gte", "eq"] = Field(
        default="lte", description="Comparison operator (metric <operator> threshold)"
    )


class MetricProbe(BaseModel):
    """Telemetry metric threshold probe."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["metric_probe"] = "metric_probe"
    name: str = Field(min_length=1, description="Probe identifier")
    description: Optional[str] = Field(default=None, description="Probe purpose")
    params: MetricProbeParams


SuccessProbe = Annotated[
    Union[HttpProbe, HealthProbe, MetricProbe],
    Field(discriminator="type", description="Polymorphic success probe definition"),
]


# ------------------------------------------------------------------------------
# Ground-Truth (Evaluator-Only)
# ------------------------------------------------------------------------------

class GroundTruth(BaseModel):
    """Evaluator-only ground-truth explaining root cause and required fix."""

    model_config = ConfigDict(extra="forbid")

    root_cause: str = Field(min_length=1, description="Full explanation of root cause")
    affected_services: List[str] = Field(min_length=1, description="List of degraded/failed services")
    expected_fix: str = Field(min_length=1, description="Expected remediation action to recover")
    symptoms: List[str] = Field(default_factory=list, description="Expected symptoms observable by agent")


# ------------------------------------------------------------------------------
# Environment Info
# ------------------------------------------------------------------------------

class EnvironmentInfo(BaseModel):
    """Environment services and endpoint topology visible to agents."""

    model_config = ConfigDict(extra="forbid")

    services: List[str] = Field(min_length=1, description="List of running services in target lab")
    endpoints: Dict[str, str] = Field(default_factory=dict, description="Accessible URLs/ports")
    notes: Optional[str] = Field(default=None, description="Additional context about environment")


# ------------------------------------------------------------------------------
# Complete Scenario (Evaluator View) & Agent Payload (Agent View)
# ------------------------------------------------------------------------------

class AgentScenarioPayload(BaseModel):
    """Agent-visible scenario view with ground-truth and internal probes stripped."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, description="Unique scenario ID")
    name: str = Field(min_length=1, description="Scenario title")
    description: str = Field(min_length=1, description="Task briefing for the agent")
    difficulty: Difficulty = Field(description="Difficulty rating")
    initial_symptoms: List[str] = Field(default_factory=list, description="Initial alerts/symptoms observed")
    budget: BudgetConstraint = Field(description="Time and step budget limits")
    environment: EnvironmentInfo = Field(description="Environment topology and endpoints")
    tags: List[str] = Field(default_factory=list, description="Categorization tags")


class Scenario(BaseModel):
    """Full scenario specification containing evaluator ground-truth and fault specs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$", description="Schema version (e.g. 1.0)")
    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_\-]+$", description="Unique alphanumeric identifier")
    name: str = Field(min_length=1, description="Human-readable scenario name")
    description: str = Field(min_length=1, description="Comprehensive scenario briefing")
    difficulty: Difficulty = Field(description="Scenario difficulty level")
    tags: List[str] = Field(default_factory=list, description="List of tags for filtering")
    seed: int = Field(default=42, ge=0, description="Deterministic seed for reproducing fault and traffic")
    environment: EnvironmentInfo = Field(description="Target environment configuration")
    initial_symptoms: List[str] = Field(default_factory=list, description="Initial alerting symptoms")
    budget: BudgetConstraint = Field(description="Agent resource budget")
    faults: List[FaultDefinition] = Field(min_length=1, description="List of faults to inject")
    ground_truth: GroundTruth = Field(description="Hidden ground-truth root cause (evaluator only)")
    success_probes: List[SuccessProbe] = Field(min_length=1, description="Probes validating successful resolution")

    def to_agent_payload(self) -> AgentScenarioPayload:
        """Strip evaluator-only fields and produce agent-visible payload."""
        return AgentScenarioPayload(
            scenario_id=self.id,
            name=self.name,
            description=self.description,
            difficulty=self.difficulty,
            initial_symptoms=list(self.initial_symptoms),
            budget=self.budget.model_copy(),
            environment=self.environment.model_copy(),
            tags=list(self.tags),
        )
