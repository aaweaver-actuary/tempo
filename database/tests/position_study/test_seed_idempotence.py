"""Detached preview tests; these do not qualify a migration or its locks."""
import json

import pytest

from app.repertoire import build_index
from tempo_evidence_math.position_learning import SourceCertificate
from tempo_legacy_database.adapters.authored_repertoires import position_study_source as owner
from tooling.position_study_inventory import InventoryRequest, SourceFacts
from tooling.position_study_seed import preview_seed_page
from test_position_source_authority import setup


def test_preview_uses_owner_prepared_source_and_retains_provenance(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    membership = prepared.snapshot.membership
    certificate = SourceCertificate(membership.snapshot_id, membership.source_revision,
                                    membership.digest, True, True)
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), certificate),), ())
    first = preview_seed_page(prepared, request, limit=1)
    assert first == preview_seed_page(prepared, request, limit=1)
    assert first.decisions and all(item.hold for item in first.decisions)
    assert json.loads(first.source_provenance_json) == prepared.authored_token.as_dict()
    assert first.authored_digest == prepared.authored_token.authored_digest
    assert first.authored_digest != membership.digest


def test_preview_refuses_unprepared_or_unbounded_requests(monkeypatch):
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, "rep")
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), None),), ())
    with pytest.raises(ValueError, match="prepared_position_source_required"):
        preview_seed_page(object(), request)
    with pytest.raises(ValueError, match="invalid_page_limit"):
        preview_seed_page(prepared, request, limit=501)
    # Source-level holds remain explicit, even with a cursor.
    assert preview_seed_page(prepared, request, after=("standard", "z")).decisions[0].hold


def test_legacy_preflight_pages_and_rejects_bad_or_reserved_ids():
    from tooling.position_study_seed import scan_legacy_id_page
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class Database:
        def __init__(self, rows): self.rows = rows
        def execute_native(self, sql, params):
            after, _, limit = params
            return Result([row for row in self.rows if after is None or row['id'] > after][:limit])
    rows = [{'id': 'a', 'fsrs_card_json': '{"card_id":1}'},
            {'id': 'b', 'fsrs_card_json': '{"card_id":2}'}]
    assert scan_legacy_id_page(Database(rows), limit=1) == ('a', 1)
    assert scan_legacy_id_page(Database(rows), after='a', limit=1) == (None, 1)
    for blob, reason in [('{}', 'invalid'), ('{"card_id":true}', 'invalid'),
                         ('{"card_id":4503599627370496}', 'reserved_namespace')]:
        with pytest.raises(ValueError, match=reason):
            scan_legacy_id_page(Database([{'id': 'a', 'fsrs_card_json': blob}]))


def test_writer_holds_when_namespace_guard_unavailable(monkeypatch):
    from datetime import datetime, timezone
    import importlib.util
    from tooling.position_study_seed import seed_shadow_page
    if importlib.util.find_spec('tempo_legacy_database.adapters.study.position_fsrs_namespace') is not None:
        pytest.skip('installed namespace guard is tested with native PostgreSQL')
    db = setup(monkeypatch)
    prepared = owner.prepare_position_source(db, 'rep')
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), None),), ())
    before = list(db.calls)
    with pytest.raises(ValueError, match='position_namespace_guard_unavailable'):
        seed_shadow_page(db, prepared, request, epoch='e', now=datetime.now(timezone.utc))
    assert db.calls == before


def test_native_seed_restart_clone_and_original_unchanged(monkeypatch):
    """Disposable PG transaction proof; authority and guard are test stubs here."""
    import os
    from datetime import datetime, timezone
    from pathlib import Path
    from types import ModuleType
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from tempo_legacy_database.adapters.postgres_store import tempo_row_factory
    from tooling.position_study_inventory import DonorFact
    from tooling.position_study_seed import seed_shadow_page, verify_seed_receipts_page

    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN')
    if not dsn:
        pytest.skip('requires task-owned disposable TEMPO_POSITION_TEST_DSN')
    prepared_db = setup(monkeypatch)
    prepared = owner.prepare_position_source(prepared_db, 'rep')
    membership = prepared.snapshot.membership
    cert = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
    blob = json.dumps({'card_id': 42, 'state': 1, 'step': 0, 'stability': None, 'difficulty': None,
                       'due': '2026-11-03T00:00:00+00:00', 'last_review': None})
    fact = DonorFact('physical', 'rep', 4, 'learning', '2026-11-03', blob,
                     '2026-10-20T00:00:00Z', False, False, None, False, ('rep',))
    request = InventoryRequest((SourceFacts(prepared.snapshot, build_index(prepared.snapshot), cert),), (fact,))
    # Only this test replaces external authority/guard; SQL, savepoints,
    # sequence, receipts and state are real PostgreSQL operations.
    monkeypatch.setattr(owner, 'fence_position_source', lambda db, source: None)
    namespace = ModuleType('tempo_legacy_database.adapters.study.position_fsrs_namespace')
    namespace.fence_namespace = lambda db: None
    monkeypatch.setitem(__import__('sys').modules, namespace.__name__, namespace)
    migration = Path(__file__).resolve().parents[2] / 'tempo_legacy_database/ddl/migrations/057_position_study.sql'
    now = datetime(2026, 11, 2, tzinfo=timezone.utc)
    with psycopg.connect(dsn, autocommit=True) as admin:
        assert admin.execute('SELECT current_database()').fetchone()[0].startswith('tempo_position_test_')
        schema = 'position_f2_' + uuid4().hex
        admin.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        try:
            admin.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
            admin.execute('CREATE TABLE tempo_schema_migrations(version integer PRIMARY KEY)')
            admin.execute('''CREATE TABLE cards(id text PRIMARY KEY, repertoire_id text, revision bigint,
                state text,due_date text,fsrs_card_json text,introduced_at text,archived bigint,
                pending_validation bigint,superseded_by text,content_type text)''')
            admin.execute('CREATE TABLE repertoire_cards(repertoire_id text,card_id text REFERENCES cards(id))')
            admin.execute('CREATE TABLE repertoire_integrity_card_blocks(repertoire_id text,card_id text REFERENCES cards(id))')
            admin.execute(migration.read_text())
            admin.execute("INSERT INTO cards VALUES ('physical','rep',4,'learning','2026-11-03',%s,'2026-10-20T00:00:00Z',0,0,NULL,'opening')", (blob,))
            admin.execute("INSERT INTO repertoire_cards VALUES ('rep','physical')")
            class Borrowed:
                read_only = False
                def __init__(self, conn): self.raw = conn
                def execute_native(self, statement, params=()): return self.raw.execute(statement, params)
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as conn:
                conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(schema)))
                db = Borrowed(conn)
                assert seed_shadow_page(db, prepared, request, epoch='e', now=now, page_size=1)['stage'] == 'preflight'
                conn.commit()
                conn.execute('BEGIN')
                from dataclasses import replace
                bad = replace(fact, fsrs_card_json='{}')
                bad_request = InventoryRequest(request.sources, (bad,))
                conn.execute("UPDATE cards SET fsrs_card_json='{}' WHERE id='physical'")
                bad_result = seed_shadow_page(db, prepared, bad_request, epoch='e', now=now, page_size=1)
                assert bad_result['held'] == 1 and bad_result['seeded'] == 0
                conn.rollback()
                conn.execute('BEGIN')
                from tempo_evidence_math.position_learning import Hold
                monkeypatch.setattr(owner, 'fence_position_source', lambda db, source: Hold('source_changed'))
                with pytest.raises(ValueError, match='source_changed'):
                    seed_shadow_page(db, prepared, request, epoch='e', now=now, page_size=1)
                assert conn.execute('SELECT count(*) FROM study_position_states').fetchone()[0] == 0
                monkeypatch.setattr(owner, 'fence_position_source', lambda db, source: None)
                assert seed_shadow_page(db, prepared, request, epoch='e', now=now, page_size=1)['stage'] == 'seeding'
                conn.rollback()  # deliberately lose a whole page
                assert conn.execute('SELECT count(*) FROM study_position_states').fetchone()[0] == 0
                while True:
                    result = seed_shadow_page(db, prepared, request, epoch='e', now=now, page_size=1)
                    conn.commit()
                    if result['stage'] == 'complete': break
                    conn.execute('BEGIN')
                states = conn.execute('SELECT fsrs_internal_card_id,fsrs_json FROM study_position_states ORDER BY position').fetchall()
                assert len(states) == 2 and len({row[0] for row in states}) == 2
                assert all(json.loads(row[1])['card_id'] != 42 for row in states)
                assert conn.execute("SELECT fsrs_card_json FROM cards WHERE id='physical'").fetchone()[0] == blob
                # A later accepted grade may change position state without replay overwriting it.
                conn.execute('UPDATE study_position_states SET version=2')
                page = verify_seed_receipts_page(db, prepared, request, epoch='e', limit=100)
                assert page['verified'] == 2 and page['next_after'] is None
                assert conn.execute('SELECT min(version) FROM study_position_states').fetchone()[0] == 2
                conn.rollback()
        finally:
            admin.execute('SET search_path TO public')
            admin.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(schema)))
