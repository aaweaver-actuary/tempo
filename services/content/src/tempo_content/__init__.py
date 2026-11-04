"""Independent authored Content and immutable publication component."""

from . import final_generation, generation_envelope
from .models import SourceSnapshot
from .domain import (ContentError, build_publication, canonical_position,
                     canonical_source, inventory_segment, stable_id,
                     validate_exercise_step)

__all__ = ["SourceSnapshot", "ContentService", "ContentError", "stable_id", "canonical_source",
           "canonical_position", "inventory_segment", "validate_exercise_step",
           "build_publication", "generation_envelope", "final_generation"]


def __getattr__(name: str):
    """Load the supported concrete service only when it is requested."""
    if name == "ContentService":
        from tempo_content_database.adapters.store import ContentService
        return ContentService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
