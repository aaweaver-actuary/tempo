"""Public independent Intelligence composition and configuration."""

from .config import Configuration
from .api import create_app

__version__ = "0.1.0"
__all__ = ["Configuration", "create_app"]
