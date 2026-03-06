# Rust Data Path — Implementation Guide

This document is the build spec for the full Rust data path in Velo.
See `architecture.md` for the design rationale.

**Status**: approved, ready to build

---

## What Changes

### Rust side (`velo-core/src/scheduler.rs`)

**Current state**: `send_input`, `recv_input`, `send_output`, `recv_output` are
implemented but Python never calls them. `close_stream()` removes the stream
from DashMap but does not drop channels explicitly.

**Required changes**:

#### 1. `StreamChannels` struct — own the channel endpoints

```rust
pub struct StreamChannels {
    // input: scheduler holds Sender, worker holds Receiver
    pub input_tx: crossbeam::channel::Sender<Vec<u8>>,
    pub input_rx: crossbeam::channel::Receiver<Vec<u8>>,
    // output: worker holds Sender, scheduler holds Receiver
    pub output_tx: crossbeam::channel::Sender<Vec<u8>>,
    pub output_rx: crossbeam::channel::Receiver<Vec<u8>>,
}
```

When `close_stream()` removes the entry from DashMap, `input_tx` is dropped.
The worker's `recv_input()` call returns `Err(Disconnected)` → Python maps to
`None` with `closed=True` → worker exits.

#### 2. `close_stream()` — sync, not async

Remove `async` from `close_stream`. It only needs to remove the entry from
DashMap. No drain logic. Python handles worker shutdown via channel disconnect.

```rust
pub fn close_stream(&self, stream_id: &str) -> Result<(), String> {
    self.streams
        .remove(stream_id)
        .ok_or_else(|| format!("Stream {} not found", stream_id))?;
    // Dropping StreamChannels here drops input_tx → worker sees Disconnected
    let mut count = self.active_count.lock();
    *count = count.saturating_sub(1);
    info!("Closed stream: {} (active: {})", stream_id, *count);
    Ok(())
}
```

#### 3. `recv_input()` — return disconnect signal

```rust
pub fn recv_input(
    &self,
    stream_id: &str,
    timeout_ms: u64,
) -> Result<Option<Vec<u8>>, String> {
    let entry = self.streams.get(stream_id)
        .ok_or_else(|| format!("Stream {} not found", stream_id))?;

    match entry.input_rx.recv_timeout(Duration::from_millis(timeout_ms)) {
        Ok(data) => Ok(Some(data)),
        Err(RecvTimeoutError::Timeout) => Ok(None),
        Err(RecvTimeoutError::Disconnected) => {
            // Sender was dropped (close_stream called) — signal worker to exit
            Ok(None)  // Python checks _closed flag after None
        }
    }
}
```

#### 4. `send_output_nowait()` — non-blocking push from worker

```rust
pub fn send_output_nowait(&self, stream_id: &str, data: Vec<u8>) -> Result<(), String> {
    let entry = self.streams.get(stream_id)
        .ok_or_else(|| format!("Stream {} not found", stream_id))?;

    entry.output_tx.try_send(data)
        .map_err(|e| format!("Output channel error: {:?}", e))
}
```

Worker calls this synchronously (no `asyncio.to_thread` overhead for output push).

#### 5. `PyStreamScheduler` — remove tokio runtime dependency

Channel ops are synchronous crossbeam calls. No tokio needed. Remove the
`Runtime` field. `close_stream` in `#[pymethods]` becomes a simple sync call:

```rust
fn close_stream(&self, py: Python, stream_id: String) -> PyResult<()> {
    py.allow_threads(|| {
        self.inner.close_stream(&stream_id)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e))
    })
}
```

---

### Python side (`velo/runtime.py`)

**Remove**: `asyncio.Queue`, `_CLOSE_SENTINEL`, all queue-based logic.

**Add**: `_serialize()`, `_deserialize()` using msgpack + fallback.

#### Serialization helpers

```python
def _serialize(obj: Any) -> bytes:
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
    if not data:
        return data
    tag = data[0:1]
    payload = data[1:]
    if tag == b"\x01":
        import msgpack
        return msgpack.unpackb(payload, raw=False)
    elif tag == b"\x02":
        import pickle
        return pickle.loads(payload)
    else:
        # Raw bytes (no tag) — passthrough
        return data
```

Tag byte `\x01` = msgpack, `\x02` = pickle, no tag = raw bytes passthrough.

#### `_run_worker()`

```python
async def _run_worker(self) -> None:
    try:
        async def input_stream() -> AsyncIterator[Any]:
            while True:
                data = await asyncio.to_thread(
                    self._scheduler.recv_input, self.stream_id, 50
                )
                if data is None:
                    if self._closed:
                        return      # channel disconnected → exit
                    continue        # timeout, keep polling
                yield _deserialize(data)

        async for result in self._fn(input_stream()):
            data = _serialize(result)
            # Non-blocking sync push — no asyncio.to_thread overhead
            self._scheduler.send_output_nowait(self.stream_id, data)
            self._metrics._record_out(len(data))

    except Exception as exc:
        # Serialize and push exception so recv() can raise it
        try:
            self._scheduler.send_output_nowait(
                self.stream_id, _serialize(exc)
            )
        except Exception:
            pass
```

#### `send()`

```python
async def send(self, event: Any) -> None:
    if self._closed:
        raise RuntimeError("Stream is closed")
    data = _serialize(event)
    await asyncio.to_thread(self._scheduler.send_input, self.stream_id, data)
    self._metrics._record_in(len(data))
```

#### `recv()`

```python
async def recv(self) -> Any:
    data = await asyncio.to_thread(
        self._scheduler.recv_output, self.stream_id, 5000
    )
    if data is None:
        raise RuntimeError("Stream closed before result was ready")
    result = _deserialize(data)
    if isinstance(result, Exception):
        raise result
    return result
```

#### `close()`

```python
async def close(self) -> None:
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
```

---

## What to Delete

Once the new data path is working:

1. `velo-core/src/worker.rs` — `StreamWorker` with its own channels (replaced by `StreamChannels` in scheduler)
2. `velo-core/src/arena.rs` — arena allocator (unused)
3. `asyncio.Queue` imports and usage in `runtime.py`
4. `_CLOSE_SENTINEL` in `runtime.py`
5. tokio `Runtime` from `PyStreamScheduler`

---

## Build Order

1. **Rust**: refactor `scheduler.rs` — introduce `StreamChannels`, simplify `close_stream`, add `send_output_nowait`, remove tokio runtime
2. **Test Rust**: `cargo test` — all scheduler unit tests pass
3. **Python**: rewrite `runtime.py` — new `_run_worker`, `send`, `recv`, `close`, serialization helpers
4. **Build**: `maturin develop --manifest-path velo-core/Cargo.toml --release`
5. **Smoke test**: single stream open/send/recv/close completes in <5ms
6. **Benchmarks**: `python benchmarks/runner.py --scenario all --output results/bench_v02.json`
7. **README**: update architecture section + performance table with real numbers
8. **Diagram**: generate architecture diagram from `docs/architecture.md`
9. **Commit & push**

---

## Success Criteria

| Metric | Target | Rationale |
|--------|--------|-----------|
| Stream startup (open→first event) | < 2ms | Rust DashMap insert + asyncio task spawn |
| P99 latency (send→recv round trip) | < 1ms | crossbeam channel + GIL release |
| Throughput (1KB events) | > 100K events/sec | bounded by GIL on user generator |
| Throughput (raw bytes) | > 500K events/sec | zero-copy passthrough |
| 1000 concurrent idle streams | < 50MB overhead | DashMap entries only |
| close() latency | < 5ms | channel drop → disconnect signal |

---

## Open Questions (resolve before build)

1. **Channel buffer size**: current default `config.buffer`. Keep or make per-stream?
   → Recommendation: keep global default (configurable via `StreamConfig.buffer`).

2. **msgpack dependency**: add to `pyproject.toml` as required or optional?
   → Recommendation: optional (`extras = ["fast"]`), pickle fallback always works.

3. **send_output_nowait backpressure**: if output channel is full (slow caller),
   `try_send` fails. Should worker block or drop?
   → Recommendation: block (use `send_output` with timeout, not `try_send`).
   Update spec if agreed.
