"""Compatibility location for the sole explicit Study calendar conversion.

Callers supply an instant and configured timezone; no process-local mutable
calendar or implicit today override remains. Current composition captures once
and passes local_day separately from an original recorded review_day.
"""

from tempo_learning_review import review_day_at

__all__ = ["review_day_at"]
