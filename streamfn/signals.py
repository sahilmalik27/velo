"""Signal handling for graceful shutdown."""

import asyncio
import signal
from typing import Optional


class GracefulShutdown:
    """Handle graceful shutdown on SIGTERM/SIGINT."""

    def __init__(self) -> None:
        self._shutdown_event: Optional[asyncio.Event] = None
        self._original_handlers: dict = {}

    def setup(self) -> None:
        """Set up signal handlers."""
        self._shutdown_event = asyncio.Event()

        # Store original handlers
        self._original_handlers = {
            signal.SIGTERM: signal.getsignal(signal.SIGTERM),
            signal.SIGINT: signal.getsignal(signal.SIGINT),
        }

        # Set new handlers
        for sig in [signal.SIGTERM, signal.SIGINT]:
            signal.signal(sig, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        """Handle shutdown signal."""
        if self._shutdown_event is not None:
            self._shutdown_event.set()

    def restore(self) -> None:
        """Restore original signal handlers."""
        for sig, handler in self._original_handlers.items():
            signal.signal(sig, handler)

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        if self._shutdown_event is not None:
            await self._shutdown_event.wait()

    @property
    def should_shutdown(self) -> bool:
        """Check if shutdown was requested."""
        return self._shutdown_event is not None and self._shutdown_event.is_set()


# Global instance
_shutdown_handler: Optional[GracefulShutdown] = None


def get_shutdown_handler() -> GracefulShutdown:
    """Get or create global shutdown handler."""
    global _shutdown_handler
    if _shutdown_handler is None:
        _shutdown_handler = GracefulShutdown()
        _shutdown_handler.setup()
    return _shutdown_handler
