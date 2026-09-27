"""Generate the first PostgreSQL schema from a verified SQLite snapshot.

Run this only while preparing a versioned migration. Review the generated SQL
and check it in; production startup must never regenerate or mutate schema.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sqlite3

import sqlglot


def sorted_tables(database: sqlite3.Connection) -> list[tuple[str, str]]:
    statements = {
        name: statement
        for name, statement in database.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    }
    dependencies = {
        name: {
            row[2]
            for row in database.execute(
                f'PRAGMA foreign_key_list("{name.replace(chr(34), chr(34) * 2)}")'
            )
            if row[2] != name
        }
        for name in statements
    }
    remaining = set(statements)
    ordered: list[tuple[str, str]] = []
    while remaining:
        ready = sorted(name for name in remaining if not dependencies[name] & remaining)
        if not ready:
            raise ValueError(f"Circular table dependencies: {sorted(remaining)}")
        ordered.extend((name, statements[name]) for name in ready)
        remaining.difference_update(ready)
    return ordered


def postgres_statement(sqlite_statement: str) -> str:
    converted = sqlglot.transpile(sqlite_statement, read="sqlite", write="postgres")
    if len(converted) != 1:
        raise ValueError(f"Expected one SQL statement: {sqlite_statement[:80]}")
    # SQLite INTEGER and REAL are both 64-bit values. PostgreSQL INT and REAL
    # would silently narrow identifiers and analysis scores during COPY.
    statement = re.sub(r"\bINT\b", "BIGINT", converted[0])
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", statement)


def generate_schema(sqlite_path: Path) -> str:
    database = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        statements = [
            "-- Generated from the verified SQLite source and reviewed as migration 001.",
            "-- Existing textual date and JSON columns keep their wire semantics.",
            "CREATE TABLE tempo_schema_migrations (version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW());",
        ]
        for table_name, sqlite_statement in sorted_tables(database):
            statements.append(f"-- {table_name}")
            statements.append(postgres_statement(sqlite_statement) + ";")
        statements.extend([
            "-- SQLite JSON and date functions used by existing domain queries.",
            "CREATE FUNCTION json_extract_path_text(document TEXT, VARIADIC path TEXT[]) RETURNS TEXT "
            "LANGUAGE SQL IMMUTABLE STRICT AS $$ "
            "SELECT jsonb_extract_path_text(document::jsonb, VARIADIC path) $$;",
            "CREATE FUNCTION json_each(document TEXT) RETURNS TABLE(key TEXT, value TEXT) "
            "LANGUAGE SQL IMMUTABLE STRICT AS $$ "
            "SELECT (ordinality - 1)::text, element::text FROM "
            "jsonb_array_elements(CASE WHEN jsonb_typeof(document::jsonb) = 'array' "
            "THEN document::jsonb ELSE '[]'::jsonb END) "
            "WITH ORDINALITY AS items(element, ordinality) "
            "UNION ALL SELECT object_key, element::text FROM "
            "jsonb_each(CASE WHEN jsonb_typeof(document::jsonb) = 'object' "
            "THEN document::jsonb ELSE '{}'::jsonb END) AS items(object_key, element) $$;",
            "CREATE FUNCTION json_array_length(document TEXT) RETURNS BIGINT "
            "LANGUAGE SQL IMMUTABLE STRICT AS $$ "
            "SELECT CASE WHEN jsonb_typeof(document::jsonb) = 'array' "
            "THEN jsonb_array_length(document::jsonb) ELSE 0 END $$;",
            "CREATE FUNCTION julianday(moment TEXT) RETURNS DOUBLE PRECISION "
            "LANGUAGE SQL IMMUTABLE STRICT AS $$ "
            "SELECT EXTRACT(EPOCH FROM moment::timestamptz) / 86400.0 + 2440587.5 $$;",
            "CREATE FUNCTION datetime(moment TEXT, modifier TEXT) RETURNS TEXT "
            "LANGUAGE SQL VOLATILE AS $$ SELECT CASE "
            "WHEN moment = 'now' AND modifier = '-1 day' "
            "THEN to_char(clock_timestamp() - interval '1 day', 'YYYY-MM-DD HH24:MI:SS') "
            "ELSE NULL END $$;",
            "CREATE FUNCTION last_insert_rowid() RETURNS BIGINT "
            "LANGUAGE SQL VOLATILE AS $$ SELECT lastval() $$;",
        ])
        for index_name, sqlite_statement in database.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='index' "
            "AND sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ):
            statements.append(f"-- {index_name}")
            statements.append(postgres_statement(sqlite_statement) + ";")
        statements.extend([
            "CREATE TABLE tempo_migration_progress (table_name TEXT PRIMARY KEY, source_count BIGINT NOT NULL, "
            "source_sha256 TEXT NOT NULL, copied_at TIMESTAMPTZ NOT NULL DEFAULT NOW());",
            "CREATE TABLE operation_receipts (operation_id TEXT PRIMARY KEY, command_name TEXT NOT NULL, "
            "request_hash TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('pending','complete','failed')), "
            "response_json TEXT, error_json TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW());",
            "CREATE INDEX idx_operation_receipts_state_updated ON operation_receipts(state, updated_at);",
            "INSERT INTO tempo_schema_migrations(version) VALUES (1);",
        ])
        return "\n".join(statements) + "\n"
    finally:
        database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("output_path", type=Path)
    arguments = parser.parse_args()
    arguments.output_path.write_text(generate_schema(arguments.sqlite_path))


if __name__ == "__main__":
    main()
