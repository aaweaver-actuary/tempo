"""Shared encounter values reach the existing importance owner unchanged."""
from pathlib import Path

import pytest

from app.contracts.product_v02.learning import GameEncounter
from app.product_composition.encounters import importance_from_encounters

FIXTURES = Path(__file__).resolve().parents[2] / "contracts/product-v02/fixtures/encounters"


def encounter(name):
    import json
    return GameEncounter.model_validate_json(json.dumps(json.loads((FIXTURES/(name+".json")).read_text())["payload"]))


@pytest.mark.parametrize("introduced", [False, True])
def test_historical_importance_uses_current_mapping_without_a_lapse_filter(introduced):
    observed = encounter("historical-missed")
    current = observed.decision.model_copy(update={"introduction": "introduced" if introduced else "unintroduced",
        "introduced_at": observed.decision.introduced_at if introduced else None})
    value, = importance_from_encounters(workspace_id=observed.key.workspace_id, account="synthetic", current_approved_decisions=(current,),
        encounters=(observed, observed))
    assert value.decision == current and value.facts.personal_encounters == 1
    assert len(value.encounter_sources) == len(value.differing_response_sources) == 1
    assert value.policy_version == "current-decision-personal-importance-v1"


def test_correct_play_is_one_frequency_support_without_a_missed_response():
    observed = encounter("unintroduced-correct")
    value, = importance_from_encounters(workspace_id=observed.key.workspace_id, account="synthetic", current_approved_decisions=(observed.decision,),
        encounters=(observed, observed))
    assert value.facts.personal_encounters == 1 and len(value.encounter_sources) == 1
    assert value.differing_response_sources == ()


def test_later_recomparison_has_one_original_game_contribution():
    original = encounter("unintroduced-missed")
    later = original.model_copy(update={"decision": original.decision.model_copy(update={
        "introduction": "introduced", "introduced_at": "2026-10-27T12:00:00Z"})})
    value, = importance_from_encounters(workspace_id=original.key.workspace_id, account="synthetic", current_approved_decisions=(later.decision,),
        encounters=(original, later))
    assert value.facts.personal_encounters == len(value.differing_response_sources) == 1
    assert value.encounter_sources[0][2:] == (original.key.game_id, original.source.source_revision)
