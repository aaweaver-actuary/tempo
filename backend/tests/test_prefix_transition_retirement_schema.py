"""The retirement migration disables only historical advisory scope fences.

This static contract is usable without a PostgreSQL server. Native migration
execution remains a deployment qualification requirement.
"""
from pathlib import Path
import re


MIGRATIONS = Path(__file__).resolve().parents[2] / "database/tempo_legacy_database/ddl/migrations"


def test_retirement_removes_exact_historical_scope_triggers_and_keeps_data():
    historical = "\n".join(
        (MIGRATIONS / name).read_text()
        for name in (
            "038_prefix_transition_application.sql",
            "039_prefix_transition_lock_budget.sql",
        )
    )
    triggers = {
        (name, table): function
        for name, table, function in re.findall(
            r"CREATE TRIGGER (\w+)\b.*?\bON (\w+)\b.*?EXECUTE FUNCTION (\w+)\(\);",
            historical,
            flags=re.DOTALL,
        )
    }
    expected = {
        identity
        for identity, function in triggers.items()
        if function in {"guard_prefix_transition_write", "guard_permanent_deletion_scope"}
    }
    assert len(expected) == 10
    source = (MIGRATIONS / "063_retire_prefix_transition_fences.sql").read_text()
    sql = re.sub(r"--[^\n]*", "", source)
    removed = re.findall(r"DROP TRIGGER (\w+) ON (\w+);", sql)
    assert len(removed) == len(expected) and set(removed) == expected
    functions = re.findall(r"DROP FUNCTION (\w+)\(([^)]*)\) RESTRICT;", sql)
    assert functions == [
        ("guard_prefix_transition_write", ""),
        ("guard_permanent_deletion_scope", ""),
        ("guard_prefix_transition_scope", "TEXT, TEXT"),
    ]
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    assert len(statements) == 14
    assert all(statement.startswith(("DROP TRIGGER ", "DROP FUNCTION ")) for statement in statements[:-1])
    assert statements[-1] == "INSERT INTO tempo_schema_migrations(version) VALUES(63)"
    assert not re.search(r"\b(?:DELETE|UPDATE|TRUNCATE|CASCADE)\b|DROP TABLE", sql)
    # These retained barriers also protect permanent deletion and recreation.
    retained = {identity for identity in triggers if identity not in expected}
    assert {
        ("prefix_transition_queue_write", "daily_queue"),
        ("prefix_transition_queue_day_write", "daily_queue_days"),
        ("prefix_transition_queue_projection_write", "queue_projections"),
        ("permanent_deletion_reservation", "deleted_cards"),
    } <= retained
    assert not any(name in {function[0] for function in functions} for name in (
        "reserve_prefix_transition_queue_write",
        "reserve_permanent_deletion_write",
        "prevent_deleted_card_recreation",
    ))
