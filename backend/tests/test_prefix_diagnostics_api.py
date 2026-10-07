"""HTTP admission, immutable scopes, and fixed SQL bounds for #82."""
from contextlib import contextmanager
from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import prefix_diagnostics_api as api
from app.services.opening_decision_evidence import reduce_observations
from test_prefix_diagnostics import manifest, event


@pytest.fixture
def prepared(monkeypatch):
    presentation = manifest()
    active = False
    statements = []
    attempts = [{'attempt_id': 'one', 'manifest_id': presentation['manifest_id'], 'state': 'partial', 'started_at': '2026-09-28T12:00:00Z'}]
    observations = [{'attempt_id': 'one', 'decision_index': 0, 'observation_json': json.dumps(reduce_observations([event(presentation, 0, 1)], 'UTC')[0])}]
    row = {'id': 1, 'card_id': 'card', 'revision': 1, 'start_fen': presentation['decisions'][0]['fen'],
           'moves_json': json.dumps(['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5']), 'trained_color': 'white', 'colors': ['white']}
    def execute(sql, parameters):
        assert active
        statements.append((sql, parameters))
        if sql.startswith('SELECT id FROM repertoires'): rows = [{'id': 'rep'}]
        elif 'SELECT publication.generation' in sql: rows = [{'generation': 1, 'state': 'ready', 'task_generation': None, 'task_state': None}]
        elif 'WITH prefix_ids' in sql: rows = [row]
        elif 'FROM opening_evidence_attempts' in sql: rows = attempts
        elif 'FROM opening_evidence_observations' in sql: rows = observations
        else: raise AssertionError(sql)
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows)
    @contextmanager
    def read():
        nonlocal active
        active = True
        try: yield SimpleNamespace(execute_native=execute)
        finally: active = False
    derive = api.decision_manifest
    def outside_read(*arguments):
        assert not active
        return derive(*arguments)
    project = api.project_prefix_diagnostics
    def project_outside_read(*arguments):
        assert not active
        return project(*arguments)
    monkeypatch.setattr(api, 'diagnostic_read', read)
    monkeypatch.setattr(api, 'decision_manifest', outside_read)
    monkeypatch.setattr(api, 'project_prefix_diagnostics', project_outside_read)
    return presentation, row, attempts, observations, statements


def detail(presentation, **changes):
    return api.prefix_diagnostics_detail('rep', 'card', manifest_id=presentation['manifest_id'], graph_generation=changes.get('graph_generation', 1))


def test_prefix_diagnostics_bounds_history_and_closes_reads_before_projection(prepared):
    presentation, _, _, _, statements = prepared
    listing = api.prefix_diagnostics_list('rep', after_card_id=None, graph_generation=None)
    assert listing['prefixes'][0]['manifest'] == presentation
    result = detail(presentation)
    assert result['decisions'][0]['clean_successes'] == 1
    attempt_sql, attempt_parameters = next(item for item in statements if 'FROM opening_evidence_attempts' in item[0])
    assert 'ORDER BY started_at DESC,attempt_id DESC LIMIT %s' in attempt_sql and attempt_parameters[-1] == 101
    observation_sql, observation_parameters = next(item for item in statements if 'FROM opening_evidence_observations' in item[0])
    assert 'attempt_id=ANY' in observation_sql and observation_parameters == (['one'], 2000)
    assert all('OFFSET' not in sql and 'FOR UPDATE' not in sql for sql, _ in statements)
    assert next(parameters for sql, parameters in statements if 'WITH prefix_ids' in sql)[6] == 9


def test_prefix_diagnostics_isolates_repertoire_color_revision_and_occurrence(prepared):
    presentation, row, _, _, statements = prepared
    detail(presentation)
    sql, parameters = next(item for item in statements if 'FROM opening_evidence_attempts' in item[0])
    assert all(field in sql for field in ['presentation_snapshot_id=%s', 'repertoire_id=%s', 'trained_color=%s', 'card_id=%s', 'card_revision=%s'])
    assert parameters == (1, 'rep', 'white', 'card', 1, 101)
    row['revision'] = 2
    with pytest.raises(HTTPException, match='presentation changed'): detail(presentation)
    row['revision'] = 1
    row['colors'] = ['black', 'white']
    with pytest.raises(HTTPException, match='unambiguous'): detail(presentation)


def test_prefix_diagnostics_legacy_reviews_create_no_observations(prepared):
    presentation, _, attempts, observations, statements = prepared
    attempts.clear(); observations.clear()
    result = detail(presentation)
    assert all(decision['coverage'] == 'unknown' and decision['reached_observations'] == 0 for decision in result['decisions'])
    assert all('FROM reviews' not in sql and 'opening_evidence_summaries' not in sql for sql, _ in statements)


def test_prefix_diagnostics_pagination_and_detail_reject_changed_context(prepared):
    presentation, _, attempts, _, statements = prepared
    with pytest.raises(HTTPException): api.prefix_diagnostics_list('rep', after_card_id='card', graph_generation=None)
    assert statements == []
    with pytest.raises(HTTPException, match='repertoire changed'): detail(presentation, graph_generation=2)
    attempts[0]['manifest_id'] = 'x' * 64
    with pytest.raises(HTTPException, match='Stored evidence'): detail(presentation)


@pytest.mark.parametrize('route', ['/api/repertoires/rep/prefix-diagnostics', '/api/repertoires/rep/prefix-diagnostics/card?manifest_id='+'a'*64+'&graph_generation=1'])
def test_prefix_diagnostics_background_admission_preserves_foreground_progress(monkeypatch, route):
    from app import main
    scopes = []
    @contextmanager
    def scope(name):
        scopes.append(name)
        yield
    monkeypatch.setattr(main.activity_gate, 'foreground', lambda: scope('foreground'))
    monkeypatch.setattr(main.activity_gate, 'background_request', lambda: scope('background'))
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: False)
    assert TestClient(main.app).get(route).status_code == 503
    assert scopes == ['background']
    scopes.clear()
    monkeypatch.setattr(main, '_queue_payload', lambda *arguments, **options: {'cards': [], 'count': 0})
    assert TestClient(main.app).get('/api/queue/today').status_code == 200
    assert scopes == ['foreground']


def test_prefix_diagnostics_connection_is_authoritative_bounded_and_admitted(monkeypatch):
    lifecycle = []
    @contextmanager
    def enter(name, **options):
        lifecycle.append((name, options))
        try: yield 'database'
        finally: lifecycle.append(('closed:'+name, {}))
    monkeypatch.setattr(api.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(api.activity_gate, 'background_request', lambda: enter('background'))
    monkeypatch.setattr(api.activity_gate, 'background_database_section', lambda: enter('admitted'))
    monkeypatch.setattr(api.postgres_store, 'connection', lambda **options: enter('connection', **options))
    with api.diagnostic_read() as database: assert database == 'database'
    assert lifecycle == [('background', {}), ('admitted', {}), ('connection', {
        'read_only': True, 'background': True, 'repeatable_read': True}),
        ('closed:connection', {}), ('closed:admitted', {}), ('closed:background', {})]


def test_prefix_diagnostics_index_migration_only_adds_read_indexes():
    from pathlib import Path
    from app.schema_version import POSTGRES_SCHEMA_VERSION
    migrations = sorted((Path(__file__).resolve().parents[1]/'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    assert [int(path.name[:3]) for path in migrations] == list(range(1, POSTGRES_SCHEMA_VERSION+1))
    source = next(path for path in migrations if path.name.endswith('_prefix_diagnostics_indexes.sql')).read_text()
    assert source.count('CREATE INDEX') == 2
    assert 'opening_evidence_attempts' in source and 'opening_graph_steps' in source
    assert 'UPDATE ' not in source and 'CREATE TRIGGER' not in source


def test_prefix_diagnostics_reader_credentials_are_sufficient(monkeypatch):
    """The actual API container has no writer URL; diagnostics must use its reader."""
    monkeypatch.delenv('TEMPO_DATABASE_WRITE_URL', raising=False)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', 'postgresql://tempo_reader@postgres/tempo')
    @contextmanager
    def raw_connection():
        yield SimpleNamespace(execute=lambda *arguments: None)
    def pool(read_only):
        assert read_only, 'Diagnostic incorrectly requires the unavailable writer pool'
        return SimpleNamespace(connection=raw_connection)
    @contextmanager
    def scope(): yield
    monkeypatch.setattr(api.postgres_store, '_pool', pool)
    monkeypatch.setattr(api.activity_gate, 'background_request', scope)
    monkeypatch.setattr(api.activity_gate, 'background_database_section', scope)
    with api.diagnostic_read(): pass
