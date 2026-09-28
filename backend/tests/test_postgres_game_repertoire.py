"""Named regressions for bounded, versioned game-repertoire comparison."""

from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path
import threading

from app import tasks
from app.services import postgres_game_repertoire, repertoire_comparison


def test_postgres_game_repertoire_import_keeps_three_legacy_views_until_publication():
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    sys.path.insert(0, str(scripts))
    try:
        from migrate_sqlite_to_postgres import COPY_TARGETS
    finally:
        sys.path.pop(0)
    migration = (Path(__file__).resolve().parents[1] / "migrations"
                 / "012_versioned_game_repertoire.sql").read_text()
    for table in ("game_repertoire_matches", "repertoire_decision_events",
                  "repertoire_comparisons"):
        assert COPY_TARGETS[table] == f"{table}_legacy"
        assert f"CREATE VIEW {table} AS" in migration
    assert migration.count("COALESCE(job.published_repertoire_version,0)=0") == 3
    assert migration.count("job.published_repertoire_version=staged.derivation_version") == 3


def test_postgres_game_repertoire_stages_one_item_and_switches_all_views_together(monkeypatch):
    assert "game_derivation_compare" in tasks._SUPPORTED_BACKGROUND_KINDS
    game = {"id": "game-one", "played_at": "2026-09-27T12:00:00Z"}
    matches = [
        {"repertoire_id": "rep-one", "classification": "covered",
         "deviation": None, "decision_events": [{"ply": 0}]},
        {"repertoire_id": "rep-two", "classification": "covered",
         "deviation": None, "decision_events": []},
    ]
    stored = {"matches": [], "events": [], "published": False, "lease_current": True,
              "followups": []}
    advances = []
    task = {"id": "compare", "generation": 2, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 3,
                        "phase": "matches", "cursor": 0}}

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Database:
        def execute(self, statement, _parameters):
            if "FROM game_derivation_jobs" in statement:
                return Cursor({"derivation_version": 3, "completed_phases": 1,
                               "status": "queued"})
            if "COUNT(*) FROM game_repertoire_matches_staged" in statement:
                return Cursor((len(stored["matches"]),))
            if "COUNT(*) FROM repertoire_decision_events_staged" in statement:
                return Cursor((len(stored["events"]),))
            if "published_repertoire_version" in statement:
                stored["published"] = True
            return Cursor()

    @contextmanager
    def open_database(*, background):
        assert background
        yield Database()

    monkeypatch.setattr(postgres_game_repertoire, "_prepared_comparison",
                        lambda _game_id: (game, "source-one", matches))
    monkeypatch.setattr(postgres_game_repertoire, "connection", open_database)
    monkeypatch.setattr(postgres_game_repertoire, "lock_current_slice",
                        lambda *_arguments: stored["lease_current"])
    monkeypatch.setattr(postgres_game_repertoire, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        advances.append((next_phase, next_payload)) or True)
    monkeypatch.setattr(postgres_game_repertoire, "complete_task_slice_in_transaction",
                        lambda *_arguments: True)
    monkeypatch.setattr(postgres_game_repertoire,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        stored["followups"].append((kind, key, payload, priority)))
    monkeypatch.setattr(postgres_game_repertoire, "_stage_match",
                        lambda _database, _game_id, _version, index, _match:
                        stored["matches"].append(index))
    monkeypatch.setattr(postgres_game_repertoire, "_stage_primary_event",
                        lambda _database, _game, _version, _primary, event:
                        stored["events"].append(event["ply"]))

    for phase, cursor in (("matches", 0), ("matches", 1), ("matches", 2),
                          ("events", 0), ("events", 1)):
        task["payload"] = {**task["payload"], "phase": phase, "cursor": cursor}
        assert postgres_game_repertoire.execute_game_repertoire_comparison_slice(task)
        assert not stored["published"]
    assert stored["matches"] == [0, 1]
    assert stored["events"] == [0]
    assert advances[-1][0] == "publish"
    task["payload"] = {**task["payload"], "phase": "publish"}
    assert postgres_game_repertoire.execute_game_repertoire_comparison_slice(task)
    assert stored["published"]
    assert stored["followups"] == [
        ("game_derivation_findings", "game-one",
         {"game_id": "game-one", "derivation_version": 3,
          "phase": "stage", "cursor": 0}, 126),
    ]
    stored["lease_current"] = False
    assert not postgres_game_repertoire.execute_game_repertoire_comparison_slice(task)


def test_postgres_game_repertoire_restarts_on_source_change_without_publishing(monkeypatch):
    game = {"id": "game-one", "played_at": "2026-09-27T12:00:00Z"}
    updates = []
    enqueued = []

    class Cursor:
        def fetchone(self):
            return {"derivation_version": 3, "completed_phases": 1, "status": "queued"}

    class Database:
        def execute(self, statement, parameters):
            updates.append((statement, parameters))
            return Cursor()

    @contextmanager
    def open_database(*, background):
        yield Database()

    monkeypatch.setattr(postgres_game_repertoire, "_prepared_comparison",
                        lambda _game_id: (game, "new-source", []))
    monkeypatch.setattr(postgres_game_repertoire, "connection", open_database)
    monkeypatch.setattr(postgres_game_repertoire, "lock_current_slice", lambda *_arguments: True)
    monkeypatch.setattr(postgres_game_repertoire,
                        "enqueue_compact_postgres_task_in_transaction",
                        lambda _database, kind, key, payload, *, priority:
                        enqueued.append((kind, key, payload, priority)))
    task = {"id": "compare", "generation": 2, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 3,
                        "phase": "events", "cursor": 2, "source_signature": "old-source"}}
    assert postgres_game_repertoire.execute_game_repertoire_comparison_slice(task)
    assert any("SET derivation_version=?" in statement for statement, _ in updates)
    assert enqueued == [("game_derivation_compare", "game-one",
                         {"game_id": "game-one", "derivation_version": 4,
                          "phase": "matches", "cursor": 0}, 127)]
    assert not any("published_repertoire_version" in statement for statement, _ in updates)


def test_postgres_game_repertoire_foreground_can_run_during_chess_computation(monkeypatch):
    from app.services.activity_gate import activity_gate

    computation_started = threading.Event()
    resume_computation = threading.Event()
    write_started = threading.Event()
    task = {"id": "compare", "generation": 2, "lease_token": "live",
            "payload": {"game_id": "game-one", "derivation_version": 3,
                        "phase": "matches", "cursor": 0}}

    class Cursor:
        def fetchone(self):
            return {"derivation_version": 3, "completed_phases": 1, "status": "queued"}

    class Database:
        def execute(self, _statement, _parameters):
            return Cursor()

    @contextmanager
    def open_database(*, background):
        assert background
        write_started.set()
        yield Database()

    def paused_comparison(_game_id):
        computation_started.set()
        assert resume_computation.wait(5)
        return {"id": "game-one"}, "source", []

    monkeypatch.setattr(postgres_game_repertoire, "_prepared_comparison", paused_comparison)
    monkeypatch.setattr(postgres_game_repertoire, "connection", open_database)
    monkeypatch.setattr(postgres_game_repertoire, "lock_current_slice", lambda *_arguments: True)
    monkeypatch.setattr(postgres_game_repertoire, "advance_task_slice_in_transaction",
                        lambda *_arguments, **_keyword_arguments: True)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(postgres_game_repertoire.execute_game_repertoire_comparison_slice,
                             task)
        assert computation_started.wait(2)
        assert not write_started.is_set()
        with activity_gate.foreground():
            assert not write_started.is_set()
        resume_computation.set()
        assert worker.result(timeout=5)
    assert write_started.is_set()


def test_postgres_repertoire_index_reads_bounded_pages_and_reuses_source_digest(monkeypatch):
    start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    lines = [{"id": f"line-{index:03}", "repertoire_id": "rep",
              "trained_color": "white", "start_fen": start_fen, "moves_json": "[]"}
             for index in range(65)]
    cards = [{"repertoire_id": "rep", "id": f"card-{index:03}",
              "start_fen": start_fen, "moves_json": "[]"} for index in range(65)]
    state = {"active_sections": 0, "page_reads": 0, "largest_page": 0}

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute(self, statement, parameters=()):
            if "FROM repertoires" in statement:
                return Cursor([{"id": "rep", "is_main": 0}])
            if "FROM repertoire_lines" in statement:
                page = [row for row in lines if row["id"] > parameters[0]][:64]
                state["page_reads"] += 1
                state["largest_page"] = max(state["largest_page"], len(page))
                return Cursor(page)
            raise AssertionError(statement)

        def execute_native(self, statement, parameters=()):
            if "string_agg" in statement:
                return Cursor([("lines" if "repertoire_lines" in statement else "cards",)])
            if "FROM cards" in statement:
                page = [row for row in cards if
                        (row["repertoire_id"], row["id"]) > tuple(parameters)][:64]
                state["page_reads"] += 1
                state["largest_page"] = max(state["largest_page"], len(page))
                return Cursor(page)
            raise AssertionError(statement)

    @contextmanager
    def read_section():
        state["active_sections"] += 1
        try:
            yield Database()
        finally:
            state["active_sections"] -= 1

    def graph_without_open_connection(_lines):
        assert state["active_sections"] == 0
        return {}, set()

    monkeypatch.setattr(repertoire_comparison.postgres_store, "configured", lambda: True)
    monkeypatch.setattr(repertoire_comparison, "background_read_connection", read_section)
    monkeypatch.setattr(repertoire_comparison, "_cached_signature", "")
    monkeypatch.setattr(repertoire_comparison, "_position_graph", graph_without_open_connection)
    first = repertoire_comparison._load_repertoire_index_snapshot(background=True)
    first_page_reads = state["page_reads"]
    second = repertoire_comparison._load_repertoire_index_snapshot(background=True)
    assert first[0] == second[0]
    assert len(first[1]) == 1
    assert first_page_reads == 4
    assert state["page_reads"] == first_page_reads
    assert state["largest_page"] == 64
    assert state["active_sections"] == 0
