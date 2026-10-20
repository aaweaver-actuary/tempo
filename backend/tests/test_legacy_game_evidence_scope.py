"""Bridge predicate and real pinned-driver binding; no database or services."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bridge_scope_sql', ROOT / 'backend/app/storage/legacy_game_evidence_scope.py')
owner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(owner)


def test_bridge_owned_scope_survives_real_psycopg_parameter_binding_during_review():
    # This is the pinned psycopg parameter converter, without a connection.
    # The native HTTP/Study case separately owns SQL translation and execution.
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer
    statement = 'SELECT 1 WHERE ' + owner.owned_legacy_scope_sql() + ' AND event.card_id=%s'
    query = PostgresQuery(Transformer())
    query.convert(statement, ('synthetic-protected-card',))
    assert query.query.count(b'$1') == 1 and len(query.params) == 1
    assert b'remediation_contributions' in query.query


def test_bridge_scope_preserves_jsonb_extraction_through_sqlite_translation():
    import sqlglot
    statement = 'SELECT 1 WHERE ' + owner.owned_legacy_scope_sql() + ' AND event.card_id=?'
    translated = sqlglot.transpile(statement, read='sqlite', write='postgres')[0]
    # PostgreSQL's json_extract_path accepts JSON, not JSONB. Preserve the
    # JSONB function family through the actual compatibility compiler.
    assert 'JSON_EXTRACT_PATH(' not in translated.upper()
    assert translated.upper().count('JSONB_EXTRACT_PATH_TEXT(') == 3
