from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from tempo_intelligence_contracts import v1
from .conftest import load_fixture, load_reference


def order(availability, **changes):
    arguments = dict(
        workspace_id="workspace-a",
        publication_id="publication-a",
        generation=1,
        learning_revision=1,
        eligible_card_ids=["card-b", "card-a", "card-a"],
        admitted_capacity=1,
        now=datetime(2026, 10, 14, 13, tzinfo=timezone.utc),
    )
    return load_reference().suggested_order(availability, **{**arguments, **changes})


def test_intelligence_unavailable_preserves_deterministic_introductions():
    unavailable = v1.IntelligenceAvailability.model_validate(
        load_fixture("unavailable")
    )
    assert order(unavailable) == ["card-a"]
    assert unavailable.publication is None


def test_unknown_probability_is_distinct_from_zero():
    publication = v1.AdvisoryPublication.model_validate(
        load_fixture("advice-probabilities")
    )
    assert publication.advisory.moves[0].probability == 0
    assert publication.advisory.moves[1].probability is None
    data = publication.model_dump()
    data["advisory"]["unknown_mass"] = 0.0
    with pytest.raises(ValidationError):
        v1.AdvisoryPublication.model_validate(data)


def test_ranking_never_overrides_eligibility_quota_or_deduplication():
    data = load_fixture("available")
    data["publication"]["advisory"]["cards"] = [
        {"card_id": "ineligible", "value": 1.0},
        {"card_id": "card-b", "value": 0.9},
    ]
    availability = v1.IntelligenceAvailability.model_validate(data)
    assert order(availability) == ["card-b"]
    assert order(availability, admitted_capacity=0) == []
    assert order(availability, admitted_capacity=3) == ["card-b", "card-a"]


@pytest.mark.parametrize(
    "change",
    [
        {"generation": 2},
        {"learning_revision": 2},
        {"workspace_id": "other"},
        {"now": datetime(2026, 10, 15, 13, tzinfo=timezone.utc)},
    ],
)
def test_incompatible_or_stale_advice_uses_current_learning_fallback(change):
    data = load_fixture("available")
    data["publication"]["advisory"]["cards"] = [{"card_id": "card-b", "value": 0.9}]
    assert order(v1.IntelligenceAvailability.model_validate(data), **change) == [
        "card-a"
    ]


def test_unavailable_publication_cannot_manufacture_advice():
    data = load_fixture("available")
    data["status"] = "unavailable"
    with pytest.raises(ValidationError):
        v1.IntelligenceAvailability.model_validate(data)
