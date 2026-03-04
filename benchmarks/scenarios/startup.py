"""Startup benchmark - stream open-to-first-event latency."""

import asyncio
import time
from typing import Dict, Any, List


def run() -> Dict[str, Any]:
    """Run startup benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async startup benchmark."""
    from streamfn import stream_fn

    @stream_fn
    async def simple_fn(events):
        """Simple function."""
        async for event in events:
            yield event

    num_iterations = 1000
    startup_times: List[float] = []

    for i in range(num_iterations):
        start = time.perf_counter_ns()

        async with simple_fn.open() as stream:
            await stream.send({"id": i})
            await stream.recv()

        elapsed_ns = time.perf_counter_ns() - start
        startup_times.append(elapsed_ns / 1000)  # Convert to microseconds

    startup_times.sort()

    def percentile(data: List[float], p: float) -> float:
        idx = int(len(data) * p)
        return data[min(idx, len(data) - 1)]

    results = {
        "min_us": min(startup_times),
        "mean_us": sum(startup_times) / len(startup_times),
        "p50_us": percentile(startup_times, 0.50),
        "p95_us": percentile(startup_times, 0.95),
        "p99_us": percentile(startup_times, 0.99),
        "max_us": max(startup_times),
    }

    print(f"  Min: {results['min_us']:.0f}μs")
    print(f"  Mean: {results['mean_us']:.0f}μs")
    print(f"  P99: {results['p99_us']:.0f}μs")

    return results
