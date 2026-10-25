"""Primary source work remains usable without optional adaptive-priority effects."""

from contextlib import contextmanager
import json

from app import canonical_prefix_api
from app.services import (
    game_sync_coordinator, introduction_priorities, postgres_coverage_explorer,
    postgres_coverage_seed, repertoire_opportunities,
)


def unavailable_priority(*_args, **_kwargs):
    raise AssertionError("Retired optional adaptive-priority callback was invoked")


def test_prefix_save_preserves_source_and_coverage_without_optional_priority(monkeypatch):
    database = object()
    effects = []
    storage = canonical_prefix_api.storage
    monkeypatch.setattr(storage, "repertoire_presence", lambda *_args: True)
    monkeypatch.setattr(canonical_prefix_api, "read_prefix", lambda *_args, **_kwargs: {
        "moves": [], "revision": 2, "source_revision": 9,
    })
    monkeypatch.setattr(storage, "prefix_preview", lambda *_args: {
        "state": "ready", "expected_revision": 2, "source_revision": 9,
        "moves_json": '["e2e4"]',
    })
    monkeypatch.setattr(storage, "postgres_is_configured", lambda: True)
    monkeypatch.setattr(storage, "save_prefix_values", lambda *args: effects.append(("prefix", args)))
    monkeypatch.setattr(canonical_prefix_api, "enqueue_task_in_transaction",
                        lambda *args, **kwargs: effects.append(("graph-refresh", args, kwargs)))
    monkeypatch.setattr(repertoire_opportunities, "enqueue_opportunity_refresh_in_transaction",
                        lambda *args: effects.append(("opportunity", args)))
    monkeypatch.setattr(storage, "advance_priority_source_epoch",
                        lambda *args: effects.append(("source-epoch", args)))
    monkeypatch.setattr(postgres_coverage_seed, "request_coverage_seed_in_transaction",
                        lambda *args, **kwargs: effects.append(("coverage", args, kwargs)))
    monkeypatch.setattr(storage, "delete_priority_publication",
                        lambda *args: effects.append(("invalidate-publication", args)))
    monkeypatch.setattr(storage, "delete_introduction_priorities",
                        lambda *args: effects.append(("invalidate-scores", args)))
    monkeypatch.setattr(introduction_priorities, "enqueue_priority_refresh_in_transaction", unavailable_priority)

    result = canonical_prefix_api.save_prefix(database, {"repertoire_id": "rep", "request": {
        "preview_id": "preview", "expected_revision": 2,
    }})

    assert result["revision"] == 3 and result["moves_uci"] == ["e2e4"]
    assert effects == [
        ("prefix", (database, json.dumps(["e2e4"]), 3, "preview", "rep")),
        ("graph-refresh", (database, "repertoire_game_refresh", "all", {"after_game_id": ""}), {"priority": 90}),
        ("opportunity", (database, "rep")),
        ("source-epoch", (database, "rep")),
        ("coverage", (database, "rep"), {"automatic": True, "supersede_active": True}),
        ("invalidate-publication", (database, "rep")),
        ("invalidate-scores", (database, "rep")),
    ]


def test_native_explorer_publication_preserves_progress_and_cursor_without_optional_priority(monkeypatch):
    explorer = postgres_coverage_explorer
    database = object()
    effects = []
    node = {"id": "node", "repertoire_id": "rep", "run_id": "run", "run_status": "running",
            "explorer_status": "queued", "covered_replies_json": '["e7e5"]',
            "fen_key": "position", "settings_json": "{}"}
    task = {"payload": {"run_id": "run", "after_node_id": ""}}
    monkeypatch.setattr(explorer, "read_prefix", lambda *args, **kwargs: effects.append(("prefix-lock", args, kwargs)))
    monkeypatch.setattr(explorer, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(explorer, "coverage_run_is_current", lambda *_args: True)
    monkeypatch.setattr(explorer.storage, "locked_node", lambda *_args: node)
    for name in ("upsert_cache", "upsert_candidates", "complete_node", "publish_progress"):
        monkeypatch.setattr(explorer.storage, name,
                            lambda *args, name=name: effects.append((name, args)))
    monkeypatch.setattr(explorer.storage, "advance_run", lambda *_args: {"completed_nodes": 1, "total_nodes": 2})
    monkeypatch.setattr(explorer.storage, "maia_completed_count", lambda *_args: 1)
    monkeypatch.setattr(explorer, "_now", lambda: "now")
    monkeypatch.setattr(explorer, "recalculate_coverage_node",
                        lambda *args: effects.append(("recalculate", args)))
    monkeypatch.setattr(explorer, "enqueue_priority_refresh_in_transaction", unavailable_priority, raising=False)
    monkeypatch.setattr(explorer, "enqueue_opportunity_refresh_in_transaction",
                        lambda *args: effects.append(("opportunity", args)))
    monkeypatch.setattr(explorer, "advance_task_slice_in_transaction",
                        lambda *args, **kwargs: effects.append(("cursor", args, kwargs)) or True)

    assert explorer._publish_node(database, task, {"id": "node", "repertoire_id": "rep"}, {
        "moves": [{"uci": "e7e5", "white": 6, "draws": 2, "black": 2}],
    }, "cache", "rapid:1.000000", "1600") is True

    assert [effect[0] for effect in effects] == [
        "prefix-lock", "upsert_cache", "upsert_candidates", "recalculate",
        "complete_node", "publish_progress", "opportunity", "cursor",
    ]
    assert effects[0] == ("prefix-lock", (database, "rep"), {"lock": True})
    assert json.loads(effects[2][1][1][1]) == [
        {"move_uci": "e7e5", "explorer_probability": 1.0, "covered": 1},
    ]
    assert effects[3] == ("recalculate", (database, "node", {}, 10))
    assert effects[4] == ("complete_node", (database, (10, "now", "node")))
    assert effects[5] == ("publish_progress", (database, ("run", "run", 2, 4, "now")))
    assert effects[6] == ("opportunity", (database, "rep"))
    assert effects[7] == ("cursor", (database, task), {
        "next_phase": "explorer", "next_payload": {"run_id": "run", "after_node_id": "node"},
    })


def test_local_game_handoff_preserves_opportunities_without_optional_priority(monkeypatch):
    coordinator = game_sync_coordinator
    database = object()
    open_read = False
    opportunities = []
    foreground_waits = []

    @contextmanager
    def background_connection(*, background):
        nonlocal open_read
        assert background
        open_read = True
        try:
            yield database
        finally:
            open_read = False

    def matching_repertoires(actual_database, game_id):
        assert open_read and actual_database is database and game_id == "game"
        return ["white-repertoire", "black-repertoire"]

    def opportunity(repertoire_id, *, background):
        assert not open_read and background
        opportunities.append(repertoire_id)

    monkeypatch.setattr(coordinator, "connection", background_connection)
    monkeypatch.setattr(coordinator.storage, "matching_repertoire_ids", matching_repertoires)
    monkeypatch.setattr(coordinator, "enqueue_priority_refreshes_for_game", unavailable_priority, raising=False)
    monkeypatch.setattr(coordinator, "enqueue_opportunity_refresh", opportunity)
    monkeypatch.setattr(coordinator.activity_gate, "wait_for_foreground", lambda: foreground_waits.append(True))

    coordinator._enqueue_game_repertoire_refreshes("game")

    assert opportunities == ["white-repertoire", "black-repertoire"]
    assert foreground_waits == [True, True]
