from copy import deepcopy
from datetime import datetime, timezone
import json, uuid
import chess, psycopg
from tempo_chess_core import card_id
from tempo_learning_runtime.frozen_maia_math import content_fingerprint
from test_runtime import activate, bundle, head, review_payload, submit


def stage(case, payload):
    return case.client.post(
        "/internal/publications/stage",
        json=payload,
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )


def activate_generation(case, generation, expected=0):
    return case.client.post(
        "/internal/publications/activate",
        json={
            "publication_id": "publication-a",
            "publication_generation": generation,
            "expected_activation_revision": expected,
            "activation_id": uuid.uuid4().hex,
        },
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )


def test_publication_is_immutable_complete_and_activation_rechecks_latest_generation(
    case,
):
    payload = bundle()
    assert stage(case, payload).status_code == 200
    changed = deepcopy(payload)
    changed["facts"][0]["route_order"] = 8
    assert stage(case, changed).status_code == 409
    incomplete = deepcopy(payload)
    incomplete["facts"] = []
    assert stage(case, incomplete).status_code == 422
    newer = deepcopy(payload)
    newer["publication"]["publication_generation"] = 2
    for definition in newer["publication"]["definitions"]:
        definition["publication_generation"] = 2
        definition["exercise_revision"] = 2
    assert stage(case, newer).status_code == 200
    assert activate_generation(case, 1).status_code == 409
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    assert activate_generation(case, 2).status_code == 200
    assert stage(case, payload).status_code == 409


def test_frozen_ranks_control_introductions_without_score_producers(case):
    payload = bundle(
        [
            (
                "opening",
                {
                    "type": "move_line",
                    "accepted_lines": [[move]],
                    "mode": "single",
                    "grading_policy": "reference",
                },
            )
            for move in ["e2e4", "d2d4"]
        ]
    )
    ranks = []
    for index, (definition, fact) in enumerate(
        zip(payload["publication"]["definitions"], payload["facts"])
    ):
        card = {
            "id": definition["card_id"],
            "revision": 1,
            "start_fen": chess.STARTING_FEN,
            "moves_json": json.dumps(definition["specification"]["accepted_lines"][0]),
            "trained_color": "white",
            "kind": "checkpoint",
            "content_type": "opening",
            "source_ref": None,
            "source_fen": None,
        }
        ranks.append(
            {
                "card_id": card["id"],
                "ordinal": 2 - index,
                "fingerprint": content_fingerprint(card, fact["memberships"]),
            }
        )
    payload["frozen_order"] = {
        "reader_version": "frozen-maia-v2",
        "run_id": "frozen-run-a",
        "ranks": ranks,
    }
    activate(case, payload)
    assert head(case)["id"] == payload["facts"][1]["card_id"]
    assert case.query("SELECT COUNT(*) FROM learning.frozen_opening_ranks")[0][0] == 2


def test_workspace_binding_migration_history_and_admin_credentials_fail_readiness(case):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from tempo_learning_runtime import create_app

    with TestClient(
        create_app(replace(case.configuration, workspace_id="wrong-workspace"))
    ) as client:
        assert client.get("/health/ready").status_code == 503
    with TestClient(
        create_app(
            replace(case.configuration, database_url=case.admin, reader_url=case.admin)
        )
    ) as client:
        assert client.get("/health/ready").status_code == 503
    with psycopg.connect(case.admin) as database:
        database.execute(
            "UPDATE learning.migration_history SET checksum='changed' WHERE id=(SELECT MIN(id) FROM learning.migration_history)"
        )
    assert case.client.get("/health/ready").status_code == 503
