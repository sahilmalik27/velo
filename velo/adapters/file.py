"""File streaming adapter."""

import asyncio
from pathlib import Path
from typing import AsyncIterator, Union
from uuid import uuid4

from ..types import StreamEvent


class FileAdapter:
    """Adapter for streaming from files and named pipes."""

    @staticmethod
    async def stream_lines(
        path: Union[str, Path],
        encoding: str = "utf-8",
        chunk_size: int = 8192,
    ) -> AsyncIterator[StreamEvent]:
        """Stream file line by line.

        Args:
            path: Path to file or named pipe
            encoding: Text encoding
            chunk_size: Read buffer size

        Yields:
            StreamEvent for each line
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        # Use asyncio to read file
        loop = asyncio.get_event_loop()

        def read_lines():
            with open(path, "r", encoding=encoding) as f:
                for line in f:
                    yield line.rstrip("\n\r")

        for line in await loop.run_in_executor(None, lambda: list(read_lines())):
            yield StreamEvent(
                id=str(uuid4()),
                data=line,
                metadata={"file": str(path)},
            )

    @staticmethod
    async def stream_bytes(
        path: Union[str, Path],
        chunk_size: int = 8192,
    ) -> AsyncIterator[StreamEvent]:
        """Stream file in chunks.

        Args:
            path: Path to file
            chunk_size: Size of chunks in bytes

        Yields:
            StreamEvent for each chunk
        """
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        loop = asyncio.get_event_loop()

        def read_chunks():
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk

        for chunk in await loop.run_in_executor(None, lambda: list(read_chunks())):
            yield StreamEvent(
                id=str(uuid4()),
                data=chunk,
                metadata={"file": str(path), "chunk_size": len(chunk)},
            )

    @staticmethod
    async def stream_stdin() -> AsyncIterator[StreamEvent]:
        """Stream from stdin line by line.

        Yields:
            StreamEvent for each line from stdin
        """
        import sys

        loop = asyncio.get_event_loop()

        def read_stdin_lines():
            for line in sys.stdin:
                yield line.rstrip("\n\r")

        for line in await loop.run_in_executor(None, lambda: list(read_stdin_lines())):
            yield StreamEvent(
                id=str(uuid4()),
                data=line,
                metadata={"source": "stdin"},
            )
