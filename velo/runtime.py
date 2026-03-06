"""Stream runtime and Stream handle — full Rust data path."""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable, Optional
from uuid import uuid4

from .types import StreamConfig, StreamMetrics
from .signals import get_shutdown_handler


def _serialize(obj: Any) -> bytes:
    """Serialize an object to bytes for the Rust channel."""
    if isinstance(obj, bytes):
        return obj
    if isinstance(obj, str):
        return obj.encode("utf-8")
    try:
        import msgpack
        return b"\x01" + msgpack.packb(obj, use_bin_type=True)
    except Exception:
        import pickle
        return b"\x02" + pickle.dumps(obj, protocol=5)


def _deserialize(data: bytes) -> Any:
    """Deserialize bytes from the Rust channel."""
    if not data:
        return data
    tag = data[0:1]
    if tag == b"\x01":
        import msgpack
        return msgpack.unpackb(data[1:], raw=False)
    elif tag == b"\x02":
        import pickle
        return pickle.loads(data[1:])
    else:
        # Raw bytes (no tag) — passthrough
        return data


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
    """Handle to an active stream — full Rust data path via crossbeam channels."""

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
        """Worker: pulls events from Rust input channel, runs user generator, pushes results."""
        try:
            async def input_stream() -> AsyncIterator[Any]:
                while True:
                    data = await asyncio.to_thread(
                        self._scheduler.recv_input, self.stream_id, 50
                    )
                    if data is None:
                        if self._closed:
                            return  # channel disconnected → exit
                        continue  # timeout, keep polling
                    yield _deserialize(data)

            async for result in self._fn(input_stream()):
                data = _serialize(result)
                self._scheduler.send_output_nowait(self.stream_id, data)
                self._metrics._record_out(len(data))

        except Exception as exc:
            try:
                self._scheduler.send_output_nowait(
                    self.stream_id, _serialize(exc)
                )
            except Exception:
                pass

    async def send(self, event: Any) -> None:
        """Push one event into the stream."""
        if self._closed:
            raise RuntimeError("Stream is closed")
        data = _serialize(event)
        await asyncio.to_thread(self._scheduler.send_input, self.stream_id, data)
        self._metrics._record_in(len(data))

    async def recv(self) -> Any:
        """Pull one result from the stream (waits if not ready)."""
        data = await asyncio.to_thread(
            self._scheduler.recv_output, self.stream_id, 5000
        )
        if data is None:
            raise RuntimeError("Stream closed before result was ready")
        result = _deserialize(data)
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
            while not self._closed:
                try:
                    data = await asyncio.to_thread(
                        self._scheduler.recv_output, self.stream_id, 50
                    )
                    if data is None:
                        if feed_task.done():
                            break
                        continue
                    result = _deserialize(data)
                    if isinstance(result, Exception):
                        raise result
                    yield result
                except RuntimeError:
                    if feed_task.done():
                        break
                    raise
        finally:
            if not feed_task.done():
                feed_task.cancel()
                try:
                    await feed_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def close(self) -> None:
        """Close the stream — drops Rust input channel, worker exits on Disconnected."""
        if self._closed:
            return
        self._closed = True

        # Drops input_tx in Rust → worker's recv_input returns Disconnected → exits
        try:
            self._scheduler.close_stream(self.stream_id)
        except Exception:
            pass

        # Give worker up to 100ms to exit naturally
        if self._worker_task and not self._worker_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._worker_task), timeout=0.1)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                self._worker_task.cancel()

    async def __aenter__(self) -> "Stream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()
