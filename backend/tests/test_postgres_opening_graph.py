"""PostgreSQL opening-graph slice regressions."""

from __future__ import annotations

from contextlib import contextmanager
import json

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
