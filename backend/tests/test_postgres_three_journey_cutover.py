"""Focused guard contract; PostgreSQL cutover proof is separately required."""
import copy
import json

import pytest

from database.tooling import verify_postgres_cli_state as guard


class FakeDatabase:
    def execute(self, query, params=None):
        assert query == 'SELECT to_regclass(%s)'
        class Result:
            def fetchone(self):
                return (None,)
        return Result()


def test_cut62_guard_retains_full_fixed_ledger_and_rejects_tamper(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    layout = {name: [['id'], [['id', 'text', True]]] for name in source}
    state = {name: {'count': 1, 'digest': name} for name in source}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    monkeypatch.setattr(guard, '_cut62_catalog', lambda database, secret: {'count': 1, 'digest': 'catalog'})
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'sequences'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    monkeypatch.setattr(guard, '_cut62_layout', lambda database, names: {name: layout[name] for name in names})
    monkeypatch.setattr(guard, '_cut62_table', lambda database, name, key, secret: state[name].copy())
    witness = guard.capture_cut62(FakeDatabase(), b'x' * 32)
    assert len(guard.CUT62_PROTECTED_TABLES) == 184
    assert guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)['to'] == 62
    name = next(iter(guard.CUT62_PROTECTED_TABLES))
    state[name]['digest'] = 'tampered'
    with pytest.raises(RuntimeError, match='retained rows changed'):
        guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)
    changed = copy.deepcopy(witness)
    del changed['rows'][name]
    with pytest.raises(RuntimeError, match='coverage'):
        guard.verify_cut62(FakeDatabase(), changed, b'x' * 32)
    changed['version'] = 99
    with pytest.raises(RuntimeError, match='unsupported'):
        guard.verify_cut62(FakeDatabase(), changed, b'x' * 32)


def test_cut62_witness_is_private_exclusive_and_authenticated(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, 'capture_cut62', lambda database, secret: {'version': 1})
    path = tmp_path / 'original.json'
    guard.save_cut62_witness(path, None)
    assert path.stat().st_mode & 0o777 == 0o600
    assert guard.load_cut62_witness(path)[0] == {'version': 1}
    with pytest.raises(FileExistsError):
        guard.save_cut62_witness(path, None)
    payload = json.loads(path.read_text())
    payload['witness']['version'] = 2
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match='authentication'):
        guard.load_cut62_witness(path)


def test_cut62_layout_rejects_unexpected_table(monkeypatch):
    monkeypatch.setattr(guard, 'table_layout', lambda db: {'extra': (('id',), (('id', 'text', True),))})
    with pytest.raises(RuntimeError, match='table coverage'):
        guard._cut62_layout(None, {'expected'})


def test_cut62_rejects_reintroduced_retired_relation(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    witness = {'version': 2, 'from': 61, 'to': 62,
               'layout': dict.fromkeys(source), 'rows': dict.fromkeys(source - {guard.CUT62_LEDGER})}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    class Database:
        def execute(self, query, params):
            class Result:
                def fetchone(self):
                    return ('still_exists',)
            return Result()
    with pytest.raises(RuntimeError, match='retired relation remains'):
        guard.verify_cut62(Database(), witness, b'x' * 32)


def test_cut62_sequence_drift_is_rejected(monkeypatch):
    source = guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    layout = {name: [['id'], [['id', 'text', True]]] for name in source}
    monkeypatch.setattr(guard, '_cut62_session', lambda database: None)
    monkeypatch.setattr(guard, '_cut62_versions', lambda database, version: None)
    monkeypatch.setattr(guard, '_cut62_profile_objects', lambda database, should_exist: None)
    monkeypatch.setattr(guard, '_cut62_layout', lambda database, names: {name: layout[name] for name in names})
    monkeypatch.setattr(guard, '_cut62_table', lambda database, name, key, secret: {'count': 1, 'digest': name})
    monkeypatch.setattr(guard, '_cut62_catalog', lambda database, secret: {'count': 1, 'digest': 'catalog'})
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'before'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    witness = guard.capture_cut62(FakeDatabase(), b'x' * 32)
    monkeypatch.setattr(guard, '_cut62_sequences', lambda database, secret, source: {'protected': {'count': 9, 'digest': 'after'}, 'retired': {'count': int(source), 'digest': 'retired'}})
    with pytest.raises(RuntimeError, match='sequences changed'):
        guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)


def test_cut62_native_schema61_to62(tmp_path):
    """Explicitly allocated empty local DB only; never an ambient/serving DB."""
    import os
    from urllib.parse import urlparse
    import psycopg
    from database.tooling.apply_postgres_migrations import MIGRATIONS, apply_migrations, migration_source

    dsn = os.environ.get('TEMPO_W07_NATIVE_REGRESSION_DSN')
    if not dsn:
        pytest.skip('W07 native disposable database not allocated')
    parsed = urlparse(dsn)
    if parsed.hostname != '127.0.0.1' or parsed.path != '/w07_regression':
        raise ValueError('refusing non-W07 disposable database')
    with psycopg.connect(dsn) as db:
        assert db.execute("SELECT to_regclass('public.tempo_schema_migrations')").fetchone()[0] is None
        for path in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql')):
            if int(path.name[:3]) <= 61:
                db.execute(migration_source(path), prepare=False)
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('synthetic','synthetic','synthetic','2026-01-01')")
    path = tmp_path / 'cut62-original.json'
    with psycopg.connect(dsn) as db:
        guard.save_cut62_witness(path, db)
    witness, secret = guard.load_cut62_witness(path)
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        guard.verify_cut62(db, witness, secret)
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        guard.verify_cut62(db, witness, secret)
        db.execute("UPDATE repertoires SET name='tampered' WHERE id='synthetic'")
        with pytest.raises(RuntimeError, match='retained rows changed'):
            guard.verify_cut62(db, witness, secret)
        db.rollback()
        guard.verify_cut62(db, witness, secret)
        db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('synthetic','lichess','synthetic','2026-01-01T00:00:00Z','rapid',1,'white','draw','synthetic','[]')")
        assert db.execute("SELECT to_regclass('public.next_opponent_accounts')").fetchone()[0] is None
        db.rollback()


def test_cut62_sequence_inventory_separates_removed_identity():
    class Result:
        def __init__(self, value):
            self.value = value
        def fetchall(self):
            return self.value
        def fetchone(self):
            return self.value
    class SequenceDB:
        def __init__(self, names):
            self.names = names
        def execute(self, statement):
            if isinstance(statement, str):
                return Result([(name, 'owner', '', 1, 1, 100, 1, 1, False)
                               for name in sorted(self.names)])
            return Result((1, False))
    source = guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES), b'x' * 32, True)
    target = guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b'x' * 32, False)
    assert source['protected'] == target['protected']
    assert source['retired']['count'] == 1
    assert target['retired']['count'] == 0
    with pytest.raises(RuntimeError, match='sequence coverage'):
        guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b'x' * 32, True)
    with pytest.raises(RuntimeError, match='sequence coverage'):
        guard._cut62_sequences(SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES), b'x' * 32, False)
