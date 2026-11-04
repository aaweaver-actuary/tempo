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
        object.__setattr__(historical, 'payload', historical.payload + b' ')
        raw.execute('BEGIN')
        assert capture_content_origin(port, prepared=historical, export_id='tampered').reason == 'CORR_SOURCE_FACTS_MISMATCH'
        raw.rollback()
        changed = prepare()
        with psycopg.connect(dsn) as writer:
            writer.execute("UPDATE repertoire_lines SET moves_json='[\"d2d4\"]' WHERE id='route'")
        raw.execute('BEGIN')
        held = capture_content_origin(port, prepared=changed, export_id='stale')
        assert isinstance(held, Hold)
        assert raw.execute("SELECT count(*) FROM legacy_content_historical_origins WHERE export_id='stale'").fetchone()[0] == 0
        raw.rollback()
        with psycopg.connect(dsn) as writer:
            writer.execute("UPDATE repertoire_lines SET moves_json='[\"e2e4\",\"e7e5\",\"g1f3\"]' WHERE id='route'")
        rollback_value = prepare()
        raw.execute('BEGIN')
        result = capture_content_origin(port, prepared=rollback_value, export_id='rollback')
        assert not isinstance(result, Hold)
        raw.rollback()
        assert raw.execute("SELECT count(*) FROM legacy_content_historical_origins WHERE export_id='rollback'").fetchone()[0] == 0
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
