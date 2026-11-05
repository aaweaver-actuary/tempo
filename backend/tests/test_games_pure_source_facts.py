"""The retained source projection and HTTP selection are pure public facts."""

import pytest
from pydantic import ValidationError

from app.contracts.product_v02.games import ImportedFeedbackSelection
from app.games.ingestion.source_facts import retained_time_precision


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        (42, "unknown"),
        ("2026-09-18", "unknown"),
        ("2026-09-18T12:30:00", "unknown"),
        ("invalidT12:30:00Z", "unknown"),
        ("2026-09-18T00:00:00Z", "unknown"),
        ("2026-09-18T02:00:00+02:00", "unknown"),
        ("2026-09-18T12:30:00Z", "second"),
        ("2026-09-18T00:00:00.000001Z", "second"),
    ],
)
def test_retained_time_precision_preserves_source_uncertainty(value, expected):
    assert retained_time_precision({"played_at": value}) == expected
    assert retained_time_precision({}) == "unknown"


def test_feedback_selection_contract_and_compatibility_identity():
    from app.product_composition.feedback_commands import (
        ImportedFeedbackSelection as previous_selection,
    )
    from app.product_composition.feedback_commands import (
        retained_time_precision as previous_precision,
    )

    assert previous_selection is ImportedFeedbackSelection
    assert previous_precision is retained_time_precision
    selection = ImportedFeedbackSelection(
        repertoire_id="r", game_id="g", occurrence_ply=1
    )
    assert selection.model_dump() == {
        "repertoire_id": "r",
        "game_id": "g",
        "occurrence_ply": 1,
    }
    for invalid in (0, 1001):
        with pytest.raises(ValidationError):
            ImportedFeedbackSelection(
                repertoire_id="r", game_id="g", occurrence_ply=invalid
            )
