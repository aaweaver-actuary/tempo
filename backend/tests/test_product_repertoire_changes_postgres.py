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


def test_native_retained_import_approval_retry_and_outer_rollback(case, monkeypatch):
    """Existing native fixture + real gateway/importer; no second import engine.

    The real registered explicit handler validates retained approval before its
    sole importer/refresh invocation. This is owner proof, not root/UI assembly.
    """
    from contextlib import contextmanager
    from datetime import date, datetime
    import json
    from app import main, pgn_import_commands, queue_commands
    from app import command_gateway as gateway
    from app.chess.pgn import parse_pgn
    from app.repertoire.changes.imports import retained_imports
    from app.services import redis_admission_gate
    from tempo_legacy_database.adapters import postgres_store
    from test_explicit_pgn_import_postgres import _protected_state
    from test_product_game_lapse import NOW

    class PolicyDate(date):
        @classmethod
        def today(cls): return NOW.date()

    class PolicyDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(main, 'date', PolicyDate)
    # Issuance derives the local Study day from the authoritative UTC clock.
    monkeypatch.setattr(queue_commands, 'datetime', PolicyDateTime)
    monkeypatch.setattr(pgn_import_commands, 'date', PolicyDate)

    @contextmanager
    def transport(*, read_only=False, **_options):
        with case.transaction() as db:
            if read_only: db.execute_native('SET TRANSACTION READ ONLY')
            yield db

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(postgres_store, 'connection', transport)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    assert gateway._handlers['imports.pgn.explicit'] is pgn_import_commands.explicit_pgn_import
    with case.transaction() as db:
        db.execute_native('UPDATE settings SET study_only_mode=1,new_cards_per_day=0,timezone=%s WHERE id=1', ('UTC',))
        db.execute_native("UPDATE repertoires SET is_main=1 WHERE id='white'")
        db.execute_native("UPDATE cards SET trained_color='white' WHERE id=%s", (case.card_id,))
        assert case.service(db).accept_lapse(case.lapse).status == 'applied'
        entry = db.execute_native("SELECT id FROM daily_queue WHERE card_id=%s AND status='queued'",
            (case.card_id,)).fetchone()[0]
    issue = 'retained-protected-' + uuid4().hex
    assert gateway.execute_command(issue, 'queue.issue_head', dict(entry_id=entry,
        card_id=case.card_id, expected_revision=1, queue_date=NOW.date().isoformat())) == {'issued': True}
    operation = 'conflicted-' + uuid4().hex
    name = operation + '.pgn'
    games, lines = parse_pgn('1. e4 c5 (1... e5) 2. Nf3 e6 *')
    original = pgn_import_commands.prepare_import_payload(name, 'black', 2, games, lines)
    original['operation_id'] = operation
    gateway.record_operation_attempt(operation, 'imports.pgn.explicit', original, background=False)
    assert gateway.execute_command(operation, 'imports.pgn.explicit', original) is None
    with case.transaction() as db:
        rejected = dict(db.execute_native('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone())
        assert rejected['state'] == 'failed'
        assert json.loads(rejected['error_json'])['detail']['code'] == 'repertoire_conflict'
        assert db.execute_native('SELECT count(*) FROM repertoires WHERE source_name=%s', (name,)).fetchone()[0] == 0
        protected = _protected_state(db, case.card_id, issue)
        main_before = [tuple(row) for row in db.execute_native('SELECT id,is_main FROM repertoires ORDER BY id').fetchall()]
        _, resolved = parse_pgn('1. e4 c5 2. Nf3 (2. Nc3 e6) e6 *')
        preview = retained_imports(db).preview(operation, tuple(resolved))
    with case.transaction() as db:
        assert retained_imports(db).read(operation).original_payload_json == rejected['payload_json']
    approval = 'approved-' + uuid4().hex
    payload = preview.approval_payload(approval)
    gateway.record_operation_attempt(approval, 'imports.pgn.explicit', payload, background=False)

    @contextmanager
    def roll_back_after_handler(*, read_only=False, **options):
        with transport(read_only=read_only, **options) as db:
            yield db
            raise RuntimeError('owned outer rollback after publication')

    with monkeypatch.context() as rollback:
        rollback.setattr(postgres_store, 'connection', roll_back_after_handler)
        with pytest.raises(RuntimeError, match='outer rollback'):
            gateway.execute_command(approval, 'imports.pgn.explicit', payload)
    with case.transaction() as db:
        assert db.execute_native('SELECT count(*) FROM repertoires WHERE source_name=%s', (name,)).fetchone()[0] == 0
        assert db.execute_native('SELECT state FROM operation_receipts WHERE operation_id=%s', (approval,)).fetchone()[0] != 'complete'
        assert dict(db.execute_native('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()) == rejected
        assert _protected_state(db, case.card_id, issue) == protected
    result = gateway.execute_command(approval, 'imports.pgn.explicit', payload)
    assert result['preparation_state'] == 'pending' and result['cards_admitted_today'] == 0
    assert gateway.execute_command(approval, 'imports.pgn.explicit', payload) == result
    with case.transaction() as db:
        assert dict(db.execute_native('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()) == rejected
        assert _protected_state(db, case.card_id, issue) == protected
        assert db.execute_native('SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1').fetchone()[0] == 1
        assert [(row[0], row[1]) for row in db.execute_native('SELECT id,is_main FROM repertoires ORDER BY id').fetchall()
            if row[0] != result['repertoire_id']] == main_before
        assert db.execute_native('SELECT is_main FROM repertoires WHERE id=%s', (result['repertoire_id'],)).fetchone()[0] == 0
        assert db.execute_native('SELECT count(*) FROM repertoire_lines WHERE repertoire_id=%s', (result['repertoire_id'],)).fetchone()[0] == 2
        stale = retained_imports(db).preview(operation, tuple(resolved))
    # A real intervening owner-source change invalidates an existing-target
    # preview, even though its proposed import would otherwise deduplicate.
    with case.transaction() as db:
        db.execute_native('UPDATE repertoires SET name=%s WHERE id=%s',
            ('changed after displayed preview', result['repertoire_id']))
    stale_id = 'stale-' + uuid4().hex
    stale_payload = stale.approval_payload(stale_id)
    gateway.record_operation_attempt(stale_id, 'imports.pgn.explicit', stale_payload, background=False)
    assert gateway.execute_command(stale_id, 'imports.pgn.explicit', stale_payload) is None
    with case.transaction() as db:
        failed = db.execute_native('SELECT state,error_json FROM operation_receipts WHERE operation_id=%s', (stale_id,)).fetchone()
        assert failed[0] == 'failed' and json.loads(failed[1])['status_code'] == 409
        assert json.loads(failed[1])['detail']['code'] == 'stale_or_unapproved_import_preview'
        assert db.execute_native('SELECT count(*) FROM repertoire_lines WHERE repertoire_id=%s', (result['repertoire_id'],)).fetchone()[0] == 2
        assert dict(db.execute_native('SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()) == rejected
        assert _protected_state(db, case.card_id, issue) == protected


# Reuse the existing capped fixture; collection alone opens no database.
from test_product_game_lapse_postgres import case, owned_postgres  # noqa: E402,F401


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
    from app.services import postgres_opening_graph
    from app.repertoire.changes import retained_mutations
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    connection, _, schema, _, _ = database
    connection.row_factory = tempo_row_factory
    connection.execute("ALTER TABLE repertoires ADD name text, ADD scope_source_revision bigint NOT NULL DEFAULT 0, "
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
    connection.execute("CREATE TABLE legacy_learning_identity(id integer PRIMARY KEY,workspace_id text)")
    connection.execute("INSERT INTO legacy_learning_identity VALUES(1,'task492')")
    connection.execute("CREATE TABLE opening_graph_publications(repertoire_id text PRIMARY KEY,generation bigint,state text)")
    connection.execute("INSERT INTO opening_graph_publications VALUES('rep',3,'ready')")
    connection.execute("CREATE TABLE opening_graph_steps(repertoire_id text,generation bigint,line_id text,decision_index bigint,card_id text,starting_fen text,moves_json text,trained_color text)")
    connection.execute("CREATE TABLE repertoire_integrity_card_blocks(repertoire_id text,card_id text)")
    connection.execute("CREATE TABLE deleted_cards(card_id text PRIMARY KEY)")
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
    connection.execute("INSERT INTO opening_graph_steps VALUES('rep',3,'loser',0,%s,%s,'[\"d2d4\",\"d7d5\",\"c2c4\"]','white')",(losing,chess.STARTING_FEN))
    connection.execute("INSERT INTO repertoire_integrity_card_blocks VALUES('rep',%s)",(losing,))
    connection.execute("INSERT INTO daily_queue VALUES('other-active',%s,'active','other')", (losing,))
    database_adapter = PostgresConnection(connection)
    graph_requests = []
    monkeypatch.setattr(postgres_opening_graph, 'request_graph_rebuild_in_transaction',
        lambda db, rep, day: graph_requests.append((rep,day)) or {'id':'task-graph','state':'queued'})
    # Existing compatibility helper is public-schema-specific; exercise the same
    # ordered introspection for this isolated task schema, not another writer.
    monkeypatch.setattr(retained_mutations, 'card_columns', lambda db: [row[0] for row in connection.execute(
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
    owner = retained_changes(database,'rep')
    preview = owner.preview_resolution(position=chess.STARTING_FEN.rsplit(' ',2)[0],selected_uci='e2e4',proposal_id='retained-approval')
    proposal = preview.proposal
    assert {item.route_id for item in proposal.edits} == {'loser'}
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
    from app.repertoire.changes.retained import current_card_definitions
    return manual_resolution(retained_reader(database,'rep'), repertoire_id='rep',
        position=chess.STARTING_FEN.rsplit(' ',2)[0], selected_uci='e2e4', proposal_id=proposal_id,card_definitions=current_card_definitions(database,'rep'))


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
    from app.repertoire.changes.retained import current_card_definitions
    plan = validate_proposal(current,proposal,card_definitions=current_card_definitions(database,'rep'))
    with pytest.raises(ChangeError,match='borrowed_transaction_required'):
        publish_validated_change(database,plan,current_snapshot=retained_reader)
    assert not graph_requests and same_retained_source(retained_reader(database,'rep'),current)


def test_retained_snapshot_is_stable_bounded_read_without_game_or_clean_integrity(retained_database):
    from app.repertoire.changes.application import conflict_report
    database, losing, graph_requests = retained_database
    initial = retained_reader(database,'rep')
    assert conflict_report(initial).conflicts
    assert {route.route_id for route in initial.membership.routes} == {'winner','loser','fork'}
    mapped = [item for item in initial.decision_presentations if item.presentation_id == losing]
    assert mapped and all(not item.available and not item.introduction_matches_current_content for item in mapped)
    assert all(item.introduction=='introduced' and item.introduced_at=='2026-10-01T00:00:00Z' for item in mapped)
    assert all(item.route_id=='loser' for item in mapped)
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
    database.execute_native("UPDATE repertoire_lines SET moves_json='[\"d2d4\",\"d7d5\",\"d2d3\"]' WHERE id='loser'")
    value = retained_reader(database,'rep')
    report = conflict_report(value)
    assert not report.clean and any(item.code=='illegal_move' for item in report.diagnostics)
    assert not value.decision_presentations


@pytest.mark.parametrize('scenario,prefix', [
    pytest.param('unsupported-canonical-card', (), id='unsupported-canonical-card'),
    pytest.param('shared-direct-owner-card', ('g1f3', 'g8f6'), id='shared-direct-owner-card'),
    pytest.param('legal-repair-replay', ('b1c3', 'b8c6'), id='legal-repair-replay'),
    pytest.param('existing-opposite-color-replacement', ('g2g3', 'g7g6'), id='existing-opposite-color-replacement'),
])
def test_native_mutation_validates_actual_sources_before_receipt(case, monkeypatch, scenario, prefix):
    """Real registered commands, savepoint, source reads and mutation adapters.

    Reuses the capped checked-source-DDL fixture above. Only database transport,
    Redis availability and policy day are injected. Graph enqueue, canonical
    scope, refresh decorator, archival/rewrite/revision and receipts stay real;
    no worker or installed-product qualification is claimed. The validator
    observer delegates unchanged and retains its exceptions.
    """
    from contextlib import contextmanager
    from datetime import date
    import json
    import chess
    from app import card_commands, integrity_repair_commands
    from app import command_gateway as gateway
    from app.chess.cards import card_id
    from app.repertoire.changes.application import validate_source_rows, validate_repair_source_rows
    from app.services import postgres_opening_graph, redis_admission_gate
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import lock_sources
    from test_product_game_lapse import NOW

    class PolicyDate(date):
        @classmethod
        def today(cls):
            return NOW.date()

    @contextmanager
    def transport(*, read_only=False, **_options):
        with case.transaction() as database:
            if read_only:
                database.execute_native('SET TRANSACTION READ ONLY')
            yield database

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(postgres_store, 'connection', transport)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(card_commands, 'date', PolicyDate)
    monkeypatch.setattr(integrity_repair_commands, 'date', PolicyDate)
    assert gateway._handlers['integrity.issue.resolve'] is integrity_repair_commands.resolve_integrity_issue
    assert gateway._handlers['cards.revise'] is card_commands.revise_card

    board = chess.Board()
    for move in prefix:
        board.push_uci(move)
    starting_fen = board.fen()
    assert board.is_valid() and all(chess.Move.from_uci(move) in board.legal_moves for move in ('e2e4', 'd2d4'))
    old_card = card_id(starting_fen, ['e2e4'])
    replacement = card_id(starting_fen, ['d2d4'])
    identity = uuid4().hex
    repertoire = 'actual-source-' + identity
    other = 'other-source-' + identity
    line = 'line-' + identity
    issue = 'issue-' + identity
    operation = 'actual-source-command-' + identity
    day = NOW.date().isoformat()
    old_moves = json.dumps(['e2e4'])
    revised_moves = json.dumps(['d2d4'])
    card_revision = scenario == 'existing-opposite-color-replacement'
    legal = scenario == 'legal-repair-replay'

    # All these rows live in the existing uniquely owned fixture database.
    # Full-table snapshots also retain unrelated fixture history and counters.
    tables = (
        'repertoires', 'repertoire_lines', 'repertoire_line_training_depths',
        'cards', 'repertoire_cards', 'card_revisions', 'reviews', 'daily_queue',
        'queue_attempt_origins', 'study_attempts', 'study_game_feedback',
        'opening_graph_publications', 'opening_graph_steps', 'background_tasks',
        'background_task_events', 'repertoire_integrity_issues',
        'repertoire_integrity_state', 'repertoire_integrity_card_blocks',
        'opening_segmentation_state', 'canonical_prefix_positions',
        'repertoire_game_scope', 'priority_source_epoch',
    )

    def durable_state(database):
        return {table: [dict(row) for row in database.execute_native(
            f'SELECT * FROM {table} AS stored ORDER BY to_jsonb(stored)::text').fetchall()]
            for table in tables}

    def receipt(database):
        return dict(database.execute_native(
            'SELECT * FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone())

    with case.transaction() as database:
        assert database.execute_native('SELECT id FROM cards WHERE id IN (%s,%s)',
            (old_card, replacement)).fetchall() == []
        database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) '
            'VALUES(%s,%s,%s,%s),(%s,%s,%s,%s)',
            (repertoire, repertoire, 'synthetic-native-guard', NOW.isoformat(),
             other, other, 'synthetic-native-guard', NOW.isoformat()))
        database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) '
            'VALUES(%s,%s,%s,\'white\',%s,%s,%s)',
            (line, repertoire, line, starting_fen, revised_moves if card_revision else old_moves, NOW.isoformat()))
        database.execute_native('INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(%s,1)', (line,))
        database.execute_native('INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,state,due_date,revision,introduced_at) '
            'VALUES(%s,%s,\'response\',%s,%s,\'white\',\'mature\',%s,7,%s)',
            (old_card, repertoire, starting_fen, old_moves, day, NOW.isoformat()))
        database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',
            (repertoire, old_card))
        if scenario == 'shared-direct-owner-card':
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',
                (other, old_card))
        if card_revision:
            # The existing replacement keeps its actual facts; copying the old
            # white card into an early forecast cannot establish final validity.
            database.execute_native('INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,content_type,due_date,revision) '
                'VALUES(%s,%s,\'response\',%s,%s,\'black\',\'opening\',%s,11)',
                (replacement, other, starting_fen, revised_moves, day))
        database.execute_native('INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_ref) '
            'VALUES(%s,\'correct\',%s,10,20,%s)', (old_card, NOW.isoformat(), 'original-history:' + identity))
        database.execute_native('INSERT INTO daily_queue(queue_date,card_id,position,status,admission_repertoire_id) '
            'VALUES(%s,%s,1,\'queued\',%s)', (day, old_card, repertoire))
        database.execute_native('INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,fen_key,fen,trained_color,signature,created_at,updated_at) '
            'VALUES(%s,%s,\'multiple_responses\',%s,%s,\'white\',%s,%s,%s)',
            (issue, repertoire, ' '.join(starting_fen.split()[:4]), starting_fen, identity, NOW.isoformat(), NOW.isoformat()))
        database.execute_native('INSERT INTO repertoire_integrity_state(repertoire_id,status) VALUES(%s,\'needs_repair\')', (repertoire,))
        database.execute_native('INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) '
            'VALUES(%s,%s,%s,\'synthetic-generation\',%s)', (repertoire, old_card, issue, NOW.isoformat()))
        graph = postgres_opening_graph.request_graph_rebuild_in_transaction(database, repertoire, day)
        database.execute_native('UPDATE background_tasks SET state=\'complete\' WHERE id=%s', (graph['id'],))
        database.execute_native('INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(%s,%s,%s)',
            (repertoire, graph['generation'], NOW.isoformat()))
        database.execute_native('INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) '
            'VALUES(%s,%s,%s,0,%s,%s,%s,%s,\'white\')',
            (repertoire, graph['generation'], line, old_card, ' '.join(starting_fen.split()[:4]), starting_fen,
             revised_moves if card_revision else old_moves))
        before = durable_state(database)
        history_before = before['reviews']

    validations = []

    def observe_validation(repertoire_id, rows):
        validations.append((repertoire_id, tuple(dict(row) for row in rows)))
        return validate_source_rows(repertoire_id, rows)

    command_module = card_commands if card_revision else integrity_repair_commands
    if card_revision:
        monkeypatch.setattr(command_module, 'validate_source_rows', observe_validation)
    else:
        def observe_repair_validation(repertoire_id, before_rows, after_rows):
            validations.append((repertoire_id, tuple(dict(row) for row in after_rows)))
            return validate_repair_source_rows(repertoire_id, before_rows, after_rows)
        monkeypatch.setattr(command_module, 'validate_repair_source_rows', observe_repair_validation)
    if card_revision:
        command = 'cards.revise'
        payload = dict(card_id=old_card, request=dict(starting_fen=starting_fen,
            moves=['d2d4'], history_mode='preserve', expected_revision=7))
    else:
        command = 'integrity.issue.resolve'
        rewrite = scenario in {'shared-direct-owner-card', 'legal-repair-replay'}
        payload = dict(repertoire_id=repertoire, issue_id=issue, signature=identity,
            changed_lines={line: ['d2d4']}, expected_line_moves={line: old_moves},
            changed_cards={old_card: ['d2d4']} if rewrite else {},
            expected_card_moves={old_card: old_moves} if rewrite else {},
            unsupported_card_ids=[] if rewrite else [old_card])
    gateway.record_operation_attempt(operation, command, payload, background=False)
    result = gateway.execute_command(operation, command, payload)
    assert len(validations) == 2 and all(item[0] == repertoire for item in validations)
    early, actual = (item[1] for item in validations)
    assert {json.loads(row['moves_json'])[0] for row in early} == {'d2d4'}
    if card_revision:
        assert {row['trained_color'] for row in early} == {'white'}
        assert {row['trained_color'] for row in actual} == {'white', 'black'}
        assert next(row for row in actual if row['source_id'] == 'card:' + replacement)['trained_color'] == 'black'
    elif not legal:
        assert {json.loads(row['moves_json'])[0] for row in actual} == {'e2e4', 'd2d4'}
        assert 'card:' + old_card in {row['source_id'] for row in actual}

    with case.transaction() as database:
        saved_receipt = receipt(database)
        assert saved_receipt['command_name'] == command
        assert saved_receipt['request_hash'] == gateway.request_digest(command, payload)
        assert json.loads(saved_receipt['payload_json']) == payload
        if not legal:
            assert result is None and saved_receipt['state'] == 'failed'
            error = json.loads(saved_receipt['error_json'])
            assert error['status_code'] == 409
            assert error['detail'] == ('invalid_source' if card_revision else 'conflict_pending_resolution')
            assert saved_receipt['response_json'] is None
            assert durable_state(database) == before
        else:
            assert result['repertoire_id'] == repertoire and result['issue_id'] == issue
            assert saved_receipt['state'] == 'complete' and json.loads(saved_receipt['response_json']) == result
            current = lock_sources(database, repertoire)
            validate_source_rows(repertoire, current)
            assert {json.loads(row['moves_json'])[0] for row in current} == {'d2d4'}
            assert 'card:' + old_card not in {row['source_id'] for row in current}
            assert tuple(database.execute_native('SELECT archived,superseded_by FROM cards WHERE id=%s',
                (old_card,)).fetchone()) == (1, replacement)
            assert database.execute_native('SELECT count(*) FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s',
                (repertoire, replacement)).fetchone()[0] == 1
            assert [dict(row) for row in database.execute_native('SELECT * FROM reviews AS stored ORDER BY to_jsonb(stored)::text').fetchall()] == history_before
            assert database.execute_native('SELECT generation FROM background_tasks WHERE id=%s',
                (graph['id'],)).fetchone()[0] == graph['generation'] + 1
            committed = durable_state(database)

    if legal:
        assert gateway.execute_command(operation, command, payload) == result
    else:
        with pytest.raises(RuntimeError, match='invalid_source' if card_revision else 'conflict_pending_resolution'):
            gateway.execute_command(operation, command, payload)
    assert len(validations) == 2  # Terminal replay never invokes either handler again.
    with case.transaction() as database:
        assert receipt(database) == saved_receipt
        assert durable_state(database) == (committed if legal else before)
        assert database.execute_native('SELECT count(*) FROM operation_receipts WHERE operation_id=%s',
            (operation,)).fetchone()[0] == 1


def test_native_locked_repair_sources_allow_one_existing_conflict_only(case):
    import chess
    import json
    from app.repertoire.changes.application import validate_repair_source_rows
    from tempo_legacy_database.adapters.authored_repertoires.product_changes import lock_sources
    from test_product_game_lapse import NOW

    repertoire = 'multi-conflict-' + uuid4().hex
    other_fen = 'rnbqkb1r/pppppppp/5n2/8/8/5N2/PPPPPPPP/RNBQKB1R w KQkq - 2 2'
    with case.transaction() as database:
        database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)',
                                (repertoire, repertoire, 'synthetic', NOW.isoformat()))
        for name, fen, move in (('a', chess.STARTING_FEN, 'e2e4'), ('b', chess.STARTING_FEN, 'd2d4'),
                                ('c', other_fen, 'e2e4'), ('d', other_fen, 'd2d4')):
            database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,'white',%s,%s,%s)",
                                    (repertoire + name, repertoire, name, fen, json.dumps([move]), NOW.isoformat()))
        before = tuple(dict(row) for row in lock_sources(database, repertoire))
        after = tuple(dict(row, moves_json=json.dumps(['e2e4'])) if row['source_id'] == 'line:' + repertoire + 'b' else row for row in before)
        validate_repair_source_rows(repertoire, before, after)
        with pytest.raises(ChangeError, match='conflict_pending_resolution'):
            validate_repair_source_rows(repertoire, before, before)
        worsened = after + (dict(after[-1], source_id='line:new', moves_json=json.dumps(['c2c4'])),)
        with pytest.raises(ChangeError, match='conflict_pending_resolution'):
            validate_repair_source_rows(repertoire, before, worsened)
