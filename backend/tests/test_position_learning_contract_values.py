from dataclasses import FrozenInstanceError
from datetime import date, datetime, timezone

import pytest

from app.study.position_learning.contracts import (
    DueAtIssuance, FirstResponse, PositionDefinition, PositionKey, SourceCertificate,
)

START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq -"
NOW = datetime(2026, 11, 2, tzinfo=timezone.utc)


def test_position_identity_is_exact_legal_and_separate_from_answer():
    key = PositionKey("repertoire", START)
    assert PositionDefinition(key, "e2e4", "v1").key == PositionDefinition(key, "d2d4", "v2").key
    assert key != PositionKey("other", START)
    for invalid in (START + " 0 1", START.replace("KQkq", "qkQK"),
                    "8/8/8/8/8/8/8/8 w - -", START.replace(" w ", " x ")):
        with pytest.raises(ValueError):
            PositionKey("repertoire", invalid)
    with pytest.raises(FrozenInstanceError):
        key.position = START


def test_detached_witness_and_response_require_identity_and_aware_times():
    definition = PositionDefinition(PositionKey("r", START), "e2e4", "v1")
    witness = DueAtIssuance(definition, False, date(2026, 11, 2), "epoch", 1, NOW)
    response = FirstResponse("attempt", 0, definition, "e2e4", False, NOW, "event")
    assert not witness.due and response.correct
    with pytest.raises(ValueError):
        DueAtIssuance(definition, True, date(2026, 11, 2), "epoch", 1, NOW.replace(tzinfo=None))
    with pytest.raises(ValueError):
        FirstResponse("attempt", 0, definition, None, False, NOW, "event")
    with pytest.raises(ValueError):
        SourceCertificate("", "revision", "digest", True, True)
