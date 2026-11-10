"""Shared eligibility and diagnostic consumers retain SQLite recovery compatibility."""

from tempo_storage.http_composition import (
    availability,
    queue_projection,
)
from tempo_storage.http_composition import (
    retained_position_availability as retained,
)


def test_retained_position_guard_is_shared_by_postgres_availability_only(monkeypatch):
    monkeypatch.setattr(availability.postgres, "configured", lambda: False)
    sqlite = availability.available_attempt_sql("miss")
    assert "study_position_seed_runs" not in sqlite
    monkeypatch.setattr(availability.postgres, "configured", lambda: True)
    postgres = availability.available_attempt_sql("miss")
    assert postgres.endswith(retained.availability_predicate())
    assert postgres.count(retained.omission_predicate()) == 1


def test_retained_position_diagnostic_uses_same_read_only_eligibility(monkeypatch):
    calls = []

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Reader:
        def execute(self, sql, params):
            calls.append((sql, params))
            return Result(
                [{"card_id": "retained", "message": retained.RETAINED_HOLD}]
                if "study_position_seed_runs" in sql
                else []
            )

    monkeypatch.setattr(queue_projection.postgres, "PostgresConnection", Reader)
    assert queue_projection.queue_diagnostics(Reader(), "2026-11-03") == [
        {"card_id": "retained", "message": retained.RETAINED_HOLD}
    ]
    assert len(calls) == 2
    assert all(
        sql.startswith("SELECT ") and "FOR UPDATE" not in sql for sql, _ in calls
    )
    assert retained.omission_predicate() in calls[1][0]
    assert calls[1][1] == (retained.RETAINED_HOLD, "2026-11-03")


def test_retained_position_guard_compiles_through_shared_sql_adapter(monkeypatch):
    from tempo_storage.postgres import postgres_sql

    monkeypatch.setattr(availability.postgres, "configured", lambda: True)
    compiled = postgres_sql(availability.available_attempt_sql("real_game_miss"))
    assert "study_position_seed_runs" in compiled
    assert compiled.count("%s") == 1

    # Validate every extraction in the NEW predicate, including bare seed-token
    # fields and physical move-array reads. The adapter emits PostgreSQL json_*
    # overloads; a JSONB argument caused native undefined-function failures.
    import sqlglot
    from sqlglot import exp

    compiled_guard = postgres_sql("SELECT 1 WHERE " + retained.omission_predicate())
    tree = sqlglot.parse_one(compiled_guard, read="postgres")
    extractions = [
        node
        for node in tree.walk()
        if isinstance(node, (exp.JSONExtract, exp.JSONExtractScalar))
    ]
    assert len(extractions) == 12
    for extraction in extractions:
        document = extraction.this
        if isinstance(document, exp.Case):
            casts = list(document.find_all(exp.Cast))
            assert len(casts) == 2
            assert all(cast.args["to"].is_type("JSON") for cast in casts)
        elif isinstance(document, exp.Cast):
            assert document.args["to"].is_type("JSON"), extraction.sql()
        else:
            # Only a nested json_extract_path output is another JSON document.
            # Scalar/text extraction output and an uncast JSONB column are not.
            assert isinstance(document, exp.JSONExtract), extraction.sql()

    numeric_generation = next(
        node
        for node in tree.find_all(exp.JSONExtract)
        if "graph_generation" in node.expression.sql()
    )
    assert isinstance(numeric_generation.parent, exp.Cast)
    assert numeric_generation.parent.args["to"].is_type("JSONB")
    for field in ("header", "integrity"):
        certificate = next(
            node
            for node in tree.find_all(exp.JSONExtract)
            if field in node.expression.sql()
        )
        assert isinstance(certificate.parent, exp.Cast)
        assert certificate.parent.args["to"].is_type("JSONB")

    # Explicit nested two-argument calls preserve the full remapped Games path;
    # SQLite parsing of a variadic native helper silently drops later keys.
    effect_target = next(
        node
        for node in tree.find_all(exp.JSONExtractScalar)
        if "card_id" in node.expression.sql()
    )
    assert len(list(effect_target.find_all(exp.JSONExtract))) == 3
    assert all(
        name in effect_target.sql()
        for name in ("owner", "progress", "target", "card_id")
    )
