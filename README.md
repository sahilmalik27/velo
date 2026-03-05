# velo ⚡

**Stateful stream processing without the infrastructure overhead.**

```bash
pip install velo-py
```

---

## The problem

**For one stream, a Python variable is fine.** No argument there.

```python
prev_frame = None

def process_frame(frame):
    global prev_frame
    diff = compare(frame, prev_frame)
    prev_frame = frame
    return diff
```

The problem starts when you have **many streams simultaneously** — hundreds of users, sessions, devices, clips — each needing their own isolated state.

### What happens when you scale with a dict

```python
# Step 1: one dict per state variable
prev_frames = {}

def process_frame(user_id, frame):
    diff = compare(frame, prev_frames.get(user_id))
    prev_frames[user_id] = frame
    return diff
```

Fine. Now the problems:

**When do you delete `prev_frames[user_id]`?** The user disconnected. Or did they time out? Or crash? `prev_frames` grows forever. Memory leak.

```python
# Step 2: add timeout cleanup
last_seen = {}

def cleanup():
    stale = [k for k, v in last_seen.items() if time.time() - v > 30]
    for k in stale:
        del prev_frames[k]
        del last_seen[k]
```

**Two events from the same user arrive simultaneously.** Race condition.

```python
# Step 3: add locks
lock = threading.Lock()

def process_frame(user_id, frame):
    with lock:
        diff = compare(frame, prev_frames.get(user_id))
        prev_frames[user_id] = frame
        last_seen[user_id] = time.time()
    cleanup()
    return diff
```

**Your state is more than one variable.** Now every new piece of state needs its own dict, its own cleanup entry, its own lock path.

```python
# Step 4: five state variables = five dicts to manage
prev_frames = {}
frame_counts = {}
motion_scores = {}
last_seen = {}
alert_thresholds = {}
# all need cleanup. all need locking. all need the same boilerplate.
```

You've spent 50 lines building a fragile lifecycle manager instead of writing business logic.

**Velo replaces all of that:**

```python
@stream_fn
async def process_frames(frames):
    prev = None
    count = 0
    motion_scores = []

    async for frame in frames:
        diff = compare(frame, prev) if prev else 0
        count += 1
        motion_scores.append(diff)
        prev = frame
        yield {"frame": count, "motion": diff, "avg": sum(motion_scores) / count}

# State lifecycle is automatic.
# Stream closes → all variables are garbage collected.
# No dicts. No cleanup. No locks. No boilerplate.
```

One worker per stream. All state is just local variables. Worker lives exactly as long as the stream — then disappears.

**Velo is a dict of streams with automatic cleanup, timeout handling, backpressure, and thread safety — so you don't write that yourself.**

### When to use Velo

| Situation | Recommendation |
|-----------|----------------|
| One stream, simple state | Plain variable — don't use Velo |
| A few streams you control manually | Dict is fine |
| Many short-lived concurrent streams | Velo saves real complexity |
| Already on Flink/Faust and happy | Stay there |

---

## What Velo is (and isn't)

**Velo is:** A library you run inside your existing server or container. It manages stateful worker lifecycles so you don't have to.

**Velo is not:** A serverless platform. Velo runs in a long-lived process — the same way your web server does. You deploy it like any other service.

**Velo competes with:** Flink, Faust, Bytewax — for the specific case of short-lived, bursty, stateful streams.

**Velo does not compete with:** Lambda, Cloud Functions — those are different deployment models entirely.

### vs the alternatives

| Tool | Startup | Has state | Idle cost | Complexity |
|------|---------|-----------|-----------|------------|
| **Velo** | ~microseconds | ✅ (local vars) | Near zero — workers are dropped | Low — just write generators |
| Redis + functions | ~ms (+ Redis RTT) | ✅ (external) | Redis always running | Medium — manage keys + TTLs |
| Apache Flink | 2–10 seconds | ✅ | High — always on | High — JVM, cluster setup |
| Faust | ~seconds | ✅ | Medium — always on | Medium — Kafka required |
| Bytewax | ~ms | ✅ | Medium — always on | Medium — continuous pipeline |

Velo's position: **lower startup than Flink, no external storage like Redis, workers go idle (drop to zero memory) when there's no load.**

---

## Quickstart

```python
from velo import stream_fn

# Define — just write an async generator
@stream_fn
async def running_average(events):
    total, count = 0.0, 0
    async for event in events:
        total += event
        count += 1
        yield total / count

# Batch — process a list, get results
results = await running_average.run([1, 2, 3, 4, 5])
# → [1.0, 1.5, 2.0, 2.5, 3.0]

# Live stream — open, send events, receive results
async with running_average.open() as stream:
    await stream.send(10)
    print(await stream.recv())  # 10.0
    await stream.send(20)
    print(await stream.recv())  # 15.0

# Compose — chain functions with |
pipeline = normalize | running_average | alert_if_high
async with pipeline.open() as stream:
    async for result in stream.feed(sensor_data):
        print(result)
```

---

## The API

Four exports. That's the entire public surface.

```python
from velo import stream_fn      # the decorator
from velo import Stream         # type hint for stream handles
from velo import StreamMetrics  # per-stream metrics
from velo import StreamConfig   # optional config
```

### `@stream_fn` — define a stream function

Write it exactly like a Python async generator. `events` is an async iterable.

```python
@stream_fn
async def my_fn(events):
    state = {}                     # any Python state you want
    async for event in events:
        state = update(state, event)
        yield result(state)
```

### `.run(iterable)` — batch mode

```python
results = await my_fn.run([e1, e2, e3])
```

### `.open()` — live stream mode

```python
async with my_fn.open() as stream:
    await stream.send(event)
    result = await stream.recv()

    # or iterate results:
    async for result in stream.feed(source):
        handle(result)
```

### `|` — pipe composition

```python
pipeline = fn_a | fn_b | fn_c   # output of fn_a feeds fn_b, etc.
results = await pipeline.run(data)
```

### Optional config

```python
@stream_fn(
    buffer=256,           # events buffered before backpressure (default: 256)
    timeout=30.0,         # idle seconds before auto-close (default: 30.0)
    max_concurrent=1000,  # max parallel instances (default: 1000)
)
async def my_fn(events):
    ...
```

All config is optional. Works with zero config.

---

## Real examples

### Video — motion detection across frames

```python
@stream_fn
async def detect_motion(frames):
    prev = None
    async for frame in frames:
        if prev is not None:
            diff = abs(frame.astype(int) - prev.astype(int)).mean()
            yield {"frame": frame.id, "motion": diff > 5.0, "score": diff}
        prev = frame

results = await detect_motion.run(video.frames())
```

### IoT — rolling window over sensor bursts

```python
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

async with rolling_stats.open() as stream:
    async for stat in stream.feed(sensor_readings):
        if stat["max"] > threshold:
            trigger_alert(stat)
```

### LLM — stateful token stream post-processing

```python
import json

@stream_fn
async def extract_json(tokens):
    """Accumulate tokens until a complete JSON object forms."""
    buffer, depth = "", 0
    async for token in tokens:
        buffer += token
        depth += token.count("{") - token.count("}")
        if depth == 0 and buffer.strip().startswith("{"):
            yield json.loads(buffer)
            buffer = ""

async with extract_json.open() as stream:
    async for obj in stream.feed(llm.stream("List 3 items as JSON")):
        process(obj)
```

### Session — stateful user event correlation

```python
@stream_fn
async def detect_fraud(events):
    seen_ips = set()
    total_spend = 0.0
    async for event in events:
        seen_ips.add(event["ip"])
        total_spend += event.get("amount", 0)
        risk = len(seen_ips) > 3 or total_spend > 1000
        yield {"event": event, "risk_score": risk}

# One stream per user session — isolated state, auto cleanup
async with detect_fraud.open() as stream:
    async for result in stream.feed(user_events):
        if result["risk_score"]:
            flag_for_review(result)
```

---

## Performance

Velo's Rust runtime (tokio + crossbeam) delivers:

| Metric | Target |
|--------|--------|
| Stream startup | < 500μs |
| Inter-event P99 latency | < 500μs |
| Throughput | > 500K events/sec (1KB payloads) |
| 1000 concurrent idle streams | Near-zero memory (workers dropped) |

Run the benchmarks yourself:

```bash
python benchmarks/runner.py --scenario all
python benchmarks/runner.py --scenario all --format markdown
```

---

## How it works

Event Flow (data path in Rust):

```
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
```

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

---

## Installation

### From PyPI

```bash
pip install velo-py
```

### From source (requires Rust)

```bash
# Install Rust
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

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

Contributions are welcome. Velo is early — there's a lot of room to improve.

**Good first issues:**
- New adapters (Kafka, Redis Streams, WebSocket, gRPC)
- Improve benchmark scenarios with real-world workloads
- JavaScript / Node.js bindings

**How to contribute:**

```bash
git clone https://github.com/sahilmalik27/velo.git
cd velo
pip install maturin && maturin develop
pip install -e ".[dev]"
pytest tests/ -v
```

1. Fork → branch → change → test → PR
2. All Rust changes need a before/after benchmark
3. Keep the public API surface small — resist adding to the 4 exports

**Project structure:**
```
velo/
├── velo-core/      # Rust runtime (tokio, crossbeam, PyO3)
├── velo/           # Python API (@stream_fn, Stream, config)
├── benchmarks/     # Performance suite
├── examples/       # Real-world usage
└── tests/          # Unit + integration
```

---

## Roadmap

- [ ] PyPI wheel publishing (no Rust required to install)
- [ ] Kafka adapter
- [ ] Redis Streams adapter
- [ ] Persistent state (checkpoint to disk between streams)
- [ ] Prometheus / OpenTelemetry metrics export
- [ ] JavaScript / Node.js bindings

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

Free for commercial use. Includes explicit patent grant.

---

*Built on [tokio](https://tokio.rs), [crossbeam](https://github.com/crossbeam-rs/crossbeam), and [PyO3](https://pyo3.rs). Inspired by [arXiv:2603.03089](https://arxiv.org/abs/2603.03089).*
