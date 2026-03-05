"""Integration tests for stateful processing."""

import pytest
from velo import stream_fn


@pytest.mark.asyncio
async def test_running_average():
    """Test stateful running average."""

    @stream_fn
    async def running_average(events):
        total, count = 0.0, 0
        async for event in events:
            total += event
            count += 1
            yield total / count

    results = await running_average.run([1, 2, 3, 4, 5])
    assert results == pytest.approx([1.0, 1.5, 2.0, 2.5, 3.0])


@pytest.mark.asyncio
async def test_rolling_window():
    """Test rolling window aggregation."""
    from collections import deque

    @stream_fn
    async def rolling_max(events):
        window = deque(maxlen=3)
        async for event in events:
            window.append(event)
            yield max(window)

    results = await rolling_max.run([1, 5, 3, 2, 8, 4])
    assert results == [1, 5, 5, 5, 8, 8]


@pytest.mark.asyncio
async def test_event_correlation():
    """Test event correlation (session tracking)."""

    @stream_fn
    async def correlate_pairs(events):
        """Emit events in pairs."""
        buffer = []
        async for event in events:
            buffer.append(event)
            if len(buffer) == 2:
                yield {"pair": buffer.copy()}
                buffer.clear()

    results = await correlate_pairs.run([1, 2, 3, 4, 5, 6])
    assert len(results) == 3
    assert results[0] == {"pair": [1, 2]}
    assert results[1] == {"pair": [3, 4]}
    assert results[2] == {"pair": [5, 6]}


@pytest.mark.asyncio
async def test_stateful_filter():
    """Test stateful filtering (deduplicate)."""

    @stream_fn
    async def deduplicate(events):
        seen = set()
        async for event in events:
            if event not in seen:
                seen.add(event)
                yield event

    results = await deduplicate.run([1, 2, 2, 3, 1, 4, 3, 5])
    assert results == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_stateful_transform():
    """Test stateful data transformation."""

    @stream_fn
    async def accumulate_string(events):
        """Build up a string from tokens."""
        buffer = ""
        async for token in events:
            buffer += token
            yield buffer

    results = await accumulate_string.run(["Hello", " ", "World", "!"])
    assert results == ["Hello", "Hello ", "Hello World", "Hello World!"]


@pytest.mark.asyncio
async def test_stateful_pipeline():
    """Test pipeline with multiple stateful stages."""

    @stream_fn
    async def running_sum(events):
        total = 0
        async for event in events:
            total += event
            yield total

    @stream_fn
    async def running_avg(events):
        total, count = 0.0, 0
        async for event in events:
            total += event
            count += 1
            yield total / count

    pipeline = running_sum | running_avg

    # First stage: cumsum [1, 3, 6, 10, 15]
    # Second stage: running avg of cumsum [1, 2, 3, 4, 5]
    results = await pipeline.run([1, 2, 3, 4, 5])
    assert results == pytest.approx([1.0, 2.0, 3.0, 4.0, 5.0])
