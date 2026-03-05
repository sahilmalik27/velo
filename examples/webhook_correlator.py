"""
Webhook correlator example - session-window event correlation.

Demonstrates: correlating events within a session, session timeout
"""

import asyncio
from datetime import datetime
from velo import stream_fn


@stream_fn
async def correlate_sessions(events):
    """Correlate events into sessions with 5-second timeout."""
    from collections import defaultdict

    sessions = defaultdict(lambda: {"events": [], "last_seen": 0})
    session_timeout = 5.0  # seconds

    async for event in events:
        session_id = event["session_id"]
        timestamp = event["timestamp"]

        # Check if session expired
        if sessions[session_id]["last_seen"] > 0:
            elapsed = timestamp - sessions[session_id]["last_seen"]
            if elapsed > session_timeout:
                # Session expired, emit and reset
                session_data = sessions[session_id]
                yield {
                    "session_id": session_id,
                    "event_count": len(session_data["events"]),
                    "events": session_data["events"].copy(),
                    "duration": session_data["last_seen"] - session_data["events"][0]["timestamp"],
                    "status": "expired",
                }
                sessions[session_id] = {"events": [], "last_seen": 0}

        # Add event to session
        sessions[session_id]["events"].append(event)
        sessions[session_id]["last_seen"] = timestamp


@stream_fn
async def analyze_sessions(events):
    """Analyze completed sessions."""
    async for session in events:
        # Compute session metrics
        event_types = {}
        for event in session["events"]:
            event_type = event.get("type", "unknown")
            event_types[event_type] = event_types.get(event_type, 0) + 1

        yield {
            "session_id": session["session_id"],
            "event_count": session["event_count"],
            "duration": session["duration"],
            "event_types": event_types,
            "has_error": any(e.get("error") for e in session["events"]),
        }


# Compose pipeline
webhook_pipeline = correlate_sessions | analyze_sessions


async def main():
    """Run the webhook correlator example."""
    import random

    # Generate synthetic webhook events
    async def webhook_stream():
        """Simulate webhook events from multiple sessions."""
        base_time = datetime.now().timestamp()
        sessions = ["session-a", "session-b", "session-c"]

        for i in range(100):
            session_id = random.choice(sessions)
            event_type = random.choice(["page_view", "click", "submit", "error"])

            yield {
                "session_id": session_id,
                "type": event_type,
                "timestamp": base_time + i * 0.5,
                "path": f"/page/{random.randint(1, 5)}",
                "error": event_type == "error",
            }

            await asyncio.sleep(0.01)

    # Batch mode
    print("Correlating webhooks (batch)...")

    def batch_webhooks():
        base_time = datetime.now().timestamp()
        events = [
            {"session_id": "s1", "type": "page_view", "timestamp": base_time + 0, "path": "/home"},
            {"session_id": "s1", "type": "click", "timestamp": base_time + 1, "path": "/home"},
            {"session_id": "s2", "type": "page_view", "timestamp": base_time + 2, "path": "/about"},
            {"session_id": "s1", "type": "submit", "timestamp": base_time + 3, "path": "/form"},
            # Session 1 expires here (5s timeout)
            {"session_id": "s1", "type": "page_view", "timestamp": base_time + 10, "path": "/new"},
            {"session_id": "s2", "type": "click", "timestamp": base_time + 11, "path": "/about"},
        ]
        for e in events:
            yield e

    sessions = await webhook_pipeline.run(batch_webhooks())
    print(f"Completed {len(sessions)} sessions:")
    for session in sessions:
        print(f"  {session['session_id']}: {session['event_count']} events, {session['duration']:.1f}s")
        print(f"    Types: {session['event_types']}")

    # Live mode
    print("\nCorrelating webhooks (live stream)...")
    async with webhook_pipeline.open() as stream:
        count = 0
        async for session in stream.feed(webhook_stream()):
            count += 1
            print(f"  Session {session['session_id']} completed:")
            print(f"    Events: {session['event_count']}, Duration: {session['duration']:.1f}s")
            print(f"    Types: {session['event_types']}, Has error: {session['has_error']}")

        print(f"\nProcessed {count} completed sessions")
        print(f"Metrics: {stream.metrics.events_in} in, {stream.metrics.events_out} out")


if __name__ == "__main__":
    asyncio.run(main())
