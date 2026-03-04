"""Throughput benchmark - events/sec at varying payload sizes."""

import asyncio
import time
from typing import Dict, Any


def run() -> Dict[str, Any]:
    """Run throughput benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async throughput benchmark."""
    from streamfn import stream_fn

    @stream_fn
    async def passthrough(events):
        """Simple passthrough function."""
        async for event in events:
            yield event

    results = {
        "payload_sizes": {},
        "stream_sizes": {},
    }

    # Test varying payload sizes (64B, 1KB, 64KB)
    payload_sizes = [64, 1024, 64 * 1024]
    num_events = 10000

    for size in payload_sizes:
        payload = b"x" * size
        events = [{"data": payload, "id": i} for i in range(num_events)]

        start = time.perf_counter()
        await passthrough.run(events)
        elapsed = time.perf_counter() - start

        events_per_sec = num_events / elapsed
        mb_per_sec = (num_events * size) / (1024 * 1024) / elapsed

        results["payload_sizes"][f"{size}B"] = {
            "events_per_sec": events_per_sec,
            "mb_per_sec": mb_per_sec,
            "elapsed_sec": elapsed,
        }

        print(f"  Payload {size}B: {events_per_sec:.0f} events/sec, {mb_per_sec:.1f} MB/sec")

    # Test varying stream sizes (10, 100, 1K, 10K events)
    payload = b"x" * 1024
    stream_sizes = [10, 100, 1000, 10000]

    for num in stream_sizes:
        events = [{"data": payload, "id": i} for i in range(num)]

        start = time.perf_counter()
        await passthrough.run(events)
        elapsed = time.perf_counter() - start

        events_per_sec = num / elapsed

        results["stream_sizes"][f"{num}_events"] = {
            "events_per_sec": events_per_sec,
            "elapsed_sec": elapsed,
        }

        print(f"  Stream {num} events: {events_per_sec:.0f} events/sec ({elapsed*1000:.1f}ms)")

    # Overall metrics
    best_throughput = max(
        r["events_per_sec"] for r in results["payload_sizes"].values()
    )

    results["events_per_sec"] = best_throughput

    return results
