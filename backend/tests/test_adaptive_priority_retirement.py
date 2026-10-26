"""Retained graph/repertoire/Study boundaries with adaptive producer source absent."""

from importlib.util import find_spec

from tempo_legacy_database import connections as database
from app import main, tasks
from app.services import background_activity, game_sync_coordinator


def test_adaptive_priority_source_absence_keeps_primary_runtime_registration():
    for module in ("introduction_priorities", "postgres_priority", "priority_retention", "refresh_requests"):
        assert find_spec("app.services." + module) is None
    assert not {"repertoire_priority", "priority_retention"} & set(tasks._SUPPORTED_BACKGROUND_KINDS)
    assert "daily_queue" in tasks._SUPPORTED_BACKGROUND_KINDS
    assert "opening_graph_rebuild" in tasks._SUPPORTED_BACKGROUND_KINDS
    assert "game_derivation_features" in tasks._SUPPORTED_BACKGROUND_KINDS
    assert "game_derivation_priorities" in tasks._SUPPORTED_BACKGROUND_KINDS
    assert "priority_retention" not in game_sync_coordinator._durable_task_handlers


def test_repertoire_definition_response_omits_retired_adaptive_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "definitions.db")
    database.initialize()
    with database.connection() as connection:
        connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('source','Source','source.pgn','2026-10-25')"
        )
    item = next(item for item in main.list_repertoires()["repertoires"] if item["id"] == "source")
    assert item["name"] == "Source" and item["line_count"] == 0
    assert item["canonical_prefix"]["moves_uci"] == []
    assert "introduction_priority" not in item


def test_retired_priority_manual_control_cannot_open_storage(monkeypatch):
    def unavailable_storage(*_args, **_kwargs):
        raise AssertionError("Retired priority control opened storage")

    monkeypatch.setattr(background_activity, "connection", unavailable_storage)
    assert background_activity.set_control("priority", "discarded-job", "resume") is False
