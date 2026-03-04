"""
streamfn - Production-grade Python streaming library with Rust core.

Fast, stateful, ephemeral, scale-to-zero stream functions.

Public API:
    - stream_fn: decorator to create stream functions
    - Stream: handle for live streams (for type hints)
    - StreamMetrics: metrics dataclass (for type hints)
    - StreamConfig: config dataclass (for advanced use)
"""

from .decorator import stream_fn
from .runtime import Stream
from .types import StreamConfig, StreamMetrics

__version__ = "0.1.0"

__all__ = [
    "stream_fn",
    "Stream",
    "StreamMetrics",
    "StreamConfig",
]
