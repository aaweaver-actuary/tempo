"""Real disposable PostgreSQL proof; no legacy/live fixtures or migrations.

Publication is an injected task-only content owner. The adapter/transactions are
real. T20 still owns composing the existing source/presentation publication.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest

from test_product_repertoire_changes import snapshot, resolve_fixture, decision
from app.contracts.product_v02.repertoire import RepertoireSnapshot
from app.repertoire.changes.application import RepertoireChanges, study_eligibility
from app.repertoire.changes.ports import ChangeError
from tempo_legacy_database.adapters.authored_repertoires.product_changes import PostgresChangeStore, SCHEMA_SQL


@pytest.fixture
def database():
    dsn = os.environ.get("TEMPO_492_TEST_DSN")
    if not dsn:
        pytest.fail("TEMPO_492_TEST_DSN must identify the task-owned disposable tempo492 database")
    info = psycopg.conninfo.conninfo_to_dict(dsn)
    if info.get("dbname") != "tempo492" or info.get("host") not in {"127.0.0.1", "localhost"}:
        pytest.fail("refusing non-task PostgreSQL target")
    schema = "task492_" + uuid4().hex
    connection = psycopg.connect(dsn, autocommit=True)
    connection.execute(sql.SQL("CREATE SCHEMA {} ").format(sql.Identifier(schema)))
    connection.execute(sql.SQL("SET search_path TO {} ").format(sql.Identifier(schema)))
    connection.execute("CREATE TABLE repertoires(id text PRIMARY KEY)")
    connection.execute("INSERT INTO repertoires VALUES('rep')")
    connection.execute(SCHEMA_SQL)
    connection.execute("CREATE TABLE task_source(repertoire_id text PRIMARY KEY, snapshot_json text NOT NULL)")
    connection.execute("CREATE TABLE task_publications(proposal_id text PRIMARY KEY, display_digest text NOT NULL)")
    value, proposal = resolve_fixture()
    connection.execute("INSERT INTO task_source VALUES('rep',%s)", (value.model_dump_json(),))
    try:
        yield connection, dsn, schema, value, proposal
    finally:
        connection.execute("SET search_path TO public")
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        connection.close()


def reader(connection, repertoire_id):
    row = connection.execute("SELECT snapshot_json FROM task_source WHERE repertoire_id=%s", (repertoire_id,)).fetchone()
    return RepertoireSnapshot.model_validate_json(row[0])


def publisher(connection, change):
    connection.execute("UPDATE task_source SET snapshot_json=%s WHERE repertoire_id=%s",
                       (change.after.model_dump_json(), change.proposal.repertoire_id))
    connection.execute("INSERT INTO task_publications VALUES(%s,%s)",
                       (change.proposal.proposal_id, change.proposal.display_digest))


def service(connection, publish=publisher):
    return RepertoireChanges(PostgresChangeStore(connection, "rep", reader, publish))


def counts(connection):
    return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                 for table in ("task_publications", "product_repertoire_change_receipts"))


def test_postgres_acceptance_receipt_and_original_evidence_commit_once(database):
    connection, _, _, value, proposal = database
    changes = service(connection); changes.preview(proposal)
    result = changes.accept_change(decision(proposal))
    assert result.status == "accepted" and counts(connection) == (1, 1)
    assert changes.accept_change(decision(proposal)) == result
    before, after = connection.execute("SELECT before_json,after_json FROM product_repertoire_change_receipts").fetchone()
    assert RepertoireSnapshot.model_validate_json(before) == value
    assert RepertoireSnapshot.model_validate_json(after) == reader(connection, "rep")
    assert counts(connection) == (1, 1)


def test_postgres_publication_failure_rolls_back_content_receipt_and_effect(database):
    connection, _, _, value, proposal = database
    def fail(connection, change):
        publisher(connection, change)
        raise RuntimeError("task owner failed after writes")
    changes = service(connection, fail); changes.preview(proposal)
    with pytest.raises(RuntimeError, match="after writes"): changes.accept_change(decision(proposal))
    assert reader(connection, "rep") == value and counts(connection) == (0, 0)
    assert service(connection).accept_change(decision(proposal)).status == "accepted"


def test_postgres_borrows_outer_transaction_and_rollback_includes_receipt(database):
    connection, _, _, value, proposal = database
    changes = service(connection); changes.preview(proposal)
    with pytest.raises(RuntimeError, match="outer rollback"):
        with connection.transaction():
            changes.accept_change(decision(proposal))
            assert counts(connection) == (1, 1)
            raise RuntimeError("outer rollback")
    assert reader(connection, "rep") == value and counts(connection) == (0, 0)


def test_postgres_revision_race_rejects_stale_approval(database):
    connection, dsn, schema, value, proposal = database
    other = proposal.model_copy(update={"proposal_id": "other-preview"})
    other = other.model_copy(update={"display_digest": other.content_digest()})
    service(connection).preview(proposal); service(connection).preview(other)
    def accept(item):
        with psycopg.connect(dsn, autocommit=True) as candidate:
            candidate.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            try: return service(candidate).accept_change(decision(item)).status
            except ChangeError as error: return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, (proposal, other)))
    assert sorted(results) == ["accepted", "stale_source"]
    assert counts(connection) == (1, 1)


def test_postgres_committed_eligibility_is_current_and_other_membership_survives(database):
    connection, dsn, schema, value, proposal = database
    service(connection).preview(proposal)
    with psycopg.connect(dsn, autocommit=True) as observer:
        observer.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        def eligible(source, route="loser", repertoire="rep", revision=None, digest=None):
            return study_eligibility(source, repertoire_id=repertoire, route_id=route, ply=0,
                source_revision=source.membership.source_revision,
                revision=revision or source.membership.revision, digest=digest or source.membership.digest)
        assert eligible(reader(observer, "rep")).reason == "own_response_conflict"
        with connection.transaction():
            service(connection).accept_change(decision(proposal))
            assert eligible(reader(observer, "rep")).reason == "own_response_conflict"
        current = reader(observer, "rep")
        assert eligible(current).eligible
        assert eligible(current, "other", "other").eligible
        assert eligible(current, revision=1, digest=value.membership.digest).reason == "stale_integrity"


def test_postgres_unenlisted_publication_cannot_create_accepted_receipt(database):
    connection, _, _, value, proposal = database
    changes = service(connection, lambda *_: None); changes.preview(proposal)
    with pytest.raises(ChangeError, match="publication_not_enlisted"):
        changes.accept_change(decision(proposal))
    assert counts(connection) == (0, 0) and reader(connection, "rep") == value


def test_postgres_current_source_guard_reads_memberships_without_writing_learning(database):
    from types import SimpleNamespace
    from psycopg.rows import dict_row
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import lock_sources
    from app.repertoire.changes.application import validate_source_rows
    connection, _, _, value, proposal = database
    connection.execute("CREATE TABLE repertoire_lines(id text PRIMARY KEY,repertoire_id text,start_fen text,moves_json text,trained_color text,created_at text)")
    connection.execute("CREATE TABLE cards(id text PRIMARY KEY,repertoire_id text,start_fen text,moves_json text,trained_color text,archived integer,content_type text,superseded_by text)")
    connection.execute("CREATE TABLE repertoire_cards(repertoire_id text,card_id text)")
    import chess
    connection.execute("INSERT INTO repertoire_lines VALUES('line','rep',%s,'[\"e2e4\"]','white','now')", (chess.STARTING_FEN,))
    connection.execute("INSERT INTO cards VALUES('shared','other',%s,'[\"d2d4\"]','white',0,'opening',NULL)", (chess.STARTING_FEN,))
    connection.execute("INSERT INTO repertoire_cards VALUES('rep','shared')")
    def execute_native(statement, params):
        return connection.cursor(row_factory=dict_row).execute(statement, params)
    borrowed = SimpleNamespace(execute_native=execute_native)
    with connection.transaction():
        rows = lock_sources(borrowed, 'rep')
        assert {item['source_id'] for item in rows} == {'line:line', 'card:shared'}
        with pytest.raises(ChangeError, match='pending_resolution'): validate_source_rows('rep', rows)
        validate_source_rows('rep', tuple(item for item in rows if item['source_id'] != 'card:shared'))
    assert connection.execute("SELECT moves_json FROM cards WHERE id='shared'").fetchone()[0] == '["d2d4"]'
    assert connection.execute("SELECT count(*) FROM repertoire_cards").fetchone()[0] == 1
    assert counts(connection) == (0, 0)
