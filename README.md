# InfraBench — Autonomous SRE Benchmark Infrastructure (Phase 0 & Phase 1)

InfraBench is an autonomous Site Reliability Engineering (SRE) benchmark environment providing a realistic target infrastructure for evaluating autonomous agents and SRE diagnostics.

---

## 🏛 System Architecture

The infrastructure consists of three minimal, production-grade containerized services orchestrated via Docker Compose:

1. **FastAPI Application (`app`)**:
   - Python 3.11 asynchronous web service (`uvicorn`).
   - Lifespan management for graceful startup and shutdown.
   - Database connection pooling with SQLAlchemy and `asyncpg`.
   - Redis caching layer (`redis.asyncio`) with cache-aside pattern.
   - Built-in telemetry middleware recording CPU, memory, request volume, status codes, latency percentiles (p50, p95, p99), and error rates.
2. **PostgreSQL Database (`db`)**:
   - PostgreSQL 16 on Alpine Linux.
   - Persistent Docker volume (`infrabench_postgres_data`).
   - Automated deterministic schema and initial seed data via `init-scripts/01_init.sql`.
   - Native container health check (`pg_isready`).
3. **Redis Cache (`cache`)**:
   - Redis 7 on Alpine Linux.
   - Persistent AOF volume (`infrabench_redis_data`).
   - Native container health check (`redis-cli ping`).
4. **Synthetic Load Generator (`load-generator`)**:
   - Deterministic HTTP traffic simulator generating reproducible workload mixes.
   - Configurable request counts, concurrency, rate limits, and random seed.
   - Accessible via CLI or Docker Compose profile (`loadgen`).

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

## 🛠 Quick Start (Docker Compose)

### 1. Environment Setup
Copy the example environment configuration:
```bash
cp .env.example .env
```

### 2. Start Infrastructure
Start the database, cache, and FastAPI service:
```bash
docker compose up -d --build
```

Verify service status:
```bash
docker compose ps
```

### 3. Verify Health Probes
```bash
curl http://localhost:8000/health
curl http://localhost:8000/db/health
curl http://localhost:8000/redis/health
```

### 4. Query Orders & Inspect Caching
```bash
# First request (fetches from PostgreSQL, populates Redis cache):
curl http://localhost:8000/orders

# Second request (served from Redis cache with "cached": true):
curl http://localhost:8000/orders
```

### 5. Inspect Structured Telemetry
```bash
curl http://localhost:8000/metrics
```

### 6. Run Synthetic Load Generator
To generate deterministic HTTP traffic against the live infrastructure:
```bash
docker compose run --rm load-generator
```
Or directly from the host:
```bash
python -m load_generator.generator --url http://localhost:8000 -n 200 -c 5 --seed 42
```

### 7. Run Automated Tests
Execute the unit and integration test suite:
```bash
docker compose run --rm app pytest -v
```

### 8. Teardown
```bash
docker compose down -v
```
