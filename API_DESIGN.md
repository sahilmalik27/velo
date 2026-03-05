# velo — API Design Spec

## Guiding Philosophy

> "A stream function is a generator that remembers between events."

One sentence. That's the mental model. Everything in the API must reinforce this — no extra concepts, no ceremony. A developer reads the README once and remembers the API forever.

### Principles
1. **Write it like Python** — the function body is a plain async generator. No new primitives.
2. **Two modes, two methods** — `.run()` for batch, `.open()` for live. That's it.
3. **Compose with `|`** — Unix pipes. Everyone already knows this.
4. **No required config** — works out of the box. Config is opt-in, all keyword args.
5. **Errors are just exceptions** — no Result types, no error channels. Raise, it propagates.

---

## The API

### Step 1: Define a stream function

Write it exactly like a Python async generator. `events` is an async iterable — iterate it, yield results.

```python
from velo import stream_fn

@stream_fn
async def running_average(events):
    total, count = 0.0, 0
    async for event in events:
        total += event
        count += 1
        yield total / count
```

That's it. No classes. No inheritance. No special types. Just a function.

---

### Step 2a: Run on a list (batch mode)

```python
results = await running_average.run([1, 2, 3, 4, 5])
# [1.0, 1.5, 2.0, 2.5, 3.0]
```

`.run(iterable)` → processes all events, returns list of results.

---

### Step 2b: Open a live stream

```python
async with running_average.open() as stream:
    await stream.send(10)
    print(await stream.recv())  # 10.0

    await stream.send(20)
    print(await stream.recv())  # 15.0
```

`.open()` → async context manager, returns a `Stream` handle.
- `stream.send(event)` → push one event
- `stream.recv()` → pull one result (waits if not ready)
- `stream.close()` → called automatically on context exit

For async iteration over results:

```python
async with running_average.open() as stream:
    async for result in stream.feed([10, 20, 30]):
        print(result)  # 10.0, 15.0, 20.0
```

---

### Step 3: Compose with `|`

```python
normalize = stream_fn(lambda events: (e / 255.0 async for e in events))

pipeline = normalize | running_average

results = await pipeline.run([128, 64, 192])
```

`fn1 | fn2` chains the output of `fn1` as the input of `fn2`. The pipe returns a new stream function — fully composable.

```python
# Multi-stage pipeline
pipeline = (
    parse_frames
    | normalize
    | detect_motion
    | filter_threshold(0.1)  # partial application
    | emit_alerts
)

async with pipeline.open() as stream:
    async for alert in stream.feed(camera_feed):
        notify(alert)
```

---

### Optional config

Config is optional. Pass it to the decorator — all keyword, all named in plain English.

```python
@stream_fn(
    buffer=256,           # events buffered before backpressure kicks in (default: 256)
    timeout=30.0,         # seconds of inactivity before stream auto-closes (default: 30.0)
    max_concurrent=1000,  # max parallel instances of this function (default: 1000)
)
async def my_fn(events):
    ...
```

No magic strings. No numeric codes. No flags. If you need to look it up, the name tells you what it does.

---

### Partial application (for config-carrying stages)

```python
def filter_threshold(threshold):
    @stream_fn
    async def _filter(events):
        async for event in events:
            if event > threshold:
                yield event
    return _filter

# Use in a pipeline
pipeline = normalize | filter_threshold(0.1) | emit_alerts
```

---

## What the Stream object exposes

```python
class Stream:
    async def send(self, event) -> None: ...       # push one event
    async def recv(self) -> Any: ...               # pull one result
    async def feed(self, iterable) -> AsyncIter:   # push many, iterate results
    async def close(self) -> None: ...             # drain and close
    @property
    def metrics(self) -> StreamMetrics: ...        # events_in, events_out, latency_p99_us
```

`StreamMetrics` is a dataclass — just read the fields, no methods.

---

## Error handling

Exceptions raised inside the generator propagate out through `recv()` or `feed()`. No special handling needed.

```python
@stream_fn
async def safe_parse(events):
    async for event in events:
        try:
            yield json.loads(event)
        except json.JSONDecodeError:
            yield {"error": "invalid json", "raw": event}
```

---

## Full example (20 lines, production-ready)

```python
from velo import stream_fn

@stream_fn
async def track_position(events):
    """Stateful dead-reckoning from delta events."""
    x, y = 0.0, 0.0
    async for delta in events:
        x += delta["dx"]
        y += delta["dy"]
        yield {"x": x, "y": y, "dist": (x**2 + y**2) ** 0.5}

# Batch
path = await track_position.run([
    {"dx": 1, "dy": 0},
    {"dx": 0, "dy": 1},
    {"dx": -1, "dy": 0},
])

# Live
async with track_position.open() as stream:
    await stream.send({"dx": 1, "dy": 0})
    pos = await stream.recv()
    print(f"Position: ({pos['x']}, {pos['y']})")
```

---

## What NOT to do

❌ Don't make users subclass anything
❌ Don't require users to import `StreamEvent`, `StreamConfig`, `StreamContext` for basic use
❌ Don't add positional args to `@stream_fn` — all config is keyword
❌ Don't expose Rust internals (no `PyChannel`, `PyArena`, `PyScheduler` in the public API)
❌ Don't add `emit()`, `push()`, `publish()` — it's just `yield`
❌ Don't make the context manager return something complex — just a `Stream`

---

## Public API surface (everything exported from `velo`)

```python
from velo import stream_fn        # the decorator
from velo import Stream           # the handle (for type hints)
from velo import StreamMetrics    # metrics dataclass (for type hints)
from velo import StreamConfig     # config dataclass (for advanced use)
```

Four exports. That's the entire public surface.
