"""Historical source preparation parity; native lifecycle requires 061 integration."""
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
for path in [ROOT, ROOT / 'backend', ROOT / 'backend/tests', ROOT / 'database',
             ROOT / 'database/tests/position_study', *ROOT.glob('packages/*/src'),
             *ROOT.glob('contracts/*/src'), *ROOT.glob('services/*/src'),
             *ROOT.glob('services/learning/*/src')]:
    sys.path.insert(0, str(path))

import pytest

from tempo_evidence_math.position_learning import Hold
from tempo_legacy_database.adapters.authored_repertoires import position_study_source as owner
from tempo_legacy_database.adapters.authored_repertoires.content_origin import PreparedContentOrigin, capture_content_origin
from test_position_source_authority import setup


def test_owner_single_read_and_detached_facts(monkeypatch):
    db = setup(monkeypatch)
    calls = []
    def read(database, repertoire_id):
        calls.append(repertoire_id)
        return deepcopy(database.facts)
    monkeypatch.setattr(owner, 'read_repertoire', read)
    both = owner.prepare_position_source_with_facts(db, 'rep')
    assert not isinstance(both, Hold) and calls == ['rep']
    facts = both.publication_facts()
    facts['lines'][0]['moves_json'] = '[]'
    assert both.publication_facts()['lines'][0]['moves_json'] != '[]'
    from app.repertoire.snapshot import project_published_repertoire
    assert project_published_repertoire(both.publication_facts()).membership.digest == both.prepared.authored_token.original_source_digest
    calls.clear()
    original = owner.prepare_position_source(db, 'rep')
    assert calls == ['rep'] and original.authored_token == both.prepared.authored_token
    assert original.routes == both.prepared.routes
    with pytest.raises(ValueError):
        owner.PreparedPositionSourceWithFacts(None, original, '{}')
    with pytest.raises(ValueError):
        PreparedContentOrigin(None, original, b'{}')


def test_original_preparation_does_not_retain_publication_facts(monkeypatch):
    from app.repertoire.snapshot import PreparedPublishedRepertoire
    db = setup(monkeypatch)
    def unexpected_retention(_self):
        raise AssertionError('ordinary preparation must not retain facts')
    monkeypatch.setattr(PreparedPublishedRepertoire, 'publication_facts', unexpected_retention)
    prepared = owner.prepare_position_source(db, 'rep')
    assert not isinstance(prepared, Hold)
    with pytest.raises(AssertionError, match='must not retain'):
        owner.prepare_position_source_with_facts(db, 'rep')


def test_historical_origin_native(monkeypatch):
    """Owner snapshot, historical physical race, authored race, replay and rollback."""
    import os
    from pathlib import Path
    import secrets
    import sys
    import json
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    root = Path(__file__).resolve().parents[3]
    for path in [root, root / 'backend', root / 'database', *root.glob('packages/*/src'),
                 *root.glob('contracts/*/src'), *root.glob('services/*/src'), *root.glob('services/learning/*/src')]:
        sys.path.insert(0, str(path))
    from database.tests.position_study import acceptance
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    from tempo_legacy_database.adapters.authored_repertoires.content_origin import prepare_content_origin, capture_content_origin
    admin = os.environ.get('TEMPO_LEARNING_TEST_ADMIN')
    assert admin and os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-')
    params = conninfo_to_dict(admin)
    assert params.get('host') in ('127.0.0.1', 'localhost') and params.get('dbname') == 'postgres'
    name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    with psycopg.connect(admin, autocommit=True) as conn:
        assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
        for role in ('tempo_reader', 'tempo_writer'):
            assert not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(params['password'])))
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(admin, dbname=name)
    monkeypatch.setenv('TEMPO_POSITION_N2_DSN', dsn)
    def checks(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs):
        raw.commit()
        def prepare():
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
                reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                value = prepare_content_origin(PostgresConnection(reader, read_only=True), repertoire_id='n2',
                    content_workspace_id='future-content', content_source_key='future-source')
                assert not isinstance(value, Hold), value
                return value
        historical = prepare()
        data = json.loads(historical.payload)
        assert data['contract_version'] == 'LEGACY_CONTENT_HISTORICAL_ORIGIN.v1'
        assert data['physical_currentness'] == data['physical_instance_qualification'] == 'UNQUALIFIED'
        assert data['activation_eligible'] is False and data['final_legacy_fence_receipt'] is None
        assert data['content_projection_status'] == 'BLOCKED_AUTHORITY_SCOPE' and data['content_source_bytes'] is None
        assert len(data['occurrences']) >= 2
        assert all(route['authority_start_ply'] is None for route in data['routes'])
        # Physical-only changes leave the receipt historical, not a current certificate.
        with psycopg.connect(dsn) as writer:
            writer.execute("UPDATE cards SET revision=revision+1 WHERE id='card'")
        raw.execute('BEGIN')
        first = capture_content_origin(port, prepared=historical, export_id='one')
        assert not isinstance(first, Hold), first
        raw.commit()
        raw.execute('BEGIN')
        assert capture_content_origin(port, prepared=historical, export_id='one') == first
        raw.commit()
        with pytest.raises(psycopg.errors.CheckViolation):
            raw.execute('UPDATE legacy_content_historical_origins SET export_id=export_id')
        raw.rollback()
        # Tampering with detached prepared payload never authorizes publication.
        original_payload = historical.payload
        object.__setattr__(historical, 'payload', original_payload + b' ')
        raw.execute('BEGIN')
        assert capture_content_origin(port, prepared=historical, export_id='tampered').reason == 'CORR_SOURCE_FACTS_MISMATCH'
        raw.rollback()
        object.__setattr__(historical, 'payload', original_payload)
        changed = prepare()
        raw.execute('BEGIN')
        result = capture_content_origin(port, prepared=changed, export_id='rollback')
        assert not isinstance(result, Hold)
        raw.rollback()
        assert raw.execute("SELECT count(*) FROM legacy_content_historical_origins WHERE export_id='rollback'").fetchone()[0] == 0
        raw.rollback()
        with psycopg.connect(dsn) as writer:
            writer.execute("UPDATE repertoire_lines SET moves_json='[\"d2d4\"]' WHERE id='route'")
        raw.execute('BEGIN')
        assert capture_content_origin(port, prepared=historical, export_id='one') == first
        raw.commit()
        raw.execute('BEGIN')
        assert capture_content_origin(port, prepared=changed, export_id='one').reason == 'CORR_IMMUTABLE_CONFLICT'
        raw.rollback()
        raw.execute('BEGIN')
        held = capture_content_origin(port, prepared=changed, export_id='stale')
        assert isinstance(held, Hold)
        assert raw.execute("SELECT count(*) FROM legacy_content_historical_origins WHERE export_id='stale'").fetchone()[0] == 0
        raw.rollback()
    monkeypatch.setattr(acceptance, '_real_review_journey', checks)
    try:
        acceptance._run_schema_journey(monkeypatch, branches=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
            for role in ('tempo_reader', 'tempo_writer'):
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_native_cross_repertoire_scope_preserves_complete_family_and_anchor(monkeypatch):
    """Actual owner snapshot retains competing authored routes without publication."""
    import json
    import os
    import secrets
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    from database.tests.position_study import acceptance
    from tempo_legacy_database.adapters.postgres_store import PostgresConnection, tempo_row_factory
    from tempo_legacy_database.adapters.authored_repertoires.content_origin import prepare_content_origin
    from tempo_legacy_database.adapters.authored_repertoires.authority_scope import prepare_cross_repertoire_authority_scope
    admin = os.environ.get('TEMPO_LEARNING_TEST_ADMIN')
    assert admin and os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-')
    params = conninfo_to_dict(admin)
    assert params.get('host') in ('127.0.0.1', 'localhost') and params.get('dbname') == 'postgres'
    name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    with psycopg.connect(admin, autocommit=True) as conn:
        assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
        for role in ('tempo_reader', 'tempo_writer'):
            assert not conn.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            conn.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(params['password'])))
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(admin, dbname=name)
    monkeypatch.setenv('TEMPO_POSITION_N2_DSN', dsn)
    def checks(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs):
        raw.commit()
        with psycopg.connect(dsn) as writer:
            for rep in ('competitor', 'ended', 'opposite', '__internal'):
                writer.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES (%s,%s,%s,%s)', (rep,rep,'fixture','now'))
            fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
            for route, rep, color, moves in (
                ('other','competitor','white',['e2e4','c7c5']),
                ('empty','ended','white',[]), ('black','opposite','black',['e2e4']),
                ('internal','__internal','white',['e2e4','e7e5','g1f3'])):
                writer.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s)', (route,rep,route,color,fen,json.dumps(moves),'now'))
            writer.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,archived) VALUES (%s,%s,%s,%s,%s,%s,1)", ('archived-competitor','competitor','checkpoint',fen,'[]','day'))
        def prepare(reader):
            scope = PostgresConnection(reader, read_only=True)
            origin = prepare_content_origin(scope, repertoire_id='n2',content_workspace_id='future',content_source_key='future')
            assert not isinstance(origin, Hold), origin
            result = prepare_cross_repertoire_authority_scope(scope, prepared_origin=origin)
            assert not isinstance(result, Hold), result
            return origin, result, json.loads(result.payload)
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            origin, result, data = prepare(reader)
            route = next(r for r in data['route_scopes'] if r['original_route_id'] == 'route')
            assert route['prefix_policies'][:4] == [['competitor','ended','n2'], ['competitor','n2'], ['n2'], ['n2']]
            from types import SimpleNamespace
            from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof
            assert LegacyScopedCatalogueProof.authority_anchor(SimpleNamespace(prefix_policies=route['prefix_policies']), 'n2') == 2
            assert route['authority_start_ply'] == 2
            assert [x['status'] for x in data['occurrence_map']] == ['BEFORE_AUTHORITY_ANCHOR','QUALIFIED_IN_SNAPSHOT']
            assert data['activation_eligible'] is False and data['physical_currentness'] == 'UNQUALIFIED'
            assert '__internal' not in [r['id'] for r in data['catalogue']['repertoires']]
            with psycopg.connect(dsn) as writer:
                writer.execute("UPDATE repertoire_lines SET moves_json=%s WHERE id='other'", (json.dumps(['e2e4','e7e5','g1f3']),))
            assert prepare_cross_repertoire_authority_scope(PostgresConnection(reader, read_only=True), prepared_origin=origin) == result
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            assert prepare_cross_repertoire_authority_scope(PostgresConnection(reader, read_only=True), prepared_origin=origin).reason == 'AUTH_SCOPE_ORIGINAL_SNAPSHOT_UNAVAILABLE'
            _, _, changed = prepare(reader)
            changed_route = next(r for r in changed['route_scopes'] if r['original_route_id'] == 'route')
            assert changed_route['authority_start_ply'] is None and changed_route['anchor_status'] == 'AMBIGUOUS_FAMILY'
            assert all(x['status'] == 'AMBIGUOUS_FAMILY' for x in changed['occurrence_map'])
        with psycopg.connect(dsn) as writer:
            writer.execute("DELETE FROM repertoire_lines WHERE id IN ('other','empty')")
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            _, _, unique = prepare(reader)
            assert unique['route_scopes'][0]['authority_start_ply'] == 0
        with psycopg.connect(dsn) as writer:
            writer.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES ('other','competitor','other','white',%s,'not json','now')", (fen,))
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = PostgresConnection(reader, read_only=True)
            origin = prepare_content_origin(scope,repertoire_id='n2',content_workspace_id='future',content_source_key='future')
            assert prepare_cross_repertoire_authority_scope(scope, prepared_origin=origin).reason == 'AUTH_SCOPE_CATALOGUE_INVALID'
    monkeypatch.setattr(acceptance, '_real_review_journey', checks)
    try:
        acceptance._run_schema_journey(monkeypatch, branches=False)
    finally:
        with psycopg.connect(admin, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            assert not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
            for role in ('tempo_reader', 'tempo_writer'):
                conn.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(role)))


def test_authority_scope_stream_refuses_limit_and_incomplete_page():
    from tempo_legacy_database.adapters.authored_repertoires.authority_scope import _read_stream
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows
    class Reader:
        def __init__(self, count): self.count = count
        def execute_native(self, query, parameters=()):
            if 'count(*)' in query: return Result([{'n': self.count}])
            if 'DESC LIMIT 1' in query: return Result([{'id': 'last'}])
            return Result([])
    assert _read_stream(Reader(10001), 'repertoires').reason == 'AUTH_SCOPE_LIMIT'
    assert _read_stream(Reader(1), 'repertoires').reason == 'AUTH_SCOPE_SOURCE_INCOMPLETE'
