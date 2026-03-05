"""Generic async generator adapter."""

from typing import Any, AsyncIterator, Callable
from uuid import uuid4

from ..types import StreamEvent


class GeneratorAdapter:
    """Adapter for wrapping any async generator as a stream source."""

    @staticmethod
    async def from_async_generator(
        generator: AsyncIterator[Any],
        event_id_fn: Callable[[Any], str] = lambda _: str(uuid4()),
    ) -> AsyncIterator[StreamEvent]:
        """Wrap an async generator as a stream source.

        Args:
            generator: Any async generator
            event_id_fn: Function to generate event IDs from data

        Yields:
            StreamEvent for each item from generator
        """
        async for item in generator:
            yield StreamEvent(
                id=event_id_fn(item),
                data=item,
            )

    @staticmethod
    async def from_iterable(
        items: AsyncIterator[Any],
    ) -> AsyncIterator[StreamEvent]:
        """Wrap an async iterable as a stream source.

        Args:
            items: Any async iterable

        Yields:
            StreamEvent for each item
        """
        async for item in items:
            yield StreamEvent(
                id=str(uuid4()),
                data=item,
            )

    @staticmethod
    async def from_callback(
        callback: Callable[[], Any],
        stop_fn: Callable[[], bool],
        interval_ms: int = 100,
    ) -> AsyncIterator[StreamEvent]:
        """Poll a callback function and yield events.

        Args:
            callback: Function to call to get data
            stop_fn: Function that returns True when done
            interval_ms: Polling interval in milliseconds

        Yields:
            StreamEvent for each callback result
        """
        import asyncio

        while not stop_fn():
            try:
                data = callback()
                if data is not None:
                    yield StreamEvent(
                        id=str(uuid4()),
                        data=data,
                    )
            except Exception:
                pass

            await asyncio.sleep(interval_ms / 1000)
