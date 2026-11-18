"""Tests for the deterministic synthetic load generator."""

import pytest
from unittest.mock import AsyncMock, patch
from load_generator.generator import SyntheticLoadGenerator


@pytest.mark.asyncio
async def test_load_generator_deterministic_selection():
    """Verify that identical random seeds produce identical request sequences."""
    gen1 = SyntheticLoadGenerator(seed=42, total_requests=10)
    gen2 = SyntheticLoadGenerator(seed=42, total_requests=10)

    seq1 = [gen1._select_endpoint() for _ in range(10)]
    seq2 = [gen2._select_endpoint() for _ in range(10)]

    assert seq1 == seq2


@pytest.mark.asyncio
async def test_load_generator_run_and_summary():
    """Verify that SyntheticLoadGenerator.run computes metrics correctly."""
    gen = SyntheticLoadGenerator(
        base_url="http://mock-infrabench",
        total_requests=20,
        concurrency=2,
        seed=123,
    )

    # Mock _send_request to avoid actual network traffic during unit test
    async def mock_send(client, method, path, payload):
        if path == "/non-existent-endpoint":
            return 404, 5.0
        return 200, 10.0

    with patch.object(gen, "_send_request", side_effect=mock_send):
        summary = await gen.run()

        assert summary["total_requests"] == 20
        assert summary["target_url"] == "http://mock-infrabench"
        assert summary["successful_requests"] > 0
        assert summary["duration_seconds"] > 0
        assert summary["requests_per_second"] > 0
        assert "latencies_ms" in summary

        lats = summary["latencies_ms"]
        assert lats["min"] > 0
        assert lats["max"] >= lats["min"]
        assert lats["p50"] >= lats["min"]
        assert lats["p99"] >= lats["p95"]
