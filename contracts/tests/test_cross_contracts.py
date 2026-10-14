from pathlib import Path
import json
from tempo_learning_contracts import v1 as learning
from tempo_content_publication_contracts import v1 as content
from tempo_game_evidence_contracts import v1 as evidence

ROOT = Path(__file__).resolve().parents[1]


def test_content_executable_definitions_are_consumable_by_learning_without_shared_orm():
    for path in (ROOT / "content-publication/fixtures/v1").glob("publication*.json"):
        fixture = json.loads(path.read_text())
        if fixture["model"] != "Publication":
            continue
        publication = content.Publication.model_validate(fixture["payload"])
        definition = learning.ExecutableExercise.model_validate(
            publication.definitions[0].model_dump()
        )
        assert definition.card_id == "card-a"
        assert definition.publication_generation == publication.publication_generation
        assert type(definition) is not type(publication.definitions[0])


def test_game_evidence_obligation_preserves_identity_in_learning_contract():
    fixture = json.loads((ROOT / "game-evidence/fixtures/v1/delivery.json").read_text())
    delivery = evidence.EvidenceDelivery.model_validate(fixture["payload"])
    obligation = delivery.obligations[0]
    consumed = learning.LearningObligation.model_validate(
        {
            "contract_version": 1,
            "workspace_id": delivery.workspace_id,
            "source_kind": "game_evidence",
            **obligation.model_dump(),
        }
    )
    assert consumed.obligation_id == obligation.obligation_id == "obligation-a"
    assert consumed.source_evidence_id == delivery.evidence_id
    assert consumed.source_revision == delivery.evidence_revision
