"""Stream adapters for various input sources."""

from .file import FileAdapter
from .generator import GeneratorAdapter
from .http import HttpAdapter

__all__ = ["FileAdapter", "GeneratorAdapter", "HttpAdapter"]
