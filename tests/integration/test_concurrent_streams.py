"""Integration tests for concurrent streams."""

import asyncio
import pytest
from velo import stream_fn


@pytest.mark.asyncio
async def test_multiple_independent_streams():
    """Test multiple independent streams with separate state."""

    @stream_fn
    async def counter(events):
        count = 0
        async for event in events:
            count += 1
            yield count

    # Open 3 concurrent streams
    async with counter.open() as stream1, \
                 counter.open() as stream2, \
                 counter.open() as stream3:

        # Send different numbers of events to each
        await stream1.send(1)
        await stream2.send(1)
        await stream2.send(2)
        await stream3.send(1)
        await stream3.send(2)
        await stream3.send(3)

        # Each stream should have independent counts
        r1 = await stream1.recv()
        r2_1 = await stream2.recv()
        r2_2 = await stream2.recv()
        r3_1 = await stream3.recv()
        r3_2 = await stream3.recv()
        r3_3 = await stream3.recv()

        assert r1 == 1
        assert r2_1 == 1
        assert r2_2 == 2
        assert r3_1 == 1
        assert r3_2 == 2
        assert r3_3 == 3


@pytest.mark.asyncio
async def test_concurrent_batch_processing():
    """Test concurrent batch processing."""

    @stream_fn
    async def square(events):
        async for event in events:
            yield event ** 2

    # Process multiple batches concurrently
    async def process_batch(data):
        return await square.run(data)

    batches = [
        [1, 2, 3],
        [4, 5, 6],
        [7, 8, 9],
    ]

    results = await asyncio.gather(*[process_batch(batch) for batch in batches])

    assert results[0] == [1, 4, 9]
    assert results[1] == [16, 25, 36]
    assert results[2] == [49, 64, 81]


@pytest.mark.asyncio
async def test_concurrent_live_streams():
    """Test concurrent live streams."""

    @stream_fn
    async def add_stream_id(events):
        async for event in events:
            yield {"data": event, "stream_id": id(events)}

    # Open multiple streams concurrently
    streams = []
    for _ in range(5):
        stream = await stream_fn(add_stream_id).__call__(None).__aenter__()
        # Note: This is a simplified test - actual usage would use .open()
        # Just testing concurrent initialization
        streams.append(stream)

    # Cleanup
    for stream in streams:
        try:
            await stream.__aexit__(None, None, None)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_stream_isolation():
    """Test that streams are properly isolated."""

    @stream_fn
    async def stateful_sum(events):
        total = 0
        async for event in events:
            total += event
            yield total

    # Run two streams with same function
    results1 = await stateful_sum.run([1, 2, 3])
    results2 = await stateful_sum.run([10, 20, 30])

    # Each should have independent state
    assert results1 == [1, 3, 6]
    assert results2 == [10, 30, 60]
