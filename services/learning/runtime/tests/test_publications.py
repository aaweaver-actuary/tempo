from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Event
import hashlib, json, uuid
import chess, psycopg, pytest
from fastapi import HTTPException
from psycopg import sql
from psycopg.conninfo import make_conninfo
from tempo_chess_core import card_id
from tempo_content_publication_contracts.v1 import Publication
from tempo_learning_runtime.frozen_maia_math import content_fingerprint
from tempo_learning_runtime import gateway, publications
from tempo_learning_database.connections import connection
from concurrency_helpers import extend_gate_budget, wait_for_lock
from test_runtime import activate, bundle, head, review_payload, submit


def pre_retirement_publication_document(payload):
    """Frozen c63c8947 ordinary-fact wire shape, independent of ExecutionFacts."""
    facts = []
    for fact in payload["facts"]:
        facts.append({
            "card_id": fact["card_id"],
            "content_type": fact["content_type"],
            "memberships": fact.get("memberships", []),
            "trained_color": fact.get("trained_color"),
            "due_date": fact["due_date"],
            "parent_card_id": fact.get("parent_card_id"),
            "route_order": fact.get("route_order", 0),
            "integrity_allowed": fact.get("integrity_allowed", True),
            "legacy_specification": fact.get("legacy_specification"),
            "study_id": fact.get("study_id"),
            "exercise_id": fact.get("exercise_id"),
            "sibling_group": fact.get("sibling_group"),
            "source_ref": fact.get("source_ref"),
            "source_fen": fact.get("source_fen"),
            "endgame": None,
            "defense": fact.get("defense"),
        })
    return {
        "schema_version": payload["schema_version"],
        "publication": Publication.model_validate(
            payload["publication"]
        ).model_dump(mode="json"),
        "facts": facts,
        "repertoire_limits": payload.get("repertoire_limits", {}),
        "frozen_order": payload.get("frozen_order"),
    }


def seed_pre_retirement_publication(case, payload, encoded):
    publication = payload["publication"]
    fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    case.query(
        "INSERT INTO learning.accepted_publications"
        "(publication_id,generation,digest,payload_json) "
        "VALUES(%s,%s,%s,%s)",
        (
            publication["publication_id"],
            publication["publication_generation"],
            fingerprint,
            encoded,
        ),
    )
    return fingerprint


@pytest.mark.parametrize("family", ["opening", "tactic"])
def test_pre_retirement_v1_stage_replay_preserves_digest(case, family):
    payload = bundle([(family, {
        "type": "move_line", "accepted_lines": [["e2e4"]],
        "mode": "single", "grading_policy": "reference",
    })])
    payload["publication"]["built_at"] = "2026-10-29T12:00:00+00:00"
    for fact in payload["facts"]:
        fact["due_date"] = "2026-10-29"
    document = pre_retirement_publication_document(payload)
    for fact in document["facts"]:
        assert "endgame" in fact and fact["endgame"] is None
        keys = list(fact)
        assert keys.index("endgame") + 1 == keys.index("defense")
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    fingerprint = seed_pre_retirement_publication(case, payload, encoded)
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications WHERE state='staged'")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    response = stage(case, payload)
    assert response.status_code == 200, response.text
    replay = stage(case, payload)
    assert replay.status_code == 200, replay.text
    assert replay.json() == response.json()
    accepted = case.query(
        "SELECT digest,payload_json FROM learning.accepted_publications "
        "WHERE publication_id=%s AND generation=%s",
        (payload["publication"]["publication_id"], payload["publication"]["publication_generation"]),
    )
    assert [tuple(row) for row in accepted] == [(fingerprint, encoded)]
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0


@pytest.mark.parametrize("family", ["opening", "tactic"])
def test_pre_retirement_v1_staged_publication_activates_and_replays(case, family):
    payload = bundle([(family, {
        "type": "move_line", "accepted_lines": [["e2e4"]],
        "mode": "single", "grading_policy": "reference",
    })])
    payload["publication"]["built_at"] = "2026-10-29T12:00:00+00:00"
    for fact in payload["facts"]:
        fact["due_date"] = "2026-10-29"
    document = pre_retirement_publication_document(payload)
    for fact in document["facts"]:
        assert "endgame" in fact and fact["endgame"] is None
        keys = list(fact)
        assert keys.index("endgame") + 1 == keys.index("defense")
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    fingerprint = seed_pre_retirement_publication(case, payload, encoded)
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications WHERE state='staged'")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 1
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    request = activation(payload)
    response = publication_command(case, "activate", request)
    assert response.status_code == 200, response.text
    replay = publication_command(case, "activate", request)
    assert replay.status_code == 200, replay.text
    assert replay.json() == response.json()
    assert [tuple(row) for row in case.query("SELECT id,content_type FROM learning.cards")] == [
        (payload["facts"][0]["card_id"], family)
    ]
    assert case.query("SELECT COUNT(*) FROM learning.activation_receipts")[0][0] == 1
    operation = case.client.get(
        "/internal/publications/operations/" + request["activation_id"],
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )
    assert operation.status_code == 200, operation.text
    assert operation.json()["state"] == "complete"
    assert operation.json()["response"] == response.json()
    accepted = case.query(
        "SELECT digest,payload_json FROM learning.accepted_publications "
        "WHERE publication_id=%s AND generation=%s",
        (payload["publication"]["publication_id"], payload["publication"]["publication_generation"]),
    )
    assert [tuple(row) for row in accepted] == [(fingerprint, encoded)]
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 1


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
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "domain": "credentials",
            "reason": "workspace_binding_mismatch",
        }
    with TestClient(
        create_app(
            replace(case.configuration, database_url=case.admin, reader_url=case.admin)
        )
    ) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        assert response.json() == {
            "status": "unavailable",
            "domain": "credentials",
            "reason": "restricted_role_required",
        }
    with psycopg.connect(case.admin) as database:
        database.execute(
            "UPDATE learning.migration_history SET checksum='changed' WHERE id=(SELECT MIN(id) FROM learning.migration_history)"
        )
    response = case.client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "domain": "schema",
        "reason": "migration_history_outdated",
    }


def test_readiness_requires_explicit_owner_migration_and_never_runs_it_implicitly(case):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from tempo_learning_runtime import create_app

    name = "learning_" + uuid.uuid4().hex[:12]
    owner = name + "_owner"
    writer = name + "_writer"
    reader = name + "_reader"
    password = uuid.uuid4().hex
    writer_url = make_conninfo(case.admin, dbname=name, user=writer, password=password)
    reader_url = make_conninfo(case.admin, dbname=name, user=reader, password=password)
    with psycopg.connect(case.admin, autocommit=True) as database:
        database.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(owner))
        )
        for role in [writer, reader]:
            database.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOINHERIT PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        database.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only=on").format(
                sql.Identifier(reader)
            )
        )
        database.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(owner)
            )
        )
        for role in [writer, reader]:
            database.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(name), sql.Identifier(role)
                )
            )
    try:
        with TestClient(
            create_app(
                replace(
                    case.configuration,
                    database_url=writer_url,
                    reader_url=reader_url,
                )
            )
        ) as client:
            response = client.get("/health/ready")
            assert response.status_code == 503
            assert response.json() == {
                "status": "unavailable",
                "domain": "schema",
                "reason": "migration_state_unavailable",
            }
        with psycopg.connect(writer_url) as database:
            assert database.execute(
                "SELECT to_regclass('learning.migration_history')"
            ).fetchone()[0] is None
            assert database.execute(
                "SELECT to_regclass('learning.instance')"
            ).fetchone()[0] is None
    finally:
        with psycopg.connect(case.admin, autocommit=True) as database:
            database.execute(
                sql.SQL("DROP DATABASE {}").format(sql.Identifier(name))
            )
            for role in [writer, reader, owner]:
                database.execute(
                    sql.SQL("DROP ROLE {}").format(sql.Identifier(role))
                )


def test_readiness_rejects_writer_only_privileged_credentials(case):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from tempo_learning_runtime import create_app

    with TestClient(
        create_app(replace(case.configuration, database_url=case.admin))
    ) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "domain": "credentials",
        "reason": "restricted_role_required",
    }


def test_readiness_rejects_unavailable_writer_with_healthy_reader(case):
    from dataclasses import replace
    from fastapi.testclient import TestClient
    from tempo_learning_runtime import create_app

    missing_database = "learning_missing_" + uuid.uuid4().hex[:12]
    with psycopg.connect(case.admin) as database:
        assert (
            database.execute(
                "SELECT 1 FROM pg_database WHERE datname=%s", (missing_database,)
            ).fetchone()
            is None
        )
    with TestClient(
        create_app(
            replace(
                case.configuration,
                database_url=make_conninfo(case.admin, dbname=missing_database),
            )
        )
    ) as client:
        response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "domain": "database",
        "reason": "connection_unavailable",
    }


def test_readiness_is_data_preserving_for_both_pools(case):
    def readiness_snapshot():
        return {
            "cards": [
                tuple(row)
                for row in case.query(
                    "SELECT id,revision,serving_active,archived,state,due_date,repetitions,interval_days FROM learning.cards ORDER BY id"
                )
            ],
            "queue": [
                tuple(row)
                for row in case.query(
                    "SELECT id,card_id,queue_date,cycle,position,status,attempt_failed FROM learning.daily_queue ORDER BY id"
                )
            ],
            "reviews": [
                tuple(row)
                for row in case.query(
                    "SELECT id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref FROM learning.reviews ORDER BY id"
                )
            ],
            "receipts": [
                tuple(row)
                for row in case.query(
                    "SELECT operation_id,command_name,state,request_hash,payload_json,response_json,error_json FROM learning.operation_receipts ORDER BY operation_id"
                )
            ],
            "review_receipts": [
                tuple(row)
                for row in case.query(
                    "SELECT attempt_id,card_id,queue_entry_id,outcome,guided,completed_at,review_id,scheduling_status,warning,result_json FROM learning.review_attempt_receipts ORDER BY attempt_id"
                )
            ],
            "instance": tuple(
                case.query(
                    "SELECT i.workspace_id,s.timezone FROM learning.instance i JOIN learning.settings s ON s.id=i.id WHERE i.id=1"
                )[0]
            ),
            "migrations": [
                tuple(row)
                for row in case.query(
                    "SELECT id,checksum FROM learning.migration_history ORDER BY id"
                )
            ],
        }

    before = readiness_snapshot()
    for _ in range(3):
        response = case.client.get("/health/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "ready",
            "workspace_id": case.configuration.workspace_id,
        }
    assert readiness_snapshot() == before


def opening_bundle(moves=("e2e4", "d2d4"), identity="publication-a", generation=1):
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
            for move in moves
        ]
    )
    payload["publication"].update(
        publication_id=identity, publication_generation=generation
    )
    for definition in payload["publication"]["definitions"]:
        definition.update(publication_id=identity, publication_generation=generation)
    return payload


def activation(payload, expected=0):
    return {
        "publication_id": payload["publication"]["publication_id"],
        "publication_generation": payload["publication"]["publication_generation"],
        "expected_activation_revision": expected,
        "activation_id": uuid.uuid4().hex,
    }


def publication_command(case, action, request):
    return case.client.post(
        "/internal/publications/" + action,
        json=request,
        headers={"Authorization": "Bearer " + case.configuration.operator_token},
    )


@pytest.mark.parametrize("omitted", [False, True])
def test_duplicate_opening_route_positions_are_rejected_before_staging(case, omitted):
    payload = opening_bundle()
    for fact in payload["facts"]:
        if omitted:
            fact.pop("route_order")
        else:
            fact["route_order"] = 0
    response = stage(case, payload)
    assert response.status_code == 422 and "route positions" in response.text
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 0
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0


@pytest.mark.parametrize("separate_repertoires", [False, True])
def test_every_valid_opening_membership_has_an_executable_route(
    case, separate_repertoires
):
    payload = opening_bundle()
    if separate_repertoires:
        payload["facts"][1].update(memberships=["repertoire-b"], route_order=0)
    else:
        payload["facts"][0]["memberships"].append("repertoire-b")
    assert stage(case, payload).status_code == 200
    assert stage(case, payload).status_code == 200
    assert publication_command(case, "activate", activation(payload)).status_code == 200
    expected = {
        (membership, fact["route_order"], fact["card_id"])
        for fact in payload["facts"]
        for membership in fact["memberships"]
    }
    actual = {
        tuple(row)
        for row in case.query(
            "SELECT repertoire_id,decision_index,card_id FROM learning.opening_graph_steps"
        )
    }
    assert actual == expected
    first = head(case)
    assert submit(case, first, review_payload(first)).status_code == 200
    second = head(case)
    assert {first["id"], second["id"]} == {fact["card_id"] for fact in payload["facts"]}
    assert first["opening_decision_manifest"] and second["opening_decision_manifest"]


@pytest.mark.parametrize(
    "lines,trained_color,valid",
    [
        ([["e2e4", "e7e5"], ["d2d4"]], "white", False),
        ([["d2d4"], ["e2e4", "e7e5"]], "white", False),
        ([["e2e4"], ["d2d4"]], "white", True),
        ([["e2e4", "e7e5"]], "white", False),
        ([["e2e4"], ["e2e5"]], "white", False),
        ([["e2e4", "e7e5"], ["d2d4", "d7d5"]], "black", True),
        ([["e2e4"], ["d2d4", "d7d5"]], "black", False),
    ],
)
def test_every_opening_line_finishes_on_the_trained_learner_move(
    case, lines, trained_color, valid
):
    payload = opening_bundle(moves=("e2e4",))
    definition = payload["publication"]["definitions"][0]
    definition["specification"].update(accepted_lines=lines, mode="stepwise_line")
    identifier = card_id(chess.STARTING_FEN, lines[0])
    definition["card_id"] = identifier
    payload["facts"][0].update(card_id=identifier, trained_color=trained_color)
    response = stage(case, payload)
    assert response.status_code == (200 if valid else 422), response.text
    if valid:
        assert (
            publication_command(case, "activate", activation(payload)).status_code
            == 200
        )
        presented = head(case)
        assert presented["id"] == identifier
        stored_moves = case.query(
            "SELECT moves_json FROM learning.cards WHERE id=%s", (identifier,)
        )[0][0]
        assert json.loads(stored_moves) == lines[0]
        assert presented["opening_decision_manifest"]["card_id"] == identifier
    else:
        assert (
            "learner move" in response.text
            or "Illegal executable line" in response.text
        )
        assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0


def test_duplicate_memberships_are_rejected_during_staging(case):
    payload = opening_bundle(moves=("e2e4",))
    payload["facts"][0]["memberships"] *= 2
    response = stage(case, payload)
    assert response.status_code == 422 and "memberships must be unique" in response.text
    assert case.query("SELECT COUNT(*) FROM learning.accepted_publications")[0][0] == 0


def test_old_staged_duplicate_memberships_record_replayable_terminal_failure(case):
    payload = opening_bundle(moves=("e2e4",))
    assert stage(case, payload).status_code == 200
    payload["facts"][0]["memberships"] *= 2
    case.query(
        "UPDATE learning.accepted_publications SET payload_json=%s",
        (json.dumps(payload),),
    )
    request = activation(payload)
    failed = publication_command(case, "activate", request)
    assert failed.status_code == 409 and "memberships must be unique" in failed.text
    assert publication_command(case, "activate", request).json() == failed.json()
    with case.database.scope():
        receipt = gateway.read_operation(request["activation_id"])
    assert receipt["state"] == "failed"
    assert all(
        case.query(f"SELECT COUNT(*) FROM learning.{table}")[0][0] == 0
        for table in (
            "cards",
            "repertoires",
            "repertoire_cards",
            "opening_graph_steps",
            "activation_receipts",
            "background_tasks",
        )
    )


def test_graph_completeness_failure_rolls_back_activation(case, monkeypatch):
    from tempo_learning_database.adapters.postgres_store import PostgresConnection

    payload = opening_bundle()
    assert stage(case, payload).status_code == 200
    execute = PostgresConnection.execute

    def lose_one_route(self, statement, parameters=()):
        if (
            statement.startswith("INSERT INTO opening_graph_steps")
            and parameters[3] == 1
        ):
            return self.execute_native("SELECT 1")
        return execute(self, statement, parameters)

    monkeypatch.setattr(PostgresConnection, "execute", lose_one_route)
    response = publication_command(case, "activate", activation(payload))
    assert response.status_code == 409 and "graph does not match" in response.text
    assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    assert case.query("SELECT COUNT(*) FROM learning.opening_graph_steps")[0][0] == 0


def run_publication_transaction(case, action, request, started, backend):
    try:
        with case.database.scope(), connection() as database:
            extend_gate_budget(database)
            backend.append(
                database.execute_native("SELECT pg_backend_pid()").fetchone()[0]
            )
            started.set()
            result = getattr(publications, action)(database, request)
        return 200, result
    except HTTPException as error:
        return error.status_code, error.detail


def test_concurrent_publications_cannot_share_an_opening_repertoire(case):
    payloads = [
        opening_bundle((move,), identity, generation)
        for move, identity, generation in (
            ("e2e4", "publication-a", 1),
            ("d2d4", "publication-b", 2),
        )
    ]
    for payload in payloads:
        assert stage(case, payload).status_code == 200
    case.query(
        "INSERT INTO learning.repertoires(id,name,source_name,created_at) VALUES('repertoire-a','gate','test',NOW()::text)"
    )
    requests = [activation(payload) for payload in payloads]
    started = [Event(), Event()]
    backends = [[], []]
    with ThreadPoolExecutor(2) as executor:
        with psycopg.connect(case.admin) as blocker:
            blocker.execute(
                "SELECT id FROM learning.repertoires WHERE id='repertoire-a' FOR UPDATE"
            )
            futures = [
                executor.submit(
                    run_publication_transaction,
                    case,
                    "activate",
                    request,
                    event,
                    backend,
                )
                for request, event, backend in zip(requests, started, backends)
            ]
            for event, backend in zip(started, backends):
                assert event.wait(3)
                wait_for_lock(case, backend[0])
        results = [future.result() for future in futures]
    assert sorted(result[0] for result in results) == [200, 409], results
    winner = next(index for index, result in enumerate(results) if result[0] == 200)
    loser = 1 - winner
    owner = payloads[winner]["publication"]
    assert [
        tuple(row)
        for row in case.query(
            "SELECT publication_id FROM learning.cards WHERE serving_active=1"
        )
    ] == [(owner["publication_id"],)]
    assert (
        case.query("SELECT generation FROM learning.opening_graph_publications")[0][0]
        == owner["publication_generation"]
    )
    assert [
        tuple(row)
        for row in case.query("SELECT card_id FROM learning.opening_graph_steps")
    ] == [(payloads[winner]["facts"][0]["card_id"],)]
    assert case.query("SELECT COUNT(*) FROM learning.daily_queue")[0][0] == 0
    assert case.query("SELECT COUNT(*) FROM learning.background_tasks")[0][0] == 1
    failed = publication_command(case, "activate", requests[loser])
    assert failed.status_code == 409 and "active publication owner" in failed.text
    assert (
        publication_command(case, "activate", requests[loser]).json() == failed.json()
    )
    with case.database.scope():
        assert (
            gateway.read_operation(requests[loser]["activation_id"])["state"]
            == "failed"
        )
    retired = publication_command(
        case,
        "retire",
        {
            "publication_id": owner["publication_id"],
            "publication_generation": owner["publication_generation"],
            "expected_activation_revision": 1,
            "retirement_id": uuid.uuid4().hex,
        },
    )
    assert retired.status_code == 200, retired.text
    assert (
        publication_command(case, "activate", activation(payloads[loser])).status_code
        == 200
    )


def test_concurrent_retirement_and_successor_activation_use_the_same_lock_order(case):
    old = opening_bundle(("e2e4",))
    activate(case, old)
    successor = opening_bundle(("d2d4",), "successor", 2)
    assert stage(case, successor).status_code == 200
    retirement = {
        "publication_id": "publication-a",
        "publication_generation": 1,
        "expected_activation_revision": 1,
        "retirement_id": uuid.uuid4().hex,
    }
    started = [Event(), Event()]
    backends = [[], []]
    with ThreadPoolExecutor(2) as executor:
        with psycopg.connect(case.admin) as blocker:
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('learning:repertoire-owner:repertoire-a',0))"
            )
            retiring = executor.submit(
                run_publication_transaction,
                case,
                "retire",
                retirement,
                started[0],
                backends[0],
            )
            assert started[0].wait(3)
            wait_for_lock(case, backends[0][0])
            activating = executor.submit(
                run_publication_transaction,
                case,
                "activate",
                activation(successor),
                started[1],
                backends[1],
            )
            assert started[1].wait(3)
            wait_for_lock(case, backends[1][0])
        assert retiring.result()[0] == 200
        assert activating.result()[0] == 200
    assert [
        tuple(row)
        for row in case.query(
            "SELECT publication_id FROM learning.cards WHERE serving_active=1"
        )
    ] == [("successor",)]


def test_unrelated_repertoire_activation_is_independent(case):
    payload = opening_bundle(("e2e4",))
    payload["facts"][0]["memberships"] = ["unrelated-repertoire"]
    assert stage(case, payload).status_code == 200
    with psycopg.connect(case.admin) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('learning:repertoire-owner:repertoire-a',0))"
        )
        response = publication_command(case, "activate", activation(payload))
        assert response.status_code == 200, response.text


def test_publication_owner_contention_keeps_original_intent_retryable(case):
    payload = opening_bundle(("e2e4",))
    assert stage(case, payload).status_code == 200
    request = activation(payload)
    with psycopg.connect(case.admin) as blocker:
        blocker.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended('learning:repertoire-owner:repertoire-a',0))"
        )
        response = publication_command(case, "activate", request)
        assert response.status_code == 503, response.text
        with case.database.scope():
            assert gateway.read_operation(request["activation_id"])["state"] == "queued"
        assert case.query("SELECT COUNT(*) FROM learning.cards")[0][0] == 0
    assert publication_command(case, "activate", request).status_code == 200


def test_replacement_locks_removed_repertoires_until_new_ownership_commits(case):
    old = opening_bundle(("e2e4",))
    activate(case, old)
    replacement = opening_bundle(("d2d4",), "publication-a", 2)
    replacement["facts"][0]["memberships"] = ["repertoire-b"]
    successor = opening_bundle(("c2c4",), "successor")
    for payload in (replacement, successor):
        assert stage(case, payload).status_code == 200
    started = [Event(), Event()]
    backends = [[], []]
    with ThreadPoolExecutor(2) as executor:
        with psycopg.connect(case.admin) as blocker:
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('learning:repertoire-owner:repertoire-a',0))"
            )
            replacing = executor.submit(
                run_publication_transaction,
                case,
                "activate",
                activation(replacement, 1),
                started[0],
                backends[0],
            )
            assert started[0].wait(3)
            wait_for_lock(case, backends[0][0])
            activating = executor.submit(
                run_publication_transaction,
                case,
                "activate",
                activation(successor),
                started[1],
                backends[1],
            )
            assert started[1].wait(3)
            wait_for_lock(case, backends[1][0])
        assert replacing.result()[0] == 200
        assert activating.result()[0] == 200
    assert {
        tuple(row)
        for row in case.query(
            "SELECT c.publication_id,rc.repertoire_id FROM learning.cards c JOIN learning.repertoire_cards rc ON rc.card_id=c.id WHERE c.serving_active=1"
        )
    } == {("publication-a", "repertoire-b"), ("successor", "repertoire-a")}
