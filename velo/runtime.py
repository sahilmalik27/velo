"""Stream runtime and Stream handle."""

import asyncio
import pickle
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


def _serialize(obj: Any) -> bytes:
    """Serialize object using msgpack (if available) or pickle."""
    if isinstance(obj, bytes):
        return obj
    try:
        import msgpack
        return msgpack.packb(obj, use_bin_type=True)
    except Exception:
        return pickle.dumps(obj, protocol=5)


def _deserialize(data: bytes) -> Any:
    """Deserialize data using msgpack (if available) or pickle."""
    try:
        import msgpack
        return msgpack.unpackb(data, raw=False)
    except Exception:
        return pickle.loads(data)

try:
    from velo._core import PyStreamScheduler, init_tracing
except ImportError:
    PyStreamScheduler = None
    init_tracing = None


_runtimes: dict[str, "StreamRuntime"] = {}


def get_runtime(config: StreamConfig) -> "StreamRuntime":
    """Get or create runtime instance for a config."""
    key = f"{config.max_concurrent}:{config.buffer}:{config.timeout}"
    if key not in _runtimes:
        _runtimes[key] = StreamRuntime(config)
    return _runtimes[key]


class StreamRuntime:
    """Singleton runtime that manages stream lifecycle."""

    _instance: Optional["StreamRuntime"] = None

    def __init__(self, config: StreamConfig) -> None:
        if PyStreamScheduler is None:
            raise RuntimeError(
                "velo._core not available. Run 'maturin develop' to build Rust extension."
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
            # Create an async generator that reads from Rust input channel
            async def input_stream():
                while not self._closed:
                    data = await asyncio.to_thread(
                        self._scheduler.recv_input, self.stream_id, 100
                    )
                    if data is None:
                        if self._closed:
                            return
                        continue
                    yield _deserialize(data)

            # Initialize generator
            self._generator = self._fn(input_stream())

            # Process outputs - send to Rust output channel
            async for result in self._generator:
                data = _serialize(result)
                await asyncio.to_thread(
                    self._scheduler.send_output, self.stream_id, data
                )
                self._metrics._record_out(_estimate_size(result))

        except Exception as e:
            # Serialize and send exception to output channel
            data = _serialize(e)
            await asyncio.to_thread(
                self._scheduler.send_output, self.stream_id, data
            )

    async def send(self, event: Any) -> None:
        """Push one event to the stream."""
        if self._closed:
            raise RuntimeError("Stream is closed")

        data = _serialize(event)
        await asyncio.to_thread(self._scheduler.send_input, self.stream_id, data)
        self._metrics._record_in(_estimate_size(event))

    async def recv(self) -> Any:
        """Pull one result from the stream (waits if not ready)."""
        data = await asyncio.to_thread(
            self._scheduler.recv_output, self.stream_id, 5000
        )
        if data is None:
            raise RuntimeError("Stream timed out or closed")

        result = _deserialize(data)

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
            while not self._closed:
                try:
                    result = await self.recv()
                    yield result
                except RuntimeError:
                    if feed_task.done():
                        break
        finally:
            await feed_task

    async def close(self) -> None:
        """Drain and close the stream."""
        if self._closed:
            return

        self._closed = True

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
