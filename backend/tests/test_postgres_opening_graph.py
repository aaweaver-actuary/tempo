"""PostgreSQL opening-graph slice regressions."""

from __future__ import annotations

from contextlib import contextmanager
import json

import chess

from app.services import postgres_opening_graph
from app.services.opening_graph import GraphInput, build_graph


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
