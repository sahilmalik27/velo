"""Unit tests for types."""

import pytest
from streamfn import StreamConfig, StreamMetrics


def test_stream_config_defaults():
    """Test StreamConfig default values."""
    config = StreamConfig()
    assert config.buffer == 256
    assert config.timeout == 30.0
    assert config.max_concurrent == 1000


def test_stream_config_custom():
    """Test StreamConfig with custom values."""
    config = StreamConfig(buffer=128, timeout=10.0, max_concurrent=500)
    assert config.buffer == 128
    assert config.timeout == 10.0
    assert config.max_concurrent == 500


def test_stream_config_to_dict():
    """Test StreamConfig to_dict conversion."""
    config = StreamConfig(buffer=64, timeout=5.0, max_concurrent=100)
    d = config.to_dict()
    assert d == {
        "buffer": 64,
        "timeout": 5.0,
        "max_concurrent": 100,
    }


def test_stream_metrics_defaults():
    """Test StreamMetrics default values."""
    metrics = StreamMetrics()
    assert metrics.events_in == 0
    assert metrics.events_out == 0
    assert metrics.bytes_in == 0
    assert metrics.bytes_out == 0


def test_stream_metrics_record():
    """Test recording events in metrics."""
    metrics = StreamMetrics()

    metrics._record_in(100)
    assert metrics.events_in == 1
    assert metrics.bytes_in == 100

    metrics._record_out(200)
    assert metrics.events_out == 1
    assert metrics.bytes_out == 200

    metrics._record_in(50)
    assert metrics.events_in == 2
    assert metrics.bytes_in == 150
