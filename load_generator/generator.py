"""Deterministic synthetic HTTP load generator for InfraBench."""

import argparse
import asyncio
import math
import random
import sys
import time
from typing import Dict, List, Optional, Tuple

import httpx


class SyntheticLoadGenerator:
    """Generates deterministic HTTP traffic against target endpoints."""

    # Weighted endpoint distribution: (method, path, body_fn, weight)
    # Allows deterministic sequence based on RNG seed
    ENDPOINT_PROFILES = [
        ("GET", "/orders", None, 45),
        ("GET", "/health", None, 20),
        ("GET", "/metrics", None, 15),
        ("GET", "/db/health", None, 10),
        ("GET", "/redis/health", None, 5),
        ("GET", "/non-existent-endpoint", None, 3),  # Intentionally generates 404 for error-rate telemetry
        ("POST", "/orders", "create_order_body", 2),
    ]

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        total_requests: int = 100,
        concurrency: int = 5,
        rate_limit_rps: float = 0.0,
        seed: int = 42,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.total_requests = total_requests
        self.concurrency = max(1, concurrency)
        self.rate_limit_rps = rate_limit_rps
        self.seed = seed
        self.timeout = timeout
        self.rng = random.Random(seed)

        # Metrics collectors
        self.status_counts: Dict[int, int] = {}
        self.latencies: List[float] = []
        self.errors: List[str] = []
        self._lock = asyncio.Lock()

    def _sample_order_payload(self) -> dict:
        """Deterministically generate an order payload."""
        names = ["Alice", "Bob", "Charlie", "Diana", "Evan", "Fiona", "George"]
        customer = self.rng.choice(names) + f"_{self.rng.randint(100, 999)}"
        items = self.rng.randint(1, 10)
        amount = round(self.rng.uniform(10.0, 500.0), 2)
        return {
            "customer_name": customer,
            "item_count": items,
            "total_amount": amount,
            "status": "PENDING",
        }

    def _select_endpoint(self) -> Tuple[str, str, Optional[dict]]:
        """Select endpoint deterministically based on RNG weights."""
        total_weight = sum(p[3] for p in self.ENDPOINT_PROFILES)
        r = self.rng.uniform(0, total_weight)
        cumulative = 0.0
        for method, path, body_key, weight in self.ENDPOINT_PROFILES:
            cumulative += weight
            if r <= cumulative:
                payload = self._sample_order_payload() if body_key else None
                return method, path, payload
        return "GET", "/orders", None

    async def _send_request(
        self, client: httpx.AsyncClient, method: str, path: str, payload: Optional[dict]
    ) -> Tuple[int, float]:
        """Send a single HTTP request and record its latency."""
        url = f"{self.base_url}{path}"
        start = time.perf_counter()
        try:
            if method == "POST":
                resp = await client.post(url, json=payload, timeout=self.timeout)
            else:
                resp = await client.get(url, timeout=self.timeout)
            latency = (time.perf_counter() - start) * 1000.0
            return resp.status_code, latency
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000.0
            return 0, latency

    async def _worker(
        self,
        worker_id: int,
        client: httpx.AsyncClient,
        queue: asyncio.Queue,
    ) -> None:
        """Worker task processing items from the request queue."""
        while True:
            item = await queue.get()
            if item is None:
                queue.task_done()
                break

            method, path, payload = item
            status_code, latency = await self._send_request(client, method, path, payload)

            async with self._lock:
                self.status_counts[status_code] = self.status_counts.get(status_code, 0) + 1
                self.latencies.append(latency)

            # Apply rate limiting delay if configured
            if self.rate_limit_rps > 0:
                await asyncio.sleep(1.0 / self.rate_limit_rps)

            queue.task_done()

    async def run(self) -> Dict[str, any]:
        """Execute the load generation run."""
        # Pre-generate deterministic sequence of requests
        requests_plan = [self._select_endpoint() for _ in range(self.total_requests)]

        queue: asyncio.Queue = asyncio.Queue()
        for req in requests_plan:
            queue.put_nowait(req)

        # Worker shutdown sentinels
        for _ in range(self.concurrency):
            queue.put_nowait(None)

        start_time = time.perf_counter()

        limits = httpx.Limits(max_keepalive_connections=self.concurrency, max_connections=self.concurrency * 2)
        async with httpx.AsyncClient(limits=limits) as client:
            workers = [
                asyncio.create_task(self._worker(i, client, queue))
                for i in range(self.concurrency)
            ]
            await asyncio.gather(*workers)

        total_duration = time.perf_counter() - start_time
        return self._build_summary(total_duration)

    def _build_summary(self, total_duration: float) -> Dict[str, any]:
        """Calculate and format summary metrics."""
        sorted_latencies = sorted(self.latencies) if self.latencies else [0.0]
        count = len(sorted_latencies)

        def pct(p: float) -> float:
            idx = int(math.ceil((p / 100.0) * count)) - 1
            return round(sorted_latencies[max(0, min(idx, count - 1))], 2)

        successful_requests = sum(v for k, v in self.status_counts.items() if 200 <= k < 400)
        client_errors = sum(v for k, v in self.status_counts.items() if 400 <= k < 500)
        server_errors = sum(v for k, v in self.status_counts.items() if 500 <= k < 600 or k == 0)

        summary = {
            "target_url": self.base_url,
            "total_requests": count,
            "duration_seconds": round(total_duration, 3),
            "requests_per_second": round(count / total_duration, 2) if total_duration > 0 else 0.0,
            "successful_requests": successful_requests,
            "client_errors": client_errors,
            "server_errors": server_errors,
            "status_code_distribution": self.status_counts,
            "latencies_ms": {
                "min": round(sorted_latencies[0], 2),
                "avg": round(sum(sorted_latencies) / count, 2) if count else 0.0,
                "p50": pct(50.0),
                "p95": pct(95.0),
                "p99": pct(99.0),
                "max": round(sorted_latencies[-1], 2),
            },
        }
        return summary


def print_summary(summary: Dict[str, any]) -> None:
    """Print readable summary to stdout."""
    print("=" * 60)
    print("           INFRABENCH SYNTHETIC LOAD SUMMARY           ")
    print("=" * 60)
    print(f"Target:                {summary['target_url']}")
    print(f"Total Requests:        {summary['total_requests']}")
    print(f"Duration:              {summary['duration_seconds']}s")
    print(f"Throughput:            {summary['requests_per_second']} req/sec")
    print(f"Successful (2xx/3xx):  {summary['successful_requests']}")
    print(f"Client Errors (4xx):   {summary['client_errors']}")
    print(f"Server Errors / Drop:  {summary['server_errors']}")
    print("-" * 60)
    print("Status Code Breakdown:")
    for code, count in sorted(summary['status_code_distribution'].items()):
        status_label = f"HTTP {code}" if code > 0 else "Connection Failed"
        print(f"  {status_label:<20}: {count}")
    print("-" * 60)
    print("Latency Distribution (ms):")
    lats = summary["latencies_ms"]
    print(f"  Min: {lats['min']}ms | Avg: {lats['avg']}ms | Max: {lats['max']}ms")
    print(f"  p50: {lats['p50']}ms | p95: {lats['p95']}ms | p99: {lats['p99']}ms")
    print("=" * 60)


def main() -> None:
    """CLI entrypoint for synthetic load generator."""
    parser = argparse.ArgumentParser(description="InfraBench Deterministic Synthetic Load Generator")
    parser.add_argument("--url", default="http://localhost:8000", help="Target base URL")
    parser.add_argument("-n", "--requests", type=int, default=100, help="Total number of requests")
    parser.add_argument("-c", "--concurrency", type=int, default=5, help="Concurrent workers")
    parser.add_argument("-r", "--rate", type=float, default=0.0, help="Rate limit per worker (RPS, 0=unlimited)")
    parser.add_argument("-s", "--seed", type=int, default=42, help="Deterministic random seed")
    parser.add_argument("-t", "--timeout", type=float, default=10.0, help="Request timeout in seconds")

    args = parser.parse_args()

    generator = SyntheticLoadGenerator(
        base_url=args.url,
        total_requests=args.requests,
        concurrency=args.concurrency,
        rate_limit_rps=args.rate,
        seed=args.seed,
        timeout=args.timeout,
    )

    summary = asyncio.run(generator.run())
    print_summary(summary)


if __name__ == "__main__":
    main()
