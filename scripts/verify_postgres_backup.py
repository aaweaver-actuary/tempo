"""Compare all public PostgreSQL tables after restoring a custom-format backup.

Run against two disposable databases before opening traffic, and repeat the
restore drill after cutover. Both databases must be stopped for writes while
the comparison runs.
"""

from __future__ import annotations

import argparse

import psycopg

from migrate_sqlite_to_postgres import destination_fingerprint


def table_layout(database: psycopg.Connection) -> dict[str, tuple[tuple[str, ...], tuple[tuple[str, str, bool], ...]]]:
    rows = database.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name"
    ).fetchall()
    layout = {}
    for (table_name,) in rows:
        primary_key = tuple(row[0] for row in database.execute(
            "SELECT attribute.attname FROM pg_index index_row "
            "JOIN pg_class relation ON relation.oid=index_row.indrelid "
            "JOIN pg_namespace namespace ON namespace.oid=relation.relnamespace "
            "JOIN LATERAL unnest(index_row.indkey) WITH ORDINALITY key(attnum,position) ON TRUE "
            "JOIN pg_attribute attribute ON attribute.attrelid=relation.oid "
            "AND attribute.attnum=key.attnum "
            "WHERE namespace.nspname='public' AND relation.relname=%s "
            "AND index_row.indisprimary ORDER BY key.position",
            (table_name,),
        ))
        if not primary_key:
            raise RuntimeError(f"Cannot verify {table_name}: no primary key")
        columns = tuple(database.execute(
            "SELECT column_name,data_type,is_nullable='NO' "
            "FROM information_schema.columns WHERE table_schema='public' "
            "AND table_name=%s ORDER BY ordinal_position",
            (table_name,),
        ))
        layout[table_name] = (primary_key, columns)
    return layout


def compare_backup(source: psycopg.Connection, restored: psycopg.Connection) -> None:
    source_layout = table_layout(source)
    restored_layout = table_layout(restored)
    if source_layout != restored_layout:
        missing = sorted(set(source_layout) - set(restored_layout))
        extra = sorted(set(restored_layout) - set(source_layout))
        changed = sorted(table for table in source_layout.keys() & restored_layout.keys()
                         if source_layout[table] != restored_layout[table])
        raise RuntimeError(f"Restore schema mismatch: missing={missing}, extra={extra}, changed={changed}")
    for table_name, (primary_key, columns) in source_layout.items():
        column_names = [column[0] for column in columns]
        expected = destination_fingerprint(
            source, table_name, column_names, list(primary_key), set()
        )
        actual = destination_fingerprint(
            restored, table_name, column_names, list(primary_key), set()
        )
        if expected != actual:
            raise RuntimeError(f"Restore row mismatch in {table_name}: {expected} != {actual}")
        print(f"RESTORED {table_name}: {actual[0]} rows", flush=True)
    print(f"Verified all {len(source_layout)} PostgreSQL tables", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dsn")
    parser.add_argument("restored_dsn")
    arguments = parser.parse_args()
    with psycopg.connect(arguments.source_dsn, autocommit=True) as source:
        with psycopg.connect(arguments.restored_dsn, autocommit=True) as restored:
            compare_backup(source, restored)


if __name__ == "__main__":
    main()
