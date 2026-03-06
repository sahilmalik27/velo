# Velo Throughput — Design Decision

**Date**: 2026-03-06  
**Status**: Open — decision required

---

## Current State

The full Rust data path is live. Crossbeam SPSC channels carry event bytes between Python and Rust, with GIL released on all blocking calls via `py.allow_threads`.

Latency and lifecycle numbers are solid:

| Metric | Result | Target |
|--------|--------|--------|
| Stream startup (open→close) | 0.35ms | <2ms ✅ |
| P99 round-trip latency | 0.48ms | <1ms ✅ |
| close() | 0.08ms | <5ms ✅ |
| 1000 concurrent streams | works | ✅ |
| Throughput (small events) | ~6K ev/s | >100K ❌ |

Throughput is the one miss.

---

## Root Cause

Every `send()` and `recv()` call crosses the thread pool boundary via `asyncio.to_thread`. This is unavoidable as long as:

1. The Rust channel operations are blocking
2. The Python caller lives on the asyncio event loop (which cannot block)

`asyncio.to_thread` submits work to a `ThreadPoolExecutor` and awaits the result. Each submission costs approximately 150–200μs in scheduling overhead, independent of payload size. A single send→recv round-trip makes two such calls (one for `send_input`, one for `recv_output`), which puts a hard floor of ~300–400μs on latency and ~3–5K events/sec on throughput for sequential processing.

For batch workloads (`.run(events)`) the same overhead applies per event, since the worker's `recv_input` is also inside `asyncio.to_thread`.

This is not a bug — it is a consequence of the design: the asyncio event loop cannot block, but the Rust channel recv is blocking.

---

## Options

### Option A — Dedicated Thread per Stream
**Approach**: Each stream spawns a dedicated OS thread running its own `asyncio.run()` loop. The worker runs entirely in that thread. Communication between the caller (main event loop) and the worker thread uses thread-safe queues or the crossbeam channels directly (no `asyncio.to_thread` on the hot path inside the worker).

**How send/recv works**:
- `send()`: caller serializes, calls `scheduler.send_input()` directly via `asyncio.to_thread` (one thread hop, unavoidable)
- Worker: `recv_input()` blocks in its own thread — no thread pool, no scheduling overhead
- `recv()`: caller calls `asyncio.to_thread(recv_output)` — one thread hop

**Throughput**: removes `to_thread` from the worker inner loop. Estimated 200K–500K+ ev/s for small events, bounded by serialization and GIL on user generator code.

**Tradeoffs**:
- ✅ Highest throughput achievable with this architecture
- ✅ True isolation — each stream's generator runs independently
- ❌ 1 OS thread per stream. At 1000 concurrent streams: 1000 threads (~8GB stack memory at default 8MB per thread, though Linux lazy-allocates)
- ❌ OS thread creation adds ~1ms to stream startup
- ❌ Thread pool exhaustion under high concurrency without a cap

**Fits**: high-throughput pipelines, batch processing, streaming ML inference where per-stream rate matters.

---

### Option B — Batch Channel Calls
**Approach**: Replace per-event `asyncio.to_thread(recv_input, stream_id, 50)` with a batched call `asyncio.to_thread(recv_input_batch, stream_id, N, 50)` that returns up to N events in one thread hop. Similarly, `send_input_batch` pushes N events in one call.

**How it works**:
- Add `send_input_batch(stream_id, events: Vec<Vec<u8>>)` and `recv_input_batch(stream_id, max: usize, timeout_ms: u64) -> Vec<Vec<u8>>` to the Rust scheduler
- Python worker calls `recv_input_batch` in a loop, yielding each event from the batch before crossing the thread boundary again
- `run(events)` sends all events in one `send_input_batch` call

**Throughput**: amortizes the ~150–200μs `to_thread` overhead across N events. At batch size 64, effective overhead per event drops to ~3μs. Estimated 50K–200K ev/s depending on batch size and payload.

**Tradeoffs**:
- ✅ No new threads — stays within the existing ThreadPoolExecutor model
- ✅ Smaller change — Rust + Python changes, no threading model shift
- ✅ Works well for `run()` batch workloads
- ❌ Adds latency for interactive use: a batch doesn't return until N events accumulate or timeout fires
- ❌ Timeout tuning required: too short → frequent small batches → low throughput; too long → high interactive latency
- ❌ Does not help for true one-at-a-time streaming (chat, agents) where events arrive one by one

**Fits**: batch processing, `run()` workloads. Not ideal for interactive streaming.

---

### Option C — Accept Current Throughput
**Approach**: No change. Document 6K ev/s as the throughput characteristic for sequential streaming and focus optimization energy elsewhere.

**Rationale**: The primary use case for Velo is stateful stream management — chat sessions, realtime agents, WebSocket conversations, streaming ML inference. In these workloads, the bottleneck is model inference time (100ms–10s per response), not event routing. A 0.48ms P99 round-trip and sub-millisecond open/close are what matter.

6K events/sec means one event every 167μs. For a conversational agent sending 10–50 tokens per response, that is 1.67–8.35ms of routing overhead per turn — negligible against any real model inference time.

**Tradeoffs**:
- ✅ No additional complexity
- ✅ Latency characteristics are already excellent
- ✅ Right fit for the actual use cases Velo targets
- ❌ Not suitable for high-frequency data pipelines (log streaming, market data, sensor feeds)
- ❌ README throughput claims need to be honest about this number

**Fits**: agent runtimes, chat backends, streaming inference wrappers. Not for high-frequency event processing.

---

## Comparison

| | Option A | Option B | Option C |
|---|---|---|---|
| Throughput | 200K–500K+ ev/s | 50K–200K ev/s | ~6K ev/s |
| Interactive latency | Same | Higher (batch wait) | Same (0.48ms P99) |
| Implementation effort | High (threading model change) | Medium (Rust + Python) | None |
| Memory at 1000 streams | High (threads) | Same as now | Same as now |
| Complexity added | High | Medium | None |
| Best fit | Batch pipelines | Batch + moderate interactive | Agent / chat workloads |

---

## Recommendation

**If Velo stays focused on agent/chat use cases: Option C.**  
The throughput number is honest and the latency is excellent. No point adding complexity for a metric that doesn't matter for the target workload.

**If Velo needs to serve batch data pipelines: Option B first.**  
Batching is a targeted, low-risk change that gets throughput to a competitive range without a threading model overhaul. Can be layered on top of the current design.

**Option A** is the right call only if per-stream throughput >200K ev/s is a hard requirement. It is achievable but it is a significant architecture change that introduces thread lifecycle management complexity.

---

## Decision

**2026-03-06 — Option C selected.**

Velo's target workload is agent runtimes, chat backends, and streaming inference. At those use cases, model inference time dominates by orders of magnitude. 6K ev/s with 0.48ms P99 latency is the right tradeoff. Throughput optimization deferred until there's a concrete use case that requires it.

- [ ] Option A — dedicated thread per stream
- [ ] Option B — batch channel calls
- [x] Option C — accept current throughput, document honestly
