"""One authored-line fixture independent of retired Games capture tests."""

import json
import chess


def repertoire_facts():
    moves = json.dumps(["e2e4", "e7e5", "g1f3"])
    return dict(
        repertoire=dict(id="rep", name="Synthetic", workspace_id="fixture",
            scope_source_revision=7, canonical_prefix_moves_json="[]",
            canonical_prefix_revision=0, graph_generation=3, graph_state="ready"),
        lines=[dict(id="route", repertoire_id="rep", trained_color="white",
                    start_fen=chess.STARTING_FEN, moves_json=moves)],
        cards=[dict(id="physical", revision=4, trained_color="white", content_type="opening",
                    start_fen=chess.STARTING_FEN, moves_json=moves, archived=0,
                    pending_validation=0, superseded_by=None, introduced_at="2026-10-20T00:00:00Z")],
        memberships=[dict(repertoire_id="rep", card_id="physical")],
        graphs=[dict(repertoire_id="rep", generation=3, line_id="route", card_id="physical",
                     starting_fen=chess.STARTING_FEN, trained_color="white", moves_json=moves,
                     decision_index=0)],
    )
