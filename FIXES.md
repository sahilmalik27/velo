# Velo v0.2 — Fix Spec

## Goal
Fastest possible stateful stream runtime. Rust on the data path, not just control plane.

## Fix 1 — Zombie Stream Bug (CRITICAL)

**File:** `velo/runtime.py` — `_run_worker()` / `input_stream()`

**Bug:** When idle timeout fires inside `input_stream()`, the worker exits but
`self._closed` is never set. Caller can still call `.send()`, events pile up,
`recv()` hangs forever. Deadlock.

**Fix:**
```python
async def input_stream():
    while not self._closed:
        try:
            event = await asyncio.wait_for(
                self._input_queue.get(), timeout=self._config.timeout
            )
            yield event
        except asyncio.TimeoutError:
            # Timeout = stream is idle. Close it properly.
            asyncio.create_task(self.close())  # trigger full cleanup
            return  # exit generator cleanly
```

Also: after `close()` is called, `send()` must raise immediately, not silently
enqueue to a dead worker.

## Fix 2 — Remove Pickle from Hot Path

**File:** `velo/runtime.py` — `send()` and `_run_worker()`

**Bug:** `len(pickle.dumps(event))` on every send/recv. Slow. Breaks for
non-picklable objects (locks, generators, file handles).

**Fix — size estimation without pickle:**
```python
def _estimate_size(obj: Any) -> int:
    if isinstance(obj, (bytes, bytearray)):
        return len(obj)
    elif isinstance(obj, str):
        return len(obj.encode("utf-8"))
    elif isinstance(obj, dict):
        return sum(_estimate_size(k) + _estimate_size(v) for k, v in obj.items())
    else:
        try:
            return obj.__sizeof__()
        except Exception:
            return 0
```

Never let metrics code raise an exception or slow down the stream.

## Fix 3 — Move Event Routing to Rust Data Path

**This is the main architectural change.** Currently Rust only handles
`open_stream()` / `close_stream()` (control plane). All events flow through
Python `asyncio.Queue` (slow, GIL-contended).

**Target architecture (same as Bytewax):**
```
send(event)
    → serialize (msgpack or pickle)
    → Rust crossbeam SPSC input channel
    → Python worker reads from Rust channel (in thread, GIL released)
    → Python generator processes
    → result → serialize
    → Rust crossbeam SPSC output channel
    → recv() reads from Rust output channel
    → deserialize → return to caller
```

### Rust changes (`velo-core/src/scheduler.rs`)

Add to `PyStreamScheduler`:

```rust
/// Send event bytes to a stream's input channel
fn send_input(&self, stream_id: &str, data: Vec<u8>) -> PyResult<()>

/// Worker pulls next event from input channel (blocking, GIL released)
fn recv_input(&self, stream_id: &str, timeout_ms: u64) -> PyResult<Option<Vec<u8>>>

/// Worker pushes result to output channel
fn send_output(&self, stream_id: &str, data: Vec<u8>) -> PyResult<()>

/// Caller reads result from output channel (blocking, GIL released)  
fn recv_output(&self, stream_id: &str, timeout_ms: u64) -> PyResult<Option<Vec<u8>>>
```

Each stream gets two SPSC channels (crossbeam bounded):
- `input: (Sender<Vec<u8>>, Receiver<Vec<u8>>)` — caller → worker
- `output: (Sender<Vec<u8>>, Receiver<Vec<u8>>)` — worker → caller

The GIL is released for all channel operations using PyO3's
`py.allow_threads(|| ...)`.

### Python changes (`velo/runtime.py`)

Replace asyncio.Queue with Rust channel calls:

```python
# Worker reads from Rust input channel
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

# send() pushes to Rust input channel
async def send(self, event: Any) -> None:
    if self._closed:
        raise RuntimeError("Stream is closed")
    data = _serialize(event)
    await asyncio.to_thread(self._scheduler.send_input, self.stream_id, data)

# recv() pulls from Rust output channel
async def recv(self) -> Any:
    data = await asyncio.to_thread(
        self._scheduler.recv_output, self.stream_id, 5000
    )
    if data is None:
        raise RuntimeError("Stream timed out or closed")
    return _deserialize(data)
```

**Serialization:** use `msgpack` if installed, fallback to `pickle`. Add
`msgpack` as optional dep. For bytes/str events, pass through directly (no
serialization overhead).

```python
def _serialize(obj: Any) -> bytes:
    if isinstance(obj, bytes):
        return obj
    try:
        import msgpack
        return msgpack.packb(obj, use_bin_type=True)
    except Exception:
        return pickle.dumps(obj, protocol=5)

def _deserialize(data: bytes) -> Any:
    try:
        import msgpack
        return msgpack.unpackb(data, raw=False)
    except Exception:
        return pickle.loads(data)
```

## Fix 4 — Unify Naming to `velo`

Files that still say `streamfn`:
- `API_DESIGN.md` — replace all `streamfn` → `velo`
- `PLAN.md` — replace all `streamfn` / `p2p-streamfn` → `velo`
- `BUILD.md` — replace all `streamfn` → `velo`
- `runtime.py` error message: `"streamfn._core not available"` → `"velo._core not available"`

## Fix 5 — Fix License Mismatch

`PLAN.md` line 95 and 255 says `MIT LICENSE`. Change to `Apache-2.0`.

## Fix 6 — Update README Architecture Section

Replace current architecture section with honest description:

```
Event Flow (data path in Rust):

  send(event)
       │
       ▼ serialize (msgpack/pickle)
  Rust crossbeam SPSC input channel   ← lock-free, GIL released
       │
       ▼
  Python worker (asyncio task)
  reads from Rust channel via to_thread()
       │
       ▼
  Python async generator (your code)
       │
       ▼ result
  Rust crossbeam SPSC output channel  ← lock-free, GIL released
       │
       ▼ deserialize
  recv() → caller

Rust handles:
  - Stream lifecycle (open/close in microseconds)
  - Channel buffering (crossbeam, lock-free)
  - Backpressure (bounded channels)
  - Concurrency limits (max_concurrent enforcement)
  - Metrics aggregation

Python handles:
  - Your stream function logic (async generators)
  - Serialization boundary (msgpack/pickle)
  - asyncio integration (to_thread for blocking channel ops)

Note: Python generator code runs under the GIL. Rust channels release the
GIL for I/O ops. True parallelism applies to channel operations and lifecycle
management, not to generator execution.
```

## Fix 7 — Singleton Runtime

Add `get_runtime()` factory that respects per-decorator config:

```python
_runtimes: dict[str, StreamRuntime] = {}

def get_runtime(config: StreamConfig) -> StreamRuntime:
    key = f"{config.max_concurrent}:{config.buffer}:{config.timeout}"
    if key not in _runtimes:
        _runtimes[key] = StreamRuntime(config)
    return _runtimes[key]
```

## After All Fixes — Run Benchmarks

```bash
cd /home/sahil/workspace/p2p-streamfn
maturin develop --release
python benchmarks/runner.py --scenario all --output results/bench_v02.json --format markdown
```

Capture results and update README performance table with real numbers.

## Final Step

```bash
git add -A
git commit -m "feat: v0.2 — Rust data path, zombie stream fix, pickle removal, naming cleanup"
git push git@github.com:sahilmalik27/velo.git main
```

## Privacy Rules
This is a PUBLIC repo. No personal info, no workspace paths, no internal tool refs.
Copyright: `Copyright (c) 2026 Velo Contributors`
