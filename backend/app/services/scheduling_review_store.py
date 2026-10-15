"""Legacy adapter exports; Learning owns the shared implementation."""

from tempo_learning_runtime import scheduling_review_store as _owned

OwnedConnectionSchedulingReviewStore = _owned.OwnedConnectionSchedulingReviewStore

# Keep the legacy contract dependency explicit for consumer selection.
from .scheduling_review_contracts import SchedulingReviewStore as SchedulingReviewStore
