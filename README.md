# velo ⚡

**Fast, stateful, ephemeral stream functions — with a Rust core.**

```python
pip install velo-py
```

---

## The problem

You have a stream of events that need **stateful processing** — but your tools don't fit:

- **AWS Lambda / Cloud Functions** — stateless. You need Redis just to count events.
- **Apache Kafka + Flink** — always-on, heavyweight. Startup takes seconds. Overkill for a 10-second video clip.
- **Pure asyncio queues** — fine for small loads, but slow and fragile under pressure.

There's a gap: **short-lived, bursty, stateful streams** that arrive unpredictably and need to vanish when done.

Velo fills that gap.

---

## What is Velo?

Velo is a Python streaming library with a **Rust runtime underneath**.

You write stream functions as plain Python async generators. Velo handles scheduling, state isolation, backpressure, and scale-to-zero — in Rust, so it's fast.

**The mental model in one sentence:**
> A stream function is a generator that remembers between events.

---

## When to use Velo

✅ **Use Velo when:**
- Streams are short-lived (seconds to minutes), not always-on pipelines
- Stream arrival is unpredictable — you need scale-to-zero
- You need state across events within a stream (running totals, windows, deduplication)
- You want low overhead — not the weight of Kafka + Flink for a small workload
- You're building in Python and want Rust-level throughput

❌ **Don't use Velo when:**
- You have massive, always-on, high-throughput pipelines (use Flink or Bytewax)
- You need cross-stream aggregation across millions of streams (use a proper OLAP system)
- You need strict exactly-once delivery guarantees (use Kafka Streams)

---

## Quickstart

```python
from velo import stream_fn

# 1. Define — write it like a Python generator
@stream_fn
async def running_average(events):
    total, count = 0.0, 0
    async for event in events:
        total += event
        count += 1
        yield total / count

# 2a. Batch mode — process a list, get results back
results = await running_average.run([1, 2, 3, 4, 5])
# → [1.0, 1.5, 2.0, 2.5, 3.0]

# 2b. Live stream mode — open, send, receive
async with running_average.open() as stream:
    await stream.send(10)
    print(await stream.recv())  # 10.0

    await stream.send(20)
    print(await stream.recv())  # 15.0

# 3. Compose with | — Unix pipes, but for streams
pipeline = normalize | running_average | alert_if_high

async with pipeline.open() as stream:
    async for result in stream.feed(sensor_data):
        print(result)
```

---

## The API

Velo has **4 public exports**. That's the whole surface.

```python
from velo import stream_fn      # the decorator
from velo import Stream         # type hint for stream handles
from velo import StreamMetrics  # metrics dataclass
from velo import StreamConfig   # config dataclass (optional)
```

### `@stream_fn` — define a stream function

```python
@stream_fn
async def my_fn(events):
    state = {}
    async for event in events:
        # process event, update state, yield result
        yield result
```

No special base classes. No magic. Just an async generator.

### `.run(iterable)` — batch mode

```python
results = await my_fn.run([event1, event2, event3])
```

Processes all events synchronously, returns a list of results.

### `.open()` — live stream mode

```python
async with my_fn.open() as stream:
    await stream.send(event)
    result = await stream.recv()

    # or iterate:
    async for result in stream.feed(source):
        handle(result)
```

The stream is alive until the `async with` block exits. State is maintained throughout.

### `|` — pipe composition

```python
pipeline = fn_a | fn_b | fn_c
```

Creates a new stream function where output of `fn_a` feeds `fn_b`, and so on. Fully composable, returns a stream function you can `.run()` or `.open()`.

### Optional config

```python
@stream_fn(
    buffer=256,           # event buffer size before backpressure (default: 256)
    timeout=30.0,         # idle seconds before auto-close (default: 30.0)
    max_concurrent=1000,  # max parallel instances (default: 1000)
)
async def my_fn(events):
    ...
```

All config is optional and keyword-only. Works without any config.

---

## Real examples

### IoT sensor burst — rolling window

```python
from velo import stream_fn
from collections import deque

@stream_fn
async def rolling_stats(events):
    window = deque(maxlen=10)
    async for reading in events:
        window.append(reading["value"])
        yield {
            "mean": sum(window) / len(window),
            "min": min(window),
            "max": max(window),
        }

# Process a 30-second burst of sensor readings
async with rolling_stats.open() as stream:
    async for stat in stream.feed(sensor_readings):
        if stat["max"] > threshold:
            trigger_alert(stat)
```

### LLM token stream — stateful post-processing

```python
from velo import stream_fn

@stream_fn
async def extract_json(tokens):
    """Accumulate tokens until a complete JSON object is formed."""
    buffer = ""
    depth = 0
    async for token in tokens:
        buffer += token
        depth += token.count("{") - token.count("}")
        if depth == 0 and buffer.strip().startswith("{"):
            yield json.loads(buffer)
            buffer = ""

# Works directly on LLM streaming output
async with extract_json.open() as stream:
    async for obj in stream.feed(llm.stream("List 3 items as JSON")):
        process(obj)
```

### Video pipeline — stateful frame processing

```python
from velo import stream_fn
import numpy as np

@stream_fn
async def detect_motion(frames):
    prev = None
    async for frame in frames:
        if prev is not None:
            diff = np.abs(frame.astype(int) - prev.astype(int)).mean()
            yield {"frame_id": frame.id, "motion_score": diff, "motion": diff > 5.0}
        prev = frame

# Process a short video clip — starts in microseconds, gone when done
results = await detect_motion.run(video.frames())
```

### Pipeline composition

```python
@stream_fn
async def normalize(events):
    async for e in events:
        yield e / 255.0

@stream_fn
async def smooth(events):
    buf = []
    async for e in events:
        buf.append(e)
        if len(buf) == 5:
            yield sum(buf) / 5
            buf.pop(0)

@stream_fn
async def threshold(events):
    async for e in events:
        if e > 0.8:
            yield {"alert": True, "value": e}

# Compose into a pipeline
pipeline = normalize | smooth | threshold

alerts = await pipeline.run(raw_sensor_data)
```

---

## Performance

Velo's Rust runtime (tokio + crossbeam) delivers:

| Metric | Target | Notes |
|--------|--------|-------|
| Stream startup | < 500μs | vs. Flink's 2–10 seconds |
| Inter-event P99 latency | < 500μs | Measured on 1KB payloads |
| Throughput | > 500K events/sec | Per stream, single core |
| 1000 concurrent streams | < 1GB memory | Scale-to-zero when idle |

**vs. alternatives:**

| Tool | Startup | Stateful | Scale-to-zero | Language |
|------|---------|----------|---------------|----------|
| **Velo** | ~microseconds | ✅ | ✅ | Python API / Rust core |
| AWS Lambda | ~milliseconds | ❌ (needs Redis) | ✅ | Any |
| Apache Flink | 2–10 seconds | ✅ | ❌ | JVM |
| Faust | ~seconds | ✅ | ❌ | Python |
| Bytewax | ~milliseconds | ✅ | ❌ | Python API / Rust core |

Run the full benchmark suite yourself:

```bash
# Run all scenarios (throughput, latency, startup, concurrency, memory)
python benchmarks/runner.py --scenario all

# Compare against asyncio baseline
python benchmarks/runner.py --scenario all --compare asyncio

# Output results as markdown table
python benchmarks/runner.py --scenario all --format markdown
```

---

## How it works

```
Your Python code
      │
      │  @stream_fn decorator
      ▼
  Velo Python API
      │
      │  PyO3 FFI bindings (zero-cost)
      ▼
  Velo Rust Core (velo-core)
  ┌───────────────────────────────────┐
  │  StreamScheduler  (tokio)         │  ← microsecond stream startup
  │  WorkerPool       (tokio tasks)   │  ← one task per active stream
  │  RingBuffer       (crossbeam)     │  ← lock-free SPSC channels
  │  SharedMemArena   (memmap2)       │  ← zero-copy for payloads >4KB
  │  BackpressureEngine (token bucket)│  ← auto-throttle fast producers
  └───────────────────────────────────┘
```

- **Rust handles**: scheduling, state isolation, message passing, backpressure, lifecycle
- **Python handles**: your processing logic (just write generators)
- **Scale-to-zero**: idle stream workers are dropped by the tokio runtime — no polling, no cleanup code, no idle memory cost

---

## Installation

### From PyPI (wheels, no Rust required)

```bash
pip install velo-py
```

### From source (requires Rust)

```bash
# Install Rust (if not already installed)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Clone and build
git clone https://github.com/sahilmalik27/velo.git
cd velo
pip install maturin
maturin develop --release
```

### Verify

```python
import velo
print(velo.__version__)  # 0.1.0
```

---

## Contributing

Contributions are welcome and appreciated. Velo is an open-source project and we'd love your help making it better.

### Ways to contribute

- **Bug reports** — open an issue with a minimal reproduction
- **Performance improvements** — especially in the Rust core (`velo-core/src/`)
- **New adapters** — Kafka, Redis Streams, WebSocket, gRPC
- **Language bindings** — a JS/Node.js binding would be excellent
- **Documentation** — more examples, tutorials, comparisons
- **Benchmarks** — real-world workload comparisons

### Getting started

```bash
git clone https://github.com/sahilmalik27/velo.git
cd velo

# Install dev dependencies
pip install maturin
maturin develop  # debug build (faster compile)
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run a specific test
pytest tests/unit/test_decorator.py -v

# Check types
mypy velo/
```

### Project structure

```
velo/
├── velo-core/          # Rust runtime (tokio, crossbeam, PyO3)
│   └── src/
│       ├── scheduler.rs    # Stream lifecycle management
│       ├── worker.rs       # Per-stream worker tasks
│       ├── channel.rs      # Lock-free SPSC message channels
│       ├── arena.rs        # Zero-copy shared memory arena
│       └── backpressure.rs # Token bucket rate limiting
├── velo/               # Python API
│   ├── decorator.py        # @stream_fn
│   ├── runtime.py          # Stream handle (send/recv/feed)
│   └── types.py            # StreamConfig, StreamMetrics
├── benchmarks/         # Performance benchmark suite
├── examples/           # Real-world usage examples
└── tests/              # Unit + integration tests
```

### Guidelines

- Keep the public API surface small — resist adding exports beyond the core 4
- All Rust changes need a benchmark before/after
- New features need tests (unit + integration where applicable)
- Follow existing code style (Black for Python, `cargo fmt` for Rust)

### Opening a PR

1. Fork the repo
2. Create a branch: `git checkout -b feat/your-feature`
3. Make your changes + add tests
4. Run: `pytest tests/ -v && cargo test --manifest-path velo-core/Cargo.toml`
5. Open a PR with a clear description of what and why

---

## Roadmap

- [ ] PyPI wheel publishing (GitHub Actions matrix for Linux/Mac/arm64)
- [ ] Kafka adapter (`from velo.adapters import KafkaAdapter`)
- [ ] Redis Streams adapter
- [ ] WebSocket adapter
- [ ] Persistent state (optional checkpoint to disk between streams)
- [ ] Metrics export (Prometheus/OpenTelemetry)
- [ ] JavaScript / Node.js bindings

---

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

You can use Velo freely in commercial projects. The Apache 2.0 license includes an explicit patent grant.

---

*Built on [tokio](https://tokio.rs), [crossbeam](https://github.com/crossbeam-rs/crossbeam), and [PyO3](https://pyo3.rs). Inspired by [arXiv:2603.03089](https://arxiv.org/abs/2603.03089).*
