# p2p-streamfn — Implementation Plan

## Paper
- **Title:** Serverless Abstractions for Short-Running, Lightweight Streams
- **arXiv:** 2603.03089v1
- **Venue:** SESAME '26 (4th Workshop on Serverless Systems, Applications and Methodologies)
- **Authors:** Natalie Carl, Niklas Kowallik, Constantin Stahl

## What We're Building

A production-grade Python streaming library with a Rust core. The key primitive is the
**stream function** — a stateful, ephemeral, scale-to-zero unit of stream processing.

Unlike Kafka Streams / Apache Flink (always-on, heavyweight), or AWS Lambda (stateless),
stream functions are:
- **Stateful** across events within a stream (Python generator interface)
- **Ephemeral** — spin up in microseconds, tear down when stream ends
- **Scale-to-zero** — no idle resource consumption between streams
- **Fast** — Rust runtime handles scheduling and message passing (no Python GIL overhead)

## Architecture

```
User code (Python)
    │
    │  @stream_function decorator
    ▼
streamfn Python API
    │
    │  PyO3 FFI bindings
    ▼
streamfn-core (Rust / tokio)
    ├── StreamScheduler     — manages worker lifecycle
    ├── WorkerPool          — tokio tasks per active stream
    ├── RingBuffer<T>       — lock-free SPSC queues (crossbeam)
    ├── SharedMemArena      — zero-copy buffer pool for large payloads
    └── BackpressureEngine  — token bucket rate limiting per stream
```

## Repository Layout

```
p2p-streamfn/
├── streamfn-core/              # Rust crate
│   ├── Cargo.toml
│   └── src/
│       ├── lib.rs              # PyO3 module exports
│       ├── scheduler.rs        # StreamScheduler (tokio)
│       ├── worker.rs           # Worker lifecycle + state context
│       ├── channel.rs          # Lock-free SPSC queue wrapper
│       ├── arena.rs            # Shared memory arena (zero-copy)
│       └── backpressure.rs     # Token bucket per stream
├── streamfn/                   # Python package
│   ├── __init__.py             # Public API exports
│   ├── decorator.py            # @stream_function + @pipeline
│   ├── runtime.py              # Wraps Rust StreamScheduler
│   ├── context.py              # StreamContext (state, metrics)
│   ├── types.py                # StreamEvent, StreamResult, Config
│   └── adapters/
│       ├── __init__.py
│       ├── http.py             # HTTPX async streaming adapter
│       ├── file.py             # File / named pipe adapter
│       └── generator.py        # Python async generator adapter
├── benchmarks/
│   ├── README.md               # How to run + interpret results
│   ├── runner.py               # Benchmark harness + reporter
│   ├── scenarios/
│   │   ├── throughput.py       # Events/sec at varying stream sizes
│   │   ├── latency.py          # P50/P95/P99 inter-event latency
│   │   ├── startup.py          # Stream startup time (us)
│   │   ├── concurrency.py      # N concurrent streams scaling
│   │   └── memory.py           # Memory footprint vs N streams
│   └── compare/
│       ├── vs_faust.py         # vs Faust (Kafka-backed Python)
│       ├── vs_bytewax.py       # vs Bytewax (Rust-backed Python)
│       └── vs_asyncio_naive.py # vs pure asyncio baseline
├── examples/
│   ├── video_pipeline.py       # Replicate paper benchmark
│   ├── llm_token_stream.py     # LLM output post-processing
│   ├── iot_sensor_burst.py     # IoT rolling-window aggregation
│   └── webhook_correlator.py   # Session-window event correlation
├── tests/
│   ├── unit/
│   │   ├── test_decorator.py
│   │   ├── test_scheduler.py
│   │   ├── test_backpressure.py
│   │   └── test_adapters.py
│   └── integration/
│       ├── test_stateful_processing.py
│       ├── test_concurrent_streams.py
│       └── test_scale_to_zero.py
├── Cargo.toml                  # Workspace root
├── pyproject.toml              # maturin build config
├── README.md
└── LICENSE                     # MIT

```

## Implementation Steps

### Step 1 — Rust core (streamfn-core)

1. Set up Cargo workspace + maturin build config (`pyproject.toml`)
2. Install maturin: `pip install maturin`
3. Implement `channel.rs`:
   - Wrap `crossbeam::channel::bounded()` as a SPSC ring buffer
   - Expose `send(item)` / `recv_timeout(ms)` to Python via PyO3
4. Implement `arena.rs`:
   - Shared memory pool using `memmap2` for zero-copy large payloads
   - Fallback to heap for small events (<4KB)
5. Implement `worker.rs`:
   - Each active stream gets a `StreamWorker` (tokio task)
   - Holds: input channel, output channel, state dict (Python `PyObject`)
   - Lifecycle: Created → Running → Draining → Dead
6. Implement `scheduler.rs`:
   - `StreamScheduler`: manages pool of `StreamWorker`s
   - `open_stream(fn_id) -> stream_id` — microsecond startup
   - `send_event(stream_id, event)` — routes to correct worker
   - `close_stream(stream_id)` — drain + teardown
   - Scale-to-zero: idle workers are dropped, state is serialized if needed
7. Implement `backpressure.rs`:
   - Token bucket per stream (configurable rate + burst)
   - Signals to producers when consumers are falling behind
8. Implement `lib.rs`:
   - PyO3 module: expose `PyStreamScheduler`, `PyChannel`, `PyArena`

### Step 2 — Python API (streamfn/)

1. `types.py`:
   ```python
   @dataclass
   class StreamEvent:
       id: str
       data: Any
       timestamp: float
       metadata: dict

   @dataclass
   class StreamConfig:
       max_concurrent: int = 1000
       buffer_size: int = 256
       backpressure_rate: float = 1000.0  # events/sec
       idle_timeout_ms: int = 5000
   ```

2. `decorator.py`:
   ```python
   def stream_function(fn=None, *, config: StreamConfig = None):
       """Decorator that turns an async generator into a stream function."""
       # Returns StreamFunctionHandle with .open() context manager
   ```

3. `runtime.py`:
   - `StreamRuntime` singleton: wraps `PyStreamScheduler` from Rust
   - `open(fn) -> StreamHandle`
   - `StreamHandle`: async context manager, exposes `send()` / `__aiter__`

4. `context.py`:
   - `StreamContext`: per-stream state container
   - Metrics: events processed, bytes, latency histogram

5. `adapters/`:
   - `http.py`: consume from HTTPX streaming response
   - `file.py`: consume from file / named pipe / stdin
   - `generator.py`: wrap any async generator as a stream source

### Step 3 — Examples

Implement all 4 examples so each demonstrates a real use case:
- `video_pipeline.py` — replicate paper benchmark (frame diff computation)
- `llm_token_stream.py` — stateful post-processing of LLM token stream
- `iot_sensor_burst.py` — rolling window over 30-second sensor bursts
- `webhook_correlator.py` — session-window event correlation

### Step 4 — Benchmarks (Performance Evaluation Framework)

The benchmark suite is a first-class deliverable.

#### Scenarios

1. **Throughput** (`scenarios/throughput.py`)
   - Measure events/sec for stream sizes: 10, 100, 1K, 10K events
   - Vary payload sizes: 64B, 1KB, 64KB, 1MB
   - Report: events/sec, MB/sec, CPU%, memory

2. **Latency** (`scenarios/latency.py`)
   - Measure inter-event latency: time from `send()` to `yield` in generator
   - Report: P50, P95, P99, max (microseconds)
   - Target: P99 < 500μs for small payloads

3. **Startup Time** (`scenarios/startup.py`)
   - Measure stream open-to-first-event latency
   - 1000 stream open/close cycles, report min/mean/max
   - Target: < 500μs startup (vs Flink's 2–10 seconds)

4. **Concurrency** (`scenarios/concurrency.py`)
   - Ramp from 1 → 10 → 100 → 1000 concurrent streams
   - Measure throughput degradation and memory growth
   - Report: events/sec per stream, total memory

5. **Memory** (`scenarios/memory.py`)
   - Measure RSS at 0, 10, 100, 1000 concurrent idle streams
   - Verify scale-to-zero: RSS returns to baseline after all streams close

#### Comparisons

- `vs_asyncio_naive.py` — baseline: pure Python asyncio queue-based streaming
- `vs_faust.py` — Faust streaming (if installed; skip gracefully if not)
- `vs_bytewax.py` — Bytewax (if installed; skip gracefully if not)

#### Benchmark Runner (`runner.py`)

```python
# Usage:
# python benchmarks/runner.py --scenario all --output results/bench.json
# python benchmarks/runner.py --scenario throughput latency --compare all
```

- Outputs: JSON (machine-readable) + Markdown table (human-readable)
- Includes: system info (CPU, RAM, OS, Rust version, Python version)
- Plots: matplotlib charts saved to `results/plots/`
- CI mode: `--ci` flag fails if any metric regresses >10% vs baseline

### Step 5 — Tests

- Unit tests for all Python API components
- Integration tests:
  - Stateful processing correctness (running sum, windowing)
  - N concurrent streams with independent state
  - Scale-to-zero: confirm memory released after stream close
- Minimum 80% coverage

### Step 6 — Production Hardening

- Error handling: generator exceptions propagate cleanly to caller
- Graceful shutdown: `SIGTERM` drains active streams before exit
- Observability: `StreamContext.metrics` exposes per-stream counters
- Logging: structured JSON logs (optional, controlled by env var)
- Type hints: full type annotations, mypy strict mode

### Step 7 — Packaging + README

1. `pyproject.toml` with maturin backend:
   ```toml
   [build-system]
   requires = ["maturin>=1.0,<2.0"]
   build-backend = "maturin"
   ```
2. README.md:
   - What it is, why it's fast
   - Quickstart (install + hello world in 5 lines)
   - Architecture diagram
   - Benchmark results table (populated after Step 4)
   - Comparison vs Flink/Faust/Bytewax
3. MIT LICENSE

## Performance Targets

| Metric | Target | Stretch |
|--------|--------|---------|
| Stream startup | < 500μs | < 100μs |
| Inter-event P99 latency | < 500μs | < 100μs |
| Throughput (1KB payload) | > 500K events/sec | > 1M events/sec |
| Concurrent streams | 1000 (< 1GB) | 10000 |
| vs asyncio naive | 5× faster | 10× faster |
| vs Faust | 20× faster | 50× faster |

## Build Instructions

```bash
# Install Rust (if not present)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Install maturin
pip install maturin

# Build + install in dev mode
maturin develop --release

# Run tests
pytest tests/ -v

# Run benchmarks
python benchmarks/runner.py --scenario all
```

## Key Design Decisions

1. **Rust tokio runtime** — async tasks, not OS threads. Scales to 10K+ concurrent streams.
2. **crossbeam SPSC channels** — lock-free, zero-contention for single producer/consumer pairs.
3. **memmap2 shared arena** — zero-copy for payloads >4KB; avoids pickle overhead entirely.
4. **Python async generator interface** — user code is pure Python async generators; Rust handles scheduling underneath.
5. **maturin build** — standard Rust→Python wheel toolchain; wheels distributed via PyPI.
6. **Scale-to-zero via tokio task drop** — idle stream workers are simply dropped; no explicit cleanup needed.
7. **Backpressure via token bucket** — producers auto-throttle; no OOM from fast producers + slow consumers.

## Privacy / Public Repo Rules

This is a PUBLIC repo. Before any commit:
- No personal info (names, usernames, emails, phone)
- No workspace paths or internal tool references
- No API keys, tokens, service details
- Copyright line: `Copyright (c) 2026`
