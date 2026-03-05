"""Comparison vs Bytewax (Rust-backed Python dataflow)."""

from typing import Dict, Any


def run() -> Dict[str, Any]:
    """Run comparison benchmark."""
    try:
        import bytewax
    except ImportError:
        raise ImportError("Bytewax not installed - install with: pip install bytewax")

    import asyncio
    import time

    # streamfn version
    async def streamfn_bench():
        from velo import stream_fn

        @stream_fn
        async def process(events):
            total = 0
            async for event in events:
                total += event
                yield total

        events = list(range(10000))
        start = time.perf_counter()
        await process.run(events)
        return time.perf_counter() - start

    # Bytewax version
    def bytewax_bench():
        from bytewax import operators as op
        from bytewax.dataflow import Dataflow
        from bytewax.testing import TestingSource, run_main

        flow = Dataflow("comparison")
        stream = op.input("inp", flow, TestingSource(range(10000)))

        total = [0]

        def accumulate(event):
            total[0] += event
            return total[0]

        stream = op.map("accumulate", stream, accumulate)

        start = time.perf_counter()
        run_main(flow)
        return time.perf_counter() - start

    # Run benchmarks
    streamfn_time = asyncio.run(streamfn_bench())
    bytewax_time = bytewax_bench()

    speedup = bytewax_time / streamfn_time

    results = {
        "streamfn_time_sec": streamfn_time,
        "bytewax_time_sec": bytewax_time,
        "speedup": speedup,
    }

    print(f"  streamfn: {streamfn_time*1000:.1f}ms")
    print(f"  bytewax:  {bytewax_time*1000:.1f}ms")
    print(f"  Speedup:  {speedup:.1f}×")

    return results
