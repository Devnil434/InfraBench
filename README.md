# InfraBench — Autonomous SRE Benchmark Infrastructure

InfraBench is a local-first Site Reliability Engineering (SRE) benchmark and evaluation platform designed specifically for autonomous AI agents. Unlike synthetic code-generation benchmarks, InfraBench provides a realistic, multi-tier microservice environment where agents receive live infrastructure symptoms, diagnose injected faults across system layers, execute remediation tools, and undergo independent verification of system recovery.

---

## 📑 Table of Contents

- [Core Principles](#-core-principles)
- [System Architecture](#-system-architecture)
- [Subsystems & Components](#-subsystems--components)
  - [Phase 0 & 1: Target Microservice Lab](#1-target-microservice-lab-app)
  - [Phase 2: Benchmark & Fault Engine](#2-benchmark--fault-engine-benchmark-fault_engine)
  - [Phase 3: Observability & Diagnostics Pipeline](#3-observability--diagnostics-pipeline-observability)
- [Episode Lifecycle & State Machine](#-episode-lifecycle--state-machine)
- [Agent Tool Gateway](#-agent-tool-gateway)
- [Scenario Catalog](#-scenario-catalog)
- [Repository Structure](#-repository-structure)
- [API Reference](#-api-reference)
- [Quick Start Guide](#-quick-start-guide)
  - [Prerequisites](#prerequisites)
  - [Environment Setup](#1-environment-setup)
  - [Running Target Infrastructure](#2-running-target-infrastructure)
  - [Running the Test Suite](#3-running-the-test-suite)
  - [Synthetic Load Generation](#4-synthetic-load-generation)
- [Extending InfraBench](#-extending-infrabench)

---

## 🎯 Core Principles

1. **Independent Recovery Verification (Zero Self-Reporting)**: Success is never measured by what the agent *claims* it fixed. Success is determined strictly by deterministic, external health, HTTP, and telemetry probes evaluated independently against the live infrastructure.
2. **Real, Reversible Fault Injection**: Faults are injected at the infrastructure layer (e.g., container termination via Docker API, Linux kernel network latency via `tc netem`), not by mocking application return values. Every fault guarantees a reverse-order, guaranteed cleanup lifecycle.
3. **Strict Information Barrier**: Evaluator ground truth, secret injection parameters, and probe configurations are strictly isolated. Agents receive an sanitized observation payload containing only user-facing symptoms and topology.
4. **Audited Trajectories & Budget Bounds**: Every tool call, execution latency, step count, and wall-clock duration is tracked against strict step and time budgets, producing reproducible JSON trajectory artifacts.

---

## 🏛 System Architecture

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                 InfraBench Architecture                                │
└────────────────────────────────────────────────────────────────────────────────────────┘

  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │                              Scenario Orchestration                                │
  │   Scenario YAML -> Pydantic Schema -> Catalog Loader -> ScenarioRunner Loop        │
  └─────────────────────────────────────────┬──────────────────────────────────────────┘
                                            │
                    ┌───────────────────────┴───────────────────────┐
                    ▼                                               ▼
  ┌───────────────────────────────────┐           ┌────────────────────────────────────┐
  │      Environment Provisioner      │           │            Fault Engine            │
  │  (DockerCompose / MockEnv)        │◄──────────┤    FaultManager & FaultRegistry    │
  │  - Service Health Probes          │           │    - PodCrashFault (docker stop)   │
  │  - Container Restarts & Logs      │           │    - NetworkLatencyFault (tc netem)│
  │  - Baseline Reset & Teardown      │           │    - Reversible Rollback Engine    │
  └─────────────────┬─────────────────┘           └────────────────────────────────────┘
                    │
                    ▼
  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │                             Target Infrastructure Lab                              │
  │     FastAPI App (:8000)   ◄───►   PostgreSQL (:5432)   ◄───►   Redis Cache (:6379) │
  └─────────────────┬──────────────────────────────────────────────────────────────────┘
                    │
         ┌──────────┴───────────────────────────┬──────────────────────────────────────┐
         ▼                                      ▼                                      ▼
  ┌──────────────────────────────┐   ┌─────────────────────────────┐   ┌───────────────────────────────┐
  │      Agent Tool Gateway      │   │     Observability Engine    │   │    Success Probe Evaluator    │
  │  - check_health(service)     │   │  - Log Parser & Tracebacks  │   │  - HttpProbe (status & body)  │
  │  - read_metrics()            │   │  - Metrics Collector        │   │  - HealthProbe (UP/DOWN)      │
  │  - read_service_logs(tail)   │   │  - Delta & Trends           │   │  - MetricProbe (nested paths) │
  │  - restart_service(service)  │   │  - Bounded Circular Buffers │   │  - Independent Score (0 - 1)  │
  │  - Step & Time Budget Guard  │   │  - Agent Observation Barrier│   └───────────────┬───────────────┘
  └──────────────┬───────────────┘   └─────────────────────────────┘                   │
                 │                                                                     │
                 └──────────────────────────────────┬──────────────────────────────────┘
                                                    ▼
  ┌────────────────────────────────────────────────────────────────────────────────────┐
  │                            Episode Trajectory Recorder                             │
  │   - Serialized JSON: benchmark/records/{scenario_id}_{episode_id}.json             │
  │   - Audited Steps, Latencies, Tool Invocations, Reports, and Failure Causes        │
  └────────────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Subsystems & Components

### 1. Target Microservice Lab (`app/`)
The target application is an asynchronous multi-tier e-commerce order service built with:
- **FastAPI**: Async HTTP endpoints with custom telemetry middleware.
- **PostgreSQL 16**: Relational storage using `SQLAlchemy 2.0` and `asyncpg`.
- **Redis 7**: Distributed in-memory caching for order listings with write-through invalidation.
- **Telemetry Collector (`app/telemetry.py`)**: Real-time process and traffic metrics (CPU utilization, memory MB, active connections, status code counts, and latency percentiles `p50`, `p95`, `p99`).
- **Synthetic Load Generator (`app/load_generator.py`)**: Asynchronous Poisson-distributed background traffic generator simulating realistic production workloads.

### 2. Benchmark & Fault Engine (`benchmark/`, `fault_engine/`)

- **Pydantic v2 Scenario Schemas (`benchmark/schema.py`)**:
  - Validates scenario definitions with `extra="forbid"`.
  - Discriminated unions for typed faults (`pod_crash`, `network_latency`) and probes (`http_probe`, `health_probe`, `metric_probe`).
  - Separates trusted scenario configuration from sanitized `AgentScenarioPayload`.
- **Scenario Catalog Loader (`benchmark/loader.py`)**:
  - Safe YAML parser with duplicate scenario ID detection, missing file handling, and fault registry validation.
- **Environment Provisioners (`benchmark/environment.py`)**:
  - `BaseEnvironmentProvisioner(ABC)`: Standardized interface for infrastructure management.
  - `DockerComposeProvisioner`: Manages live multi-container topologies on native Docker and WSL2.
  - `MockEnvironmentProvisioner`: In-memory provisioner for sub-second deterministic unit and integration tests.
- **Fault Lifecycle & Manager (`fault_engine/`)**:
  - `BaseFault`: Standard lifecycle methods `inject()`, `validate()`, and `reset()`.
  - `FaultManager`: Coordinates multi-fault injection, verified independent validation, and guaranteed LIFO (reverse-order) rollback.
  - `PodCrashFault`: Halts target containers via the Docker engine, validates stoppage, and restores on reset.
  - `NetworkLatencyFault`: Injects kernel-level packet delay using Linux traffic control (`tc netem`), verifies active qdisc rules, and removes cleanly on teardown.
- **Independent Success Evaluator (`benchmark/evaluator.py`)**:
  - `HttpProbe`: Verifies response status codes and deep JSON key-value subset matching.
  - `HealthProbe`: Queries `/health` endpoints to ensure dependencies are fully `UP`.
  - `MetricProbe`: Resolves nested dot-notated metrics (`latency_ms.p95`, `http_requests.error_rate_percent`) against comparative operators (`lt`, `lte`, `gt`, `gte`, `eq`).
- **Episode Trajectory Recorder (`benchmark/recorder.py`)**:
  - Writes audit logs to `benchmark/records/{scenario_id}_{episode_id}.json` capturing agent decisions, tool arguments, latency durations, and evaluation verdicts.

### 3. Observability & Diagnostics Pipeline (`observability/`)

- **Standardized Schemas (`observability/schemas.py`)**:
  - `LogEntry`: Normalized log line with timezone-aware UTC timestamps, validated `LogLevel` (with case-insensitive alias normalization e.g. `WARN` $\to$ `WARNING`), and structured attributes.
  - `MetricSnapshot`: Time-indexed dictionary of telemetry measurements and labels.
  - `IncidentEvent`: Discrete alerts, health transitions, and failure notifications with normalized `EventSeverity`.
  - `AgentObservation`: Strictly sanitized view combining logs, metrics, and alerts. Enforces an **information barrier validator** that rejects any internal leakage (`ground_truth`, `faults`, `success_probes`, `injection_params`).
  - `InternalTelemetryRecord`: Trusted backend record containing complete diagnostics and evaluation parameters, convertible to `AgentObservation` via `.to_agent_observation()`.
- **Log Collection & Parsing (`observability/logs.py`)**:
  - `strip_ansi()`: Cleans terminal color codes.
  - `LogParser`: Extracts ISO-8601 timestamps, classifies log levels, parses HTTP access log details (`http_method`, `http_path`, `http_status`), and automatically collates multiline Python stack traces into coherent parent entries.
  - `LogCollector`: Bounded circular deque buffers per service with time-range, min-level, and substring search filtering.
- **Metrics Collection & Analysis (`observability/metrics.py`)**:
  - `flatten_metrics()`: Recursively converts hierarchical metrics JSON into dot-notated float values (`system.cpu_percent`, `latency_ms.p95`).
  - `MetricsCollector`: Periodically pulls from `/metrics`, buffers snapshots, calculates point-to-point change via `delta()`, produces chronological `timeseries()` tuples, and answers windowed queries.

---

## 🔄 Episode Lifecycle & State Machine

InfraBench enforces a strict state machine across the lifetime of every evaluation run:

```
                  ┌──────────────┐
                  │   CREATED    │
                  └──────┬───────┘
                         │ prepare()
                         ▼
                  ┌──────────────┐
                  │  PREPARING   │
                  └──────┬───────┘
                         │ inject_faults()
                         ▼
               ┌────────────────────┐
               │  FAULTS_INJECTED   │
               └─────────┬──────────┘
                         │ mark_ready()
                         ▼
                  ┌──────────────┐
                  │    READY     │
                  └──────┬───────┘
                         │ mark_running()
                         ▼
                  ┌──────────────┐
                  │   RUNNING    │
                  └──────┬───────┘
                         │ complete_episode() / fail_agent()
                         ▼
           ┌────────────────────────────┐
           │   COMPLETED / AGENT_FAILED │
           └─────────────┬──────────────┘
                         │ cleanup()
                         ▼
                  ┌──────────────┐
                  │    CLOSED    │
                  └──────────────┘
```

### Failure Classifications:
- **`SETUP_FAILED`**: Infrastructure, Docker, or baseline connectivity failures occurring before the agent begins. Ensures agents are never penalized for infrastructure setup issues.
- **`AGENT_FAILED`**: Agent exceeded time or step budget, crashed, or gave up.
- **`CLEANUP_FAILED`**: Infrastructure failed to reset cleanly after the episode, preserving the state for debugging while isolating subsequent benchmark runs.

---

## 🛠 Agent Tool Gateway

The agent interacts with the target environment exclusively through `AgentToolGateway`:

```python
from benchmark.agent_gateway import AgentToolGateway

gateway = AgentToolGateway(
    episode=episode,
    budget=scenario.budget,
    provisioner=provisioner,
    base_url="http://localhost:8000",
)
```

### Available Tools:

| Tool Name | Arguments | Description |
| :--- | :--- | :--- |
| `check_health` | `service_name: Optional[str]` | Queries aggregate `/health` or dedicated `/db/health`, `/redis/health`. |
| `read_metrics` | *None* | Retrieves structured JSON telemetry snapshot from `/metrics`. |
| `read_service_logs`| `service: str`, `tail: int = 100` | Fetches container logs (bounded to a safe maximum of 500 lines). |
| `restart_service` | `service: str` | Issues a safe container restart command to the provisioner. |

Every tool invocation automatically:
1. Validates available step and time budgets (`BudgetConstraint`).
2. Measures wall-clock execution latency.
3. Records the tool name, arguments, and result to the episode trajectory.
4. Increments the episode step counter.

---

## 📂 Scenario Catalog

Scenarios are defined in YAML under `scenarios/catalog/`:

### 1. `pod_crash_001.yaml`
- **Difficulty**: Easy
- **Budget**: 5 steps, 120 seconds
- **Fault**: Stops the `redis` cache container.
- **Symptoms**: Intermittent `500 Internal Server Error` responses on `/orders` and cache connection refusals.
- **Remediation**: Diagnose that PostgreSQL is healthy, observe Redis connection errors in logs, and restart the `redis` service.
- **Success Criteria**: HTTP 200 on `/health` and `/redis/health`, error rate $< 1.0\%$.

### 2. `network_latency_001.yaml`
- **Difficulty**: Medium
- **Budget**: 8 steps, 180 seconds
- **Fault**: Injects 250ms of kernel network delay into the database container via `tc netem`.
- **Symptoms**: Elevated response latency on `/orders` and elevated `latency_ms.p95` on `/metrics`.
- **Remediation**: Inspect latency distributions across endpoints, isolate database round-trip times, and clear netem rules.
- **Success Criteria**: `latency_ms.p95` $< 50.0\text{ ms}$, HTTP 200 on `/orders`.

---

## 📁 Repository Structure

```
InfraBench/
├── app/                          # Microservice Under Test (FastAPI Tier)
│   ├── config.py                 # Application configuration & environment settings
│   ├── database.py               # Async SQLAlchemy engine & sessionmaker
│   ├── load_generator.py         # Async Poisson synthetic load generator
│   ├── main.py                   # FastAPI application, middleware, & lifespan
│   ├── redis.py                  # Async Redis client wrapper
│   ├── telemetry.py              # Telemetry collector & latency middleware
│   ├── models/                   # SQLAlchemy ORM models (Order)
│   ├── routers/                  # API endpoints (health, metrics, orders)
│   └── schemas/                  # Pydantic schemas (orders, metrics, health)
├── benchmark/                    # Benchmark Orchestration Engine (Phase 2)
│   ├── agent_gateway.py          # Bounded tool gateway & budget enforcement
│   ├── environment.py            # Environment provisioners (DockerCompose & Mock)
│   ├── episode.py                # State machine & episode trajectory tracker
│   ├── evaluator.py              # Success probe evaluator (Http, Health, Metric)
│   ├── loader.py                 # YAML scenario catalog parser
│   ├── recorder.py               # JSON episode record serializer
│   ├── runner.py                 # ScenarioRunner orchestration loop
│   ├── schema.py                 # Pydantic v2 scenario schemas & discriminated unions
│   └── records/                  # Saved episode trajectory JSON files
├── fault_engine/                 # Reversible Fault Injection Engine (Phase 1 & 2)
│   ├── base.py                   # BaseFault abstract lifecycle interface
│   ├── manager.py                # FaultManager rollback & cleanup coordinator
│   ├── registry.py               # FaultRegistry & default_registry
│   ├── schemas.py                # Parameter schemas for fault injectors
│   └── faults/                   # Concrete fault implementations
│       ├── network_latency.py    # Linux tc netem delay injector
│       └── pod_crash.py          # Real container crash/stop injector
├── observability/                # Observability & Diagnostics Engine (Phase 3)
│   ├── __init__.py               # Public exports
│   ├── logs.py                   # Log parsing, ANSI stripping, stack trace collation
│   ├── metrics.py                # Telemetry pull, flattening, delta, & time-series
│   └── schemas.py                # Standard schemas (LogEntry, MetricSnapshot, IncidentEvent)
├── scenarios/                    # Scenario Definitions
│   └── catalog/                  # Production scenario YAML files
│       ├── network_latency_001.yaml
│       └── pod_crash_001.yaml
├── tests/                        # Comprehensive Test Suite (138 Tests)
│   ├── test_health.py            # Health probe tests
│   ├── test_integration.py       # Full app integration tests
│   ├── test_load_gen.py          # Load generator tests
│   ├── test_metrics.py           # App telemetry tests
│   ├── test_orders.py            # Orders and caching tests
│   ├── benchmark/                # 9 test files for orchestration & gateway
│   ├── fault_engine/             # 5 test files for faults & rollback
│   └── observability/            # 3 test files for schemas, logs, & metrics
├── docker-compose.yml            # Multi-container target lab topology
├── Dockerfile                    # Container definition for FastAPI app
├── pyproject.toml                # Project metadata & pytest configuration
└── README.md                     # Project documentation
```

---

## 🌐 API Reference

| Method | Endpoint | Description | Healthy Response | Degraded Response |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | Service root and route listing | `200 OK` | — |
| `GET` | `/health` | Aggregate application & dependency health | `200 OK` (`status: UP`) | `503 Service Unavailable` (`DOWN`) |
| `GET` | `/db/health` | Dedicated PostgreSQL probe with ping latency | `200 OK` (`status: UP`) | `503 Service Unavailable` (`DOWN`) |
| `GET` | `/redis/health`| Dedicated Redis probe with ping latency | `200 OK` (`status: UP`) | `503 Service Unavailable` (`DOWN`) |
| `GET` | `/metrics` | Structured telemetry (CPU, memory, traffic, percentiles) | `200 OK` | — |
| `GET` | `/orders` | Fetch orders (served from Redis or DB fallback) | `200 OK` | `500 Internal Server Error` |
| `POST` | `/orders` | Create an order (invalidates Redis cache) | `201 Created` | `500 Internal Server Error` |

---

## 🚀 Quick Start Guide

### Prerequisites
- **Python**: 3.12+
- **Docker & Docker Compose**: v2.20+
- **Operating System**: Linux, macOS, or Windows 11 with WSL2

### 1. Environment Setup

```bash
# Clone the repository
git clone https://github.com/Devnil434/InfraBench.git
cd InfraBench

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\Activate.ps1

# Install project dependencies
pip install -e .
pip install pytest pytest-asyncio httpx
```

Copy the example configuration:
```bash
cp .env.example .env
```

### 2. Running Target Infrastructure

Launch the microservice application, PostgreSQL, and Redis containers:
```bash
docker compose up -d --build
docker compose ps
```

Verify service connectivity:
```bash
curl http://localhost:8000/health
curl http://localhost:8000/metrics
```

### 3. Running the Test Suite

Execute the complete 138-test automated test suite:
```bash
pytest tests/ -v
```

Run specific test modules:
```bash
# Test benchmark orchestration
pytest tests/benchmark/ -v

# Test fault injection and rollback
pytest tests/fault_engine/ -v

# Test observability pipeline
pytest tests/observability/ -v
```

### 4. Synthetic Load Generation

To generate realistic background traffic for testing telemetry and incident detection:
```bash
python -m app.load_generator --base-url http://localhost:8000 --rps 15 --duration 60
```

### 5. Teardown
```bash
docker compose down -v
```

---

## 🧩 Extending InfraBench

### Adding a New Fault Injector
1. Inherit from `BaseFault` in `fault_engine/base.py`:
```python
from fault_engine.base import BaseFault
from fault_engine.registry import default_registry

@default_registry.register("disk_fill")
class DiskFillFault(BaseFault):
    def inject(self, params=None, seed=42):
        # Implementation
        pass

    def validate(self):
        # Independent validation
        return True

    def reset(self):
        # Guaranteed cleanup
        pass
```

### Adding a Scenario
Create a new YAML file in `scenarios/catalog/`:
```yaml
schema_version: "1.0"
scenario_id: "disk_pressure_001"
title: "Root Partition Disk Pressure"
description: "Simulates database failure due to full disk volume."
difficulty: "hard"
budget:
  max_steps: 10
  time_limit_seconds: 300
faults:
  - fault_type: "disk_fill"
    target_service: "db"
    parameters:
      fill_percentage: 95
success_probes:
  - probe_type: "health_probe"
    target_service: "db"
    expected_status: "UP"
```

---

## 📄 License

This project is licensed under the MIT License — see the LICENSE file for details.
