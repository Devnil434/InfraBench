"""Environment provisioner abstraction for InfraBench.

Decouples scenario orchestration from concrete infrastructure technologies (Docker, WSL2, Kubernetes,
or Mock test environments).
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("infrabench.environment")


class EnvironmentError(Exception):
    """Base exception for environment management failures."""
    pass


class EnvironmentHealthCheckError(EnvironmentError):
    """Raised when environment health verification fails."""
    pass


class EnvironmentProvisionError(EnvironmentError):
    """Raised when environment provisioning or teardown fails."""
    pass


class BaseEnvironmentProvisioner(ABC):
    """Abstract base class for benchmark environment provisioners."""

    @abstractmethod
    def provision(self) -> None:
        """Ensure the target environment is running and initialized."""
        pass

    @abstractmethod
    def teardown(self) -> None:
        """Tear down or stop environment resources."""
        pass

    @abstractmethod
    def reset_to_baseline(self) -> None:
        """Restore all containers, services, and network rules to a clean, healthy baseline."""
        pass

    @abstractmethod
    def verify_health(self) -> bool:
        """Return True if all critical services are healthy and responsive."""
        pass

    @abstractmethod
    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """Return operational status dictionary for a named service."""
        pass

    @abstractmethod
    def restart_service(self, service_name: str) -> bool:
        """Restart a specific service."""
        pass

    @abstractmethod
    def stop_service(self, service_name: str, timeout: int = 10) -> bool:
        """Stop a specific service."""
        pass

    @abstractmethod
    def start_service(self, service_name: str) -> bool:
        """Start a specific service."""
        pass

    @abstractmethod
    def get_service_logs(self, service_name: str, tail: int = 100) -> str:
        """Fetch the most recent log output for a service."""
        pass

    @abstractmethod
    def execute_command(
        self,
        service_name: str,
        command: List[str],
        timeout: float = 15.0,
    ) -> Tuple[int, str, str]:
        """Execute a command inside the service container/environment.

        Returns:
            Tuple of (exit_code, stdout, stderr).
        """
        pass


class MockEnvironmentProvisioner(BaseEnvironmentProvisioner):
    """In-memory mock environment provisioner for fast deterministic testing."""

    def __init__(
        self,
        services: Optional[Dict[str, Dict[str, Any]]] = None,
        health_status: bool = True,
    ) -> None:
        self.services = services or {
            "app": {"running": True, "healthy": True, "logs": "app running\n"},
            "db": {"running": True, "healthy": True, "logs": "db ready\n"},
            "cache": {"running": True, "healthy": True, "logs": "redis ready\n"},
        }
        self.health_status = health_status
        self.command_outputs: Dict[str, Tuple[int, str, str]] = {}
        self.command_history: List[Tuple[str, List[str]]] = []
        self.restarted_services: List[str] = []
        self.stopped_services: List[str] = []
        self.started_services: List[str] = []
        self.baseline_reset_called: bool = False

    def provision(self) -> None:
        for s in self.services.values():
            s["running"] = True
            s["healthy"] = True

    def teardown(self) -> None:
        for s in self.services.values():
            s["running"] = False
            s["healthy"] = False

    def reset_to_baseline(self) -> None:
        self.baseline_reset_called = True
        self.provision()
        self.health_status = True

    def verify_health(self) -> bool:
        if not self.health_status:
            return False
        return all(
            s.get("running", False) and s.get("healthy", False)
            for s in self.services.values()
        )

    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        if service_name not in self.services:
            return {"running": False, "healthy": False, "error": f"Unknown service: {service_name}"}
        return dict(self.services[service_name])

    def restart_service(self, service_name: str) -> bool:
        if service_name not in self.services:
            return False
        self.restarted_services.append(service_name)
        self.services[service_name]["running"] = True
        self.services[service_name]["healthy"] = True
        return True

    def stop_service(self, service_name: str, timeout: int = 10) -> bool:
        if service_name not in self.services:
            return False
        self.stopped_services.append(service_name)
        self.services[service_name]["running"] = False
        self.services[service_name]["healthy"] = False
        return True

    def start_service(self, service_name: str) -> bool:
        if service_name not in self.services:
            return False
        self.started_services.append(service_name)
        self.services[service_name]["running"] = True
        self.services[service_name]["healthy"] = True
        return True

    def get_service_logs(self, service_name: str, tail: int = 100) -> str:
        if service_name not in self.services:
            return ""
        return str(self.services[service_name].get("logs", ""))

    def execute_command(
        self,
        service_name: str,
        command: List[str],
        timeout: float = 15.0,
    ) -> Tuple[int, str, str]:
        self.command_history.append((service_name, command))
        cmd_key = " ".join(command)
        return self.command_outputs.get(cmd_key, (0, "ok", ""))


class DockerComposeProvisioner(BaseEnvironmentProvisioner):
    """Manages Docker Compose services natively on Linux/macOS or via WSL2 on Windows."""

    DEFAULT_CONTAINER_MAP = {
        "app": "infrabench-app",
        "db": "infrabench-db",
        "cache": "infrabench-cache",
        "postgresql": "infrabench-db",
        "redis": "infrabench-cache",
    }

    def __init__(
        self,
        compose_file: Optional[Path | str] = None,
        project_name: str = "infrabench",
        app_url: str = "http://localhost:8000",
        wsl_distro: Optional[str] = "Ubuntu-22.04",
        container_map: Optional[Dict[str, str]] = None,
        command_runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
        health_check_timeout: float = 3.0,
    ) -> None:
        self.compose_file = Path(compose_file) if compose_file else None
        self.project_name = project_name
        self.app_url = app_url.rstrip("/")
        self.wsl_distro = wsl_distro
        self.container_map = dict(self.DEFAULT_CONTAINER_MAP)
        if container_map:
            self.container_map.update(container_map)
        self._command_runner = command_runner or subprocess.run
        self.health_check_timeout = health_check_timeout

    def _build_command(self, docker_args: List[str]) -> List[str]:
        """Build command list, prepending WSL2 wrapper if configured."""
        if self.wsl_distro:
            return ["wsl", "-d", self.wsl_distro, "docker", *docker_args]
        return ["docker", *docker_args]

    def _run_docker(
        self,
        docker_args: List[str],
        timeout: float = 30.0,
    ) -> Tuple[int, str, str]:
        """Execute a docker CLI command safely."""
        cmd = self._build_command(docker_args)
        try:
            res = self._command_runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return res.returncode, res.stdout, res.stderr
        except subprocess.TimeoutExpired as exc:
            return 124, "", f"Command timed out after {timeout}s: {' '.join(cmd)}"
        except Exception as exc:
            return 1, "", str(exc)

    def _resolve_container(self, service_name: str) -> str:
        """Map logical service name to container name."""
        return self.container_map.get(service_name, service_name)

    def provision(self) -> None:
        """Ensure all containers are running."""
        for service, container in self.container_map.items():
            if service in {"app", "db", "cache"}:
                self.start_service(service)

    def teardown(self) -> None:
        """Stop all managed containers."""
        for service in {"app", "db", "cache"}:
            self.stop_service(service)

    def reset_to_baseline(self) -> None:
        """Restore all containers to running and remove any netem/tc rules."""
        for service in {"db", "cache", "app"}:
            container = self._resolve_container(service)
            self._run_docker(["start", container], timeout=15.0)

            # Clear any traffic control netem rules on eth0 if present
            self._run_docker(
                ["exec", container, "tc", "qdisc", "del", "dev", "eth0", "root"],
                timeout=10.0,
            )

        # Allow services a moment to report healthy
        for _ in range(5):
            if self.verify_health():
                return
            time.sleep(1.0)

    def verify_health(self) -> bool:
        """Check application aggregate health and dependent services."""
        url = f"{self.app_url}/health"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "InfraBench-Provisioner"})
            with urllib.request.urlopen(req, timeout=self.health_check_timeout) as resp:
                if resp.status != 200:
                    return False
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                if data.get("status") != "UP":
                    return False
                services = data.get("services", {})
                pg_status = services.get("postgresql", {}).get("status")
                redis_status = services.get("redis", {}).get("status")
                return pg_status == "UP" and redis_status == "UP"
        except Exception:
            return False

    def get_service_status(self, service_name: str) -> Dict[str, Any]:
        """Inspect container running state and health."""
        container = self._resolve_container(service_name)
        code, stdout, stderr = self._run_docker(
            ["inspect", "--format", "{{json .State}}", container],
            timeout=10.0,
        )
        if code != 0 or not stdout.strip():
            return {
                "service": service_name,
                "container": container,
                "running": False,
                "healthy": False,
                "error": stderr.strip() or "Container inspect failed",
            }

        try:
            state = json.loads(stdout.strip())
            running = state.get("Running", False)
            health = state.get("Health", {}).get("Status")
            is_healthy = running and (health == "healthy" or health is None)
            return {
                "service": service_name,
                "container": container,
                "running": running,
                "healthy": is_healthy,
                "health_status": health,
                "exit_code": state.get("ExitCode", 0),
            }
        except Exception as exc:
            return {
                "service": service_name,
                "container": container,
                "running": False,
                "healthy": False,
                "error": str(exc),
            }

    def restart_service(self, service_name: str) -> bool:
        """Restart container for a named service."""
        container = self._resolve_container(service_name)
        code, _, _ = self._run_docker(["restart", container], timeout=20.0)
        return code == 0

    def stop_service(self, service_name: str, timeout: int = 10) -> bool:
        """Stop container for a named service."""
        container = self._resolve_container(service_name)
        code, _, _ = self._run_docker(["stop", "-t", str(timeout), container], timeout=timeout + 5.0)
        return code == 0

    def start_service(self, service_name: str) -> bool:
        """Start container for a named service."""
        container = self._resolve_container(service_name)
        code, _, _ = self._run_docker(["start", container], timeout=15.0)
        return code == 0

    def get_service_logs(self, service_name: str, tail: int = 100) -> str:
        """Read stdout/stderr logs from a service container."""
        container = self._resolve_container(service_name)
        code, stdout, stderr = self._run_docker(
            ["logs", "--tail", str(tail), container],
            timeout=15.0,
        )
        if code != 0:
            return f"Error retrieving logs for {container}: {stderr.strip()}"
        return stdout

    def execute_command(
        self,
        service_name: str,
        command: List[str],
        timeout: float = 15.0,
    ) -> Tuple[int, str, str]:
        """Execute a command in a running service container."""
        container = self._resolve_container(service_name)
        return self._run_docker(["exec", container, *command], timeout=timeout)
