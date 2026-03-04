"""
Video pipeline example - replicate paper benchmark.

Demonstrates: stateful frame differencing for motion detection
"""

import asyncio
from streamfn import stream_fn


@stream_fn
async def decode_frames(events):
    """Decode base64 frames to numpy arrays."""
    import base64
    import numpy as np

    async for event in events:
        # Simulate frame decode
        frame_data = base64.b64decode(event["data"])
        frame = np.frombuffer(frame_data, dtype=np.uint8).reshape(event["shape"])
        yield {"id": event["id"], "frame": frame, "timestamp": event["timestamp"]}


@stream_fn
async def compute_diffs(events):
    """Compute frame differences for motion detection - stateful!"""
    prev_frame = None

    async for event in events:
        current_frame = event["frame"]

        if prev_frame is not None:
            # Compute absolute difference
            diff = abs(current_frame.astype(int) - prev_frame.astype(int))
            motion_score = diff.mean()

            yield {
                "id": event["id"],
                "motion_score": motion_score,
                "timestamp": event["timestamp"],
            }

        prev_frame = current_frame


@stream_fn
async def filter_significant_motion(events):
    """Filter frames with significant motion."""
    threshold = 15.0  # Motion threshold

    async for event in events:
        if event["motion_score"] > threshold:
            yield event


# Compose pipeline with | operator
video_pipeline = decode_frames | compute_diffs | filter_significant_motion


async def main():
    """Run the video pipeline benchmark."""
    import base64
    import numpy as np

    # Generate synthetic video frames
    def generate_frames(num_frames=1000):
        """Generate synthetic video frames."""
        for i in range(num_frames):
            # Random frame (simulating camera input)
            frame = np.random.randint(0, 255, (480, 640, 3), dtype=np.uint8)

            # Add motion every 10th frame
            if i % 10 == 0:
                frame[100:200, 100:200] = 255

            yield {
                "id": f"frame-{i}",
                "data": base64.b64encode(frame.tobytes()).decode(),
                "shape": (480, 640, 3),
                "timestamp": i / 30.0,  # 30 fps
            }

    # Batch mode - process all frames
    print("Running video pipeline (batch mode)...")
    start = asyncio.get_event_loop().time()

    motion_events = await video_pipeline.run(generate_frames(1000))

    elapsed = asyncio.get_event_loop().time() - start
    print(f"Processed 1000 frames in {elapsed:.2f}s")
    print(f"Detected {len(motion_events)} motion events")
    print(f"Throughput: {1000 / elapsed:.0f} frames/sec")

    # Live mode - process frames as they arrive
    print("\nRunning video pipeline (live mode)...")
    async with video_pipeline.open() as stream:
        count = 0
        async for event in stream.feed(generate_frames(100)):
            count += 1
            print(f"Motion detected: {event['motion_score']:.1f} at t={event['timestamp']:.2f}s")

        print(f"\nStream metrics: {stream.metrics}")


if __name__ == "__main__":
    asyncio.run(main())
