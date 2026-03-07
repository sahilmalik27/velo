# Velo Throughput v2 — Design Options

**Current state:** ~6K ev/s (Option C — accepted)  
**Root cause:** `asyncio.to_thread()` per event → OS thread pool overhead ~200μs/event  
**Target:** 100K–500K ev/s  

---

## Why we're slow

Every `stream.send(event)` today does:

```
Python caller
  → asyncio.to_thread()        # schedules work onto ThreadPoolExecutor
    → OS thread wakeup          # ~150–200μs OS scheduling latency
      → acquire crossbeam Sender (DashMap read lock)
        → sender.send(event)    # ~100ns — the actual work
          → return
    → asyncio future resolve    # back on event loop
```

The Rust crossbeam send itself is ~100ns. We're paying 2000× overhead in OS thread scheduling. The fix is to eliminate the thread hop, not optimize the Rust side.

---

## Option A — Batch API (low effort, 10-50× gain)

**Idea:** amortize the one `asyncio.to_thread` call across N events.

```python
# New API
await stream.send_batch([event1, event2, event3, ...])

# Or as a context manager flush
async with stream.batch() as b:
    b.add(event1)
    b.add(event2)
    b.add(event3)
# → single thread hop, N sends inside Rust
```

**Rust side:** add `send_batch(stream_id, events: Vec<PyObject>)` to the scheduler — iterates the Vec and sends each item through the existing SPSC channel.

**Throughput math:**
- At batch_size=50: 50 events × 1 thread hop = 50 × 100ns Rust + 200μs overhead = ~4μs/event → **~250K ev/s**
- At batch_size=10: ~50K ev/s

**Tradeoffs:**
- ✅ Minimal API change — batch is additive, `send()` still works
- ✅ No architecture change — same crossbeam channels, same Rust scheduler
- ✅ ~1 day to implement
- ❌ Latency increases with batch_size (you wait for the batch to fill or a flush timer)
- ❌ Doesn't help `send()` single-event callers
- ❌ Doesn't eliminate the thread hop — just amortizes it

**Best for:** bulk ingestion, frame pipelines, event replay.

---

## Option B — Dedicated Worker Thread per Stream (medium effort, 20-100× gain)

**Idea:** instead of spawning a thread per event, keep one persistent worker thread per stream. Python sends via a lock-free queue (crossbeam `ArrayQueue`); the worker drains it continuously.

```
Python caller
  → push to ArrayQueue      # ~50ns, no OS involvement
    → [worker thread drains queue continuously]
      → crossbeam sender.send()
        → recv on Python side via existing mechanism
```

**Architecture change:**

```rust
// Current
struct StreamChannels {
    sender: Sender<PyObject>,
    receiver: Receiver<PyObject>,
}

// New
struct StreamChannels {
    input_queue: Arc<ArrayQueue<PyObject>>,  // lock-free SPMC
    sender: Sender<PyObject>,                // worker → processor
    receiver: Receiver<PyObject>,            // processor → Python
    worker: JoinHandle<()>,                  // dedicated thread
}
```

Worker loop:
```rust
loop {
    if let Some(event) = input_queue.pop() {
        sender.send(event).unwrap();
    } else {
        std::thread::yield_now();  // or park/unpark
    }
}
```

Python `send()` becomes:
```python
async def send(self, event):
    # No asyncio.to_thread — direct push to lock-free queue
    self._scheduler.push_event(self._stream_id, event)
    # ~50ns, no await needed for high-throughput mode
```

**Throughput math:**
- ArrayQueue push: ~50ns
- Worker drain latency: ~1–5μs (yield_now loop)
- **Theoretical: 500K–1M ev/s**

**Tradeoffs:**
- ✅ Eliminates asyncio.to_thread entirely for send path
- ✅ `send()` becomes near-synchronous (no await required in hot path)
- ✅ Predictable latency — no OS scheduler jitter
- ⚠️ Worker thread spins (CPU usage even when idle) — need park/unpark for idle streams
- ❌ More complex stream lifecycle (worker must be stopped on `close()`)
- ❌ Memory overhead: one thread per stream (fine for 100s, not 100Ks)
- ❌ ~2–3 days to implement correctly

**Best for:** persistent streams with steady throughput (IoT sensors, video, audio).

---

## Option C — PyO3 + Tokio Async Runtime (high effort, cleanest long-term)

**Idea:** move the entire async runtime into Rust via Tokio. Python calls become `await rust_future()` — no thread hop at all.

```python
# Python
await stream.send(event)   # returns a Rust Future directly

# Under the hood: PyO3 coroutine wrapping tokio::spawn
```

**Architecture:**
- Replace `asyncio.to_thread` with `pyo3-asyncio` + Tokio runtime
- Rust owns the event loop for stream operations
- Python `await` drives a Tokio future natively

**Throughput math:**
- Tokio task spawn: ~200ns
- No OS thread wakeup
- **Theoretical: 1–5M ev/s**

**Tradeoffs:**
- ✅ Best possible throughput
- ✅ Eliminates Python GIL contention entirely on the send path
- ✅ Native async — no thread pool, no OS scheduling jitter
- ❌ Requires `pyo3-asyncio` or `pyo3 0.21` coroutine support — non-trivial integration
- ❌ Breaks the existing Python API surface (awaitable signature changes)
- ❌ 1–2 weeks to implement + test
- ❌ Debugging async Rust + Python interop is painful

**Best for:** if Velo becomes infrastructure (not just a library).

---

## Option D — Shared Memory Ring Buffer (medium effort, 100K+ ev/s, zero GIL)

**Idea:** use a raw shared memory ring buffer (no crossbeam, no Python object overhead) between Python and Rust. Events are serialized to bytes, written to a ring buffer that Rust reads without any Python involvement.

```python
# Python writes serialized bytes to mmap ring buffer
stream.send_raw(msgpack.encode(event))   # ~200ns

# Rust reads from ring buffer in worker thread
# Deserializes and processes
```

**Tradeoffs:**
- ✅ Near-zero Python overhead on send path
- ✅ Works across processes (not just threads)
- ❌ Requires serialization/deserialization — loses the zero-copy Python object passing we have now
- ❌ More complex than Option B for similar gain
- ❌ Not a great fit for arbitrary Python objects

---

## Recommendation

| Option | Effort | Throughput | Latency | API Change |
|--------|--------|------------|---------|------------|
| A — Batch API | 1 day | 50–250K ev/s | adds batch delay | additive |
| B — Worker Thread | 3 days | 200–500K ev/s | 1–5μs | minor |
| C — Tokio Async | 2 weeks | 1–5M ev/s | <1μs | breaking |
| D — Shared Memory | 4 days | 100–300K ev/s | <1μs | breaking |

**Suggested path:**
1. **Start with Option A** (batch API) — ships in a day, 10-50× gain, zero architecture risk, unblocks use cases that need bulk ingest
2. **Follow with Option B** (worker thread) if steady single-event throughput is needed — no API break
3. **Option C** only if Velo becomes a core infrastructure component where Python overhead matters at every call

---

## What stays the same

Regardless of which option is chosen:
- The `@stream` decorator API is unchanged
- State management (Rust DashMap, lifecycle) is unchanged
- `recv()` path is unchanged
- Existing benchmarks remain valid as the v0.2 baseline

---

*Design written 2026-03-06. Implement when a concrete throughput use case requires it.*
