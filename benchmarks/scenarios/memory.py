"""Memory benchmark - footprint vs N streams, scale-to-zero verification."""

import asyncio
import time
from typing import Dict, Any

import psutil


def run() -> Dict[str, Any]:
    """Run memory benchmark."""
    return asyncio.run(async_run())


async def async_run() -> Dict[str, Any]:
    """Async memory benchmark."""
    from streamfn import stream_fn

    @stream_fn
    async def idle_stream(events):
        """Idle stream function."""
        async for event in events:
            yield event

    process = psutil.Process()
    results = {"memory_by_streams": {}}

    # Measure baseline
    baseline_mb = process.memory_info().rss / (1024 * 1024)
    results["baseline_mb"] = baseline_mb
    print(f"  Baseline: {baseline_mb:.1f} MB")

    # Test with 0, 10, 100, 1000 idle streams
    stream_counts = [10, 100, 1000]
    streams = []

    for count in stream_counts:
        # Open streams
        current_count = len(streams)
        new_streams = count - current_count

        for _ in range(new_streams):
            stream = await idle_stream.open().__aenter__()
            streams.append(stream)

        # Let streams settle
        await asyncio.sleep(0.1)

        # Measure memory
        current_mb = process.memory_info().rss / (1024 * 1024)
        per_stream_kb = (current_mb - baseline_mb) * 1024 / count if count > 0 else 0

        results["memory_by_streams"][f"{count}_streams"] = {
            "total_mb": current_mb,
            "delta_mb": current_mb - baseline_mb,
            "per_stream_kb": per_stream_kb,
        }

        print(f"  {count} streams: {current_mb:.1f} MB total, {per_stream_kb:.1f} KB/stream")

    # Close all streams and verify scale-to-zero
    for stream in streams:
        await stream.__aexit__(None, None, None)

    streams.clear()

    # Let cleanup happen
    await asyncio.sleep(0.5)

    # Measure after cleanup
    final_mb = process.memory_info().rss / (1024 * 1024)
    recovered_mb = results["memory_by_streams"]["1000_streams"]["total_mb"] - final_mb

    results["final_mb"] = final_mb
    results["recovered_mb"] = recovered_mb
    results["peak_mb"] = max(
        r["total_mb"] for r in results["memory_by_streams"].values()
    )

    print(f"  After cleanup: {final_mb:.1f} MB (recovered {recovered_mb:.1f} MB)")

    return results
