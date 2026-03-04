"""
IoT sensor burst example - rolling window aggregation.

Demonstrates: 30-second rolling windows over sensor data bursts
"""

import asyncio
from datetime import datetime
from streamfn import stream_fn


@stream_fn
async def rolling_window_30s(events):
    """Compute rolling 30-second window statistics."""
    from collections import deque

    window = deque()
    window_duration = 30.0  # seconds

    async for event in events:
        timestamp = event["timestamp"]
        value = event["value"]

        # Add to window
        window.append((timestamp, value))

        # Remove old events outside window
        while window and window[0][0] < timestamp - window_duration:
            window.popleft()

        # Compute statistics over window
        values = [v for _, v in window]
        yield {
            "timestamp": timestamp,
            "window_size": len(values),
            "mean": sum(values) / len(values) if values else 0,
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
        }


@stream_fn
async def detect_anomalies(events):
    """Detect anomalies based on window statistics."""
    async for event in events:
        # Simple anomaly: value outside mean ± 2*range
        range_val = event["max"] - event["min"]
        threshold = 2.0 * range_val

        # Flag if range is unusually large
        if range_val > 50:  # Threshold for sensor
            yield {
                "timestamp": event["timestamp"],
                "anomaly": "high_variance",
                "range": range_val,
                "window_size": event["window_size"],
            }


# Compose pipeline
iot_pipeline = rolling_window_30s | detect_anomalies


async def main():
    """Run the IoT sensor burst example."""
    import random

    # Generate synthetic sensor data bursts
    async def sensor_stream():
        """Simulate IoT sensor bursts."""
        base_time = datetime.now().timestamp()

        for i in range(1000):
            # Normal readings
            value = 20 + random.gauss(0, 5)

            # Inject anomaly bursts
            if i % 100 == 0:
                value += random.uniform(30, 50)

            yield {
                "sensor_id": "temp-01",
                "timestamp": base_time + i * 0.1,  # 10Hz sampling
                "value": value,
                "unit": "celsius",
            }

            await asyncio.sleep(0.001)  # Simulate real-time arrival

    # Batch mode
    print("Processing sensor data (batch)...")

    def batch_sensor_data():
        base_time = datetime.now().timestamp()
        for i in range(200):
            value = 20 + random.gauss(0, 5)
            if i % 50 == 0:
                value += 40
            yield {
                "sensor_id": "temp-01",
                "timestamp": base_time + i * 0.1,
                "value": value,
                "unit": "celsius",
            }

    anomalies = await iot_pipeline.run(batch_sensor_data())
    print(f"Detected {len(anomalies)} anomalies")

    for anomaly in anomalies[:5]:
        print(f"  {anomaly['anomaly']}: range={anomaly['range']:.1f} (window: {anomaly['window_size']})")

    # Live mode
    print("\nProcessing sensor stream (live)...")
    async with iot_pipeline.open() as stream:
        count = 0
        async for anomaly in stream.feed(sensor_stream()):
            count += 1
            print(f"  Anomaly detected: {anomaly['anomaly']} at t={anomaly['timestamp']:.1f}")
            print(f"    Range: {anomaly['range']:.1f}, Window: {anomaly['window_size']}")

        print(f"\nDetected {count} anomalies")
        print(f"Stream metrics: events_in={stream.metrics.events_in}, events_out={stream.metrics.events_out}")


if __name__ == "__main__":
    asyncio.run(main())
