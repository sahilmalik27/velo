"""Latency benchmark - P50/P95/P99 inter-event latency."""

import asyncio
import time
from typing import Dict, Any, List


def run() -> Dict[str, Any]:
    """Run latency benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async latency benchmark."""
    from velo import stream_fn

    @stream_fn
    async def passthrough(events):
        """Simple passthrough function."""
        async for event in events:
            yield event

    num_events = 10000
    latencies: List[float] = []

    # Use live mode to measure per-event latency
    async with passthrough.open() as stream:
        for i in range(num_events):
            event = {"id": i, "data": b"x" * 64}

            start = time.perf_counter_ns()
            await stream.send(event)
            result = await stream.recv()
            elapsed_ns = time.perf_counter_ns() - start

            latencies.append(elapsed_ns / 1000)  # Convert to microseconds

    # Calculate percentiles
    latencies.sort()

    def percentile(data: List[float], p: float) -> float:
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    results = {
        "p50_us": percentile(latencies, 0.50),
        "p95_us": percentile(latencies, 0.95),
        "p99_us": percentile(latencies, 0.99),
        "max_us": max(latencies),
        "min_us": min(latencies),
        "mean_us": sum(latencies) / len(latencies),
    }

    print(f"  P50: {results['p50_us']:.0f}μs")
    print(f"  P95: {results['p95_us']:.0f}μs")
    print(f"  P99: {results['p99_us']:.0f}μs")
    print(f"  Max: {results['max_us']:.0f}μs")

    return results
