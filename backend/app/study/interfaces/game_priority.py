"""Study's semantic game-priority intake, status and control surface.

Single-source immutable wire definitions remain in the existing pure contract
package during #501's mechanical component migration. No implementation,
construction, database or queue capability is exported here.
"""
from app.contracts.product_v02.learning import (
    EncounterFeedbackPort, EncounterReceipt, GameEffectControl, GameEffectKey,
    GameEncounter, GameLapse, LearningFeedbackPort, LearningReceipt,
)

__all__ = [
    "EncounterFeedbackPort", "EncounterReceipt", "GameEffectControl",
    "GameEffectKey", "GameEncounter", "GameLapse", "LearningFeedbackPort",
    "LearningReceipt",
]
