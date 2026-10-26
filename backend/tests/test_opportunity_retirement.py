"""Ordinary Study and saved graph facts work without the opportunity producer."""
from datetime import date, datetime, timezone
import importlib
import json

import pytest
from fastapi.testclient import TestClient

from tempo_legacy_database import connections as database
from app import command_gateway, main
from test_repertoire_coverage import _seed_decision_route


@pytest.fixture
def graph_store(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "opportunity-retirement.sqlite")
    database.initialize()
    with database.connection() as store:
        _, target_key = _seed_decision_route(store)
    return target_key


def test_retired_opportunity_modules_are_physically_absent_after_application_composition():
    for name in (
        "app.services.repertoire_opportunities",
        "app.opportunity_commands",
        "tempo_legacy_database.adapters.authored_repertoires.opportunities.storage",
        "tempo_legacy_database.adapters.authored_repertoires.discovery_commands",
    ):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(name)
    assert main.app is not None


def test_retired_opportunity_routes_reject_requests_without_queueing_work(graph_store, monkeypatch):
    client = TestClient(main.app)
    assert client.get("/api/repertoires/rep/opportunities").status_code == 404
    assert client.get("/api/discoveries").status_code == 404
    assert client.get("/api/repertoires/rep/opportunities/saved/training-eligibility").status_code == 404
    for suffix in ("refresh", "saved/dismiss", "saved/acknowledge", "saved/snooze", "saved/train"):
        response = client.post(f"/api/repertoires/rep/opportunities/{suffix}", json={})
        assert response.status_code == 404
    with database.read_connection() as store:
        assert store.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone()[0] == 0
    def unexpected_receipt_writer(*_args, **_kwargs):
        raise AssertionError("An unknown retired command must not open receipt storage")
    monkeypatch.setattr(command_gateway, "_writer_connection", unexpected_receipt_writer)
    for action in ("refresh", "dismiss", "acknowledge", "snooze", "train"):
        with pytest.raises(ValueError, match="Unknown command"):
            command_gateway.execute_command("retired-opportunity", f"opportunities.{action}", {})


def test_ordinary_study_introduces_a_saved_graph_card_without_opportunity_generation(graph_store):
    day = date.today().isoformat()
    with database.connection() as store:
        main.seed_queue(store, day)
        queued = store.execute("SELECT card_id FROM daily_queue WHERE queue_date=? AND status='queued'", (day,)).fetchall()
        assert [row["card_id"] for row in queued] == ["root"]
        assert store.execute("SELECT COUNT(*) FROM repertoire_opportunities").fetchone()[0] == 0
        assert store.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert store.execute("SELECT parent_card_id FROM opening_graph_steps WHERE card_id='target'").fetchone()[0] == "root"


def test_saved_weak_decision_still_uses_the_ordinary_queue_owner_without_a_producer(graph_store):
    now = datetime.now(timezone.utc).isoformat()
    day = date.today().isoformat()
    with database.connection() as store:
        store.execute("UPDATE settings SET new_cards_per_day=1 WHERE id=1")
        store.execute("""INSERT INTO repertoire_opportunities(
            id,repertoire_id,kind,fen_key,card_id,status,score,evidence_json,evidence_fingerprint,
            created_at,updated_at) VALUES('saved','rep','weak_known_decision',?,'target','active',1,?,'saved-evidence',?,?)""",
            (graph_store, json.dumps({"encounter_count": 5, "miss_count": 4}), now, now))
        main.seed_queue(store, day)
        queued = store.execute("SELECT * FROM daily_queue WHERE card_id='target' AND queue_date=?", (day,)).fetchone()
        assert queued is not None
        assert queued["gameplay_priority_reason"] == "Priority introduction · reached 5 times in games, missed 4 times"
        assert store.execute("SELECT state FROM cards WHERE id='root'").fetchone()[0] == "new"
        assert store.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert store.execute("SELECT parent_card_id FROM opening_graph_steps WHERE card_id='target'").fetchone()[0] == "root"
        assert store.execute("SELECT status FROM repertoire_opportunities WHERE id='saved'").fetchone()[0] == "resolved"
