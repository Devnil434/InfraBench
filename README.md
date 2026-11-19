# InfraBench — Autonomous SRE Benchmark Infrastructure

InfraBench is an autonomous Site Reliability Engineering (SRE) benchmark and evaluation platform. It provides a realistic, containerized multi-tier microservice environment for evaluating autonomous AI agents on real-world infrastructure diagnosis, tool-based remediation, and independent recovery verification.

---

## 🏛 System Architecture

The benchmark consists of an application tier, stateful infrastructure, synthetic telemetry generation, and a complete Phase 2 benchmark orchestration engine:

```
┌───────────────────────────────────────────────────────────────────────────────────┐
│                               InfraBench Architecture                             │
└───────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                           Scenario Orchestration                            │
  │  Scenario YAML -> Pydantic Schema -> Catalog Loader -> ScenarioRunner       │
  └──────────────────────────────────────┬──────────────────────────────────────┘
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   ▼                                           ▼
  ┌─────────────────────────────────┐         ┌─────────────────────────────────┐
  │     Environment Provisioner     │         │          Fault Engine           │
  │  (DockerCompose / MockEnv)      │◄────────┤   FaultManager & BaseFault      │
  │  - Health Probes (/health)      │         │   - PodCrashFault               │
  │  - Container Restarts & Logs    │         │   - NetworkLatencyFault (netem) │
  │  - Baseline Reset & Teardown    │         │   - Independent Verification    │
  └────────────────┬────────────────┘         └─────────────────────────────────┘
                   │
                   ▼
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                         Target Infrastructure Lab                           │
  │   FastAPI App (:8000)  ◄───►  PostgreSQL (:5432)  ◄───►  Redis Cache (:6379) │
  └────────────────┬────────────────────────────────────────────────────────────┘
                   │
         ┌─────────┴───────────────────────────────┐
         ▼                                         ▼
  ┌─────────────────────────────────┐   ┌─────────────────────────────────────┐
  │       Agent Tool Gateway        │   │      Success Probe Evaluator        │
  │  - check_health(service)        │   │  (HttpProbe, HealthProbe, Metric)   │
  │  - read_metrics()               │   │  - Independent Recovery Check       │
  │  - read_service_logs(tail<=500) │   │  - Strict Objective Scoring         │
  │  - restart_service(service)     │   │  - Zero Agent Self-Reporting        │
  │  - Step & Time Budget Limits    │   └──────────────────┬──────────────────┘
  │  - Zero Ground-Truth Leakage    │                      │
  └────────────────┬────────────────┘                      │
                   │                                       │
                   └───────────────────┬───────────────────┘
                                       ▼
  ┌─────────────────────────────────────────────────────────────────────────────┐
  │                        Episode Trajectory Recorder                          │
  │  - JSON Artifacts: benchmark/records/{scenario_id}_{episode_id}.json        │
  │  - Audited Tool Trajectory, Durations, Evaluation Reports, and Root Causes  │
  └─────────────────────────────────────────────────────────────────────────────┘
```

---

## ⚙️ Phase 2 Benchmark Components

### 1. Scenario Schema & Catalog (`benchmark/schema.py`, `benchmark/loader.py`)
- Strict Pydantic v2 validation (`extra="forbid"`).
- Discriminated unions for typed faults (`pod_crash`, `network_latency`) and success probes (`http_probe`, `health_probe`, `metric_probe`).
- Strict information boundary: `AgentScenarioPayload` exposes only task description, symptoms, and environment topology, strictly stripping `ground_truth`, secret parameters, and internal evaluator probes.
- Catalog loader with duplicate ID detection, missing file handling, and unknown registry fault validation.

### 2. Episode State Machine (`benchmark/episode.py`)
Explicit lifecycle state machine with validation of allowed transitions:
```
CREATED -> PREPARING -> FAULTS_INJECTED -> READY -> RUNNING -> COMPLETED
             │                  │            │         │
             ▼                  ▼            ▼         ▼
        SETUP_FAILED       SETUP_FAILED  COMPLETED AGENT_FAILED
             ▲                                         ▲
             └───────────── CLEANUP_FAILED ────────────┘
```
- Distinguishes **`SETUP_FAILED`** (infrastructure or baseline errors) from **`AGENT_FAILED`** (agent budget exhaustion or crash). Agents are never penalized for infrastructure setup issues.
- Retains `FaultManager` on cleanup failures (`CLEANUP_FAILED`) allowing safe cleanup retries.

### 3. Environment Provisioner (`benchmark/environment.py`)
- `BaseEnvironmentProvisioner(ABC)`: Standardized contract for infrastructure lifecycle, health checks, logs, restarts, and command execution.
- `DockerComposeProvisioner`: Manages live containers natively or via WSL2 (`Ubuntu-22.04`), handles command timeouts, and parses container inspect metadata.
- `MockEnvironmentProvisioner`: In-memory implementation for deterministic, sub-second testing.

### 4. Real Reversible Fault Injectors (`fault_engine/faults/`)
- `PodCrashFault`: Stops target container (`docker stop`), independently verifies container exited via `provisioner.get_service_status()`, and restarts it during cleanup (`reset()`).
- `NetworkLatencyFault`: Applies Linux kernel queuing discipline delay (`tc qdisc add dev eth0 root netem delay {latency_ms}ms`), independently verifies active qdisc netem rules, and cleans up via `tc qdisc del`.

### 5. Success Probe Evaluator Engine (`benchmark/evaluator.py`)
- Independent verification of infrastructure recovery:
  - `HttpProbe`: Verifies HTTP response codes and recursive JSON subset matching.
  - `HealthProbe`: Verifies service status (`UP`).
  - `MetricProbe`: Resolves nested dot-notation paths (`latency_ms.p95`, `http_requests.error_rate_percent`) and evaluates comparative operators (`lt`, `lte`, `gt`, `gte`, `eq`).
- Generates `EvaluationReport` with objective scores (0.0 to 1.0).

### 6. Agent Tool Gateway (`benchmark/agent_gateway.py`)
- Bounded, auditable tool gateway:
  - `check_health(service_name=None)`: Queries aggregate `/health` or dedicated `/db/health`, `/redis/health`.
  - `read_metrics()`: Reads structured telemetry from `/metrics`.
  - `read_service_logs(service, tail=100)`: Retrieves container logs bounded to 500 lines max.
  - `restart_service(service)`: Restarts target service container.
- Enforces `max_steps` and `time_limit_seconds` from `BudgetConstraint`, raising `BudgetExceededError`.
- Provides OpenAI function-calling schemas via `get_tool_definitions()`.

### 7. Episode Outcome Recorder (`benchmark/recorder.py`)
- Serializes full episode execution into structured JSON files under `benchmark/records/`.
- Records tool call trajectories, step timestamps, execution latencies, probe evaluations, and error classifications.

---

## 🚀 API Endpoints

| Method | Endpoint | Description | Healthy Status | Unhealthy Status |
| :--- | :--- | :--- | :--- | :--- |
| `GET` | `/` | API discovery and endpoint listing | `200 OK` | - |
| `GET` | `/health` | Aggregate application & dependencies status | `200 OK` (`UP`) | `503 Service Unavailable` (`DEGRADED`/`DOWN`) |
| `GET` | `/db/health` | Dedicated PostgreSQL connectivity & probe latency | `200 OK` (`UP`) | `503 Service Unavailable` (`DOWN`) |
| `GET` | `/redis/health`| Dedicated Redis connectivity & probe latency | `200 OK` (`UP`) | `503 Service Unavailable` (`DOWN`) |
| `GET` | `/metrics` | Structured JSON telemetry (CPU, mem, latency, error rate) | `200 OK` | - |
| `GET` | `/orders` | List orders (served from Redis cache or PostgreSQL) | `200 OK` | `500 Internal Server Error` |
| `POST` | `/orders` | Create a new order (invalidates Redis cache) | `201 Created` | `500 Internal Server Error` |

---

## 🛠 Quick Start

### 1. Environment Setup
```bash
cp .env.example .env
```

### 2. Start Infrastructure
```bash
docker compose up -d --build
docker compose ps
```

### 3. Verify Health Probes
```bash
curl http://localhost:8000/health
curl http://localhost:8000/db/health
curl http://localhost:8000/redis/health
```

### 4. Run Automated Test Suite
```powershell
& "e:\100xDevs\InfraBench\.venv\Scripts\pytest.exe" tests/ -v
```

All 107 tests cover:
- FastAPI endpoints, database persistence, and Redis caching.
- Telemetry middleware and synthetic load generator.
- Fault registry and FaultManager reverse-order cleanup.
- Real reversible fault injectors (`pod_crash`, `network_latency`).
- Pydantic v2 scenario schemas and YAML catalog loading.
- Episode lifecycle state machine and transitions.
- Environment provisioner abstraction and health checks.
- Success probe evaluator engine (HTTP, Health, Metric).
- Agent Tool Gateway with budget enforcement.
- Episode trajectory recorder and full end-to-end scenario execution.

### 5. Teardown
```bash
docker compose down -v
```
