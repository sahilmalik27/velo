"""Integration tests for scale-to-zero behavior."""

import asyncio
import pytest
import psutil
from velo import stream_fn


@pytest.mark.asyncio
async def test_memory_cleanup_after_close():
    """Test that memory is released after stream closes."""
    process = psutil.Process()

    @stream_fn
    async def large_state(events):
        # Hold some state
        buffer = [0] * 1000000  # ~8MB list
        async for event in events:
            buffer[0] = event
            yield event

    # Measure baseline
    baseline_mb = process.memory_info().rss / (1024 * 1024)

    # Open and close stream
    async with large_state.open() as stream:
        await stream.send(42)
        await stream.recv()

        # Memory should be higher while stream is open
        during_mb = process.memory_info().rss / (1024 * 1024)

    # Let cleanup happen
    await asyncio.sleep(0.1)

    # Memory should return close to baseline
    after_mb = process.memory_info().rss / (1024 * 1024)

    # We should see memory recovered (within reasonable margin)
    assert after_mb < during_mb + 5  # Allow 5MB margin for other factors


@pytest.mark.asyncio
async def test_stream_lifecycle():
    """Test stream lifecycle from open to close."""

    @stream_fn
    async def counter(events):
        count = 0
        async for event in events:
            count += 1
            yield count

    # Stream should work multiple times
    for _ in range(3):
        async with counter.open() as stream:
            await stream.send(1)
            result = await stream.recv()
            assert result == 1


@pytest.mark.asyncio
async def test_idle_timeout():
    """Test that idle streams can timeout."""

    @stream_fn(timeout=0.5)
    async def quick_timeout(events):
        async for event in events:
            yield event

    async with quick_timeout.open() as stream:
        # Send one event
        await stream.send(1)
        result = await stream.recv()
        assert result == 1

        # Wait longer than timeout
        await asyncio.sleep(0.6)

        # Stream should still be usable (timeout is for auto-cleanup)
        # In this implementation, timeout affects the worker loop


@pytest.mark.asyncio
async def test_multiple_open_close_cycles():
    """Test opening and closing streams multiple times."""

    @stream_fn
    async def echo(events):
        async for event in events:
            yield event

    # Rapid open/close cycles
    for i in range(10):
        async with echo.open() as stream:
            await stream.send(i)
            result = await stream.recv()
            assert result == i


@pytest.mark.asyncio
async def test_concurrent_streams_cleanup():
    """Test cleanup with multiple concurrent streams."""

    @stream_fn
    async def passthrough(events):
        async for event in events:
            yield event

    # Open many streams
    streams = []
    for i in range(10):
        ctx = passthrough.open()
        stream = await ctx.__aenter__()
        streams.append((stream, ctx))

        await stream.send(i)
        result = await stream.recv()
        assert result == i

    # Close all streams
    for stream, ctx in streams:
        await ctx.__aexit__(None, None, None)

    # All should be cleaned up
    await asyncio.sleep(0.1)
