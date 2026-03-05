# The Gap Between Serverless and Streaming — and How We Filled It

*Introducing Velo: stateful stream processing without the infrastructure overhead*

---

Imagine you work at Instagram. A user uploads a 10-second video. Before it goes live, you need to process every frame — resize it, detect motion, check for content violations, extract thumbnails.

Simple enough. You write a function. The function gets a frame, processes it, returns a result.

But here's the catch: to detect motion, you need to compare frame 12 to frame 11. To extract a highlight reel, you need to remember which frames had the most action. To compress efficiently, you need statistics about the entire video so far.

**Your function needs to remember things between frames.**

That's the problem. And it's more common than you think.

---

## State: the thing that makes streaming hard

Every interesting stream processing problem comes down to one word: **state**.

State is just "things you need to remember." The previous frame. The running total. The last five readings. Whether you've already seen this event. The user's preference from 3 messages ago.

Processing a single event in isolation is easy. The moment you need to remember something from a previous event, everything gets complicated.

This isn't just a video problem. It shows up everywhere:

- **Fraud detection:** Did this user make 5 purchases in 10 minutes? You need to remember the last 4.
- **IoT sensors:** Is this temperature reading an anomaly? You need the baseline from the last hour.
- **Live sports stats:** What's the player's shooting percentage in the last 5 minutes? You need every shot attempt.
- **Customer support chat:** Is this the same issue the user reported yesterday? You need the session history.
- **Security monitoring:** Has this IP address tried to log in more than 10 times? You need the count.

In every case: events arrive in a sequence, the sequence has meaning, and you need memory to extract that meaning.

---

## How people solve this today

### Solution 1: Store state externally (Redis, DynamoDB)

The most common approach. Between function calls, you save your state to a database. Next call, you load it back.

```
Frame 11 arrives → load prev_frame from Redis → compare → save frame_11 to Redis
Frame 12 arrives → load prev_frame from Redis → compare → save frame_12 to Redis
Frame 13 arrives → load prev_frame from Redis → compare → save frame_13 to Redis
```

**It works.** But think about what's happening: you're paying round-trip time to a database just to pass a variable between function calls. For a 30fps video, that's 30 round trips per second to Redis, just to hold one frame in memory.

For a fraud detection system processing 10,000 sessions simultaneously, that's 10,000 Redis keys to manage, with TTLs to set, cleanup jobs to run, and failure modes to handle when Redis is slow or unavailable.

You're using a distributed database as a global variable store. It works. It's also expensive, slow, and operationally complex.

**The real cost:** You're not paying for computation. You're paying for the machinery required to simulate memory.

### Solution 2: Always-on stream processing (Apache Flink, Faust, Kafka Streams)

These are the proper tools for stateful stream processing. Flink is used by companies like Uber, Alibaba, and LinkedIn to process billions of events per day. It handles state beautifully — you define a pipeline, deploy it, and it runs continuously, maintaining state across millions of events.

The catch: it runs *continuously*. The pipeline is always on, always consuming CPU and memory, regardless of whether any data is actually flowing.

For Instagram's video processing: videos arrive unpredictably. A user uploads a video at 2pm. Nobody uploads for 45 minutes. Then 50 people upload at once. With Flink, you're running a full cluster 24/7 to handle the 50-person burst — and paying for it during the 45 minutes of silence.

For the earthquake monitoring system: a sensor might not trigger a burst for days. Flink sits there, consuming resources, waiting.

The numbers: a minimal Flink deployment costs ~$200-500/month just to exist. Startup time is 2-10 seconds per job. For a 10-second video clip, you might spend as much time starting the pipeline as actually processing the video.

**The real cost:** You're designed for rivers. You're being asked to process rain puddles.

### Solution 3: Just manage it yourself (the dict approach)

If you've been building Python services for a while, you've done this:

```python
# Simple! Just use a dict.
session_state = {}

def process_event(session_id, event):
    state = session_state.get(session_id, initial_state())
    result = process(event, state)
    session_state[session_id] = state
    return result
```

This looks simple. And for 10 sessions, it is.

Then you hit production:

- **Memory leak.** Sessions end but the dict never gets cleaned up. After a week, you're holding state for 50,000 sessions that ended days ago.
- **Race conditions.** Two events from the same session arrive simultaneously. Both read the same state, both update it, one write overwrites the other.
- **Growing complexity.** Your state is now 8 variables. You have 8 dicts. All need the same cleanup logic. One cleanup bug corrupts all 8.
- **Timeout handling.** When does a session "end"? You need a background job checking timestamps. Now you have a second system to maintain.

What started as 5 lines becomes 200 lines of lifecycle management code. You've built your own mini-Flink, but worse.

---

## The gap

Draw a line from "totally stateless" to "always-on stateful pipeline."

```
Totally stateless                              Always-on pipeline
       │                                              │
  AWS Lambda                                     Apache Flink
  Cloud Functions                                Faust
       │                                              │
  Fast startup ✅                              Stateful ✅
  Scales to zero ✅                            Scales to zero ❌
  Stateful ❌                                  Fast startup ❌
```

Everything on the left is fast and cheap but can't remember anything. Everything on the right remembers everything but never sleeps.

For short-running, bursty, stateful workloads — a 10-second video, a 2-minute sensor burst, a user session — neither end works well.

**This gap is what Velo fills.**

---

## Velo: stateful streams that scale to zero

The core idea is simple. Instead of a function that handles one event, you write a function that handles an entire stream — and stays alive for the duration.

```python
from velo import stream_fn

@stream_fn
async def detect_motion(frames):
    prev_frame = None                    # state: just a variable

    async for frame in frames:           # events arrive one by one
        if prev_frame is not None:
            motion = compare(frame, prev_frame)
            yield {"frame": frame.id, "motion_score": motion}
        prev_frame = frame               # update state
```

When a video upload starts, Velo spins up a tiny worker for it. That worker runs your function, holds state as plain Python variables, and processes frames as they arrive. When the video ends, the worker disappears. No Redis. No always-on pipeline.

**The whiteboard analogy:** Every stream gets its own whiteboard. You can write anything on it. When the stream ends, the whiteboard is erased. Other streams have their own whiteboards — completely separate, no interference.

### What Velo does that you can't do easily yourself

When you have 1,000 simultaneous video uploads, Velo runs 1,000 workers. Each has completely isolated state. Workers spin up in microseconds, not seconds. Backpressure is built in — if your processing is slow, Velo automatically slows down event delivery so you don't run out of memory. When uploads finish, workers are dropped immediately.

You don't write any of this. You write the generator function. Velo handles everything else.

---

## Real-life examples

Each example below shows the **full picture**: how the stream function is defined, and exactly where and how it gets called in a real application.

### 1. Instagram-style video processing

**The problem:** User uploads a 30fps, 10-second video (300 frames). You need to detect motion, extract highlights, compute per-scene statistics — before the video goes live.

**With Lambda + Redis:**
- 300 Redis reads + 300 Redis writes per video
- At 10ms Redis latency: 6 seconds of I/O for a 10-second video
- Plus Redis infrastructure cost, key TTL management, failure handling

**With Flink:**
- Pipeline must be running before upload arrives — you can't start it on demand
- Pipeline sits idle between uploads: ~$300/month burning for nothing

**With Velo — the full integration:**

```python
# 1. Define the stream function — your processing logic
from velo import stream_fn

@stream_fn
async def process_video(frames):
    stats = {"total": 0, "high_motion": 0, "scenes": []}
    prev = None
    async for frame in frames:
        motion = compare(frame, prev) if prev else 0
        if motion > MOTION_THRESHOLD:
            stats["high_motion"] += 1
        if is_scene_change(frame, prev):
            stats["scenes"].append(frame.timestamp)
        stats["total"] += 1
        prev = frame
        yield frame.with_metadata(stats)

# 2. Call it from your web server — this is where it actually runs
from fastapi import FastAPI, UploadFile

app = FastAPI()

@app.post("/upload")
async def upload_video(file: UploadFile):
    processed_frames = []

    # Open ONE stream per upload — one worker, isolated state
    async with process_video.open() as stream:
        async for result in stream.feed(extract_frames(file)):
            processed_frames.append(result)
            # results stream back as frames are processed

    return {
        "frames": len(processed_frames),
        "high_motion_frames": processed_frames[-1].metadata["high_motion"],
        "scene_changes": processed_frames[-1].metadata["scenes"]
    }
```

The stream opens when an upload arrives and closes when it ends. 1,000 simultaneous uploads = 1,000 isolated workers, each with their own `prev`, `stats`, and `scenes`. No worker knows about the others.

**Numbers:**
- Worker starts in ~200 microseconds
- Zero Redis round trips
- 1,000 concurrent uploads: each worker uses ~KB of memory

---

### 2. Earthquake sensor network

**The problem:** 500 seismometers worldwide. Silent 99.9% of the time. When a tremor hits, a sensor streams 60 seconds of high-frequency readings. You need rolling statistics and anomaly detection across the burst — then silence again.

**With always-on Flink:** ~$2,000/month for a cluster that processes data 0.1% of the time.

**With Velo — the full integration:**

```python
# 1. The stream function
@stream_fn
async def analyze_tremor(readings):
    window = []
    baseline = None

    async for r in readings:
        window.append(r.amplitude)
        if len(window) > 100:
            window.pop(0)
        avg = sum(window) / len(window)
        baseline = baseline or avg
        yield {
            "amplitude": r.amplitude,
            "rolling_avg": avg,
            "anomaly": abs(avg - baseline) > 2 * baseline,
            "timestamp": r.timestamp
        }

# 2. The sensor endpoint — one stream per sensor burst
@app.post("/sensor/{sensor_id}/reading")
async def receive_reading(sensor_id: str, reading: Reading):
    # Each sensor maintains its own open stream
    if sensor_id not in active_streams:
        active_streams[sensor_id] = await analyze_tremor.open().__aenter__()

    stream = active_streams[sensor_id]
    await stream.send(reading)
    result = await stream.recv()

    if result["anomaly"]:
        await alert_team(sensor_id, result)

    return result

@app.delete("/sensor/{sensor_id}/burst-end")
async def end_burst(sensor_id: str):
    # Sensor signals end of burst — worker disappears, memory freed
    if sensor_id in active_streams:
        await active_streams.pop(sensor_id).close()
```

500 sensors trigger simultaneously during a major earthquake → 500 workers spin up. When the burst ends → 500 workers disappear. Idle cost between events: zero.

---

### 3. E-commerce fraud detection

**The problem:** Detect fraud in real time, before a purchase is approved. Signals: multiple shipping addresses, fast purchase velocity, device switching, high spend in a short window.

**The challenge:** Each signal only makes sense in the context of the session. A single purchase to a new address is fine. The third purchase to a third different address in 5 minutes is not. You need memory across events.

**With stateless Lambda:** Query a database on every event to check history. At 200ms per query, fraudulent purchases get approved before the check completes.

**With Velo — the full integration:**

```python
# 1. The stream function — all signals computed from in-memory state
@stream_fn
async def fraud_check(events):
    purchase_times = []
    addresses = set()
    devices = set()
    total_spend = 0.0

    async for event in events:
        if event.type == "purchase":
            purchase_times.append(event.timestamp)
            addresses.add(event.shipping_address)
            total_spend += event.amount
        if event.type == "page_view":
            devices.add(event.device_id)

        recent = [t for t in purchase_times if event.timestamp - t < 300]
        risk = (
            len(recent) * 10 +
            len(addresses) * 15 +
            len(devices) * 20 +
            (25 if total_spend > 1000 else 0)
        )
        yield {"risk_score": risk, "block": risk > 50}

# 2. Session lifecycle — one stream per user session
# Session opens on first event, closes on checkout or timeout
user_streams: dict[str, Stream] = {}

@app.post("/event")
async def track_event(user_id: str, event: UserEvent):
    if user_id not in user_streams:
        user_streams[user_id] = await fraud_check.open().__aenter__()

    stream = user_streams[user_id]
    await stream.send(event)
    result = await stream.recv()

    if result["block"]:
        await flag_account(user_id, result["risk_score"])
        return {"status": "blocked"}
    return {"status": "ok"}

@app.post("/checkout/{user_id}")
async def checkout(user_id: str, order: Order):
    # Stream closes at checkout — state freed automatically
    if user_id in user_streams:
        await user_streams.pop(user_id).close()
    return await process_order(order)
```

Every risk signal is computed in microseconds from in-memory state. No database queries on the hot path. Session ends → worker disappears.

---

### 4. Live sports analytics

**The problem:** NBA broadcast. On-screen graphics need real-time player stats updated every time the player touches the ball — field goal percentage in last 5 minutes, current hot streak, defensive rebounds. Sub-second latency. No time for a database call.

**With Velo — the full integration:**

```python
# 1. The stream function — one per player per game
@stream_fn
async def player_stats(events):
    shots = []
    rebounds = 0
    streak = 0

    async for event in events:
        if event.type == "shot":
            shots.append({"made": event.made, "time": event.game_time})
            streak = streak + 1 if event.made else 0
        if event.type == "rebound":
            rebounds += 1

        recent = [s for s in shots if event.game_time - s["time"] < 300]
        yield {
            "fg_pct": sum(s["made"] for s in shots) / len(shots) if shots else 0,
            "last_5min_fg_pct": sum(s["made"] for s in recent) / len(recent) if recent else 0,
            "hot_streak": streak,
            "rebounds": rebounds
        }

# 2. Game event router — one stream per player, opened at tip-off
player_streams: dict[str, Stream] = {}

@app.post("/game/{game_id}/event")
async def game_event(game_id: str, event: GameEvent):
    player_id = event.player_id

    # Open a stream the first time we see a player
    if player_id not in player_streams:
        player_streams[player_id] = await player_stats.open().__aenter__()

    stream = player_streams[player_id]
    await stream.send(event)
    stats = await stream.recv()

    # Push updated stats to broadcast graphics in real time
    await broadcast_to_graphics(player_id, stats)
    return stats

@app.post("/game/{game_id}/end")
async def game_end(game_id: str):
    # Close all player streams at end of game
    for stream in player_streams.values():
        await stream.close()
    player_streams.clear()
```

10 players on the court, 2 teams = 10 concurrent workers. Each isolated, updating in microseconds. Game ends → all 10 workers disappear.

---

## Performance

Velo is built on a Rust runtime. The scheduling layer uses [tokio](https://tokio.rs) (the same async runtime that powers Discord's backend and AWS's services), lock-free message channels from [crossbeam](https://github.com/crossbeam-rs/crossbeam), and [PyO3](https://pyo3.rs) for zero-overhead Python integration.

What that means in practice:

| Metric | Velo | Flink | Lambda + Redis |
|--------|------|-------|----------------|
| Stream startup | ~200 microseconds | 2–10 seconds | N/A (stateless) |
| Inter-event latency (P99) | < 500 microseconds | < 10 milliseconds | 10–50ms (Redis RTT) |
| 1,000 concurrent streams | < 1GB memory | 5–20GB (cluster) | Redis scales separately |
| Idle cost | ~0 (workers dropped) | Full cluster running | Redis always on |

The ~99% overhead reduction claim comes from the original research paper ([arXiv:2603.03089](https://arxiv.org/abs/2603.03089)). The paper benchmarked stream functions against Apache Flink on a video processing workload and found that the lightweight, scale-to-zero model reduced processing overhead by 99% for this class of short-lived stream.

---

## When Velo is the right tool

Velo isn't for everything. Here's an honest guide:

**Velo is a good fit when:**
- Streams are short-lived (seconds to minutes), not continuous
- Stream arrival is unpredictable — you can't afford to run pipelines 24/7
- You need state within a stream, not across streams
- You're already running a service and want to add stateful stream processing without new infrastructure
- You want to avoid the operational complexity of Flink/Kafka

**Velo is not the right fit when:**
- You have massive, continuous, high-throughput pipelines (use Flink)
- You need exactly-once delivery guarantees and replay (use Kafka Streams)
- You need to aggregate state across millions of streams simultaneously (use a proper analytics database)
- You truly need serverless (Lambda + a state store is simpler for low volume)

---

## Why this matters

The original research paper this is based on ([arXiv:2603.03089](https://arxiv.org/abs/2603.03089)) identified an underserved class of workloads: short-running, lightweight, unpredictable, stateful streams. The paper proposed stream functions as the right abstraction and showed 99% overhead reduction vs mature stream engines.

The paper proposed this as a future cloud platform feature. Velo is that feature, available today, as an open-source library.

The goal isn't to replace Flink for large-scale continuous pipelines. The goal is to make the common case — hundreds of short-lived stateful streams arriving unpredictably — as simple to write as a Python function, as fast as a Rust runtime, and as cheap as paying for what you actually use.

---

## Get started

```bash
pip install velo-py
```

```python
from velo import stream_fn

@stream_fn
async def my_first_stream(events):
    count = 0
    async for event in events:
        count += 1
        yield {"count": count, "event": event}

# Try it immediately:
import asyncio

async def main():
    results = await my_first_stream.run(["hello", "world", "from", "velo"])
    for r in results:
        print(r)

asyncio.run(main())
```

The source code, benchmarks, and examples are at [github.com/sahilmalik27/velo](https://github.com/sahilmalik27/velo).

If you're working on a problem that fits this pattern — or if you have feedback on the approach — open an issue. This is early and we'd love to hear how people are using it.

---

*Velo is open source under the Apache 2.0 license. Built on tokio, crossbeam, and PyO3.*
