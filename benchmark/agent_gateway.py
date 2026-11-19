"""Agent Tool Gateway and observation interface for InfraBench.

Provides bounded, safe, auditable tool execution for autonomous SRE agents while enforcing
strict step limits, time budgets, and isolation guarantees (never leaking ground-truth or
evaluator probe secrets).
"""

from __future__ import annotations

import datetime
import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional, Tuple

from benchmark.environment import BaseEnvironmentProvisioner
from benchmark.episode import Episode
from benchmark.schema import BudgetConstraint

logger = logging.getLogger("infrabench.gateway")


class GatewayError(RuntimeError):
    """Base exception for agent gateway errors."""
    pass


class BudgetExceededError(GatewayError):
    """Raised when an agent exceeds its step or time budget."""
    pass


class ToolExecutionError(GatewayError):
    """Raised when an agent invokes an unknown or unauthorized tool."""
    pass


class AgentToolGateway:
    """Secured, rate-limited, and auditable gateway exposing diagnostic and remediation tools."""

    ALLOWED_SERVICES = {"app", "db", "cache", "postgresql", "redis"}

    def __init__(
        self,
        episode: Episode,
        budget: BudgetConstraint,
        provisioner: BaseEnvironmentProvisioner,
        base_url: str = "http://localhost:8000",
        http_requester: Optional[
            Callable[[str, str, Optional[Dict[str, Any]], float], Tuple[int, Any, float]]
        ] = None,
    ) -> None:
        self.episode = episode
        self.budget = budget
        self.provisioner = provisioner
        self.base_url = base_url.rstrip("/")
        self._http_requester = http_requester or self._default_http_request

    def _default_http_request(
        self,
        url: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Tuple[int, Any, float]:
        """Perform an HTTP request returning (status_code, parsed_body, latency_ms)."""
        req_data = json.dumps(data).encode("utf-8") if data else None
        headers = {"User-Agent": "InfraBench-AgentGateway"}
        if req_data:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=req_data, headers=headers, method=method)
        start_time = time.perf_counter()

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0
                body = resp.read().decode("utf-8")
                try:
                    parsed = json.loads(body)
                except Exception:
                    parsed = body
                return resp.status, parsed, elapsed_ms
        except urllib.error.HTTPError as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            body = exc.read().decode("utf-8")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = body
            return exc.code, parsed, elapsed_ms
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start_time) * 1000.0
            return 0, str(exc), elapsed_ms

    def _check_budget(self) -> None:
        """Enforce step count and elapsed time budgets."""
        # 1. Step Budget Check
        if self.episode.step_count >= self.budget.max_steps:
            raise BudgetExceededError(
                f"Step budget exceeded: {self.episode.step_count}/{self.budget.max_steps} steps consumed."
            )

        # 2. Time Budget Check
        if self.episode.started_at:
            try:
                if isinstance(self.episode.started_at, datetime.datetime):
                    started_dt = self.episode.started_at
                else:
                    started_dt = datetime.datetime.fromisoformat(str(self.episode.started_at))
                now_dt = datetime.datetime.now(datetime.timezone.utc)
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=datetime.timezone.utc)
                elapsed_seconds = (now_dt - started_dt).total_seconds()
                if elapsed_seconds > self.budget.time_limit_seconds:
                    raise BudgetExceededError(
                        f"Time budget exceeded: {elapsed_seconds:.1f}s / "
                        f"{self.budget.time_limit_seconds}s limit."
                    )
            except BudgetExceededError:
                raise
            except Exception:
                pass

    def execute(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        """Dispatch a tool call, audit execution in episode trajectory, and track budget."""
        self._check_budget()

        start_time = time.perf_counter()
        result: Dict[str, Any] = {}
        error_msg: Optional[str] = None

        try:
            if tool_name == "check_health":
                result = self.check_health(kwargs.get("service_name"))
            elif tool_name == "read_metrics":
                result = self.read_metrics()
            elif tool_name == "read_service_logs":
                service = kwargs.get("service")
                if not service:
                    raise ToolExecutionError("Missing required argument 'service' for read_service_logs")
                tail = int(kwargs.get("tail", 100))
                result = self.read_service_logs(service, tail=tail)
            elif tool_name == "restart_service":
                service = kwargs.get("service")
                if not service:
                    raise ToolExecutionError("Missing required argument 'service' for restart_service")
                result = self.restart_service(service)
            else:
                raise ToolExecutionError(f"Unknown or unauthorized tool: '{tool_name}'")

        except Exception as exc:
            error_msg = str(exc)
            result = {"error": error_msg, "success": False}
            raise
        finally:
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            self.episode.record_tool_call(
                tool_name=tool_name,
                arguments=kwargs,
                result=result,
                duration_ms=duration_ms,
                error=error_msg,
            )

        return result

    def check_health(self, service_name: Optional[str] = None) -> Dict[str, Any]:
        """Query aggregate or service-specific health endpoint."""
        if service_name in {"db", "postgresql"}:
            endpoint = "/db/health"
        elif service_name in {"cache", "redis"}:
            endpoint = "/redis/health"
        else:
            endpoint = "/health"

        url = f"{self.base_url}{endpoint}"
        status_code, body, latency_ms = self._http_requester(url, "GET", None, 5.0)

        return {
            "status_code": status_code,
            "response": body,
            "latency_ms": latency_ms,
            "queried_service": service_name or "aggregate",
        }

    def read_metrics(self) -> Dict[str, Any]:
        """Fetch current telemetry metrics from the application."""
        url = f"{self.base_url}/metrics"
        status_code, body, latency_ms = self._http_requester(url, "GET", None, 5.0)

        return {
            "status_code": status_code,
            "metrics": body if isinstance(body, dict) else {},
            "latency_ms": latency_ms,
        }

    def read_service_logs(self, service: str, tail: int = 100) -> Dict[str, Any]:
        """Fetch trailing logs from a managed service container (max 500 lines)."""
        if service not in self.ALLOWED_SERVICES:
            raise ToolExecutionError(
                f"Unauthorized service '{service}'. Allowed services: {sorted(self.ALLOWED_SERVICES)}"
            )

        # Bound tail length to protect agent context window and memory
        bounded_tail = max(1, min(tail, 500))
        logs = self.provisioner.get_service_logs(service, tail=bounded_tail)

        return {
            "service": service,
            "lines_requested": bounded_tail,
            "logs": logs,
        }

    def restart_service(self, service: str) -> Dict[str, Any]:
        """Restart a specific service container."""
        if service not in self.ALLOWED_SERVICES:
            raise ToolExecutionError(
                f"Unauthorized service '{service}'. Allowed services: {sorted(self.ALLOWED_SERVICES)}"
            )

        success = self.provisioner.restart_service(service)

        return {
            "service": service,
            "success": success,
            "action": "restart",
            "message": f"Service '{service}' restarted successfully" if success else f"Failed to restart '{service}'",
        }

    @classmethod
    def get_tool_definitions(cls) -> List[Dict[str, Any]]:
        """Return OpenAI-compatible function calling schemas for agent integration."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "check_health",
                    "description": "Probe aggregate or service-specific health endpoints.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service_name": {
                                "type": "string",
                                "enum": ["app", "db", "cache", "postgresql", "redis"],
                                "description": "Optional specific service to check. Omit for aggregate health.",
                            }
                        },
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_metrics",
                    "description": "Fetch real-time system, HTTP traffic, and latency telemetry metrics.",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "read_service_logs",
                    "description": "Retrieve recent log lines from a target container service.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service": {
                                "type": "string",
                                "enum": ["app", "db", "cache"],
                                "description": "Target service name to fetch logs for.",
                            },
                            "tail": {
                                "type": "integer",
                                "default": 100,
                                "description": "Number of trailing log lines to fetch (max 500).",
                            },
                        },
                        "required": ["service"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "restart_service",
                    "description": "Restart a failed, degraded, or stopped service container.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "service": {
                                "type": "string",
                                "enum": ["app", "db", "cache"],
                                "description": "Target service to restart.",
                            }
                        },
                        "required": ["service"],
                    },
                },
            },
        ]
