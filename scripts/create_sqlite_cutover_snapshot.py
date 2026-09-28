"""Create and verify a self-contained SQLite snapshot for PostgreSQL cutover.

Stop all Tempo writers first. SQLite's backup API includes committed WAL pages;
copying only tempo.db from a WAL database can silently omit recent study data.
Keep the output and its manifest outside the repository and source data volume.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from reclaim_priority_storage import queue_order_checksum


def _table_counts(database: sqlite3.Connection) -> dict[str, int]:
    table_names = [row[0] for row in database.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    counts: dict[str, int] = {}
    for table_name in table_names:
        quoted_table_name = '"' + table_name.replace('"', '""') + '"'
        counts[table_name] = database.execute(
            f"SELECT COUNT(*) FROM {quoted_table_name}"
        ).fetchone()[0]
    return counts


def _snapshot_manifest(snapshot_path: Path) -> dict:
    with closing(sqlite3.connect(snapshot_path.as_uri() + "?mode=ro", uri=True)) as snapshot:
        integrity_result = snapshot.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = snapshot.execute("PRAGMA foreign_key_check").fetchall()
        if integrity_result != "ok" or foreign_key_violations:
            raise RuntimeError(
                f"Snapshot failed SQLite checks: integrity={integrity_result}, "
                f"foreign_keys={len(foreign_key_violations)}"
            )
        table_counts = _table_counts(snapshot)
        queue_fingerprint = queue_order_checksum(snapshot)
    with snapshot_path.open("rb") as snapshot_file:
        database_digest = hashlib.file_digest(snapshot_file, "sha256").hexdigest()
    return {
        "snapshot_path": str(snapshot_path),
        "snapshot_bytes": snapshot_path.stat().st_size,
        "snapshot_sha256": database_digest,
        "integrity_check": integrity_result,
        "foreign_key_check_violations": 0,
        "table_counts": table_counts,
        "queue_order_sha256": queue_fingerprint,
    }


def _write_manifest(manifest_path: Path, manifest: dict) -> None:
    temporary_manifest = manifest_path.with_name(
        f".{manifest_path.name}.{uuid.uuid4().hex}.partial"
    )
    file_descriptor = os.open(temporary_manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary_manifest.replace(manifest_path)
    finally:
        temporary_manifest.unlink(missing_ok=True)


def create_snapshot(source_path: Path, snapshot_path: Path) -> Path:
    source_path = source_path.resolve(strict=True)
    snapshot_path = snapshot_path.resolve()
    if source_path == snapshot_path or source_path.parent == snapshot_path.parent:
        raise ValueError("Place the cutover snapshot outside the source data directory")
    if any((directory / ".git").exists() for directory in (snapshot_path.parent, *snapshot_path.parents)):
        raise ValueError("Place the cutover snapshot outside the repository")
    if snapshot_path.exists() or snapshot_path.with_suffix(snapshot_path.suffix + ".manifest.json").exists():
        raise FileExistsError("The snapshot or manifest already exists")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_snapshot = snapshot_path.with_name(
        f".{snapshot_path.name}.{uuid.uuid4().hex}.partial"
    )
    file_descriptor = os.open(temporary_snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(file_descriptor)
    try:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True, timeout=30)) as source:
            version_before = source.execute("PRAGMA data_version").fetchone()[0]
            with closing(sqlite3.connect(temporary_snapshot)) as destination:
                source.backup(destination, pages=1024, sleep=0.05)
            version_after = source.execute("PRAGMA data_version").fetchone()[0]
            if version_before != version_after:
                raise RuntimeError("SQLite changed during backup; stop writers and retry")
        with temporary_snapshot.open("rb") as snapshot_file:
            os.fsync(snapshot_file.fileno())
        temporary_snapshot.replace(snapshot_path)
        manifest = _snapshot_manifest(snapshot_path)
        manifest.update({
            "source_path": str(source_path),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        manifest_path = snapshot_path.with_suffix(snapshot_path.suffix + ".manifest.json")
        _write_manifest(manifest_path, manifest)
        verify_snapshot(snapshot_path, manifest_path)
        return manifest_path
    finally:
        temporary_snapshot.unlink(missing_ok=True)


def verify_snapshot(snapshot_path: Path, manifest_path: Path) -> dict:
    expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = _snapshot_manifest(snapshot_path.resolve(strict=True))
    for field, value in actual.items():
        if field == "snapshot_path":
            # Docker and the host mount the same verified bytes at different
            # paths. The digest, counts, and queue fingerprint bind identity.
            continue
        if expected.get(field) != value:
            raise RuntimeError(f"Snapshot manifest mismatch for {field}")
    return actual


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create_command = commands.add_parser("create")
    create_command.add_argument("source", type=Path)
    create_command.add_argument("snapshot", type=Path)
    verify_command = commands.add_parser("verify")
    verify_command.add_argument("snapshot", type=Path)
    verify_command.add_argument("manifest", type=Path)
    arguments = parser.parse_args()
    if arguments.command == "create":
        manifest_path = create_snapshot(arguments.source, arguments.snapshot)
        print(f"Created and verified {arguments.snapshot}; manifest {manifest_path}")
    else:
        result = verify_snapshot(arguments.snapshot, arguments.manifest)
        print(f"Verified {len(result['table_counts'])} tables and queue order")


if __name__ == "__main__":
    main()
