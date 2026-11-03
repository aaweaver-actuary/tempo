"""Shared eligibility and diagnostic consumers retain SQLite recovery compatibility."""
from tempo_legacy_database.adapters.http_composition import availability, queue_projection
from tempo_legacy_database.adapters.http_composition import retained_position_availability as retained


def test_retained_position_guard_is_shared_by_postgres_availability_only(monkeypatch):
    monkeypatch.setattr(availability.postgres_store, 'configured', lambda: False)
    sqlite = availability.available_attempt_sql('miss')
    assert 'study_position_seed_runs' not in sqlite
    monkeypatch.setattr(availability.postgres_store, 'configured', lambda: True)
    postgres = availability.available_attempt_sql('miss')
    assert postgres.endswith(retained.availability_predicate())
    assert postgres.count(retained.omission_predicate()) == 1


def test_retained_position_diagnostic_uses_same_read_only_eligibility(monkeypatch):
    calls = []
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows
    class Reader:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Result([{'card_id':'retained', 'message':retained.RETAINED_HOLD}]
                          if 'study_position_seed_runs' in sql else [])
    monkeypatch.setattr(queue_projection.postgres_store, 'PostgresConnection', Reader)
    assert queue_projection.queue_diagnostics(Reader(), '2026-11-03') == [
        {'card_id':'retained', 'message':retained.RETAINED_HOLD}]
    assert len(calls) == 2
    assert all(sql.startswith('SELECT ') and 'FOR UPDATE' not in sql for sql, _ in calls)
    assert retained.omission_predicate() in calls[1][0]
    assert calls[1][1] == (retained.RETAINED_HOLD, '2026-11-03')


def test_retained_position_guard_compiles_through_shared_sql_adapter(monkeypatch):
    from tempo_legacy_database.adapters.postgres_store import postgres_sql
    monkeypatch.setattr(availability.postgres_store, 'configured', lambda: True)
    compiled = postgres_sql(availability.available_attempt_sql('real_game_miss'))
    assert 'study_position_seed_runs' in compiled
    assert compiled.count('%s') == 1
    # The SQLite-to-PG adapter emits the json_* overloads. Their document
    # argument must be JSON; JSONB here caused a real native undefined-function
    # failure before any queue head could issue.
    import sqlglot
    from sqlglot import exp
    tree = sqlglot.parse_one(compiled, read='postgres')
    guarded_documents = [node.this for node in tree.walk()
        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar))
        and isinstance(node.this, exp.Case)]
    assert len(guarded_documents) == 3
    for document in guarded_documents:
        casts = list(document.find_all(exp.Cast))
        assert len(casts) == 2
        assert all(cast.args['to'].is_type('JSON') for cast in casts)
    numeric_generation = next(node for node in tree.find_all(exp.JSONExtract)
        if 'graph_generation' in node.expression.sql())
    assert isinstance(numeric_generation.parent, exp.Cast)
    assert numeric_generation.parent.args['to'].is_type('JSONB')
    # Keep remapped accepted Games targets protected. A variadic native JSON
    # helper is parsed as a two-argument SQLite function and loses later path
    # components; explicit nested calls preserve the entire path.
    effect_target = next(node for node in tree.find_all(exp.JSONExtractScalar)
        if 'card_id' in node.expression.sql())
    assert len(list(effect_target.find_all(exp.JSONExtract))) == 3
    assert all(name in effect_target.sql() for name in ('owner', 'progress', 'target', 'card_id'))
