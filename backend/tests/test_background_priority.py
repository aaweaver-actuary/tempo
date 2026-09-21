from datetime import datetime, timezone
import json
import threading

import pytest
from fastapi.testclient import TestClient

from app import database
from app.main import app
from app.services import repertoire_integrity
from app.services.activity_gate import (
    ApplicationActivityGate,
    BackgroundContractError,
    activity_gate,
)


START = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def _seed_repertoire(db, identifier="background-rep", *, lines=1):
    now = datetime.now(timezone.utc).isoformat()
    db.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
        (identifier, "Background", "background.pgn", now),
    )
    for index in range(lines):
        db.execute(
            "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (f"line-{index}", identifier, "Line", "white", START, json.dumps(["e2e4"]), now),
        )


def test_startup_serves_training_and_tactics_while_integrity_sweep_is_running(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db, lines=3)
    entered = threading.Event()
    release = threading.Event()
    original_scan = repertoire_integrity._scan_source

    def blocked_scan(*args, **kwargs):
        entered.set()
        assert release.wait(timeout=3)
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(repertoire_integrity, "_scan_source", blocked_scan)
    with TestClient(app) as client:
        assert entered.wait(timeout=2)
        assert client.get("/api/queue/today").status_code == 200
        assert client.get("/api/tactics/progress").status_code == 200
        release.set()


def test_foreground_review_preempts_each_background_database_slice():
    gate = ApplicationActivityGate()
    section_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()

    def background_slice():
        with gate.background_job("test", "slice"):
            with gate.background_database_section():
                section_started.set()
                assert release.wait(timeout=2)
            with gate.background_database_section():
                second_started.set()

    worker = threading.Thread(target=background_slice)
    worker.start()
    assert section_started.wait(timeout=1)
    with gate.foreground():
        release.set()
        assert not second_started.wait(timeout=0.1)
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert second_started.is_set()


def test_background_connection_contract_rejects_foreground_connection():
    gate = ApplicationActivityGate()
    with gate.background_job("test", "contract"):
        with pytest.raises(BackgroundContractError):
            with database.connection():
                pass


def test_foreground_connection_waits_for_active_background_section(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    section_started = threading.Event()
    release = threading.Event()
    foreground_finished = threading.Event()

    def background_slice():
        with activity_gate.background_job("test", "database-section"):
            with activity_gate.background_database_section():
                section_started.set()
                assert release.wait(timeout=2)

    def foreground_read():
        with database.connection():
            pass
        foreground_finished.set()

    worker = threading.Thread(target=background_slice)
    worker.start()
    assert section_started.wait(timeout=1)
    reader = threading.Thread(target=foreground_read)
    reader.start()
    assert not foreground_finished.wait(timeout=0.1)
    release.set()
    reader.join(timeout=2)
    worker.join(timeout=2)
    assert foreground_finished.is_set()
    assert not reader.is_alive()
    assert not worker.is_alive()


def test_large_integrity_sweep_commits_and_resumes_one_source_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db, lines=3)
    repertoire_integrity.enqueue_integrity_scans("background-rep")
    completed = 0
    while True:
        job = repertoire_integrity.claim_integrity_slice()
        assert job is not None
        repertoire_integrity.execute_integrity_slice(job)
        with database.connection() as db:
            completed = db.execute(
                "SELECT scan_completed_sources FROM repertoire_integrity_state WHERE repertoire_id='background-rep'"
            ).fetchone()[0]
        if job["status"] == "finalizing":
            break
        assert completed == job["source_offset"] + 1
    summary = None
    with database.connection() as db:
        summary = repertoire_integrity.integrity_summary(db, "background-rep")
        assert db.execute(
            "SELECT COUNT(*) FROM repertoire_integrity_issues WHERE repertoire_id='background-rep'"
        ).fetchone()[0] == 0
    assert summary["scan_status"] == "idle"
    assert summary["scan_progress"]["completed"] == summary["scan_progress"]["total"]


def test_last_known_good_repertoire_remains_trainable_during_rescan(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_repertoire(db)
        repertoire_integrity.sweep_repertoire(db, "background-rep")
    repertoire_integrity.enqueue_integrity_scans("background-rep")
    with database.connection() as db:
        summary = repertoire_integrity.integrity_summary(db, "background-rep")
    assert summary["status"] == "clean"
    assert summary["scan_status"] == "queued"


def test_never_validated_repertoire_is_quarantined_without_blocking_tactics(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    with TestClient(app) as client:
        with database.connection() as db:
            _seed_repertoire(db)
        response = client.get("/api/queue/today")
        assert response.status_code == 200
        assert client.get("/api/tactics/progress").status_code == 200
