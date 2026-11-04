"""Non-serving accepted position effect entry point; no command registration.

The caller owns a disposable native transaction. Current authored Content,
stage adoption and installed single-writer authority are NOT established here.
"""
from tempo_learning_database.adapters.position_effect_store import (
    PositionHold, StoredPositionReceipt, apply_position_effect,
)
from .position_grade_policy import CurrentPositionState, decide_position_grade


def accept_position_effect(database, **identity) -> StoredPositionReceipt | PositionHold:
    return apply_position_effect(database,decide=decide_position_grade,
                                 current_state_type=CurrentPositionState,**identity)
