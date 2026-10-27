"""Bounded snapshot/reservation contracts against task-owned PostgreSQL only."""
import json
import os
from pathlib import Path
import uuid

import chess
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
import pytest

from app.repertoire import build_index
from tempo_legacy_database.adapters.authored_repertoires import product_snapshot as storage

DSN = os.environ.get("TEMPO_483_SNAPSHOT_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="task-owned snapshot PostgreSQL DSN required")

DDL = """
CREATE TABLE legacy_learning_identity(id integer PRIMARY KEY,workspace_id text NOT NULL);
CREATE TABLE repertoires(id text PRIMARY KEY,name text,scope_source_revision bigint,
    canonical_prefix_revision bigint,canonical_prefix_moves_json text);
CREATE TABLE opening_graph_publications(repertoire_id text PRIMARY KEY,generation bigint,state text);
CREATE TABLE repertoire_lines(id text PRIMARY KEY,repertoire_id text,name text,trained_color text,
    start_fen text,moves_json text);
CREATE TABLE cards(id text PRIMARY KEY,repertoire_id text,revision bigint,start_fen text,moves_json text,
    trained_color text,content_type text,archived bigint,pending_validation bigint,superseded_by text,
    canonical_route_source bigint,introduced_at text);
CREATE TABLE repertoire_cards(repertoire_id text,card_id text,canonical_route_source bigint,
    PRIMARY KEY(repertoire_id,card_id));
CREATE TABLE opening_graph_steps(repertoire_id text,generation bigint,line_id text,decision_index bigint,
    card_id text,starting_fen text,moves_json text,trained_color text,
    PRIMARY KEY(repertoire_id,generation,line_id,decision_index));
CREATE TABLE repertoire_integrity_card_blocks(repertoire_id text,card_id text);
CREATE TABLE deleted_cards(card_id text PRIMARY KEY);
"""


@pytest.fixture
def database():
    parameters = conninfo_to_dict(DSN)
    if parameters.get("host") not in {"localhost", "127.0.0.1"} or parameters.get("dbname") != "tempo483_snapshot":
        raise RuntimeError("only explicit local task-owned tempo483_snapshot is permitted")
    schema = "snapshot_" + uuid.uuid4().hex
    with psycopg.connect(DSN, autocommit=True, row_factory=dict_row) as setup:
        setup.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        setup.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        setup.execute(DDL)
        moves = json.dumps(["e2e4", "e7e5", "g1f3"])
        setup.execute("INSERT INTO legacy_learning_identity VALUES(1,'fixture')")
        setup.execute("INSERT INTO repertoires VALUES('rep','Synthetic',7,0,'[]')")
        setup.execute("INSERT INTO opening_graph_publications VALUES('rep',3,'ready')")
        setup.execute("INSERT INTO repertoire_lines VALUES('route','rep','Route','white',%s,%s)", (chess.STARTING_FEN,moves))
        setup.execute("INSERT INTO cards VALUES('physical','rep',1,%s,%s,'white','opening',0,0,NULL,1,'2026-10-20T00:00:00Z')", (chess.STARTING_FEN,moves))
        setup.execute("INSERT INTO repertoire_cards VALUES('rep','physical',1)")
        setup.execute("INSERT INTO opening_graph_steps VALUES('rep',3,'route',0,'physical',%s,%s,'white')", (chess.STARTING_FEN,moves))
    try:
        with psycopg.connect(DSN, row_factory=dict_row, options=f"-c search_path={schema} -c statement_timeout=5000") as connection:
            yield connection, schema
    finally:
        with psycopg.connect(DSN, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_current_snapshot_is_one_select_in_a_real_read_only_transaction(database):
    connection, _ = database
    connection.execute("SET TRANSACTION READ ONLY")
    class ReadPort:
        def __init__(self): self.calls = 0
        def execute_native(self, statement, parameters):
            self.calls += 1
            return connection.execute(statement, parameters)
    reader = ReadPort()
    result = storage.current_snapshot(reader, "rep")
    assert reader.calls == 1
    assert connection.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
    assert result.membership.revision == 8 and result.membership.complete
    mapping = build_index(result).routes["rep", "route"][0].binding
    assert mapping.presentation_id == "physical" and mapping.available
    assert mapping.introduced_before_game is None and mapping.introduction_game is None


def test_current_conflicts_are_readable_without_game_or_integrity_authority(database):
    connection, _ = database
    connection.execute("UPDATE opening_graph_publications SET state='failed'")
    connection.execute("INSERT INTO repertoire_lines VALUES('other','rep','Other','white',%s,'[\"d2d4\"]')", (chess.STARTING_FEN,))
    snapshot = storage.current_snapshot(connection, "rep")
    assert snapshot.membership.available and snapshot.membership.complete
    assert len(snapshot.membership.routes) == 2 and not snapshot.decision_presentations
    assert build_index(snapshot).lookup("rep", chess.STARTING_FEN).conflict
    # This fixture deliberately has neither imported_games nor integrity_state.


def test_current_snapshot_revision_and_digest_are_stable_and_borrow_rollback(database):
    connection, _ = database
    before = storage.current_snapshot(connection, "rep")
    assert storage.current_snapshot(connection, "rep").membership.digest == before.membership.digest
    connection.execute("UPDATE repertoires SET scope_source_revision=8 WHERE id='rep'")
    connection.execute("UPDATE cards SET revision=2 WHERE id='physical'")
    changed = storage.current_snapshot(connection, "rep")
    assert changed.membership.revision == 9 and changed.membership.digest != before.membership.digest
    connection.rollback()
    assert storage.current_snapshot(connection, "rep").membership.digest == before.membership.digest


def test_current_snapshot_keeps_blocked_reference_and_day_only_unknown(database):
    connection, _ = database
    connection.execute("UPDATE cards SET pending_validation=1,introduced_at='2026-10-20'")
    mapping = storage.current_snapshot(connection, "rep").decision_presentations[0]
    assert mapping.presentation_id == "physical" and not mapping.available
    assert mapping.introduced_at is None and mapping.introduced_before_game is None
    assert connection.execute("SELECT introduced_at FROM cards").fetchone()["introduced_at"] == "2026-10-20"


def test_current_snapshot_row_sentinel_does_not_certify_a_partial_catalogue(database, monkeypatch):
    connection, _ = database
    connection.execute("INSERT INTO repertoire_lines VALUES('other','rep','Other','white',%s,'[\"d2d4\"]')", (chess.STARTING_FEN,))
    monkeypatch.setattr(storage, "MAX_SOURCE_ROWS", 1)
    with pytest.raises(ValueError, match="exceeds_bound"):
        storage.current_snapshot(connection, "rep")
    assert connection.execute("SELECT count(*) count FROM repertoire_lines").fetchone()["count"] == 2


def test_current_snapshot_oversized_row_is_rejected_before_projection(database):
    connection, _ = database
    connection.execute("UPDATE repertoire_lines SET moves_json=%s", ("x"*65537,))
    with pytest.raises(ValueError, match="exceeds_bound"):
        storage.current_snapshot(connection, "rep")


def test_current_snapshot_has_one_mvcc_view_during_concurrent_source_change(database):
    connection, schema = database
    class ReadPort:
        def execute_native(self, statement, parameters):
            result = connection.execute(statement, parameters)
            with psycopg.connect(DSN, options=f"-c search_path={schema}") as writer:
                writer.execute("UPDATE repertoires SET scope_source_revision=8 WHERE id='rep'")
                writer.execute("UPDATE cards SET revision=2 WHERE id='physical'")
            return result
    before = storage.current_snapshot(ReadPort(), "rep")
    assert before.membership.revision == 8
    assert before.decision_presentations[0].presentation_revision == 1
    after = storage.current_snapshot(connection, "rep")
    assert after.membership.revision == 9 and after.decision_presentations[0].presentation_revision == 2
    assert before.membership.digest != after.membership.digest


def test_current_snapshot_scope_is_parameter_bound(database):
    connection, _ = database
    with pytest.raises(ValueError, match="repertoire_not_found"):
        storage.current_snapshot(connection, "rep' OR true --")
    assert connection.execute("SELECT count(*) count FROM repertoires").fetchone()["count"] == 1


def test_published_snapshot_distinguishes_source_counter_from_graph_authority(database):
    connection, _ = database
    connection.execute("SET TRANSACTION READ ONLY")
    audit = storage.current_snapshot(connection, "rep")
    published = storage.current_published_snapshot(connection, "rep")
    assert audit.membership.revision == 8 and published.membership.revision == 3
    assert published.decision_presentations[0].repertoire_revision == 3
    assert published.membership.scope_id == "publication:rep"
    assert published.membership.digest != audit.membership.digest


@pytest.mark.parametrize("change", ["UPDATE opening_graph_publications SET state='failed'",
    "DELETE FROM opening_graph_publications"])
def test_published_snapshot_refuses_unavailable_authority_without_hiding_audit(database, change):
    connection, _ = database
    connection.execute(change)
    audit = storage.current_snapshot(connection, "rep")
    assert audit.membership.available and audit.membership.revision == 8
    with pytest.raises(ValueError, match="repertoire_publication_unavailable"):
        storage.current_published_snapshot(connection, "rep")


def test_published_snapshot_preserves_authored_freshness_when_generation_is_unchanged(database):
    connection, _ = database
    before = storage.current_published_snapshot(connection, "rep")
    connection.execute("UPDATE repertoires SET scope_source_revision=8")
    after = storage.current_published_snapshot(connection, "rep")
    assert before.membership.revision == after.membership.revision == 3
    assert before.membership.digest != after.membership.digest
    connection.execute("DELETE FROM repertoire_cards")
    assert not storage.current_published_snapshot(connection, "rep").decision_presentations


def prepare_reservation_contract(connection):
    """Same existing FK/reservation facts needed by the source owner, task schema only."""
    connection.execute("""
        CREATE TABLE background_tasks(id text PRIMARY KEY,kind text,deduplication_key text);
        INSERT INTO background_tasks VALUES('graph-task','opening_graph_rebuild','rep');
        ALTER TABLE cards ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE repertoire_lines ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE repertoire_cards ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE repertoire_cards ADD FOREIGN KEY(card_id) REFERENCES cards(id);
        ALTER TABLE opening_graph_publications ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE opening_graph_steps ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE opening_graph_steps ADD FOREIGN KEY(line_id) REFERENCES repertoire_lines(id);
        ALTER TABLE repertoire_integrity_card_blocks ADD COLUMN issue_id text DEFAULT 'fixture';
        ALTER TABLE repertoire_integrity_card_blocks ADD FOREIGN KEY(repertoire_id) REFERENCES repertoires(id);
        ALTER TABLE repertoire_integrity_card_blocks ADD FOREIGN KEY(card_id) REFERENCES cards(id);
    """)
    # Exercise the actual retained source-counter/promotion triggers, not a
    # fabricated epoch substitute. They close same-FK nonopening -> opening
    # transitions that the parent FK reservation alone cannot prevent.
    root = Path(__file__).resolve().parents[2]
    migration = (root / "database/tempo_legacy_database/ddl/migrations/035_canonical_publication_freshness.sql").read_text()
    for declaration in ("CREATE FUNCTION promote_canonical_card_source()",
                        "CREATE OR REPLACE FUNCTION advance_canonical_prefix_source()"):
        start = migration.index(declaration)
        end = migration.index("END $$;", start) + len("END $$;")
        connection.execute(migration[start:end])
    for line in migration.splitlines():
        if line.startswith(("CREATE TRIGGER canonical_card_promote ", "CREATE TRIGGER canonical_line_source ",
                            "CREATE TRIGGER canonical_link_source ", "CREATE TRIGGER canonical_card_source ",
                            "CREATE TRIGGER canonical_card_insert_source ", "CREATE TRIGGER canonical_card_delete_source ")):
            connection.execute(line)
    connection.commit()
    connection.execute("SELECT 1")  # Caller owns an active writer transaction.


def peer_database(schema):
    return psycopg.connect(DSN, row_factory=dict_row,
        options=f"-c search_path={schema} -c lock_timeout=50 -c statement_timeout=1000")


def test_snapshot_reservation_holds_introduction_fact_until_caller_commit(database):
    connection, schema = database
    prepare_reservation_contract(connection)
    before = storage.reserve_current_snapshot(connection, "rep")
    with peer_database(schema) as writer:
        with pytest.raises(psycopg.errors.LockNotAvailable):
            writer.execute("UPDATE cards SET introduced_at='2026-10-21' WHERE id='physical'")
        writer.rollback()
        assert storage.current_snapshot(connection, "rep").membership.digest == before.membership.digest
        connection.commit()
        writer.execute("UPDATE cards SET introduced_at='2026-10-21' WHERE id='physical'")
    assert storage.current_snapshot(connection, "rep").membership.digest != before.membership.digest


@pytest.mark.parametrize("statement", [
    "UPDATE repertoire_lines SET name='changed' WHERE id='route'",
    "INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','additional')",
    "INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id) VALUES('foreign','physical','new')",
    "DELETE FROM repertoire_integrity_card_blocks WHERE card_id='physical'",
    "UPDATE opening_graph_publications SET generation=4 WHERE repertoire_id='rep'",
    "UPDATE cards SET content_type='opening' WHERE id='not-opening'",
    "INSERT INTO cards SELECT 'late',repertoire_id,revision,start_fen,moves_json,trained_color,content_type,archived,pending_validation,superseded_by,canonical_route_source,introduced_at FROM cards WHERE id='physical'",
])
def test_snapshot_reservation_closes_source_membership_and_integrity_phantoms(database, statement):
    connection, schema = database
    prepare_reservation_contract(connection)
    connection.execute("INSERT INTO repertoires VALUES('foreign','Foreign',0,0,'[]')")
    connection.execute("INSERT INTO cards SELECT 'additional',repertoire_id,revision,start_fen,moves_json,trained_color,content_type,archived,pending_validation,superseded_by,canonical_route_source,introduced_at FROM cards WHERE id='physical'")
    connection.execute("INSERT INTO cards SELECT 'not-opening',repertoire_id,revision,start_fen,moves_json,trained_color,'tactic',archived,pending_validation,superseded_by,0,introduced_at FROM cards WHERE id='physical'")
    connection.execute("UPDATE cards SET moves_json='[]' WHERE id='not-opening'")
    connection.execute("INSERT INTO repertoire_integrity_card_blocks VALUES('foreign','physical','existing')")
    connection.commit(); connection.execute("SELECT 1")
    storage.reserve_current_snapshot(connection, "rep")
    with peer_database(schema) as writer:
        with pytest.raises(psycopg.errors.LockNotAvailable): writer.execute(statement)
        writer.rollback()


def test_snapshot_reservation_reuses_permanent_delete_card_edit_guard(database):
    connection, schema = database
    prepare_reservation_contract(connection)
    storage.reserve_current_snapshot(connection, "rep")
    with peer_database(schema) as writer:
        assert writer.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) held",
            ("tempo:card-edit:physical",)).fetchone()["held"] is False
    connection.rollback()
    with peer_database(schema) as writer:
        assert writer.execute("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) held",
            ("tempo:card-edit:physical",)).fetchone()["held"] is True


@pytest.mark.parametrize("statement,parameters", [
    ("SELECT id FROM background_tasks WHERE id='graph-task' FOR UPDATE", ()),
    ("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:opening-graph:rep",)),
    ("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:prefix-transition:reservations",)),
    ("SELECT id FROM cards WHERE id='physical' FOR UPDATE", ()),
])
def test_snapshot_reservation_yields_on_current_owner_contention(database, statement, parameters):
    connection, schema = database
    prepare_reservation_contract(connection)
    with peer_database(schema) as foreground:
        foreground.execute(statement, parameters)
        with pytest.raises(storage.SnapshotReservationUnavailable, match="snapshot_source_busy"):
            storage.reserve_current_snapshot(connection, "rep")
        connection.rollback()  # Caller releases partial reservations after yielding.


def test_snapshot_reservation_does_not_commit_caller_source_changes(database):
    connection, _ = database
    prepare_reservation_contract(connection)
    before = storage.current_snapshot(connection, "rep")
    connection.execute("UPDATE cards SET introduced_at='2026-10-21' WHERE id='physical'")
    assert storage.reserve_current_snapshot(connection, "rep").membership.digest != before.membership.digest
    connection.rollback()
    assert storage.current_snapshot(connection, "rep").membership.digest == before.membership.digest


def test_snapshot_reservation_requires_real_existing_writer_transaction(database):
    connection, _ = database
    prepare_reservation_contract(connection)
    connection.rollback()
    with pytest.raises(storage.SnapshotReservationUnavailable, match="borrowed_writer_transaction_required"):
        storage.reserve_current_snapshot(connection, "rep")
    connection.execute("SET TRANSACTION READ ONLY")
    with pytest.raises(storage.SnapshotReservationUnavailable, match="borrowed_writer_transaction_required"):
        storage.reserve_current_snapshot(connection, "rep")


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
def test_snapshot_reservation_requires_current_read_committed_view(database, isolation):
    connection, _ = database
    prepare_reservation_contract(connection)
    connection.rollback()
    connection.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
    with pytest.raises(storage.SnapshotReservationUnavailable, match="borrowed_read_committed_transaction_required"):
        storage.reserve_current_snapshot(connection, "rep")
