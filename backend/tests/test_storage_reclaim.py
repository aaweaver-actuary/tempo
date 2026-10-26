"""The retained snapshot checksum reader needs no priority cleanup implementation."""

import hashlib
import sqlite3
from pathlib import Path
import subprocess
import sys

from database.tooling.reclaim_priority_storage import queue_order_checksum


def test_visible_queue_checksum_preserves_order_cycle_and_status_without_cleanup():
    with sqlite3.connect(":memory:") as database:
        database.execute("""CREATE TABLE daily_queue(
            id INTEGER PRIMARY KEY,queue_date TEXT,position INTEGER,cycle INTEGER,card_id TEXT,status TEXT)""")
        database.executemany("INSERT INTO daily_queue VALUES(?,?,?,?,?,?)", [
            (2, "2026-10-25", 1, 1, "b", "complete"),
            (1, "2026-10-25", 0, 0, "a", "queued"),
        ])
        expected = (b'["2026-10-25",0,0,"a","queued"]\n'
                    b'["2026-10-25",1,1,"b","complete"]\n')
        before = queue_order_checksum(database)
        assert before == hashlib.sha256(expected).hexdigest()
        assert list(database.execute("SELECT id,position,status FROM daily_queue ORDER BY id")) == [
            (1, 0, "queued"), (2, 1, "complete"),
        ]
        database.execute("UPDATE daily_queue SET position=2 WHERE id=1")
        assert queue_order_checksum(database) != before


def test_retired_priority_cleanup_cli_rejects_before_opening_a_database(tmp_path):
    target = tmp_path / "must-not-create.sqlite"
    script = Path(__file__).resolve().parents[2] / "database/tooling/reclaim_priority_storage.py"
    result = subprocess.run([sys.executable, str(script), "--reclaim", str(target)],
                            capture_output=True, text=True)
    assert result.returncode != 0 and "retired" in result.stderr
    assert not target.exists()
