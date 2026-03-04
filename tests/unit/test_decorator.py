"""Unit tests for stream_fn decorator."""

import pytest
from streamfn import stream_fn, StreamConfig


@pytest.mark.asyncio
async def test_stream_fn_basic():
    """Test basic stream function decoration."""

    @stream_fn
    async def double(events):
        async for event in events:
            yield event * 2

    # Test batch mode
    results = await double.run([1, 2, 3, 4, 5])
    assert results == [2, 4, 6, 8, 10]


@pytest.mark.asyncio
async def test_stream_fn_with_config():
    """Test stream function with custom config."""

    @stream_fn(buffer=128, timeout=10.0, max_concurrent=500)
    async def identity(events):
        async for event in events:
            yield event

    results = await identity.run([1, 2, 3])
    assert results == [1, 2, 3]


@pytest.mark.asyncio
async def test_stream_fn_no_args():
    """Test that @stream_fn works without parens."""

    @stream_fn
    async def passthrough(events):
        async for event in events:
            yield event

    results = await passthrough.run([10, 20, 30])
    assert results == [10, 20, 30]


@pytest.mark.asyncio
async def test_stream_fn_stateful():
    """Test stateful stream function."""

    @stream_fn
    async def running_sum(events):
        total = 0
        async for event in events:
            total += event
            yield total

    results = await running_sum.run([1, 2, 3, 4, 5])
    assert results == [1, 3, 6, 10, 15]


@pytest.mark.asyncio
async def test_pipe_composition():
    """Test pipe operator composition."""

    @stream_fn
    async def add_one(events):
        async for event in events:
            yield event + 1

    @stream_fn
    async def double(events):
        async for event in events:
            yield event * 2

    # Compose with |
    pipeline = add_one | double

    # Should add 1, then double
    results = await pipeline.run([1, 2, 3])
    assert results == [4, 6, 8]  # (1+1)*2, (2+1)*2, (3+1)*2


@pytest.mark.asyncio
async def test_multi_stage_pipeline():
    """Test multi-stage pipeline."""

    @stream_fn
    async def add_one(events):
        async for event in events:
            yield event + 1

    @stream_fn
    async def double(events):
        async for event in events:
            yield event * 2

    @stream_fn
    async def subtract_three(events):
        async for event in events:
            yield event - 3

    pipeline = add_one | double | subtract_three

    # (1+1)*2-3 = 1, (2+1)*2-3 = 3, (3+1)*2-3 = 5
    results = await pipeline.run([1, 2, 3])
    assert results == [1, 3, 5]


@pytest.mark.asyncio
async def test_live_mode_send_recv():
    """Test live mode with send/recv."""

    @stream_fn
    async def echo(events):
        async for event in events:
            yield event

    async with echo.open() as stream:
        await stream.send(42)
        result = await stream.recv()
        assert result == 42

        await stream.send("hello")
        result = await stream.recv()
        assert result == "hello"


@pytest.mark.asyncio
async def test_live_mode_feed():
    """Test live mode with feed iterator."""

    @stream_fn
    async def square(events):
        async for event in events:
            yield event ** 2

    async def data_gen():
        for i in range(5):
            yield i

    async with square.open() as stream:
        results = []
        async for result in stream.feed(data_gen()):
            results.append(result)

    assert results == [0, 1, 4, 9, 16]


@pytest.mark.asyncio
async def test_empty_stream():
    """Test stream with no events."""

    @stream_fn
    async def identity(events):
        async for event in events:
            yield event

    results = await identity.run([])
    assert results == []
