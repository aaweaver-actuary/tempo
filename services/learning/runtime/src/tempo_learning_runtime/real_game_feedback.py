"""Compatibility exports for Learning's existing feedback label and miss reads."""
from tempo_learning_database.adapters.real_game_miss_projection import (
    outstanding_miss_sql, has_outstanding_real_game_miss,
)

MISS_REASON = "Priority review · missed in a recent game"
