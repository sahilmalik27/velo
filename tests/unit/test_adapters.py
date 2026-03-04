"""Unit tests for adapters."""

import pytest
from pathlib import Path
from streamfn.adapters import FileAdapter, GeneratorAdapter


@pytest.mark.asyncio
async def test_file_adapter_lines(tmp_path):
    """Test FileAdapter stream_lines."""
    # Create test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("line1\nline2\nline3\n")

    events = []
    async for event in FileAdapter.stream_lines(test_file):
        events.append(event.data)

    assert events == ["line1", "line2", "line3"]


@pytest.mark.asyncio
async def test_file_adapter_bytes(tmp_path):
    """Test FileAdapter stream_bytes."""
    test_file = tmp_path / "test.bin"
    test_file.write_bytes(b"hello world")

    chunks = []
    async for event in FileAdapter.stream_bytes(test_file, chunk_size=5):
        chunks.append(event.data)

    # Should be split into chunks of 5 bytes
    assert len(chunks) == 3
    assert b"".join(chunks) == b"hello world"


@pytest.mark.asyncio
async def test_file_adapter_not_found():
    """Test FileAdapter with non-existent file."""
    with pytest.raises(FileNotFoundError):
        async for _ in FileAdapter.stream_lines("/nonexistent/file.txt"):
            pass


@pytest.mark.asyncio
async def test_generator_adapter():
    """Test GeneratorAdapter from_async_generator."""

    async def test_gen():
        for i in range(5):
            yield i * 2

    events = []
    async for event in GeneratorAdapter.from_async_generator(test_gen()):
        events.append(event.data)

    assert events == [0, 2, 4, 6, 8]


@pytest.mark.asyncio
async def test_generator_adapter_from_iterable():
    """Test GeneratorAdapter from_iterable."""

    async def test_iter():
        for i in [10, 20, 30]:
            yield i

    events = []
    async for event in GeneratorAdapter.from_iterable(test_iter()):
        events.append(event.data)

    assert events == [10, 20, 30]
