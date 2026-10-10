#!/usr/bin/env python3
"""Print a reproducible fixture frontier. No database or live study connection."""

from dataclasses import asdict, replace
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import chess

from app.services.opening_frontier import (
    OpeningFrontierCard, OpeningFrontierObligation, OpeningFrontierPublication,
    OpeningFrontierReview, OpeningFrontierRoute, OpeningFrontierSnapshot,
    project_opening_frontier,
)
from app.services.opening_graph import GraphInput, build_graph


def example_snapshot() -> tuple[OpeningFrontierSnapshot, dict[str, str]]:
    publication = OpeningFrontierPublication("example", 2, "fixture-current-scope")

    def route(label: str, moves: str, prefix: int = 1) -> OpeningFrontierRoute:
        steps = build_graph(GraphInput(publication.repertoire_id, ({
            "id": label, "start_fen": chess.STARTING_FEN,
            "moves_json": json.dumps(moves.split()), "trained_color": "white",
        },), prefix))
        return OpeningFrontierRoute(publication, label, steps)

    main = route("short-prefix", "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4")
    rare = route("unexposed-sibling", "e2e4 c7c5 g1f3 d7d6 d2d4")
    transpose_first = route("transposed-first", "g1f3 d7d5 g2g3 g8f6 f1g2", 2)
    transpose_second = route("transposed-second", "g2g3 d7d5 g1f3 g8f6 f1g2", 2)
    disconnected = route("disconnected", "c2c4 e7e5 b1c3")
    disconnected = replace(disconnected, steps=disconnected.steps[1:])
    routes = (main, rare, transpose_first, transpose_second, disconnected)
    reviewed_ids = (main.steps[0].card_id, transpose_first.steps[0].card_id)
    unique_ids = sorted({step.card_id for item in routes for step in item.steps})
    snapshot = OpeningFrontierSnapshot(
        "opening-frontier-example-v1", (publication,), routes,
        tuple(OpeningFrontierCard(identifier, (publication.repertoire_id,),
              introduced_at="2026-10-09" if identifier in reviewed_ids else None,
              state="learning" if identifier in reviewed_ids else "locked")
              for identifier in unique_ids),
        tuple(OpeningFrontierReview(f"saved-study-{index}", identifier, outcome="again", guided=True)
              for index, identifier in enumerate(reviewed_ids, 1)),
        (OpeningFrontierObligation(publication.repertoire_id, main.steps[-1].card_id,
                                   "real_game_miss", "example-current-game-event"),),
    )
    labels = {
        main.steps[0].card_id: "practiced short prefix (still learning)",
        main.steps[1].card_id: "eligible continuation",
        main.steps[2].card_id: "unseen grandchild prerequisite",
        main.steps[3].card_id: "game miss; route prerequisites still unseen",
        rare.steps[1].card_id: "sibling continuation",
        rare.steps[2].card_id: "unexposed sibling's deeper decision",
        transpose_first.steps[-1].card_id: "shared descendant; one exposed incoming route",
        transpose_first.steps[0].card_id: "practiced transposition prefix",
        transpose_second.steps[0].card_id: "unintroduced alternate prefix",
        disconnected.steps[0].card_id: "disconnected descendant",
    }
    return snapshot, labels


def main() -> None:
    snapshot, labels = example_snapshot()
    frontier = project_opening_frontier(snapshot)
    payload = asdict(frontier)
    payload["diagnostic_only"] = True
    payload["eligible_card_ids"] = frontier.eligible_card_ids
    for card in payload["cards"]:
        card["label"] = labels[card["card_id"]]
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
