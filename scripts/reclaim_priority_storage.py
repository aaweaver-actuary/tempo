"""Offline, resumable cleanup for oversized derived priority generations.

Stop the Tempo API and verify an external backup before using --reclaim or --compact.
The fingerprint excludes only the derived priority-generation table that this tool
changes. It is designed for an operator, never for application startup.
"""

from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3


DEFAULT_BATCH_ROWS = 200

DatabaseFingerprint = dict[str, dict[str, str | int]]
DatabaseStorageSummary = dict[str, int | str | list[dict[str, int]]]


def open_database(path: Path, *, read_only: bool) -> sqlite3.Connection:
    """Open a SQLite database with the specified read-only mode."""
    if not path.is_file():
        raise FileNotFoundError(path)
    database = sqlite3.connect(
        f"file:{path}?mode={'ro' if read_only else 'rw'}", uri=True, timeout=5
    )
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys=ON")
    database.execute("PRAGMA busy_timeout=5000")
    if read_only:
        database.execute("PRAGMA query_only=ON")
    else:
        database.execute("PRAGMA synchronous=FULL")
    return database


def _encoded_row(row: sqlite3.Row) -> bytes:
    """Encode a SQLite row as a JSON byte string."""
    values = [value.hex() if isinstance(value, bytes) else value for value in row]
    return json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fingerprints(database: sqlite3.Connection) -> DatabaseFingerprint:
    """Hash every non-ephemeral table without materializing its rows in memory.

    A dictionary mapping table names to their row count and SHA-256 hash.

    Args:
        database: A SQLite connection to the database to fingerprint.

    Returns:
        A dictionary mapping table names to their row count and SHA-256 hash.

    """

    table_rows = database.execute(
        """SELECT name,sql FROM sqlite_master WHERE type='table'
           AND name NOT LIKE 'sqlite_%' AND name<>'repertoire_card_priority_generations'
           ORDER BY name"""
    ).fetchall()
    results: dict[str, dict[str, str | int]] = {}
    for table_row in table_rows:
        table_name = table_row["name"]
        quoted_table_name = '"' + table_name.replace('"', '""') + '"'
        primary_key_columns = sorted(
            (column["pk"], column["name"])
            for column in database.execute(f"PRAGMA table_info({quoted_table_name})")
            if column["pk"]
        )
        if not primary_key_columns:
            raise RuntimeError(f"Cannot fingerprint {table_name} without a stable primary key")
        order_clause = ",".join(
            '"' + column_name.replace('"', '""') + '"'
            for _, column_name in primary_key_columns
        )
        digest = hashlib.sha256()
        digest.update((table_row["sql"] or "").encode("utf-8"))
        row_count = 0
        for row in database.execute(f"SELECT * FROM {quoted_table_name} ORDER BY {order_clause}"):
            digest.update(b"\n")
            digest.update(_encoded_row(row))
            row_count += 1
        results[table_name] = {"rows": row_count, "sha256": digest.hexdigest()}
    return results


def queue_order_checksum(database: sqlite3.Connection) -> str:
    """Hash the exact visible queue sequence before and after maintenance."""
    digest = hashlib.sha256()
    for queue_entry in database.execute(
        """SELECT queue_date,position,cycle,card_id,status FROM daily_queue
           ORDER BY queue_date,position,id"""
    ):
        digest.update(_encoded_row(queue_entry))
        digest.update(b"\n")
    return digest.hexdigest()


def storage_summary(database: sqlite3.Connection, path: Path) -> DatabaseStorageSummary:
    page_size = database.execute("PRAGMA page_size").fetchone()[0]
    page_count = database.execute("PRAGMA page_count").fetchone()[0]
    free_pages = database.execute("PRAGMA freelist_count").fetchone()[0]
    return {
        "file_bytes": path.stat().st_size,
        "page_size": page_size,
        "page_count": page_count,
        "free_pages": free_pages,
        "estimated_free_bytes": page_size * free_pages,
        "queue_order_sha256": queue_order_checksum(database),
        "priority_rows": database.execute(
            "SELECT COUNT(*) FROM repertoire_card_priority_generations"
        ).fetchone()[0],
        "published": [
            dict(row)
            for row in database.execute(
                "SELECT repertoire_id,generation FROM repertoire_priority_publications ORDER BY repertoire_id"
            )
        ],
    }


def _checkpoint(database: sqlite3.Connection) -> None:
    checkpoint = database.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    if checkpoint[0] != 0:
        raise RuntimeError(f"WAL checkpoint was blocked: {tuple(checkpoint)}")


def reclaim(
    database: sqlite3.Connection, *, batch_rows: int = DEFAULT_BATCH_ROWS
) -> int:
    """Remove stale generations and duplicated evidence in committed slices."""

    if batch_rows < 1:
        raise ValueError("batch_rows must be positive")
    publications = [
        (row["repertoire_id"], int(row["generation"]))
        for row in database.execute(
            "SELECT repertoire_id,generation FROM repertoire_priority_publications"
        )
    ]
    deleted_rows = 0
    for repertoire_id, published_generation in publications:
        job = database.execute(
            "SELECT generation FROM repertoire_priority_jobs WHERE repertoire_id=?",
            (repertoire_id,),
        ).fetchone()
        active_generation = int(job["generation"]) if job else published_generation
        stale_generations = [
            int(row["generation"])
            for row in database.execute(
                """SELECT DISTINCT generation FROM repertoire_card_priority_generations
                   WHERE repertoire_id=? AND generation<>? AND generation<>?
                   ORDER BY generation""",
                (repertoire_id, published_generation, active_generation),
            )
        ]
        for stale_generation in stale_generations:
            while True:
                row_ids = [
                    row["rowid"]
                    for row in database.execute(
                        """SELECT rowid FROM repertoire_card_priority_generations
                           WHERE repertoire_id=? AND generation=? LIMIT ?""",
                        (repertoire_id, stale_generation, batch_rows),
                    )
                ]
                if not row_ids:
                    break
                with database:
                    database.executemany(
                        "DELETE FROM repertoire_card_priority_generations WHERE rowid=?",
                        [(row_id,) for row_id in row_ids],
                    )
                deleted_rows += len(row_ids)
                if deleted_rows % 10_000 < batch_rows:
                    _checkpoint(database)
                    print(f"deleted priority rows: {deleted_rows}", flush=True)
        _checkpoint(database)

        stripped_rows = 0
        while True:
            current_row_ids = [
                row["rowid"]
                for row in database.execute(
                    """SELECT rowid FROM repertoire_card_priority_generations
                       WHERE repertoire_id=? AND generation=?
                         AND json_type(evidence_json,'$.edge_states') IS NOT NULL
                       LIMIT ?""",
                    (repertoire_id, published_generation, min(batch_rows, 25)),
                )
            ]
            if not current_row_ids:
                break
            with database:
                database.executemany(
                    """UPDATE repertoire_card_priority_generations
                       SET evidence_json=json_remove(evidence_json,'$.edge_states')
                       WHERE rowid=?""",
                    [(row_id,) for row_id in current_row_ids],
                )
            stripped_rows += len(current_row_ids)
            if stripped_rows % 250 < len(current_row_ids):
                _checkpoint(database)
        _checkpoint(database)
        print(
            f"repertoire {repertoire_id}: kept generation {published_generation}, "
            f"stripped evidence from {stripped_rows} rows",
            flush=True,
        )
    return deleted_rows


def compact(database: sqlite3.Connection) -> None:
    _checkpoint(database)
    database.execute("VACUUM")
    _checkpoint(database)
    if database.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity check failed")
    if database.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("SQLite foreign-key check failed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--report", action="store_true")
    action.add_argument("--fingerprint", type=Path)
    action.add_argument("--verify-fingerprint", type=Path)
    action.add_argument("--reclaim", action="store_true")
    action.add_argument("--compact", action="store_true")
    parser.add_argument("--batch-rows", type=int, default=DEFAULT_BATCH_ROWS)
    args = parser.parse_args()
    read_only = not (args.reclaim or args.compact)
    with closing(open_database(args.database, read_only=read_only)) as database:
        if args.report:
            print(json.dumps(storage_summary(database, args.database), indent=2))
        elif args.fingerprint:
            args.fingerprint.write_text(
                json.dumps(fingerprints(database), sort_keys=True, indent=2) + "\n"
            )
            print(f"wrote {args.fingerprint}")
        elif args.verify_fingerprint:
            expected = json.loads(args.verify_fingerprint.read_text())
            actual = fingerprints(database)
            if actual != expected:
                changed = sorted(
                    name
                    for name in set(actual) | set(expected)
                    if actual.get(name) != expected.get(name)
                )
                raise RuntimeError(f"Non-priority tables changed: {changed}")
            print("non-priority table fingerprints match")
        elif args.reclaim:
            print(f"deleted {reclaim(database, batch_rows=args.batch_rows)} stale rows")
        elif args.compact:
            compact(database)
            print(json.dumps(storage_summary(database, args.database), indent=2))


if __name__ == "__main__":
    main()
