"""Installed namespace guard verification must fail closed."""
import re
from pathlib import Path

import pytest
from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace

SQL = (Path(__file__).resolve().parents[2] / 'tempo_legacy_database/ddl/migrations/057_position_study.sql').read_text()
BODY = re.search(r'CREATE FUNCTION study_position_guard_legacy_fsrs_id\(\) RETURNS TRIGGER\s+'
                 r'LANGUAGE plpgsql SET search_path = pg_catalog AS \$\$(.*?)\$\$;', SQL, re.S).group(1)


class Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Database:
    def __init__(self, row):
        self.row = row
        self.statements = []

    def execute_native(self, statement):
        self.statements.append(statement)
        return Cursor(self.row)


def test_guard_verifier_accepts_exact_packaged_body_and_holds_table():
    db = Database((BODY,))
    fence_namespace(db)
    assert db.statements[0] == 'LOCK TABLE public.cards IN ACCESS SHARE MODE NOWAIT'
    assert "t.tgenabled IN ('O','A')" in db.statements[1]
    assert "t.tgattr::text=card_id.attnum::text || ' ' || a.attnum::text" in db.statements[1]
    assert "current_setting('session_replication_role')='origin'" in db.statements[1]


@pytest.mark.parametrize('row', [None, ('different body',)])
def test_guard_verifier_rejects_missing_or_changed_body(row):
    with pytest.raises(RuntimeError, match='position_fsrs_namespace_guard_unavailable'):
        fence_namespace(Database(row))
