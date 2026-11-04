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
    monkeypatch.setattr(guard, '_cut62_layout', lambda database, names: {name: layout[name] for name in names})
    monkeypatch.setattr(guard, '_cut62_table', lambda database, name, key, secret: state[name].copy())
    witness = guard.capture_cut62(FakeDatabase(), b'x' * 32)
    assert len(guard.CUT62_PROTECTED_TABLES) == 189
    assert guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)['to'] == 62
    name = next(iter(guard.CUT62_PROTECTED_TABLES))
    state[name]['digest'] = 'tampered'
    with pytest.raises(RuntimeError, match='retained rows changed'):
        guard.verify_cut62(FakeDatabase(), witness, b'x' * 32)
    changed = copy.deepcopy(witness)
    del changed['rows'][name]
    with pytest.raises(RuntimeError, match='coverage'):
        guard.verify_cut62(FakeDatabase(), changed, b'x' * 32)
    changed['version'] = 2
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
    witness = {'version': 1, 'from': 61, 'to': 62,
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
