"""HTTP streaming adapter using httpx."""

from typing import AsyncIterator, Optional
import httpx
from uuid import uuid4

from ..types import StreamEvent


class HttpAdapter:
    """Adapter for streaming HTTP responses."""

    def __init__(self, client: Optional[httpx.AsyncClient] = None) -> None:
        self.client = client or httpx.AsyncClient()
        self._should_close = client is None

    async def stream_lines(
        self, url: str, **kwargs: any
    ) -> AsyncIterator[StreamEvent]:
        """Stream response line by line.

        Args:
            url: URL to stream from
            **kwargs: Additional arguments for httpx request

        Yields:
            StreamEvent for each line in the response
        """
        try:
            async with self.client.stream("GET", url, **kwargs) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield StreamEvent(
                            id=str(uuid4()),
                            data=line,
                            metadata={"url": url, "status": response.status_code},
                        )
        finally:
            if self._should_close:
                await self.client.aclose()

    async def stream_bytes(
        self, url: str, chunk_size: int = 8192, **kwargs: any
    ) -> AsyncIterator[StreamEvent]:
        """Stream response in chunks.

        Args:
            url: URL to stream from
            chunk_size: Size of chunks in bytes
            **kwargs: Additional arguments for httpx request

        Yields:
            StreamEvent for each chunk
        """
        try:
            async with self.client.stream("GET", url, **kwargs) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    if chunk:
                        yield StreamEvent(
                            id=str(uuid4()),
                            data=chunk,
                            metadata={
                                "url": url,
                                "status": response.status_code,
                                "chunk_size": len(chunk),
                            },
                        )
        finally:
            if self._should_close:
                await self.client.aclose()

    async def stream_json(self, url: str, **kwargs: any) -> AsyncIterator[StreamEvent]:
        """Stream NDJSON (newline-delimited JSON) responses.

        Args:
            url: URL to stream from
            **kwargs: Additional arguments for httpx request

        Yields:
            StreamEvent for each JSON object
        """
        import json

        async for event in self.stream_lines(url, **kwargs):
            try:
                data = json.loads(event.data)
                yield StreamEvent(
                    id=event.id,
                    data=data,
                    metadata=event.metadata,
                )
            except json.JSONDecodeError:
                # Skip invalid JSON
                continue

    async def __aenter__(self) -> "HttpAdapter":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: any) -> None:
        """Async context manager exit."""
        if self._should_close:
            await self.client.aclose()
