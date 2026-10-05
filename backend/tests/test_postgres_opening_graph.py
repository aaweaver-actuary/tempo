"""PostgreSQL opening-graph slice regressions."""

from __future__ import annotations

from contextlib import contextmanager
import json
import threading

import chess

from app.services import postgres_opening_graph
from app.services.opening_graph import GraphInput, GraphStep, build_graph


def test_postgres_graph_line_slices_match_whole_graph_and_close_read_before_traversal(monkeypatch):
    starting_fen = chess.STARTING_FEN
    lines = (
        {"id": "a", "repertoire_id": "rep", "start_fen": starting_fen,
         "moves_json": json.dumps(["e2e4", "e7e5", "g1f3", "b8c6"]),
         "trained_color": "white", "learner_decision_count": 2},
        {"id": "b", "repertoire_id": "rep", "start_fen": starting_fen,
         "moves_json": json.dumps(["d2d4", "d7d5", "c2c4", "e7e6"]),
         "trained_color": "white", "learner_decision_count": 2},
    )
    read_open = False

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchone(self):
            return self.rows[0] if self.rows else None

        def __iter__(self):
            return iter(self.rows)

    class Database:
        def execute_native(self, statement, parameters=()):
            if "FROM repertoire_lines line" in statement:
                return Cursor([line for line in lines if line["id"] > parameters[1]][:1])
            if "FROM settings" in statement:
                return Cursor([(2,)])
            if "FROM prefix_splits" in statement:
                return Cursor([])
            raise AssertionError(statement)

    @contextmanager
    def read():
        nonlocal read_open
        read_open = True
        try:
            yield Database()
        finally:
            read_open = False

    original_build_graph = build_graph

    def calculate(graph_input):
        assert not read_open
        return original_build_graph(graph_input)

    monkeypatch.setattr(postgres_opening_graph, "background_read_connection", read)
    monkeypatch.setattr(postgres_opening_graph, "build_graph", calculate)
    first = postgres_opening_graph.prepare_next_graph_line("rep", "")
    assert first is not None and first.line_id == "a"
    second = postgres_opening_graph.prepare_next_graph_line("rep", first.line_id)
    assert second is not None and second.line_id == "b"
    assert postgres_opening_graph.prepare_next_graph_line("rep", second.line_id) is None
    assert first.steps + second.steps == original_build_graph(GraphInput("rep", lines, 2))


def test_postgres_graph_stage_checkpoints_eight_steps_and_replays_by_cursor(monkeypatch):
    steps = tuple(GraphStep(
        repertoire_id="rep", line_id="line", decision_index=index,
        segment_kind="decision", first_decision_index=index,
        last_decision_index=index, decision_fen_keys=("fen",),
        card_id=f"card-{index}", parent_card_id=None if index == 0 else f"card-{index - 1}",
        decision_fen_key="fen", starting_fen=chess.STARTING_FEN,
        moves=("e2e4",), trained_color="white",
    ) for index in range(10))
    prepared = postgres_opening_graph.PreparedGraphLine("line", steps)
    batches = []
    next_payloads = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def executemany(self, _statement, values):
            batches.append(values)

    class Database:
        raw = type("Raw", (), {"cursor": lambda self: Cursor()})()

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)

    def advance(_database, _task, *, next_phase, next_payload):
        assert next_phase == "stage"
        next_payloads.append(next_payload)
        return True

    monkeypatch.setattr(postgres_opening_graph, "advance_task_slice_in_transaction", advance)
    task = {"generation": 3, "payload": {
        "repertoire_id": "rep", "local_day": "2026-09-27", "after_line_id": "",
        "step_offset": 0,
    }}
    assert postgres_opening_graph.stage_graph_line_in_transaction(Database(), task, prepared)
    assert [len(batch) for batch in batches] == [8, 8]
    assert next_payloads[-1]["step_offset"] == 8
    assert next_payloads[-1]["after_line_id"] == ""
    task["payload"] = next_payloads[-1]
    assert postgres_opening_graph.stage_graph_line_in_transaction(Database(), task, prepared)
    assert [len(batch) for batch in batches] == [8, 8, 2, 2]
    assert next_payloads[-1]["step_offset"] == 0
    assert next_payloads[-1]["after_line_id"] == "line"


def test_postgres_graph_links_eight_cards_before_publication(monkeypatch):
    observed_batches = []
    transitions = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def executemany(self, _statement, values):
            observed_batches.append(values)

    class Database:
        raw = type("Raw", (), {"cursor": lambda self: Cursor()})()

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
        transitions.append((next_phase, next_payload)) or True,
    )
    task = {"generation": 4, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    cards = tuple(f"card-{index}" for index in range(8))
    assert postgres_opening_graph.link_graph_cards_in_transaction(Database(), task, cards)
    assert len(observed_batches[0]) == 8
    assert transitions[-1][0] == "link"
    assert transitions[-1][1]["after_card_id"] == "card-7"
    assert postgres_opening_graph.link_graph_cards_in_transaction(Database(), task, ())
    assert transitions[-1][0] == "publish"


def test_postgres_graph_publication_rejects_missing_links_and_checks_generation(monkeypatch):
    transitions = []
    statements = []

    class Database:
        def __init__(self, missing):
            self.missing = missing

        def execute_native(self, statement, _parameters=()):
            statements.append(statement)
            return type("Cursor", (), {"fetchone": lambda _self: (1,) if self.missing else None})()

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
        transitions.append((next_phase, next_payload)) or True,
    )
    task = {"generation": 5, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    try:
        postgres_opening_graph.publish_graph_in_transaction(Database(True), task)
    except RuntimeError as error:
        assert "unlinked" in str(error)
    else:
        raise AssertionError("Graph with missing links was published")
    assert len(statements) == 1
    assert postgres_opening_graph.publish_graph_in_transaction(Database(False), task)
    assert transitions[-1][0] == "classify"
    assert transitions[-1][1]["after_card_id"] == ""


def test_postgres_graph_classifies_eight_cards_after_publication(monkeypatch):
    updates = []
    transitions = []

    class Database:
        def execute_native(self, statement, parameters=()):
            if statement.startswith("SELECT BOOL_OR"):
                return type("Cursor", (), {"fetchone": lambda _self: (True, True)})()
            updates.append((statement, parameters))
            return None

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
        transitions.append((next_phase, next_payload)) or True,
    )
    task = {"generation": 6, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    cards = tuple(f"card-{index}" for index in range(8))
    assert postgres_opening_graph.classify_graph_cards_in_transaction(Database(), task, cards)
    assert len(updates) == 8
    assert all(parameters[0:2] == ("prefix", "new") for _, parameters in updates)
    assert transitions[-1][0] == "classify"
    assert transitions[-1][1]["after_card_id"] == "card-7"
    assert postgres_opening_graph.classify_graph_cards_in_transaction(Database(), task, ())
    assert transitions[-1][0] == "integrity"


def test_postgres_graph_integrity_refreshes_two_cards_before_cleanup(monkeypatch):
    statements = []
    transitions = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
        transitions.append((next_phase, next_payload)) or True,
    )
    task = {"generation": 8, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    assert postgres_opening_graph.refresh_graph_integrity_in_transaction(
        Database(), task, ("card-a", "card-b"),
    )
    assert sum(statement.startswith("INSERT INTO repertoire_integrity_card_blocks")
               for statement, _ in statements) == 2
    assert transitions[-1][0] == "integrity"
    assert transitions[-1][1]["after_card_id"] == "card-b"
    assert postgres_opening_graph.refresh_graph_integrity_in_transaction(Database(), task, ())
    assert transitions[-1][0] == "cleanup"


def test_postgres_graph_cleanup_removes_only_obsolete_links_in_bounded_slices(monkeypatch):
    statements = []
    transitions = []

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append((statement, parameters))
            current = parameters[-1] == "current" if parameters else False
            return type("Cursor", (), {"fetchone": lambda _self: (1,) if current else None})()

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_opening_graph, "advance_task_slice_in_transaction",
        lambda _database, _task, *, next_phase, next_payload:
        transitions.append((next_phase, next_payload)) or True,
    )
    task = {"generation": 7, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    assert postgres_opening_graph.cleanup_graph_cards_in_transaction(
        Database(), task, postgres_opening_graph.PreparedGraphCleanupSlice(("obsolete", "current"), "current"),
    )
    assert sum(statement.startswith("DELETE FROM repertoire_cards") for statement, _ in statements) == 1
    assert transitions[-1][0] == "cleanup"
    assert transitions[-1][1]["after_card_id"] == "current"
    assert postgres_opening_graph.cleanup_graph_cards_in_transaction(
        Database(), task, postgres_opening_graph.PreparedGraphCleanupSlice((), None),
    )
    assert transitions[-1][0] == "finalize"


def test_postgres_graph_finalization_checkpoints_integrity_scan_and_completion(monkeypatch):
    from app.services import postgres_integrity

    events = []
    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: True)
    monkeypatch.setattr(
        postgres_integrity, "request_integrity_scan_in_transaction",
        lambda _database, repertoire_id, generation, local_day:
            events.append(("integrity", repertoire_id, generation, local_day)),
    )
    monkeypatch.setattr(
        postgres_opening_graph, "complete_task_slice_in_transaction",
        lambda _database, _task: events.append(("complete", None)) or True,
    )
    task = {"generation": 9, "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    assert postgres_opening_graph.finalize_graph_in_transaction(object(), task) is False
    assert events == [("integrity", "rep", 9, "2026-09-27"), ("complete", None)]


def test_postgres_graph_finalization_warms_sql_before_bounded_transaction(monkeypatch):
    from app.services import durable_tasks

    events = []

    @contextmanager
    def database_connection(**_kwargs):
        events.append("connection")
        yield object()

    @contextmanager
    def lease():
        yield

    monkeypatch.setattr(durable_tasks, "warm_completion_sql", lambda: events.append("warm"))
    monkeypatch.setattr(postgres_opening_graph, "background_lease", lease)
    monkeypatch.setattr(postgres_opening_graph.postgres_store, "connection", database_connection)
    monkeypatch.setattr(postgres_opening_graph, "finalize_graph_in_transaction",
                        lambda *_args: events.append("finalize") or False)
    assert postgres_opening_graph.execute_graph_finalize_slice({"payload": {}}) is False
    assert events == ["warm", "connection", "finalize"]


def test_postgres_graph_stage_yields_to_foreground_and_discards_restart_replay(monkeypatch):
    foreground_finished = threading.Event()
    connection_opened = threading.Event()
    writes = []
    current_lease = {"token": "lease-1"}
    step = GraphStep(
        repertoire_id="rep", line_id="line", decision_index=0,
        segment_kind="prefix", first_decision_index=0, last_decision_index=0,
        decision_fen_keys=("fen",), card_id="card", parent_card_id=None,
        decision_fen_key="fen", starting_fen=chess.STARTING_FEN,
        moves=("e2e4",), trained_color="white",
    )

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def executemany(self, _statement, values):
            writes.append(len(values))

    class Database:
        raw = type("Raw", (), {"cursor": lambda self: Cursor()})()

    @contextmanager
    def gated_lease():
        foreground_finished.wait()
        yield

    @contextmanager
    def database_connection(**_kwargs):
        connection_opened.set()
        yield Database()

    monkeypatch.setattr(postgres_opening_graph, "prepare_next_graph_line",
                        lambda *_args: postgres_opening_graph.PreparedGraphLine("line", (step,)))
    monkeypatch.setattr(postgres_opening_graph, "background_lease", gated_lease)
    monkeypatch.setattr(postgres_opening_graph.postgres_store, "connection", database_connection)
    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice",
                        lambda _database, task: task["lease_token"] == current_lease["token"])
    monkeypatch.setattr(postgres_opening_graph, "advance_task_slice_in_transaction",
                        lambda *_args, **_kwargs: current_lease.update(token="complete") or True)
    task = {"id": "graph", "generation": 1, "lease_token": "lease-1", "phase": "stage",
            "payload": {"repertoire_id": "rep", "local_day": "2026-09-27"}}
    results = []
    worker = threading.Thread(
        target=lambda: results.append(postgres_opening_graph.execute_graph_stage_slice(task)),
    )
    worker.start()
    assert not connection_opened.wait(0.05)
    foreground_finished.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
    assert results == [True]
    assert writes == [1, 1]
    assert postgres_opening_graph.execute_graph_stage_slice(task) is False
    assert writes == [1, 1]


def test_postgres_graph_worker_routes_each_restartable_phase(monkeypatch):
    from app import tasks

    observed = []
    assert "opening_graph_rebuild" in tasks._SUPPORTED_BACKGROUND_KINDS
    phase_handlers = (
        ("stage", "execute_graph_stage_slice"),
        ("link", "execute_graph_link_slice"),
        ("publish", "execute_graph_publish_slice"),
        ("classify", "execute_graph_classify_slice"),
        ("integrity", "execute_graph_integrity_slice"),
        ("cleanup", "execute_graph_cleanup_slice"),
        ("finalize", "execute_graph_finalize_slice"),
    )
    for phase, name in phase_handlers:
        monkeypatch.setattr(postgres_opening_graph, name,
                            lambda _task, phase=phase: observed.append(phase) or True)
    for phase, _ in phase_handlers:
        assert postgres_opening_graph.execute_postgres_opening_graph_slice({"phase": phase})
    assert observed == [phase for phase, _ in phase_handlers]


def test_postgres_graph_enqueue_advances_past_imported_generation_without_task_row(monkeypatch):
    enqueued = []

    class Database:
        def execute_native(self, statement, _parameters=()):
            if "FROM opening_graph_steps" in statement:
                row = (31,)
            elif "FROM opening_graph_publications" in statement:
                row = (30,)
            else:
                row = None
            return type("Cursor", (), {"fetchone": lambda _self: row})()

    monkeypatch.setattr(
        postgres_opening_graph, "enqueue_task_in_transaction",
        lambda _database, kind, key, payload, **options:
        enqueued.append((kind, key, payload, options)) or {"generation": 32},
    )
    result = postgres_opening_graph.request_graph_rebuild_in_transaction(
        Database(), "rep", "2026-09-27",
    )
    assert result["generation"] == 32
    assert enqueued[0][0:2] == ("opening_graph_rebuild", "rep")
    assert enqueued[0][3]["minimum_generation"] == 31
    assert enqueued[0][2]["after_line_id"] == ""


def test_postgres_graph_cleanup_prepares_bounded_current_pages_before_exhaustion(monkeypatch):
    """A no-obsolete page must advance instead of scanning the entire remaining tail."""
    statements = []
    read_open = False
    candidate_rows = [(f"card-{index:04d}", False) for index in range(512)]

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Database:
        def execute_native(self, statement, parameters):
            statements.append((statement, parameters))
            if "MATERIALIZED" in statement:
                return Cursor([row for row in candidate_rows if row[0] > parameters[1]][:parameters[2]])
            return Cursor([])  # The old anti-join has no matching obsolete card.

    @contextmanager
    def read():
        nonlocal read_open
        read_open = True
        try:
            yield Database()
        finally:
            read_open = False

    monkeypatch.setattr(postgres_opening_graph, "background_read_connection", read)
    first = postgres_opening_graph.prepare_obsolete_graph_cards("rep", 4, "")
    assert not read_open
    assert getattr(first, "checkpoint_card_id", None) == "card-0255"
    assert first.obsolete_card_ids == ()
    second = postgres_opening_graph.prepare_obsolete_graph_cards("rep", 4, first.checkpoint_card_id)
    assert second.checkpoint_card_id == "card-0511" and second.obsolete_card_ids == ()
    exhausted = postgres_opening_graph.prepare_obsolete_graph_cards("rep", 4, second.checkpoint_card_id)
    assert exhausted.checkpoint_card_id is None and exhausted.obsolete_card_ids == ()
    assert all(parameters[2] == 256 for _, parameters in statements)


def test_postgres_graph_cleanup_page_frontier_never_skips_third_obsolete_card(monkeypatch):
    candidate_rows = [("card-00", False), ("card-01", True), ("card-02", False),
                      ("card-03", True), ("card-04", True), ("card-05", False)]

    class Database:
        def execute_native(self, statement, parameters):
            if "MATERIALIZED" in statement:
                rows = [row for row in candidate_rows if row[0] > parameters[1]][:parameters[2]]
            else:
                rows = [(row[0],) for row in candidate_rows if row[0] > parameters[1] and row[1]][:2]
            return type("Cursor", (), {"fetchall": lambda _self: rows})()

    @contextmanager
    def read():
        yield Database()

    monkeypatch.setattr(postgres_opening_graph, "background_read_connection", read)
    first = postgres_opening_graph.prepare_obsolete_graph_cards("rep", 4, "")
    assert getattr(first, "checkpoint_card_id", None) == "card-03"
    assert first.obsolete_card_ids == ("card-01", "card-03")
    second = postgres_opening_graph.prepare_obsolete_graph_cards("rep", 4, first.checkpoint_card_id)
    assert second.obsolete_card_ids == ("card-04",) and second.checkpoint_card_id == "card-05"


def test_postgres_graph_cleanup_current_page_checkpoints_without_finalizing_and_fences_replay(monkeypatch):
    from types import SimpleNamespace

    saved_checkpoints = []
    statements = []
    lease_current = True

    class Database:
        def execute_native(self, statement, parameters=()):
            statements.append(statement)
            raise AssertionError("A current-only prepared page needs no card mutations")

    monkeypatch.setattr(postgres_opening_graph, "lock_current_slice", lambda *_args: lease_current)
    monkeypatch.setattr(postgres_opening_graph, "advance_task_slice_in_transaction",
                        lambda _database, _task, *, next_phase, next_payload:
                        saved_checkpoints.append((next_phase, next_payload)) or True)
    task = {"generation": 4, "payload": {"repertoire_id": "rep", "after_card_id": "card-0000"}}
    prepared = SimpleNamespace(obsolete_card_ids=(), checkpoint_card_id="card-0256")
    assert postgres_opening_graph.cleanup_graph_cards_in_transaction(Database(), task, prepared)
    assert saved_checkpoints == [("cleanup", {"repertoire_id": "rep", "after_card_id": "card-0256"})]
    lease_current = False
    assert postgres_opening_graph.cleanup_graph_cards_in_transaction(Database(), task, prepared) is False
    assert len(saved_checkpoints) == 1 and statements == []
