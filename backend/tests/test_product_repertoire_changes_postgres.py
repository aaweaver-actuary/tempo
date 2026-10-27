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


@pytest.fixture
def retained_database(database, monkeypatch):
    """Small current-content schema; actual canonical content writers, no worker.

    Existing graph enqueue remains an explicit fake owner capability. No live
    settings/engine/queue writer or whole historical DDL migration is exercised.
    """
    import chess
    from app.chess.cards import card_id
    from app.services import postgres_opening_graph, repertoire_integrity
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    connection, _, schema, _, _ = database
    connection.row_factory = tempo_row_factory
    connection.execute("ALTER TABLE repertoires ADD scope_source_revision bigint NOT NULL DEFAULT 0, "
        "ADD canonical_prefix_moves_json text NOT NULL DEFAULT '[]', ADD canonical_prefix_revision bigint NOT NULL DEFAULT 0, "
        "ADD canonical_prefix_preview_id text")
    connection.execute("INSERT INTO repertoires(id) VALUES('other')")
    connection.execute("CREATE TABLE repertoire_lines(id text PRIMARY KEY,repertoire_id text,name text,trained_color text,start_fen text,moves_json text,created_at text)")
    connection.execute("CREATE TABLE repertoire_line_training_depths(line_id text PRIMARY KEY,learner_decision_count bigint)")
    connection.execute("CREATE TABLE cards(id text PRIMARY KEY,repertoire_id text,kind text,content_type text DEFAULT 'opening', "
        "start_fen text,moves_json text,trained_color text,revision bigint DEFAULT 1,archived bigint DEFAULT 0, "
        "superseded_by text,canonical_route_source bigint DEFAULT 1,state text DEFAULT 'review',due_date text, "
        "interval_days bigint DEFAULT 10,repetitions bigint DEFAULT 5,lapses bigint DEFAULT 2,introduced_at text,pending_validation bigint DEFAULT 0)")
    connection.execute("CREATE TABLE repertoire_cards(repertoire_id text,card_id text,canonical_route_source bigint DEFAULT 1,PRIMARY KEY(repertoire_id,card_id))")
    connection.execute("CREATE TABLE reviews(id text PRIMARY KEY,card_id text,result text)")
    connection.execute("CREATE TABLE daily_queue(id text PRIMARY KEY,card_id text,status text,admission_repertoire_id text)")
    connection.execute("CREATE TABLE background_tasks(id text,kind text,deduplication_key text)")
    connection.execute("CREATE TABLE opening_segmentation_state(repertoire_id text PRIMARY KEY,content_version bigint,state text)")
    connection.execute("CREATE TABLE repertoire_integrity_state(repertoire_id text PRIMARY KEY,status text,checked_at text,scan_status text,scan_generation text,scan_completed_sources bigint,scan_total_sources bigint,scan_error text)")
    # Use the actual relevant source-revision trigger, not a hand-rolled counter.
    from pathlib import Path
    migration = Path(__file__).resolve().parents[2] / 'database/tempo_legacy_database/ddl/migrations/033_canonical_prefix.sql'
    triggers = migration.read_text().split('CREATE FUNCTION advance_canonical_prefix_source()', 1)[1]
    triggers = 'CREATE FUNCTION advance_canonical_prefix_source()' + triggers.split('-- Readers must never expose', 1)[0]
    connection.execute(triggers)
    for identifier, moves in [('winner', ['e2e4','e7e5','g1f3']), ('loser', ['d2d4','d7d5','c2c4']), ('fork', ['e2e4','c7c5','g1f3'])]:
        connection.execute("INSERT INTO repertoire_lines VALUES(%s,'rep',%s,'white',%s,%s,'2026-10-26')",
            (identifier, identifier, chess.STARTING_FEN, __import__('json').dumps(moves)))
        connection.execute("INSERT INTO repertoire_line_training_depths VALUES(%s,3)", (identifier,))
    losing = card_id(chess.STARTING_FEN, ['d2d4','d7d5','c2c4'])
    connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,revision,introduced_at) "
        "VALUES(%s,'rep','response',%s,'[\"d2d4\",\"d7d5\",\"c2c4\"]','white',3,'2026-10-01T00:00:00Z')", (losing,chess.STARTING_FEN))
    connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',%s),('other',%s)", (losing,losing))
    connection.execute("INSERT INTO reviews VALUES('history',%s,'again')", (losing,))
    connection.execute("INSERT INTO daily_queue VALUES('other-active',%s,'active','other')", (losing,))
    database_adapter = PostgresConnection(connection)
    graph_requests = []
    monkeypatch.setattr(postgres_opening_graph, 'request_graph_rebuild_in_transaction',
        lambda db, rep, day: graph_requests.append((rep,day)) or {'id':'task-graph','state':'queued'})
    # Existing compatibility helper is public-schema-specific; exercise the same
    # ordered introspection for this isolated task schema, not another writer.
    monkeypatch.setattr(repertoire_integrity, 'card_columns', lambda db: [row[0] for row in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='cards' ORDER BY ordinal_position")])
    yield database_adapter, losing, graph_requests


def retained_reader(database, repertoire_id):
    from app.repertoire.changes.retained import current_snapshot
    return current_snapshot(database,repertoire_id)


def same_retained_source(first, second):
    return (first.membership.revision,first.membership.digest,first.membership.source_revision,first.membership.routes) == (
        second.membership.revision,second.membership.digest,second.membership.source_revision,second.membership.routes)


def test_retained_current_owner_replaces_only_approved_sources_and_preserves_shared_card_history(retained_database):
    import chess
    from app.repertoire.changes.application import conflict_report, manual_resolution
    from app.repertoire.changes.retained import retained_changes
    database, losing, graph_requests = retained_database
    current = retained_reader(database, 'rep')
    proposal = manual_resolution(current,repertoire_id='rep',position=chess.STARTING_FEN.rsplit(' ',2)[0],selected_uci='e2e4',proposal_id='retained-approval')
    assert {item.route_id for item in proposal.edits} == {'line:loser','card:'+losing}
    assert len(proposal.card_changes) == 1 and proposal.card_changes[0].presentation_id == losing
    owner = retained_changes(database,'rep')
    owner.preview(proposal)
    assert not graph_requests
    receipt = owner.accept_change(decision(proposal))
    published = retained_reader(database,'rep')
    assert receipt.source_revision == published.membership.revision > proposal.source_revision
    assert conflict_report(published).clean
    assert owner.accept_change(decision(proposal)) == receipt and len(graph_requests) == 1
    assert database.execute_native("SELECT count(*) FROM reviews WHERE id='history' AND card_id=%s",(losing,)).fetchone()[0] == 1
    assert database.execute_native("SELECT repertoire_id,moves_json,archived FROM cards WHERE id=%s",(losing,)).fetchone()[0] == 'other'
    assert database.execute_native("SELECT count(*) FROM repertoire_cards WHERE repertoire_id='other' AND card_id=%s",(losing,)).fetchone()[0] == 1
    assert database.execute_native("SELECT status FROM daily_queue WHERE id='other-active'").fetchone()[0] == 'active'
    assert database.execute_native("SELECT moves_json FROM repertoire_lines WHERE id='fork'").fetchone()[0] == '["e2e4", "c7c5", "g1f3"]'
    assert same_retained_source(RepertoireSnapshot.model_validate_json(database.execute_native("SELECT before_json FROM product_repertoire_change_receipts").fetchone()[0]),current)


def retained_proposal(database, proposal_id):
    import chess
    from app.repertoire.changes.application import manual_resolution
    return manual_resolution(retained_reader(database,'rep'), repertoire_id='rep',
        position=chess.STARTING_FEN.rsplit(' ',2)[0], selected_uci='e2e4', proposal_id=proposal_id)


def test_retained_owner_requires_displayed_card_definition_and_stale_source_is_rejected(retained_database):
    from test_product_repertoire_changes import rebind
    from app.repertoire.changes.retained import retained_changes
    database, _, graph_requests = retained_database
    owner = retained_changes(database,'rep')
    proposal = rebind(retained_proposal(database,'no-hidden-card'),card_changes=())
    before = retained_reader(database,'rep')
    owner.preview(proposal)
    with pytest.raises(ChangeError,match='displayed_card_change_required'):
        owner.accept_change(decision(proposal))
    assert same_retained_source(retained_reader(database,'rep'),before) and not graph_requests
    valid = retained_proposal(database,'stale-retained-source'); owner.preview(valid)
    database.execute_native("UPDATE repertoire_lines SET name='changed outside preview' WHERE id='winner'")
    with pytest.raises(ChangeError,match='stale_source'):
        owner.accept_change(decision(valid))
    assert database.execute_native("SELECT count(*) FROM product_repertoire_change_receipts").fetchone()[0] == 0


def test_retained_owner_outer_rollback_restores_sources_shared_membership_and_receipt(retained_database):
    from app.repertoire.changes.retained import retained_changes
    database, losing, _ = retained_database
    before = retained_reader(database,'rep')
    proposal = retained_proposal(database,'rollback-retained-source')
    owner = retained_changes(database,'rep'); owner.preview(proposal)
    with pytest.raises(RuntimeError,match='abort enclosing owner command'):
        with database.raw.transaction():
            receipt = owner.accept_change(decision(proposal))
            assert receipt.status == 'accepted'
            raise RuntimeError('abort enclosing owner command')
    assert same_retained_source(retained_reader(database,'rep'),before)
    assert database.execute_native("SELECT repertoire_id FROM cards WHERE id=%s",(losing,)).fetchone()[0] == 'rep'
    assert database.execute_native("SELECT count(*) FROM repertoire_cards WHERE card_id=%s",(losing,)).fetchone()[0] == 2
    assert database.execute_native("SELECT count(*) FROM reviews WHERE id='history'").fetchone()[0] == 1
    assert database.execute_native("SELECT count(*) FROM product_repertoire_change_receipts").fetchone()[0] == 0


def test_retained_public_operation_refuses_an_unenclosed_connection(retained_database):
    from app.repertoire.changes.retained import publish_validated_change
    from app.repertoire.changes.application import validate_proposal
    database, _, graph_requests = retained_database
    current = retained_reader(database,'rep')
    proposal = retained_proposal(database,'no-outer-transaction')
    plan = validate_proposal(current,proposal)
    with pytest.raises(ChangeError,match='borrowed_transaction_required'):
        publish_validated_change(database,plan,current_snapshot=retained_reader)
    assert not graph_requests and same_retained_source(retained_reader(database,'rep'),current)


def test_retained_snapshot_is_stable_bounded_read_without_game_or_clean_integrity(retained_database):
    from app.repertoire.changes.application import conflict_report
    database, losing, graph_requests = retained_database
    initial = retained_reader(database,'rep')
    assert conflict_report(initial).conflicts
    assert {route.route_id for route in initial.membership.routes} == {'line:winner','line:loser','line:fork','card:'+losing}
    mapped = [item for item in initial.decision_presentations if item.presentation_id == losing]
    assert mapped and all(not item.available and not item.introduction_matches_current_content for item in mapped)
    assert all(item.introduction=='introduced' and item.introduced_at=='2026-10-01T00:00:00Z' for item in mapped)
    assert all(item.route_id=='card:'+losing for item in mapped)
    assert not graph_requests
    # Native PostgreSQL enforces read-only execution, including no row-lock writes.
    with database.raw.transaction():
        database.execute_native('SET TRANSACTION READ ONLY')
        later = retained_reader(database,'rep')
    assert same_retained_source(initial,later)
    database.execute_native("UPDATE cards SET introduced_at='2026-10-01' WHERE id=%s",(losing,))
    day_only = retained_reader(database,'rep')
    assert all(item.introduction=='unknown' and item.introduced_at is None for item in day_only.decision_presentations)
    assert day_only.membership.digest != initial.membership.digest


def test_retained_snapshot_invalid_tail_remains_diagnostic_without_mapping(retained_database):
    from app.repertoire.changes.application import conflict_report
    database, losing, _ = retained_database
    database.execute_native("UPDATE cards SET moves_json='[\"d2d4\",\"d7d5\",\"d2d3\"]' WHERE id=%s",(losing,))
    value = retained_reader(database,'rep')
    report = conflict_report(value)
    assert not report.clean and any(item.code=='illegal_move' for item in report.diagnostics)
    assert not value.decision_presentations
