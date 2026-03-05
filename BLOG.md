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

### 1. Instagram-style video processing

**The problem:** User uploads a 30fps, 10-second video (300 frames). You need to detect motion, extract highlights, compute per-scene statistics.

**With Lambda + Redis:**
- 300 Redis reads + 300 Redis writes per video
- At 10ms Redis latency, that's 6 seconds of I/O for a 10-second video
- Cost at scale: ~$0.003 per video in Redis operations alone

**With Flink:**
- Pipeline must be running before upload arrives
- 5-second startup time (50% of the video length)
- Pipeline sits idle between uploads, consuming ~$300/month

**With Velo:**
```python
@stream_fn
async def process_video(frames):
    stats = {"total": 0, "high_motion": 0, "scenes": []}
    prev = None

    async for frame in frames:
        stats["total"] += 1
        motion = compare(frame, prev) if prev else 0

        if motion > MOTION_THRESHOLD:
            stats["high_motion"] += 1

        if is_scene_change(frame, prev):
            stats["scenes"].append(frame.timestamp)

        prev = frame
        yield frame.with_metadata(stats)
```
- Worker starts in ~200 microseconds
- Zero Redis round trips
- Zero idle cost between uploads
- State is just Python variables

### 2. Earthquake sensor network

**The problem:** 500 seismometers worldwide. Each is silent 99.9% of the time. When a tremor is detected, it sends a burst of 60 seconds of high-frequency readings. You need rolling statistics and anomaly detection across the burst.

**The challenge:** You can't predict when bursts arrive. You might get 50 sensors triggering simultaneously during a major event. Or none for a week.

**With always-on Flink:** You're running 500 pipeline slots 24/7, waiting. ~$2,000/month for sensors that barely trigger.

**With Velo:**
```python
@stream_fn
async def analyze_tremor(readings):
    window = []
    baseline = None
    anomalies = []

    async for reading in readings:
        window.append(reading.amplitude)
        if len(window) > 100:
            window.pop(0)

        avg = sum(window) / len(window)
        if baseline is None:
            baseline = avg

        is_anomaly = abs(avg - baseline) > 2 * baseline
        if is_anomaly:
            anomalies.append(reading.timestamp)

        yield {
            "reading": reading,
            "rolling_avg": avg,
            "anomaly": is_anomaly,
            "anomaly_count": len(anomalies)
        }
```

One worker per sensor burst. 500 can run simultaneously if needed. When the burst ends, the worker is gone. Cost when sensors are idle: zero.

### 3. E-commerce fraud detection

**The problem:** Detect whether a user's session looks fraudulent. Signals: multiple shipping addresses, unusually fast purchases, device fingerprint changes, orders above a threshold in a short time.

**The challenge:** You need to track these signals *across* events within a session, in real time, before the purchase goes through.

**With stateless Lambda:** Each purchase event arrives independently. To check "is this the 4th purchase in 5 minutes," you need to query a database. Under load, that query might take 200ms — long enough to approve the fraudulent purchase before the check completes.

**With Velo:**
```python
@stream_fn
async def fraud_check(events):
    purchase_times = []
    shipping_addresses = set()
    device_ids = set()
    total_spend = 0.0

    async for event in events:
        if event.type == "purchase":
            purchase_times.append(event.timestamp)
            shipping_addresses.add(event.shipping_address)
            total_spend += event.amount

        if event.type == "page_view":
            device_ids.add(event.device_id)

        # Risk signals — computed instantly from in-memory state
        recent_purchases = [t for t in purchase_times if event.timestamp - t < 300]
        risk_score = (
            len(recent_purchases) * 10 +      # velocity
            len(shipping_addresses) * 15 +    # address hopping
            len(device_ids) * 20 +            # device switching
            (1 if total_spend > 1000 else 0) * 25  # spend threshold
        )

        yield {
            "event": event,
            "risk_score": risk_score,
            "block": risk_score > 50
        }
```

Every signal is computed from in-memory state — no database query. The entire session state is available instantly. Sessions that look clean close quickly and the worker disappears.

### 4. Live sports analytics

**The problem:** NBA game, a player is having an exceptional quarter. The broadcast system needs to update real-time stats every time the player touches the ball — shots attempted, field goal percentage in last 5 minutes, defensive rebounds, streak of made shots.

This needs to be *instant*. On-screen graphics update live. No time for a database round trip.

**With Velo:**
```python
@stream_fn
async def player_stats(events):
    shots = []          # rolling shot history
    rebounds = 0
    current_streak = 0
    best_streak = 0

    async for event in events:
        if event.type == "shot_attempt":
            shots.append({
                "made": event.made,
                "time": event.game_time,
                "distance": event.distance
            })
            if event.made:
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 0

        if event.type == "rebound":
            rebounds += 1

        # Last 5 minutes of shots
        recent = [s for s in shots if event.game_time - s["time"] < 300]
        recent_pct = sum(s["made"] for s in recent) / len(recent) if recent else 0

        yield {
            "total_shots": len(shots),
            "overall_fg_pct": sum(s["made"] for s in shots) / len(shots) if shots else 0,
            "last_5min_fg_pct": recent_pct,
            "current_streak": current_streak,
            "rebounds": rebounds
        }
```

One stream per player per game. Stats update in microseconds. When the game ends, everything cleans up automatically.

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
