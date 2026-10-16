from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event
import hashlib, json
import chess
from tempo_chess_core import canonical_fen_key
from tempo_learning_runtime.remediation_adapter import BorrowedConnection
from tempo_learning_runtime.admission import lock_card
from tempo_learning_runtime.maintenance import run_once
import psycopg, pytest
from fastapi import HTTPException
from tempo_game_evidence_contracts.v1 import EvidenceAcknowledgement
from tempo_learning_runtime import receivers
from tempo_learning_runtime.database import connection
from tempo_learning_runtime.migrate import migrate, grant_runtime, expected_migrations
from psycopg.conninfo import make_conninfo, conninfo_to_dict
from test_runtime import activate, head, review_payload, submit
from concurrency_helpers import extend_gate_budget, wait_for_lock


def delivery(card_id, revision=1, transport=None, obligations=True):
    return {
        "contract_version": 1,
        "workspace_id": "workspace-a",
        "delivery_id": transport or f"delivery-{revision}",
        "evidence_id": "evidence-a",
        "evidence_revision": revision,
        "game_id": "game-a",
        "game_revision": revision,
        "encounters": [
            {
                "encounter_id": "encounter-a",
                "ply": 0,
                "position_key": canonical_fen_key(chess.STARTING_FEN),
                "decision_id": "decision-0",
                "observed_move": "d2d4",
            }
        ],
        "comparisons": [
            {
                "encounter_id": "encounter-a",
                "card_id": card_id,
                "decision_id": "decision-0",
                "publication_id": "publication-a",
                "publication_generation": 1,
                "expected_moves": ["e2e4"],
                "status": "missed",
            }
        ],
        "misses": [
            {
                "miss_id": "observed-miss",
                "encounter_id": "encounter-a",
                "decision_id": "decision-0",
                "reason": "repertoire_deviation",
            }
        ],
        "obligations": [
            {
                "obligation_id": f"miss-{revision}",
                "source_evidence_id": "evidence-a",
                "source_revision": revision,
                "card_id": card_id,
                "decision_id": "decision-0",
                "publication_generation": 1,
                "kind": "real_game_miss",
            }
        ]
        if obligations
        else [],
    }


def send(case, payload, client=None):
    return (client or case.client).post(
        "/internal/evidence",
        json=payload,
        headers={"Authorization": "Bearer " + case.configuration.evidence_token},
    )


def domain_state(case):
    return {
        table: [
            tuple(row)
            for row in case.query(f"SELECT * FROM learning.{table} ORDER BY 1")
        ]
        for table in (
            "cards",
            "daily_queue",
            "remediation_cards",
            "remediation_source_heads",
            "remediation_game_heads",
            "remediation_contributions",
            "remediation_incident_versions",
            "remediation_obligation_heads",
        )
    }


def drain(case):
    with case.database.scope():
        for _ in range(100):
            if not run_once():
                return
        raise AssertionError("Durable Learning work did not finish")


def studied_card(case):
    activate(case)
    card = head(case)
    assert submit(case, card, review_payload(card)).status_code == 200
    return card


def test_evidence_revisions_are_monotonic_and_original_transport_replays(case):
    card = studied_card(case)
    original = delivery(card["id"])
    first = send(case, original)
    assert first.status_code == 200 and first.json()["status"] == "completed"
    assert send(case, delivery(card["id"], 2)).json()["status"] == "completed"
    before = domain_state(case)
    stale = delivery("obsolete-card", 1, "late-delivery")
    stale["obligations"][0]["obligation_id"] = "obsolete-obligation"
    response = send(case, stale)
    acknowledgement = EvidenceAcknowledgement.model_validate(response.json())
    assert acknowledgement.status == "stale_revision"
    assert acknowledgement.reason and acknowledgement.learning_revision is None
    assert acknowledgement.accepted_obligation_ids == []
    assert domain_state(case) == before
    assert send(case, stale).json() == response.json()
    assert send(case, original).json() == first.json()
    changed = deepcopy(original)
    changed["game_id"] = "another-game"
    assert send(case, changed).status_code == 409
    assert domain_state(case) == before


def test_semantic_retry_does_not_readmit_a_settled_miss(case):
    card = studied_card(case)
    payload = delivery(card["id"])
    accepted = send(case, payload).json()
    drain(case)
    promoted = head(case)
    assert submit(case, promoted, review_payload(promoted)).status_code == 200
    before = domain_state(case)
    replay = {**payload, "delivery_id": "another-transport"}
    response = send(case, replay)
    assert response.status_code == 200
    assert response.json() == {**accepted, "delivery_id": "another-transport"}
    assert domain_state(case) == before
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 2
    conflicting = {**payload, "delivery_id": "conflict", "game_revision": 9}
    assert send(case, conflicting).status_code == 409
    assert domain_state(case) == before


def test_empty_correction_advances_frontier_and_cannot_reintroduce_obligations(case):
    card = studied_card(case)
    assert send(case, delivery(card["id"], 1)).status_code == 200
    assert send(case, delivery(card["id"], 2, obligations=False)).status_code == 200
    before = domain_state(case)
    response = send(case, delivery(card["id"], 1, "delayed-correction"))
    assert response.json()["status"] == "stale_revision"
    assert domain_state(case) == before
    assert (
        case.query("SELECT evidence_revision FROM learning.remediation_source_heads")[
            0
        ][0]
        == 2
    )


def test_evidence_frontier_obligations_queue_and_receipt_rollback_together(
    case, monkeypatch
):
    card = studied_card(case)
    assert send(case, delivery(card["id"], obligations=False)).status_code == 200
    before = domain_state(case)
    execute = BorrowedConnection.execute

    def fail_receipt(self, statement, *args, **kwargs):
        if statement.startswith("INSERT INTO remediation_deliveries"):
            raise HTTPException(
                409, "Injected receipt failure after revision and admission"
            )
        return execute(self, statement, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(BorrowedConnection, "execute", fail_receipt)
        assert send(case, delivery(card["id"], 2)).status_code == 409
    assert domain_state(case) == before
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 1
    assert send(case, delivery(card["id"], 2)).json()["status"] == "completed"


@pytest.mark.parametrize("first_revision", [1, 2])
def test_concurrent_evidence_revisions_serialize_on_durable_frontier(
    case, first_revision
):
    card = studied_card(case)
    owns_identity, release, contender_started = Event(), Event(), Event()
    contender_pid = []

    def first():
        with case.database.scope(), connection() as database:
            extend_gate_budget(database)
            lock_card(database, card["id"])
            owns_identity.set()
            assert release.wait(3)
            return receivers.receive(
                database, delivery(card["id"], first_revision), "workspace-a"
            )

    def second():
        assert owns_identity.wait(3)
        with case.database.scope(), connection() as database:
            extend_gate_budget(database)
            contender_pid.append(
                database.execute_native("SELECT pg_backend_pid()").fetchone()[0]
            )
            contender_started.set()
            return receivers.receive(
                database, delivery(card["id"], 3 - first_revision), "workspace-a"
            )

    with ThreadPoolExecutor(2) as executor:
        winner, contender = executor.submit(first), executor.submit(second)
        try:
            assert contender_started.wait(3)
            wait_for_lock(case, contender_pid[0])
        finally:
            release.set()
        assert winner.result()["status"] == "completed"
        assert contender.result()["status"] == (
            "completed" if first_revision == 1 else "stale_revision"
        )
    assert (
        case.query("SELECT evidence_revision FROM learning.remediation_source_heads")[
            0
        ][0]
        == 2
    )
    revisions = [
        row[0]
        for row in case.query(
            "SELECT evidence_revision FROM learning.remediation_contributions ORDER BY evidence_revision"
        )
    ]
    assert revisions == ([1, 2] if first_revision == 1 else [2])


def test_evidence_frontier_survives_service_restart(case, tmp_path):
    from test_restart import server

    card = studied_card(case)
    with server(case, tmp_path) as (client, process):
        assert (
            send(case, delivery(card["id"], 2, obligations=False), client).status_code
            == 200
        )
    before = domain_state(case)
    with server(case, tmp_path) as (restarted, process):
        assert (
            send(case, delivery(card["id"]), restarted).json()["status"]
            == "stale_revision"
        )
    assert domain_state(case) == before


def test_revision_migration_backfills_legacy_receipts_and_preserves_replay(case):
    from pathlib import Path
    from tempo_game_evidence_contracts.v1 import EvidenceDelivery
    import tempo_learning_runtime.migrate as migrations

    payload = delivery("unused", 4, obligations=False)
    original = EvidenceAcknowledgement(
        contract_version=1,
        workspace_id="workspace-a",
        delivery_id=payload["delivery_id"],
        evidence_id="evidence-a",
        evidence_revision=4,
        status="completed",
        accepted_obligation_ids=[],
        learning_revision=4,
        reason=None,
    ).model_dump(mode="json")
    transport_digest = hashlib.sha256(
        EvidenceDelivery.model_validate(payload).model_dump_json().encode()
    ).hexdigest()
    with psycopg.connect(case.admin) as database:
        database.execute("DROP TABLE learning.accepted_evidence_revisions")
        database.execute(
            "DELETE FROM learning.migration_history WHERE id='learning.runtime/0002'"
        )
        database.execute(
            "INSERT INTO learning.evidence_deliveries VALUES(%s,%s,%s)",
            (payload["delivery_id"], transport_digest, json.dumps(original)),
        )
        # A later transport carrying an older accepted revision must not lower the backfill.
        database.execute(
            "INSERT INTO learning.evidence_deliveries VALUES(%s,%s,%s)",
            (
                "older",
                "legacy",
                json.dumps(
                    {
                        **original,
                        "delivery_id": "older",
                        "evidence_revision": 2,
                        "learning_revision": 2,
                    }
                ),
            ),
        )
    parameters = conninfo_to_dict(case.configuration.database_url)
    writer = parameters["user"]
    reader = conninfo_to_dict(case.configuration.reader_url)["user"]
    owner = writer.removesuffix("_writer") + "_owner"
    owner_url = make_conninfo(case.admin, options="-c role=" + owner)
    migrate(owner_url, "workspace-a", "America/New_York")
    migrate(owner_url, "workspace-a", "America/New_York")
    grant_runtime(owner_url, writer, reader)
    assert case.client.get("/health/ready").status_code == 200
    assert tuple(
        case.query(
            "SELECT evidence_revision,semantic_digest FROM learning.accepted_evidence_revisions"
        )[0]
    ) == (4, None)
    history = dict(
        tuple(row)
        for row in case.query("SELECT id,checksum FROM learning.migration_history")
    )
    assert history == expected_migrations()
    assert (
        history["learning.runtime/0001"]
        == hashlib.sha256(
            (Path(migrations.__file__).parent / "migrations/0001_core.sql").read_bytes()
        ).hexdigest()
    )
    assert send(case, payload).json() == original
    assert send(case, {**payload, "delivery_id": "unverifiable"}).status_code == 409
    assert (
        send(case, delivery("unused", 3, obligations=False)).json()["status"]
        == "stale_revision"
    )
    assert send(case, delivery("unused", 5, obligations=False)).status_code == 200
    assert send(case, payload).json() == original
    with psycopg.connect(case.configuration.reader_url) as reader_database:
        assert (
            reader_database.execute(
                "SELECT evidence_revision FROM learning.remediation_source_heads"
            ).fetchone()[0]
            == 5
        )
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            reader_database.execute("DELETE FROM learning.accepted_evidence_revisions")
    with psycopg.connect(case.configuration.reader_url) as reader_database:
        reader_database.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            reader_database.execute("DELETE FROM learning.accepted_evidence_revisions")


def test_registered_receiver_retains_sole_revision_authority(case, monkeypatch):
    calls = []

    def custom(database, evidence, admission):
        calls.append((database, admission))
        return EvidenceAcknowledgement(
            contract_version=1,
            workspace_id=evidence.workspace_id,
            delivery_id=evidence.delivery_id,
            evidence_id=evidence.evidence_id,
            evidence_revision=evidence.evidence_revision,
            status="completed",
            accepted_obligation_ids=[],
            learning_revision=evidence.evidence_revision,
            reason=None,
        )

    monkeypatch.setattr(receivers, "_receiver", custom)
    assert send(case, delivery("custom-owned", obligations=False)).status_code == 200
    assert len(calls) == 1 and calls[0][1] is receivers.admit
    assert (
        case.query("SELECT COUNT(*) FROM learning.remediation_source_heads")[0][0] == 0
    )


@pytest.mark.parametrize("identity_kind", ["obligation", "delivery"])
def test_cross_evidence_identity_reuse_conflicts_atomically(
    case, monkeypatch, identity_kind
):
    from test_publications import opening_bundle

    if identity_kind == "obligation":
        payload = activate(case, opening_bundle())
        cards = [fact["card_id"] for fact in payload["facts"]]
        first_payload = delivery(cards[0], transport="first-delivery")
        second_payload = delivery(cards[1], transport="second-delivery")
        second_payload["obligations"][0]["source_evidence_id"] = "evidence-b"
        second_payload["encounters"][0].update(
            decision_id="decision-1", observed_move="g1f3"
        )
        second_payload["comparisons"][0].update(
            decision_id="decision-1", expected_moves=["d2d4"]
        )
        second_payload["obligations"][0]["decision_id"] = "decision-1"
        second_payload["misses"][0]["decision_id"] = "decision-1"

        gated_insert = "INSERT INTO remediation_contributions"
    else:
        first_payload = delivery(
            "unused", transport="shared-delivery", obligations=False
        )
        second_payload = deepcopy(first_payload)
        gated_insert = "INSERT INTO remediation_deliveries"
    second_payload["evidence_id"] = "evidence-b"
    second_payload["game_id"] = "game-b"
    inserted, release, contender_started = Event(), Event(), Event()
    contender_backend = []
    execute = BorrowedConnection.execute

    def gate_creator_after_insert(self, statement, parameters=()):
        cursor = execute(self, statement, parameters)
        if statement.startswith(gated_insert):
            evidence_id = (
                parameters[3]
                if identity_kind == "obligation"
                else json.loads(parameters[3])["evidence_id"]
            )
            if evidence_id == "evidence-a":
                inserted.set()
                assert release.wait(3)
        return cursor

    monkeypatch.setattr(BorrowedConnection, "execute", gate_creator_after_insert)

    def receive(payload, contender=False):
        try:
            with case.database.scope(), connection() as database:
                extend_gate_budget(database)
                if contender:
                    contender_backend.append(
                        database.execute_native("SELECT pg_backend_pid()").fetchone()[0]
                    )
                    contender_started.set()
                result = receivers.receive(database, payload, "workspace-a")
            return 200, result
        except HTTPException as error:
            return error.status_code, error.detail

    with ThreadPoolExecutor(2) as executor:
        creator = executor.submit(receive, first_payload)
        try:
            assert inserted.wait(3)
            contender = executor.submit(receive, second_payload, True)
            assert contender_started.wait(3)
            wait_for_lock(case, contender_backend[0])
        finally:
            release.set()
        assert creator.result()[0] == 200
        assert contender.result()[0] == 409
    assert [
        tuple(row)
        for row in case.query(
            "SELECT evidence_id FROM learning.remediation_source_heads"
        )
    ] == [("evidence-a",)]
    assert case.query("SELECT COUNT(*) FROM learning.remediation_deliveries")[0][0] == 1
    if identity_kind == "obligation":
        assert [
            tuple(row)
            for row in case.query(
                "SELECT evidence_id,card_id FROM learning.remediation_contributions"
            )
        ] == [("evidence-a", cards[0])]
        assert (
            case.query(
                "SELECT COUNT(*) FROM learning.daily_queue WHERE card_id=%s AND gameplay_priority_reason IS NOT NULL",
                (cards[1],),
            )[0][0]
            == 0
        )
