"""CortexShift: Switch agents. Keep the context.

Provider-agnostic task handoff and state management for AI coding agents.
"""

from importlib.metadata import version
from typing import Final

__version__: Final[str] = version("cortexshift")
__all__ = ["__version__"]
