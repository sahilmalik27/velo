# Velo Architecture

## Goal

Build the fastest possible in-process stateful stream runtime for Python.

Write a plain async generator. Velo manages the lifecycle of thousands of them safely,
with real Rust-speed event routing on the data path.

---

## Design Principles

1. **Rust on the data path** — event bytes cross the Python/Rust boundary via crossbeam
   bounded SPSC channels. The GIL is released for all channel operations.
2. **asyncio-native API** — callers use `await send()` / `await recv()` / `async with open()`.
   No threads exposed to users.
3. **Zero-copy for bytes** — `bytes` and `str` events skip serialization entirely.
4. **Honest concurrency** — Python generator logic runs under the GIL (unavoidable).
   Rust owns lifecycle, routing, backpressure, and metrics.
5. **Clean shutdown** — closing a stream drops the Rust channel sender. Workers see
   `Disconnected` and exit immediately. No sentinel hacks, no poll loops.

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Python (asyncio main loop)                    │
│                                                                  │
│  Caller                          StreamFunctionHandle            │
│  ──────                          ────────────────────            │
│  await stream.send(event)        @stream_fn decorator            │
│  await stream.recv()             .open() / .run() / .feed()      │
│  async with stream_fn.open()                                     │
│                                                                  │
│                    Stream (handle object)                        │
│                    ─────────────────────                         │
│  send():                         Worker (asyncio task):          │
│    serialize(event) → bytes        input_stream():               │
│    asyncio.to_thread(              └─ asyncio.to_thread(         │
│      send_input, bytes)  ──────→       recv_input, 50ms)         │
│                                        GIL released ↓            │
│  recv():                               [Rust crossbeam]          │
│    asyncio.to_thread(      ←──────     GIL released ↑            │
│      recv_output, 5000ms)          └─ yield deserialize(data)    │
│    deserialize(data)                                             │
│    return result               user's async generator runs       │
│                                (under GIL, pure Python)          │
│                                    ↓                             │
│                                send_output_nowait(bytes)         │
│                                (sync non-blocking Rust push)     │
└──────────────────────┬──────────────────────────────────────────┘
                       │  PyO3 (py.allow_threads → GIL released)
┌──────────────────────▼──────────────────────────────────────────┐
│                    Rust (velo-core)                              │
│                                                                  │
│  PyStreamScheduler                                               │
│  ─────────────────                                               │
│  open_stream(id)     → insert into DashMap, enforce max_concurrent│
│  close_stream(id)    → drop input Sender → channel disconnected  │
│  send_input(id, bytes)   → crossbeam bounded SPSC push          │
│  recv_input(id, timeout) → crossbeam bounded SPSC recv (blocking)│
│  send_output_nowait(id, bytes) → non-blocking SPSC push         │
│  recv_output(id, timeout)      → blocking SPSC recv             │
│                                                                  │
│  Per-stream channels:                                            │
│    input:  Sender<Vec<u8>>  ╌╌╌╌╌╌  Receiver<Vec<u8>>          │
│            (held by scheduler)       (held by worker)            │
│    output: Sender<Vec<u8>>  ╌╌╌╌╌╌  Receiver<Vec<u8>>          │
│            (held by worker)          (held by caller)            │
│                                                                  │
│  DashMap<stream_id, StreamChannels>  ← lock-free concurrent map │
│  parking_lot::Mutex<active_count>    ← concurrency gate         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Path (step by step)

### send(event) — caller pushes an event

```
1. serialize(event)
     bytes/str  → passthrough (zero copy)
     dict/list  → msgpack.packb()
     other      → pickle.dumps() fallback

2. asyncio.to_thread(scheduler.send_input, stream_id, bytes)
     → runs in ThreadPoolExecutor (GIL released via py.allow_threads)
     → scheduler.send_input() does crossbeam bounded_channel::try_send()
     → returns immediately (non-blocking push)
     → if channel full → blocks until space (backpressure)
```

### Worker input_stream() — worker pulls events

```
3. data = await asyncio.to_thread(scheduler.recv_input, stream_id, 50)
     → runs in ThreadPoolExecutor (GIL released via py.allow_threads)
     → scheduler.recv_input() does crossbeam::recv_timeout(50ms)
     → returns Some(bytes) when event arrives, None on timeout
     → returns Err(Disconnected) when close_stream() drops the Sender
         → mapped to None with closed=True → generator exits

4. yield deserialize(data)
     bytes  → passthrough
     other  → msgpack.unpackb() or pickle.loads()
```

### User generator runs

```
5. user's async generator processes the event (pure Python, under GIL)
     async for event in events:
         yield result
```

### Worker output → caller recv()

```
6. scheduler.send_output_nowait(stream_id, serialize(result))
     → sync non-blocking Rust call (no asyncio.to_thread overhead)
     → crossbeam try_send() — near-zero latency for small results

7. recv(): await asyncio.to_thread(scheduler.recv_output, stream_id, 5000)
     → blocking Rust recv in thread pool (GIL released)
     → deserialize → return to caller
```

---

## Serialization

| Event type | Strategy | Overhead |
|------------|----------|----------|
| `bytes` | passthrough | zero |
| `str` | encode to UTF-8 bytes | ~1μs |
| `dict` / `list` / `int` / `float` | msgpack | ~2–5μs |
| anything else | pickle fallback | varies |

msgpack is the default for structured data. Pickle is a fallback only — never on the
hot path for bytes/str/dict events.

---

## Shutdown / Stream Lifecycle

```
close() called
  ↓
self._closed = True
  ↓
scheduler.close_stream(stream_id)
  → drops the input channel Sender in Rust
  → worker's recv_input() returns Err(Disconnected) → None
  → input_stream() generator returns
  → user's async for loop ends
  → worker task completes naturally
  ↓
scheduler removes stream from DashMap, decrements active count
```

No sentinel injection. No task.cancel(). No wait_for(timeout) hacks.
The channel disconnect is the shutdown signal.

---

## Concurrency Model

```
Main asyncio loop (1 thread):
  - runs all caller coroutines (send, recv, open, close)
  - runs all worker asyncio tasks
  - schedules asyncio.to_thread calls

ThreadPoolExecutor (default: min(32, cpu_count + 4) threads):
  - runs blocking Rust recv_input calls (GIL released)
  - runs blocking Rust recv_output calls (GIL released)
  - runs blocking Rust send_input calls when channel is full

Rust (no dedicated threads needed):
  - crossbeam channels are lock-free, thread-safe
  - DashMap handles concurrent access across thread pool threads
  - no tokio runtime required for channel ops
```

### Thread pool sizing

With 50ms recv_input timeout, each thread is blocked for at most 50ms waiting for
an event. The default pool handles ~600 concurrent blocking polls
(32 threads × 50ms / ~2.5ms avg hold time). For 1000+ concurrent streams with
high event rates, supply a custom executor:

```python
from concurrent.futures import ThreadPoolExecutor
import asyncio

loop = asyncio.get_event_loop()
loop.set_default_executor(ThreadPoolExecutor(max_workers=64))
```

---

## What Rust Owns

| Responsibility | Mechanism |
|----------------|-----------|
| Stream registry | `DashMap<String, StreamChannels>` (lock-free) |
| Concurrency limit | `parking_lot::Mutex<usize>` + max_concurrent check |
| Event routing (input) | `crossbeam::bounded` SPSC channel, GIL released |
| Event routing (output) | `crossbeam::bounded` SPSC channel, GIL released |
| Backpressure | bounded channel capacity |
| Shutdown signal | dropping input Sender → Disconnected |
| Open/close latency | microseconds (DashMap insert/remove) |

## What Python Owns

| Responsibility | Mechanism |
|----------------|-----------|
| User generator execution | asyncio task, runs under GIL |
| Serialization boundary | msgpack / pickle / passthrough |
| API surface | `@stream_fn`, `.open()`, `.send()`, `.recv()`, `.run()` |
| Idle timeout | asyncio.to_thread timeout propagation |
| Error propagation | exceptions passed through output channel |

---

## What This Is Not

- Not a distributed system
- Not a Kafka / Flink replacement
- Not multi-process
- Not durable / replayable

**Velo is a lightweight in-process runtime for managing many concurrent stateful
async streams safely and fast.**

Best fit: chat sessions, realtime agents, WebSocket conversations, streaming ML
inference, multiplayer state machines, online event processors.

---

## Future: Full Parallelism

Currently, user generator code runs under the GIL (Python constraint). True parallel
execution of multiple streams would require running each stream's generator in a
separate OS thread with its own event loop. This is architecturally possible but
adds significant complexity (thread pool per runtime, cross-thread result delivery).
Tracked as a future optimization once the current design is validated by benchmarks.

---

## Files

```
velo/
  runtime.py          — Stream, StreamRuntime, get_runtime(), serialization
  decorator.py        — @stream_fn, StreamFunctionHandle
  types.py            — StreamConfig, StreamMetrics
  signals.py          — shutdown handler

velo-core/src/
  lib.rs              — PyO3 module entry, init_tracing
  scheduler.rs        — PyStreamScheduler, StreamScheduler, StreamChannels
  worker.rs           — StreamWorker (legacy, to be simplified)
  channel.rs          — channel helpers
  backpressure.rs     — TokenBucket (future use)
  arena.rs            — arena allocator (future use)

docs/
  architecture.md     — this document
  rust-data-path.md   — implementation guide for the Rust data path build
```
