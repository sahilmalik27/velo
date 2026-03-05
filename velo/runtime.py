"""Stream runtime and Stream handle."""

import asyncio
import pickle
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional
from uuid import uuid4

from .types import StreamConfig, StreamMetrics
from .signals import get_shutdown_handler

try:
    from velo._core import PyStreamScheduler, init_tracing
except ImportError:
    PyStreamScheduler = None
    init_tracing = None


class StreamRuntime:
    """Singleton runtime that manages stream lifecycle."""

    _instance: Optional["StreamRuntime"] = None

    def __init__(self, config: StreamConfig) -> None:
        if PyStreamScheduler is None:
            raise RuntimeError(
                "streamfn._core not available. Run 'maturin develop' to build Rust extension."
            )

        self.config = config
        self._scheduler = PyStreamScheduler(config.max_concurrent)

        # Initialize tracing
        if init_tracing is not None:
            try:
                init_tracing("info")
            except Exception:
                pass  # Already initialized

    @classmethod
    def get_instance(cls, config: Optional[StreamConfig] = None) -> "StreamRuntime":
        """Get or create singleton instance."""
        if cls._instance is None:
            cls._instance = cls(config or StreamConfig())
        return cls._instance

    @asynccontextmanager
    async def open_stream(
        self, fn: Callable, stream_id: Optional[str] = None
    ) -> AsyncIterator["Stream"]:
        """Open a new stream."""
        if stream_id is None:
            stream_id = str(uuid4())

        # Open stream in scheduler
        self._scheduler.open_stream(stream_id)

        stream = Stream(stream_id, fn, self._scheduler, self.config)

        try:
            # Start the worker task
            stream._start_worker()
            yield stream
        finally:
            # Cleanup
            await stream.close()


class Stream:
    """Handle to an active stream."""

    def __init__(
        self,
        stream_id: str,
        fn: Callable,
        scheduler: Any,
        config: StreamConfig,
    ) -> None:
        self.stream_id = stream_id
        self._fn = fn
        self._scheduler = scheduler
        self._config = config
        self._worker_task: Optional[asyncio.Task] = None
        self._generator: Optional[AsyncIterator] = None
        self._input_queue: asyncio.Queue = asyncio.Queue(maxsize=config.buffer)
        self._output_queue: asyncio.Queue = asyncio.Queue(maxsize=config.buffer)
        self._metrics = StreamMetrics(start_time_ns=time.perf_counter_ns())
        self._closed = False

    @property
    def metrics(self) -> StreamMetrics:
        """Get stream metrics."""
        # Update duration
        self._metrics.duration_ms = (
            time.perf_counter_ns() - self._metrics.start_time_ns
        ) / 1_000_000
        return self._metrics

    def _start_worker(self) -> None:
        """Start the worker task that runs the generator."""
        self._worker_task = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """Worker loop that processes events through the generator."""
        try:
            # Create an async generator from the input queue
            async def input_stream():
                while not self._closed:
                    try:
                        event = await asyncio.wait_for(
                            self._input_queue.get(), timeout=self._config.timeout
                        )
                        yield event
                    except asyncio.TimeoutError:
                        break

            # Initialize generator
            self._generator = self._fn(input_stream())

            # Process outputs
            async for result in self._generator:
                await self._output_queue.put(result)
                self._metrics._record_out(len(pickle.dumps(result)))

        except Exception as e:
            # Put exception in output queue for propagation
            await self._output_queue.put(e)

    async def send(self, event: Any) -> None:
        """Push one event to the stream."""
        if self._closed:
            raise RuntimeError("Stream is closed")

        await self._input_queue.put(event)
        self._metrics._record_in(len(pickle.dumps(event)))

    async def recv(self) -> Any:
        """Pull one result from the stream (waits if not ready)."""
        if self._closed and self._output_queue.empty():
            raise RuntimeError("Stream is closed and no more results available")

        result = await self._output_queue.get()

        # Check if it's an exception
        if isinstance(result, Exception):
            raise result

        return result

    async def feed(self, iterable: AsyncIterator[Any]) -> AsyncIterator[Any]:
        """Push many events and iterate over results."""
        # Start a task to feed events
        async def feeder():
            async for event in iterable:
                await self.send(event)

        feed_task = asyncio.create_task(feeder())

        try:
            # Yield results as they come
            while not self._closed or not self._output_queue.empty():
                try:
                    result = await asyncio.wait_for(self._output_queue.get(), timeout=0.1)
                    if isinstance(result, Exception):
                        raise result
                    yield result
                except asyncio.TimeoutError:
                    if feed_task.done():
                        # Feeder finished, drain remaining outputs
                        while not self._output_queue.empty():
                            result = await self._output_queue.get()
                            if isinstance(result, Exception):
                                raise result
                            yield result
                        break
        finally:
            await feed_task

    async def close(self) -> None:
        """Drain and close the stream."""
        if self._closed:
            return

        self._closed = True

        # Drain remaining output events
        while not self._output_queue.empty():
            try:
                await asyncio.wait_for(self._output_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                break

        # Cancel worker task
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass

        # Close in scheduler
        try:
            self._scheduler.close_stream(self.stream_id)
        except Exception:
            pass

    async def __aenter__(self) -> "Stream":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()
