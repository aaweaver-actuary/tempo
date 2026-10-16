"""Independent installed consumers; input is exported Content JSON, never a database."""

from copy import deepcopy
import json
from pathlib import Path
import sys

from pydantic import ValidationError
from tempo_chess_core import card_id as legacy_card_id
from tempo_content_publication_contracts.v1 import Publication, serialize_contract
from tempo_game_evidence import ComparisonSnapshot
from tempo_learning_runtime.publications import PublicationBundle


def execution_facts(publication, source_id):
    return [
        {
            "card_id": definition.card_id,
            "content_type": "study_exercise",
            "memberships": [],
            "trained_color": "white",
            "due_date": "2026-10-16",
            "study_id": source_id,
            "exercise_id": definition.card_id,
            "source_ref": source_id,
            "source_fen": definition.starting_fen,
            "legacy_specification": {
                **definition.specification.model_dump(mode="json"),
                "prompt": definition.prompt,
                "hint": "",
                "explanation": "",
            },
        }
        for definition in publication.definitions
    ]


def probe(document):
    publication = Publication.model_validate(document["publication"])
    expected_kinds = {"move_line", "square_set", "knight_path", "choice", "explanation"}
    assert {item.specification.type for item in publication.definitions} == expected_kinds
    original = serialize_contract(publication)
    bundle = PublicationBundle.model_validate(
        {
            "schema_version": 1,
            "publication": publication.model_dump(mode="json"),
            "facts": execution_facts(publication, document["source_id"]),
        }
    )
    assert serialize_contract(bundle.publication) == original
    assert [item.card_id for item in bundle.facts] == [item.card_id for item in publication.definitions]

    oversized = Publication.model_validate(document["large_publication"])
    assert len(oversized.definitions) == 129
    try:
        PublicationBundle.model_validate(
            {
                "schema_version": 1,
                "publication": oversized.model_dump(mode="json"),
                "facts": execution_facts(oversized, document["source_id"]),
            }
        )
    except ValidationError as error:
        assert any(item["loc"] == ("facts",) and item["type"] == "too_long" for item in error.errors())
    else:
        raise AssertionError("Learning unexpectedly accepted 129 execution facts")

    snapshot = ComparisonSnapshot.model_validate(document["snapshot"])
    assert snapshot.activation is None
    assert serialize_contract(snapshot.publication) == original
    assert snapshot.routes and any(route.bindings for route in snapshot.routes)
    assert {route.learner_color for route in snapshot.routes} == {"white", "black"}
    assert snapshot.compiled_routes()

    # The ordinary Content identities work above; the legacy opening boundary
    # has a separate, deliberately narrower identity contract.
    definition = next(item for item in publication.definitions if item.specification.type == "move_line")
    legacy = {
        "schema_version": 1,
        "publication": {**publication.model_dump(mode="json"), "definitions": [definition.model_dump(mode="json")]},
        "facts": [{"card_id": definition.card_id, "content_type": "opening", "memberships": ["legacy-fixture"],
                   "trained_color": "white" if definition.starting_fen.split()[1] == "w" else "black",
                   "due_date": "2026-10-16", "route_order": 0}],
    }
    hashed = legacy_card_id(definition.starting_fen, definition.specification.accepted_lines[0])
    assert hashed != definition.card_id
    try:
        PublicationBundle.model_validate(legacy)
    except ValidationError as error:
        assert "Card identity differs from executable definition" in str(error)
    else:
        raise AssertionError("Legacy opening unexpectedly accepted a source-derived card identity")
    compatible_legacy = deepcopy(legacy)
    compatible_legacy["publication"]["definitions"][0]["card_id"] = hashed
    compatible_legacy["facts"][0]["card_id"] = hashed
    PublicationBundle.model_validate(compatible_legacy)

    for module_name in ("tempo_learning_runtime", "tempo_game_evidence", "tempo_content_publication_contracts"):
        module = sys.modules[module_name]
        assert "site-packages" in Path(module.__file__).resolve().parts, module_name
    assert not any(name == "backend" or name.startswith("backend.") for name in sys.modules)
    return {
        "content_valid": True,
        "exercise_kinds": sorted(expected_kinds),
        "learning_study_bundle": "compatible",
        "identities_preserved": True,
        "large_publication_definitions": 129,
        "learning_large_bundle": "learning_bundle_definition_limit_exceeded",
        "learning_definition_limit": 128,
        "legacy_opening_identity": "learning_legacy_opening_card_identity_incompatible",
        "legacy_opening_hash_fixture": "compatible",
        "game_evidence_snapshot": "compatible",
        "activation": None,
        "learning_database_used": False,
    }


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: consumer_probe.py CONTENT_EXPORT.json")
    print(json.dumps(probe(json.loads(Path(sys.argv[1]).read_text())), sort_keys=True))
