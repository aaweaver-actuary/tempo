"""Copy a stopped, verified Tempo SQLite snapshot into PostgreSQL.

The destination must have all checked-in migrations applied and no application rows.
Each table is copied in a separate transaction and recorded in
tempo_migration_progress, so an interrupted import can resume safely. Run
--verify-only afterward to compare every row in primary-key order.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import time

import psycopg
from psycopg import sql

from generate_postgres_schema import sorted_tables

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.schema_version import POSTGRES_SCHEMA_VERSION


COPY_TARGETS = {
    "game_move_analysis": "game_move_analysis_legacy",
    "game_move_analysis_candidates": "game_move_analysis_candidates_legacy",
    "game_position_occurrences": "game_position_occurrences_legacy",
    "game_repertoire_matches": "game_repertoire_matches_legacy",
    "repertoire_decision_events": "repertoire_decision_events_legacy",
    "repertoire_comparisons": "repertoire_comparisons_legacy",
    "gameplay_events": "gameplay_events_legacy",
}


def quote_sqlite_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def table_columns(source: sqlite3.Connection, table_name: str) -> tuple[list[str], list[str], set[str]]:
    columns = list(source.execute(f"PRAGMA table_info({quote_sqlite_identifier(table_name)})"))
    column_names = [column[1] for column in columns]
    primary_key_columns = [column[1] for column in sorted(columns, key=lambda item: item[5]) if column[5]]
    text_primary_key_columns = {
        column[1] for column in columns if column[5] and column[2].upper() == "TEXT"
    }
    if not primary_key_columns:
        raise ValueError(f"{table_name} has no primary key for deterministic verification")
    return column_names, primary_key_columns, text_primary_key_columns


def update_row_digest(digest: hashlib._Hash, row: tuple) -> None:
    for value in row:
        if value is None:
            digest.update(b"N")
            continue
        if isinstance(value, bytes):
            encoded = value
            kind = b"B"
        elif isinstance(value, int):
            encoded = str(value).encode("ascii")
            kind = b"I"
        elif isinstance(value, float):
            encoded = repr(value).encode("ascii")
            kind = b"F"
        else:
            encoded = str(value).encode("utf-8")
            kind = b"S"
        digest.update(kind)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    digest.update(b"\n")


def source_rows(source: sqlite3.Connection, table_name: str, primary_key_columns: list[str]):
    order = ", ".join(quote_sqlite_identifier(column) for column in primary_key_columns)
    return source.execute(f"SELECT * FROM {quote_sqlite_identifier(table_name)} ORDER BY {order}")


def source_fingerprint(source: sqlite3.Connection, table_name: str, primary_key_columns: list[str]) -> tuple[int, str]:
    count = 0
    digest = hashlib.sha256()
    for row in source_rows(source, table_name, primary_key_columns):
        update_row_digest(digest, row)
        count += 1
    return count, digest.hexdigest()


def destination_fingerprint(
    destination: psycopg.Connection,
    table_name: str,
    column_names: list[str],
    primary_key_columns: list[str],
    text_primary_key_columns: set[str],
) -> tuple[int, str]:
    order = sql.SQL(", ").join(
        sql.SQL('{} COLLATE "C"').format(sql.Identifier(column))
        if column in text_primary_key_columns else sql.Identifier(column)
        for column in primary_key_columns
    )
    projection = sql.SQL(", ").join(map(sql.Identifier, column_names))
    statement = sql.SQL("SELECT {} FROM {} ORDER BY {}").format(
        projection, sql.Identifier(table_name), order
    )
    digest = hashlib.sha256()
    count = 0
    with destination.transaction():
        with destination.cursor(name=f"verify_{table_name}") as cursor:
            cursor.execute(statement)
            for row in cursor:
                update_row_digest(digest, row)
                count += 1
    return count, digest.hexdigest()


def copy_table(
    source: sqlite3.Connection,
    destination: psycopg.Connection,
    table_name: str,
    column_names: list[str],
    primary_key_columns: list[str],
) -> tuple[int, str]:
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(
        sql.Identifier(COPY_TARGETS.get(table_name, table_name)),
        sql.SQL(", ").join(map(sql.Identifier, column_names)),
    )
    digest = hashlib.sha256()
    count = 0
    with destination.transaction():
        with destination.cursor() as cursor:
            scope_trigger = {"repertoire_lines": "canonical_line_source", "repertoire_cards": "canonical_link_source",
                             "cards": "canonical_card_insert_source", "repertoires": "canonical_game_scope",
                             "imported_games": "canonical_game_insert", "game_findings": "canonical_finding_insert",
                             "repertoire_opportunities": "canonical_opportunity_insert"}.get(table_name)
            if scope_trigger:
                # Copy the snapshot's source revision verbatim. Trigger state is
                # transactional, so a failed copy also restores its protection.
                cursor.execute(sql.SQL("ALTER TABLE {} DISABLE TRIGGER {}").format(
                    sql.Identifier(table_name), sql.Identifier(scope_trigger)))
            with cursor.copy(statement) as copy:
                for row in source_rows(source, table_name, primary_key_columns):
                    copy.write_row(row)
                    update_row_digest(digest, row)
                    count += 1
            if scope_trigger:
                cursor.execute(sql.SQL("ALTER TABLE {} ENABLE TRIGGER {}").format(
                    sql.Identifier(table_name), sql.Identifier(scope_trigger)))
            cursor.execute(
                "INSERT INTO tempo_migration_progress(table_name,source_count,source_sha256) "
                "VALUES (%s,%s,%s)",
                (table_name, count, digest.hexdigest()),
            )
    return count, digest.hexdigest()


def reseed_identifiers(source: sqlite3.Connection, destination: psycopg.Connection) -> None:
    for table_name, _ in sorted_tables(source):
        columns = list(source.execute(f"PRAGMA table_info({quote_sqlite_identifier(table_name)})"))
        identity_columns = [column[1] for column in columns if column[5] and "INT" in column[2].upper()]
        for column_name in identity_columns:
            sequence = destination.execute(
                "SELECT pg_get_serial_sequence(%s,%s)",
                (f"public.{COPY_TARGETS.get(table_name, table_name)}", column_name)
            ).fetchone()[0]
            if not sequence:
                continue
            maximum = destination.execute(
                sql.SQL("SELECT MAX({}) FROM {}").format(
                    sql.Identifier(column_name), sql.Identifier(table_name)
                )
            ).fetchone()[0]
            destination.execute("SELECT setval(%s,%s,%s)", (sequence, maximum or 1, maximum is not None))


def migrate(source_path: Path, destination_dsn: str, verify_only: bool) -> None:
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    destination = psycopg.connect(destination_dsn, autocommit=True)
    try:
        version = destination.execute("SELECT MAX(version) FROM tempo_schema_migrations").fetchone()[0]
        if version != POSTGRES_SCHEMA_VERSION:
            raise RuntimeError(
                f"PostgreSQL schema version {version!r}; expected {POSTGRES_SCHEMA_VERSION}"
            )
        expected_tables = sorted_tables(source)
        print(f"Source has {len(expected_tables)} application tables", flush=True)
        for table_name, _ in expected_tables:
            started = time.monotonic()
            column_names, primary_key_columns, text_primary_key_columns = table_columns(source, table_name)
            progress = destination.execute(
                "SELECT source_count,source_sha256 FROM tempo_migration_progress WHERE table_name=%s",
                (table_name,),
            ).fetchone()
            if verify_only:
                if progress is None:
                    raise RuntimeError(f"{table_name} has not been copied")
                source_count, source_hash = source_fingerprint(source, table_name, primary_key_columns)
                target_count, target_hash = destination_fingerprint(
                    destination, table_name, column_names, primary_key_columns,
                    text_primary_key_columns
                )
                if (source_count, source_hash) != (target_count, target_hash) or progress != (source_count, source_hash):
                    raise RuntimeError(
                        f"{table_name} mismatch: source {(source_count, source_hash)}, "
                        f"destination {(target_count, target_hash)}, progress {progress}"
                    )
                print(f"VERIFY {table_name}: {source_count} rows in {time.monotonic()-started:.1f}s", flush=True)
                continue
            if progress is not None:
                source_count, source_hash = source_fingerprint(source, table_name, primary_key_columns)
                target_count = destination.execute(
                    sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
                ).fetchone()[0]
                if progress != (source_count, source_hash) or target_count != source_count:
                    raise RuntimeError(f"Cannot resume {table_name}: source or destination changed")
                print(f"SKIP {table_name}: {source_count} rows", flush=True)
                continue
            target_count = destination.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table_name))
            ).fetchone()[0]
            if table_name == "repertoire_game_scope" and target_count == 1 and not verify_only:
                initial_generation = destination.execute("SELECT generation FROM repertoire_game_scope WHERE id=1").fetchone()[0]
                if initial_generation == 0:
                    destination.execute("DELETE FROM repertoire_game_scope WHERE id=1")
                    target_count = 0
            if target_count:
                raise RuntimeError(f"Cannot copy {table_name}: destination already has {target_count} rows")
            count, digest = copy_table(source, destination, table_name, column_names, primary_key_columns)
            print(f"COPY {table_name}: {count} rows, sha256={digest} in {time.monotonic()-started:.1f}s", flush=True)
        if not verify_only:
            reseed_identifiers(source, destination)
            destination.execute("ANALYZE")
            print("Copied all source tables and reseeded identity sequences", flush=True)
    finally:
        source.close()
        destination.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_MIGRATION_DATABASE_URL"))
    parser.add_argument("--verify-only", action="store_true")
    arguments = parser.parse_args()
    if not arguments.dsn:
        parser.error("Set --dsn or TEMPO_MIGRATION_DATABASE_URL")
    try:
        migrate(arguments.sqlite_path, arguments.dsn, arguments.verify_only)
    except Exception as error:
        print(f"Migration failed: {error}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
