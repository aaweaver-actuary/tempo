"""Regression coverage for maintenance snapshots of WAL-backed SQLite data."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from create_sqlite_cutover_snapshot import create_snapshot, verify_snapshot


def test_cutover_snapshot_includes_committed_uncheckpointed_wal_and_verifies_manifest(tmp_path):
    source_directory = tmp_path / "source"
    source_directory.mkdir()
    source_path = source_directory / "tempo.db"
    snapshot_path = tmp_path / "backups" / "tempo.db"
    source = sqlite3.connect(source_path)
    try:
        assert source.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        source.execute("PRAGMA wal_autocheckpoint=0")
        source.execute(
            "CREATE TABLE daily_queue (id INTEGER PRIMARY KEY, queue_date TEXT, "
            "position INTEGER, cycle INTEGER, card_id TEXT, status TEXT)"
        )
        source.commit()
        source.execute(
            "INSERT INTO daily_queue VALUES (1, '2026-09-27', 1, 0, 'card-1', 'ready')"
        )
        source.commit()
        assert source_path.with_name("tempo.db-wal").stat().st_size > 0

        manifest_path = create_snapshot(source_path, snapshot_path)
        manifest = verify_snapshot(snapshot_path, manifest_path)
        assert manifest["table_counts"] == {"daily_queue": 1}
        with sqlite3.connect(snapshot_path) as restored:
            assert restored.execute("SELECT card_id FROM daily_queue").fetchone() == ("card-1",)

        remounted_path = tmp_path / "remounted" / "tempo.db"
        remounted_path.parent.mkdir()
        shutil.copy2(snapshot_path, remounted_path)
        assert verify_snapshot(remounted_path, manifest_path)["snapshot_sha256"] == manifest["snapshot_sha256"]

        altered_manifest = json.loads(manifest_path.read_text())
        altered_manifest["queue_order_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(altered_manifest))
        with pytest.raises(RuntimeError, match="queue_order_sha256"):
            verify_snapshot(snapshot_path, manifest_path)
    finally:
        source.close()
