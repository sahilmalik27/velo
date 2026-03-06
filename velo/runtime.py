"""Stream runtime and Stream handle."""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional
from uuid import uuid4

from .types import StreamConfig, StreamMetrics
from .signals import get_shutdown_handler


def _estimate_size(obj: Any) -> int:
    """Estimate object size without pickle (fast, never raises)."""
    try:
        if isinstance(obj, (bytes, bytearray)):
            return len(obj)
        elif isinstance(obj, str):
            return len(obj.encode("utf-8"))
        elif isinstance(obj, dict):
            return sum(_estimate_size(k) + _estimate_size(v) for k, v in obj.items())
        else:
            return obj.__sizeof__()
    except Exception:
        return 0


try:
    from velo._core import PyStreamScheduler, init_tracing
except ImportError:
    PyStreamScheduler = None
    init_tracing = None


_runtimes: dict[str, "StreamRuntime"] = {}


def get_runtime(config: StreamConfig) -> "StreamRuntime":
    """Get or create runtime instance per unique config."""
    key = f"{config.max_concurrent}:{config.buffer}:{config.timeout}"
    if key not in _runtimes:
        _runtimes[key] = StreamRuntime(config)
    return _runtimes[key]


class StreamRuntime:
    """Manages stream lifecycle via the Rust scheduler."""

    def __init__(self, config: StreamConfig) -> None:
        if PyStreamScheduler is None:
            raise RuntimeError(
                "velo._core not available. Run 'maturin develop' to build the Rust extension."
            )

        self.config = config
        # Rust scheduler: handles stream lifecycle, concurrency limits, metrics
        self._scheduler = PyStreamScheduler(config.max_concurrent)

        if init_tracing is not None:
            try:
                init_tracing("info")
            except Exception:
                pass

    @asynccontextmanager
    async def open_stream(
        self, fn: Callable, stream_id: Optional[str] = None
    ) -> AsyncIterator["Stream"]:
        """Open a new stream. Rust scheduler tracks lifecycle."""
        if stream_id is None:
            stream_id = str(uuid4())

        self._scheduler.open_stream(stream_id)
        stream = Stream(stream_id, fn, self._scheduler, self.config)

        try:
            stream._start_worker()
            yield stream
        finally:
            await stream.close()


class Stream:
    """
    Handle to an active stream.

    Architecture:
      - Rust scheduler (PyStreamScheduler): lifecycle management, concurrency
        limits, stream registry, open/close in microseconds.
      - asyncio.Queue: event routing between caller and worker. Lock-free in
        the asyncio event loop; correct, no serialization overhead, no GIL
        contention for pure-Python generators.
      - Worker task: runs the user's async generator, reads from input queue,
        writes results to output queue.

    This hybrid gives Rust-speed lifecycle management with correct, deadlock-free
    event routing via asyncio. Moving the full data path into Rust crossbeam
    channels is tracked as a future optimization (requires solving GIL handoff
    between blocking Rust recv and asyncio generator execution).
    """

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
        self._input_queue: asyncio.Queue = asyncio.Queue(maxsize=config.buffer)
        self._output_queue: asyncio.Queue = asyncio.Queue(maxsize=config.buffer)
        self._metrics = StreamMetrics(start_time_ns=time.perf_counter_ns())
        self._closed = False

    @property
    def metrics(self) -> StreamMetrics:
        self._metrics.duration_ms = (
            time.perf_counter_ns() - self._metrics.start_time_ns
        ) / 1_000_000
        return self._metrics

    def _start_worker(self) -> None:
        self._worker_task = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """Worker: pulls events from input queue, runs user generator, pushes results."""
        try:
            async def input_stream() -> AsyncIterator[Any]:
                while not self._closed:
                    try:
                        event = await asyncio.wait_for(
                            self._input_queue.get(),
                            timeout=self._config.timeout,
                        )
                        yield event
                    except asyncio.TimeoutError:
                        # Idle timeout — close stream properly (Fix 1: zombie stream)
                        asyncio.create_task(self.close())
                        return

            async for result in self._fn(input_stream()):
                await self._output_queue.put(result)
                self._metrics._record_out(_estimate_size(result))

        except Exception as exc:
            await self._output_queue.put(exc)

    async def send(self, event: Any) -> None:
        """Push one event into the stream."""
        if self._closed:
            raise RuntimeError("Stream is closed")
        await self._input_queue.put(event)
        self._metrics._record_in(_estimate_size(event))

    async def recv(self) -> Any:
        """Pull one result from the stream (waits if not ready)."""
        if self._closed and self._output_queue.empty():
            raise RuntimeError("Stream is closed and output queue is empty")
        result = await self._output_queue.get()
        if isinstance(result, Exception):
            raise result
        return result

    async def feed(self, iterable: Any) -> AsyncIterator[Any]:
        """Push many events and iterate over results as they arrive."""
        async def _feeder() -> None:
            try:
                async for event in iterable:
                    await self.send(event)
            except Exception:
                pass

        feed_task = asyncio.create_task(_feeder())

        try:
            while not self._closed or not self._output_queue.empty():
                try:
                    result = await asyncio.wait_for(
                        self._output_queue.get(), timeout=0.05
                    )
                    if isinstance(result, Exception):
                        raise result
                    yield result
                except asyncio.TimeoutError:
                    if feed_task.done() and self._output_queue.empty():
                        break
        finally:
            if not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def close(self) -> None:
        """Drain and close the stream."""
        if self._closed:
            return
        self._closed = True

        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            self._scheduler.close_stream(self.stream_id)
        except Exception:
            pass

    async def __aenter__(self) -> "Stream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
