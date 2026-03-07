"""Stream function decorator."""

from typing import Any, AsyncIterator, Callable, List, Optional

from .runtime import StreamRuntime, get_runtime
from .types import StreamConfig


def stream_fn(
    fn: Optional[Callable] = None,
    *,
    buffer: int = 256,
    timeout: float = 30.0,
    max_concurrent: int = 1000,
) -> Callable:
    """Decorator that turns an async generator into a stream function.

    Example:
        @stream_fn
        async def running_average(events):
            total, count = 0.0, 0
            async for event in events:
                total += event
                count += 1
                yield total / count

        # Batch mode
        results = await running_average.run([1, 2, 3, 4, 5])

        # Live mode
        async with running_average.open() as stream:
            await stream.send(10)
            print(await stream.recv())

    Args:
        fn: The async generator function to decorate
        buffer: Events buffered before backpressure kicks in (default: 256)
        timeout: Seconds of inactivity before stream auto-closes (default: 30.0)
        max_concurrent: Max parallel instances of this function (default: 1000)

    Returns:
        StreamFunctionHandle with .run() and .open() methods
    """
    config = StreamConfig(
        buffer=buffer,
        timeout=timeout,
        max_concurrent=max_concurrent,
    )

    def decorator(func: Callable) -> "StreamFunctionHandle":
        return StreamFunctionHandle(func, config)

    if fn is None:
        return decorator
    return decorator(fn)


class StreamFunctionHandle:
    """Handle for a stream function with .run() and .open() methods."""

    def __init__(self, fn: Callable, config: StreamConfig) -> None:
        self._fn = fn
        self._config = config
        self._runtime: Optional[StreamRuntime] = None

    @property
    def runtime(self) -> StreamRuntime:
        """Get or create runtime."""
        if self._runtime is None:
            self._runtime = get_runtime(self._config)
        return self._runtime

    async def run(self, iterable: AsyncIterator[Any]) -> List[Any]:
        """Batch mode - process all events, return list of results.

        Args:
            iterable: Async or sync iterable of events

        Returns:
            List of all results
        """
        results = []

        # Convert sync iterable to async
        async def async_iter():
            if hasattr(iterable, "__aiter__"):
                async for item in iterable:
                    yield item
            else:
                for item in iterable:
                    yield item

        async with self.runtime.open_stream(self._fn) as stream:
            async for result in stream.feed(async_iter()):
                results.append(result)

        return results

    def run_sync(self, iterable: Any) -> List[Any]:
        """Synchronous batch mode — wraps run() with asyncio.run().

        Use this in regular scripts (not inside async functions).

        Example::

            results = my_fn.run_sync([1, 2, 3])

        Args:
            iterable: Sync or async iterable of events

        Returns:
            List of all results
        """
        import asyncio
        return asyncio.run(self.run(iterable))

    def open(self) -> Any:
        """Live mode - open a stream and return async context manager.

        Usage: async with my_fn.open() as stream: ...

        Returns:
            Async context manager that yields a Stream handle
        """
        return self.runtime.open_stream(self._fn)  # @asynccontextmanager — use directly

    def __or__(self, other: "StreamFunctionHandle") -> "StreamFunctionHandle":
        """Pipe composition: fn1 | fn2 chains output of fn1 to input of fn2."""

        async def composed_fn(events: AsyncIterator[Any]) -> AsyncIterator[Any]:
            # Run first function on events
            intermediate = self._fn(events)
            # Feed output to second function
            async for result in other._fn(intermediate):
                yield result

        # Use the more restrictive config
        combined_config = StreamConfig(
            buffer=min(self._config.buffer, other._config.buffer),
            timeout=min(self._config.timeout, other._config.timeout),
            max_concurrent=min(self._config.max_concurrent, other._config.max_concurrent),
        )

        return StreamFunctionHandle(composed_fn, combined_config)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """Allow calling the underlying function directly."""
        return await self._fn(*args, **kwargs)
