"""Compare every migrated table in a PostgreSQL restore drill with its source."""

from __future__ import annotations

import argparse
from pathlib import Path
import sqlite3

import psycopg

from generate_postgres_schema import sorted_tables
from migrate_sqlite_to_postgres import destination_fingerprint, table_columns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_schema_source", type=Path)
    parser.add_argument("source_dsn")
    parser.add_argument("restored_dsn")
    arguments = parser.parse_args()
    with sqlite3.connect(f"file:{arguments.sqlite_schema_source}?mode=ro", uri=True) as schema:
        with psycopg.connect(arguments.source_dsn, autocommit=True) as source:
            with psycopg.connect(arguments.restored_dsn, autocommit=True) as restored:
                for table_name, _ in sorted_tables(schema):
                    _, primary_key_columns, text_primary_key_columns = table_columns(schema, table_name)
                    expected = destination_fingerprint(
                        source, table_name, primary_key_columns, text_primary_key_columns
                    )
                    actual = destination_fingerprint(
                        restored, table_name, primary_key_columns, text_primary_key_columns
                    )
                    if expected != actual:
                        raise RuntimeError(f"Restore mismatch for {table_name}: {expected} != {actual}")
                    print(f"RESTORED {table_name}: {actual[0]} rows", flush=True)


if __name__ == "__main__":
    main()
