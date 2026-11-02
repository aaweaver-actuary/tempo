"""Safety checks for the preliminary owner operator."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tempo_legacy_database.adapters.study import position_owner_operator as operator


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class DB:
    def __init__(self, existing=None):
        self.existing = existing
        self.statements = []

    def execute_native(self, sql, params=()):
        self.statements.append((sql, params))
        if 'FROM operation_receipts WHERE' in sql:
            return Cursor(self.existing)
        if 'FROM study_position_owners o' in sql:
            return Cursor({'epoch': 'epoch', 'mode': 'active', 'source_digest': 'source'})
        return Cursor()


@pytest.fixture
def prepared(monkeypatch):
    monkeypatch.setattr(operator, '_lock', lambda db, prepared, **kwargs: 'rep')
    return SimpleNamespace(authored_token=SimpleNamespace(authored_digest='source'))


def call(db, prepared, action='pause'):
    return operator.transition_in_writer(db, prepared, None, action=action, epoch='epoch',
        operation_id='op', now=datetime(2026, 1, 1, tzinfo=timezone.utc))


def test_pause_writes_receipt_and_owner_in_same_borrowed_transaction(prepared):
    db = DB()
    assert call(db, prepared)['mode'] == 'paused'
    statements = [s for s, _ in db.statements]
    assert any('INSERT INTO operation_receipts' in s for s in statements)
    assert any('UPDATE study_position_owners' in s for s in statements)
    assert not any(s == 'COMMIT' for s in statements)


@pytest.mark.parametrize('action', ['stage', 'activate', 'resume', 'rollforward'])
def test_unqualified_operations_hold_and_roll_back(prepared, action):
    db = DB()
    with pytest.raises(operator.OperatorHold, match='unqualified'):
        call(db, prepared, action)
    assert any(s.startswith('ROLLBACK TO SAVEPOINT') for s, _ in db.statements)
    assert not any('UPDATE study_position_owners' in s for s, _ in db.statements)


def test_operation_id_cannot_be_reused_with_different_request(prepared):
    db = DB({'command_name': 'position_owner.transition', 'request_hash': 'different',
             'state': 'complete', 'response_json': '{}'})
    with pytest.raises(operator.OperatorHold, match='operation_conflict'):
        call(db, prepared)
    assert any(s.startswith('ROLLBACK TO SAVEPOINT') for s, _ in db.statements)
