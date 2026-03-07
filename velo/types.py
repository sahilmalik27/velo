"""Core types for streamfn."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class StreamEvent:
    """A single event flowing through a stream."""

    id: str
    data: Any
    metadata: Optional[dict] = None


@dataclass
class StreamConfig:
    """Configuration for stream function."""

    buffer: int = 256
    timeout: float = 30.0
    max_concurrent: int = 1000

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "buffer": self.buffer,
            "timeout": self.timeout,
            "max_concurrent": self.max_concurrent,
        }


@dataclass
class StreamMetrics:
    """Metrics for a stream - just read the fields, no methods."""

    events_in: int = 0
    events_out: int = 0
    bytes_in: int = 0
    bytes_out: int = 0
    latency_p50_us: float = 0.0
    latency_p95_us: float = 0.0
    latency_p99_us: float = 0.0
    start_time_ns: int = 0
    duration_ms: float = 0.0

    def _record_in(self, size_bytes: int) -> None:
        """Internal: record input event."""
        self.events_in += 1
        self.bytes_in += size_bytes

    def _record_out(self, size_bytes: int) -> None:
        """Internal: record output event."""
        self.events_out += 1
        self.bytes_out += size_bytes
