"""Concurrency benchmark - N concurrent streams scaling."""

import asyncio
import time
from typing import Dict, Any


def run() -> Dict[str, Any]:
    """Run concurrency benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async concurrency benchmark."""
    from velo import stream_fn

    @stream_fn
    async def counter(events):
        """Count events."""
        count = 0
        async for event in events:
            count += 1
            yield count

    results = {"concurrency_levels": {}}

    # Test with increasing concurrency: 1, 10, 100, 1000
    concurrency_levels = [1, 10, 100, 1000]
    events_per_stream = 100

    for num_streams in concurrency_levels:
        print(f"  Testing {num_streams} concurrent streams...")

        start = time.perf_counter()

        # Open N concurrent streams
        async def run_stream(stream_id: int):
            async with counter.open() as stream:
                for i in range(events_per_stream):
                    await stream.send({"id": i, "stream": stream_id})
                    await stream.recv()

        # Run all streams concurrently
        await asyncio.gather(*[run_stream(i) for i in range(num_streams)])

        elapsed = time.perf_counter() - start
        total_events = num_streams * events_per_stream
        events_per_sec = total_events / elapsed
        events_per_sec_per_stream = events_per_sec / num_streams

        results["concurrency_levels"][f"{num_streams}_streams"] = {
            "total_events_per_sec": events_per_sec,
            "events_per_sec_per_stream": events_per_sec_per_stream,
            "elapsed_sec": elapsed,
        }

        print(f"    Total: {events_per_sec:.0f} events/sec")
        print(f"    Per stream: {events_per_sec_per_stream:.0f} events/sec")

    results["max_streams"] = max(concurrency_levels)

    return results
