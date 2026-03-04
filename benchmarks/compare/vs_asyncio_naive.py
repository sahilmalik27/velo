"""Comparison vs pure asyncio baseline."""

import asyncio
import time
from typing import Dict, Any


def run() -> Dict[str, Any]:
    """Run comparison benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async comparison."""
    from streamfn import stream_fn

    # streamfn version
    @stream_fn
    async def streamfn_counter(events):
        count = 0
        async for event in events:
            count += 1
            yield count

    # Pure asyncio version
    async def asyncio_counter(events):
        """Naive asyncio queue-based version."""
        input_queue = asyncio.Queue()
        output_queue = asyncio.Queue()

        async def worker():
            count = 0
            while True:
                event = await input_queue.get()
                if event is None:
                    break
                count += 1
                await output_queue.put(count)

        async def producer():
            async for event in events:
                await input_queue.put(event)
            await input_queue.put(None)

        task = asyncio.create_task(worker())

        async def consumer():
            results = []
            async for event in events:
                await input_queue.put(event)
                result = await output_queue.get()
                results.append(result)
            await input_queue.put(None)
            await task
            return results

        return await consumer()

    # Benchmark data
    num_events = 10000
    events = [{"id": i} for i in range(num_events)]

    # Test streamfn
    start = time.perf_counter()
    streamfn_results = await streamfn_counter.run(events)
    streamfn_time = time.perf_counter() - start

    # Test asyncio naive
    async def event_gen():
        for e in events:
            yield e

    start = time.perf_counter()
    asyncio_results = await asyncio_counter(event_gen())
    asyncio_time = time.perf_counter() - start

    # Calculate speedup
    speedup = asyncio_time / streamfn_time

    results = {
        "streamfn_time_sec": streamfn_time,
        "asyncio_time_sec": asyncio_time,
        "speedup": speedup,
        "streamfn_events_per_sec": num_events / streamfn_time,
        "asyncio_events_per_sec": num_events / asyncio_time,
    }

    print(f"  streamfn: {streamfn_time*1000:.1f}ms ({results['streamfn_events_per_sec']:.0f} events/sec)")
    print(f"  asyncio:  {asyncio_time*1000:.1f}ms ({results['asyncio_events_per_sec']:.0f} events/sec)")
    print(f"  Speedup:  {speedup:.1f}×")

    return results
