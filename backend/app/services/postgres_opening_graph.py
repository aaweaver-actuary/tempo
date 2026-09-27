"""Per-line PostgreSQL opening-graph preparation for durable rebuild slices."""

from __future__ import annotations

from dataclasses import dataclass

from ..database import background_read_connection
from .opening_graph import GraphInput, GraphStep, build_graph


@dataclass(frozen=True)
class PreparedGraphLine:
    """One line's graph, calculated after its read transaction has closed."""

    line_id: str
    steps: tuple[GraphStep, ...]


def prepare_next_graph_line(repertoire_id: str, after_line_id: str) -> PreparedGraphLine | None:
    """Read one source line, close PostgreSQL, then traverse its moves."""

    with background_read_connection() as database:
        line = database.execute_native(
            "SELECT line.*,depth.learner_decision_count "
            "FROM repertoire_lines line "
            "LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id "
            "WHERE line.repertoire_id=%s AND line.id>%s ORDER BY line.id LIMIT 1",
            (repertoire_id, after_line_id),
        ).fetchone()
        if line is None:
            return None
        default_depth = database.execute_native(
            "SELECT initial_depth FROM settings WHERE id=1"
        ).fetchone()[0]
        prefix_overrides = tuple(dict(row) for row in database.execute_native(
            "SELECT split.source_card_id,split.shortened_card_id,"
            "shortened.start_fen shortened_start_fen,"
            "shortened.moves_json shortened_moves_json,"
            "split.continuation_card_id,"
            "continuation.start_fen continuation_start_fen,"
            "continuation.moves_json continuation_moves_json "
            "FROM prefix_splits split "
            "JOIN cards shortened ON shortened.id=split.shortened_card_id "
            "JOIN cards continuation ON continuation.id=split.continuation_card_id"
        ))
        source_line = dict(line)
    graph_input = GraphInput(repertoire_id, (source_line,), int(default_depth), prefix_overrides)
    return PreparedGraphLine(source_line["id"], build_graph(graph_input))
