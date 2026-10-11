"""Rebuild synthetic checked fixtures in a NEW directory; never captures live state.

The real pure production planner supplies the baseline. Scalar scores are explicit
hand-authored assumptions, NOT outputs of a replacement valuation implementation.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import chess

from app.main import _plan_prioritized_opening_admissions
from app.services.opening_ranking_evaluation import (
    OpeningRankingCandidate, OpeningRankingContext, canonical_json, snapshot_document,
)
from app.services.opening_segmentation import opening_position_key

AS_OF = "2026-10-10T12:00:00Z"
DAY = "2026-10-10"
MAIN_REVISION = "4dc17a96dd23da51047284d1bfb7434efee20d56"
ITALIAN = "e2e4 e7e5 g1f3 b8c6 f1c4 f8c5 c2c3 g8f6 d2d4".split()
RUY = "e2e4 e7e5 g1f3 b8c6 f1b5 a7a6 b5a4 g8f6 e1g1 f8e7 f1e1 b7b5 a4b3".split()
COHORTS = {
    "rapid-1200-1400": {"ratings": [1200, 1400], "speeds": ["rapid"]},
    "classical-1800-2000": {"ratings": [1800, 2000], "speeds": ["classical"]},
}
FAMILIES = (
    ("italian-common", "italian", ["common_italian", "exact_probabilities"]),
    ("ruy-common", "ruy", ["common_ruy_lopez", "exact_probabilities"]),
    ("shared-prefixes", "italian", ["shared_prefixes"]),
    ("rare-deviations", "italian", ["rare_sidelines", "opponent_deviations"]),
    ("cohorts-uncertainty", "italian", ["multiple_cohorts", "bounded_probabilities", "unknown_unassigned_mass"]),
    ("cumulative-history", "italian", ["newly_introduced_cards", "mature_cards", "recently_failed_decisions", "cumulative_prefix_cards"]),
)


def decision(moves):
    board = chess.Board()
    for move in moves[:-1]:
        board.push_uci(move)
    return {"decision_id": "white:" + opening_position_key(board.fen()) + ":" + moves[-1],
            "position_key": opening_position_key(board.fen()), "move_uci": moves[-1],
            "prefix": moves[:-1], "ply": len(moves)}


def opponent_evidence(routes, *, incomplete=False, cohort="rapid-1200-1400"):
    positions = {}
    for moves in routes:
        board = chess.Board()
        for move in moves:
            if board.turn == chess.BLACK:
                key = opening_position_key(board.fen())
                positions.setdefault(key, {"fen": board.fen(), "authored_replies": set()})["authored_replies"].add(move)
            board.push_uci(move)
    evidence = []
    bounded_board = chess.Board()
    for move in ITALIAN[:7]:
        bounded_board.push_uci(move)
    bounded_position_key = opening_position_key(bounded_board.fen())
    for index, (key, source) in enumerate(sorted(positions.items())):
        board = chess.Board(source["fen"])
        replies = sorted(source["authored_replies"])
        frequencies = {"e7e5": .60, "c7c5": .25, "b7b6": .05} if cohort == "rapid-1200-1400" else {"e7e5": .70, "c7c5": .15, "b7b6": .03}
        assigned = {move: frequencies.get(move, .15 if cohort == "rapid-1200-1400" else .20)
                    for move in replies}
        outside_reply = next(move for move in sorted(move.uci() for move in board.legal_moves) if move not in replies)
        unknown_mass = .10 if incomplete else 0
        unassigned_authored = ["b7b6"] if incomplete and "b7b6" in assigned else []
        for move in unassigned_authored:
            unknown_mass += assigned.pop(move)
        unknown_mass = round(unknown_mass, 10)
        assigned[outside_reply] = round(1 - sum(assigned.values()) - unknown_mass, 10)
        evidence.append({"evidence_id": "synthetic-opponent-" + cohort + "-" + str(index), "position_key": key,
            "fen": source["fen"], "cohort_id": cohort, "source_version": "synthetic-1",
            "captured_at": "2026-10-10T11:00:00Z", "provenance": "hand-authored legal reply distribution",
            "authored_replies": replies, "outside_reply": outside_reply,
            "unassigned_authored_replies": unassigned_authored,
            "moves": [{"move_uci": move, "probability": assigned.get(move, None if incomplete else 0)}
                      for move in sorted(move.uci() for move in board.legal_moves)],
            "unknown_mass": unknown_mass,
            "bounds": [{"move_uci": "g8f6", "lower": round(max(0, assigned["g8f6"] - .05), 10),
                        "upper": round(min(1, assigned["g8f6"] + .05), 10)}]
                      if incomplete and key == bounded_position_key else []})
    return evidence


def make_fixture(identifier, policy, coverage):
    main = ITALIAN if policy == "italian" else RUY
    moves_by_suffix = {
        "a": main[:7], "b": main[:9],
        "c": "e2e4 e7e5 g1f3 b8c6 f1c4 g8f6 d2d3".split() if policy == "italian" else main[:11],
        "d": "e2e4 c7c5 g1f3".split() if policy == "italian" else main[:13],
        "e": "e2e4 e7e5 g1f3 d7d6 d2d4".split() if policy == "italian" else "e2e4 e7e5 g1f3 b8c6 f1b5 g8f6 d2d3".split(),
        "f": "e2e4 b7b6 d2d4".split() if policy == "italian" else "e2e4 c7c5 g1f3".split(),
    }
    incomplete = identifier == "cohorts-uncertainty"
    all_routes = list(moves_by_suffix.values())
    card_ids = {suffix: policy + "-" + suffix for suffix in moves_by_suffix}
    card_ids["d" if policy == "italian" else "f"] = "shared-sicilian"
    rows, metadata = [], {}
    for suffix, moves in moves_by_suffix.items():
        card_id = card_ids[suffix]
        parent_card_id = (policy + "-support-failed" if moves[:5] == main[:5] and len(moves) > 5
                          else "shared-e4-e5-nf3" if moves[:3] == main[:3] and len(moves) > 3
                          else "shared-e4")
        line_id = "main" if suffix in ("a", "b") else "branch-" + suffix
        priority = {"a": .9, "b": .7, "c": .8, "d": .6, "e": .5, "f": .4}[suffix]
        row = {"id": card_id, "repertoire_id": policy, "moves_json": json.dumps(moves),
            "priority_score": priority, "gameplay_priority_reason": None, "priority_date": None,
            "completed_line_ids_json": json.dumps([line_id]), "frontier_decisions_json": '[]'}
        rows.append(row)
        decisions = [decision(moves[:length]) for length in range(1, len(moves) + 1, 2)]
        metadata[card_id] = {"revision": 1, "start_fen": chess.STARTING_FEN, "moves": moves,
            "state": "new", "introduced_at": None, "archived": 0, "pending_validation": 0,
            "presentation": "cumulative_prefix", "decisions": decisions,
            "probability_status": "bounded" if incomplete and suffix == "b" else
                                  "unknown" if incomplete and suffix == "f" else "exact",
            "memberships": [{"repertoire_id": policy, "frontier_ply": len(moves),
                "priority_score": priority, "priority_generation": 1, "completed_line_ids": [line_id]}],
            "routes": [{"repertoire_id": policy, "generation": 1, "line_id": line_id,
                "decision_index": len(decisions) - 1, "parent_card_id": parent_card_id}]}
        if identifier == "shared-prefixes" and suffix == "a":
            metadata[card_id]["routes"].append({"repertoire_id": policy, "generation": 1,
                "line_id": "main-extension", "decision_index": len(decisions) - 1,
                "parent_card_id": parent_card_id})
    planner_inputs = sorted(rows, key=lambda row: (row["repertoire_id"], row["id"]))
    order = _plan_prioritized_opening_admissions(planner_inputs, {}, DAY, len(rows))
    candidates = tuple(OpeningRankingCandidate(row["id"], index, canonical_json(metadata[row["id"]]),
        policy_id=policy) for index, (_, row) in enumerate(order, 1))
    values = dict(zip((card_ids[suffix] for suffix in "abcdef"), (.10, .30, .20, .04, .03, .02)))
    def bundle(name, axis, assumption, scores, cohort="rapid-1200-1400"):
        return {"name": name, "version": "1", "axis": axis, "assumption": assumption,
                "cohort_id": cohort, "provenance": "synthetic supplied scalar values; not adapter output",
                "scores": {card_id: {"score": score, **({"reason": "required_probability_incomplete"} if score is None else {})}
                           for card_id, score in scores.items()}}
    baseline = dict(values)
    if incomplete:
        baseline[policy + "-b"] = baseline[policy + "-f"] = None
    bundles = [bundle("precomputed-base", "baseline", "explicit baseline score assumptions", baseline),
        bundle("precomputed-ties", "ties", "equal supplied scores preserve production tie ordering",
               {card_id: None if baseline[card_id] is None else .1 for card_id in values})]
    if incomplete:
        bundles.extend([
            bundle("precomputed-bound-low", "probability_bounds", "pessimistic explicit endpoint assumptions, not scalarization of unknown evidence", values | {policy + "-b": .02, policy + "-f": None}),
            bundle("precomputed-bound-high", "probability_bounds", "optimistic explicit endpoint assumptions; unassigned mass remains unknown", values | {policy + "-b": .35, policy + "-f": None}),
            bundle("precomputed-cohort", "opponent_cohort", "separately supplied classical-cohort scores; incomplete evidence stays unscorable", baseline | {card_ids["d"]: .40}, "classical-1800-2000"),
        ])
    if identifier == "cumulative-history":
        bundles.extend([
            bundle("precomputed-readiness", "decision_readiness", "explicit higher readiness assumption for the shared-prefix decision", values | {policy + "-b": .01}),
            bundle("precomputed-effect", "learning_effect", "explicit weaker projected learning-effect assumption for the cumulative card", values | {policy + "-b": .015}),
        ])
    if identifier == "rare-deviations":
        bundles.append(bundle("precomputed-rare-exploration", "rare_line_exploration",
            "explicit rare-line study-value assumption; not a next-game probability",
            values | {policy + "-f": .15}))
    support = [
        {"card_id": "shared-e4", "policy_id": policy, "state": "learning", "introduced_at": "2026-10-10T09:00:00Z",
         "moves": main[:1], "decisions": [decision(main[:1])], "recall_assumption": .25},
        {"card_id": "shared-e4-e5-nf3", "policy_id": policy, "state": "mature", "introduced_at": "2026-09-01T09:00:00Z",
         "moves": main[:3], "decisions": [decision(main[:3])], "recall_assumption": .90},
        {"card_id": policy + "-support-failed", "policy_id": policy, "state": "learning", "introduced_at": "2026-10-09T09:00:00Z",
         "moves": main[:5], "decisions": [decision(main[:5])], "recall_assumption": .20,
         "observation": {"decision_id": decision(main[:5])["decision_id"], "outcome": "incorrect",
                         "observed_at": "2026-10-10T10:00:00Z"}},
    ]
    source = {"fixture_version": 1, "scenario_id": identifier, "reference_main": MAIN_REVISION, "policy_version": "synthetic-1",
        "provenance": "synthetic legal opening routes, histories and explicit score assumptions",
        "cohorts": COHORTS, "supporting_cards": support, "production_planner_inputs": planner_inputs,
        "opponent_evidence": opponent_evidence(all_routes, incomplete=incomplete)}
    if incomplete:
        source["opponent_evidence"].extend(opponent_evidence(all_routes, incomplete=True,
            cohort="classical-1800-2000"))
    context = OpeningRankingContext(AS_OF, DAY, "fixture", canonical_json(source), canonical_json({
        "baseline_bundle": "precomputed-base@1", "precomputed_score_bundles": bundles}),
        evaluation_scope="selected_policy", policy_id=policy)
    return snapshot_document(candidates, context,
        production_order_basis="production_planner_capacity_neutral_sorted_repertoire_then_card",
        admission_plan_basis="not_simulated_fixture_has_no_admission_plan")


def build_documents():
    documents = {identifier + ".json": make_fixture(identifier, policy, coverage)
                 for identifier, policy, coverage in FAMILIES}
    documents["manifest.json"] = {"corpus_version": 1,
        "provenance": "Synthetic representative openings and histories; precomputed score sensitivity only.",
        "scenarios": [{"id": identifier, "snapshot": identifier + ".json", "coverage": coverage,
                       "snapshot_id": documents[identifier + ".json"]["snapshot_id"]}
                      for identifier, _, coverage in FAMILIES]}
    return documents


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New directory; existing evidence is protected")
    output = parser.parse_args().output
    documents = build_documents()
    output.mkdir(parents=True, exist_ok=False)
    for filename, document in documents.items():
        (output / filename).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
