"""Legal replay and exact-scope contract for synthetic position Study fixtures."""

from collections import defaultdict
from dataclasses import FrozenInstanceError

import chess
import pytest
from tempo_evidence_math import opening_position_key

from backend.tests.fixtures.position_study_cases import (
    DECISIONS,
    EVIDENCE_CASES,
    EXPECTED_DECISION_COUNTS,
    FRAMES,
    POSITION_REFS,
    RELATIONSHIPS,
    ROUTES,
    SOURCE_CASES,
    STABLE_DECISIONS,
    Route,
)


def test_every_authored_route_and_frozen_frame_is_legal_and_exact():
    assert len({route.label for route in ROUTES}) == len(ROUTES)
    for route in ROUTES:
        board = chess.Board(route.start_fen)
        assert board.is_valid(), route.label
        frames = FRAMES[route.label]
        assert len(frames) == len(route.moves) + 1
        assert frames[0].fen == board.fen(en_passant="fen")
        for index, uci in enumerate(route.moves):
            move = chess.Move.from_uci(uci)
            assert move in board.legal_moves, (route.label, index, uci)
            board.push(move)
            assert board.is_valid(), (route.label, index, "final_position_invalid")
            frame = frames[index + 1]
            assert frame.ply == index + 1
            assert frame.fen == board.fen(en_passant="fen")
            assert frame.position == opening_position_key(frame.fen)
            assert frame.side_to_move == ("white" if board.turn else "black")
        assert frames[-1].fen == board.fen(en_passant="fen")


def test_exact_transpositions_scope_and_position_differences():
    for label in ("italian-shared-3bc4", "london-transposition", "clocks-ignored"):
        left, right, expectation = RELATIONSHIPS[label]
        assert expectation == "equal"
        assert left.position == right.position
    left, right, expectation = RELATIONSHIPS["italian-opponent-order"]
    assert expectation == "different" and left.position != right.position
    for label in ("legal-en-passant", "castling-rights"):
        left, right, expectation = RELATIONSHIPS[label]
        assert expectation == "different"
        assert left.position != right.position
    left, right, expectation = RELATIONSHIPS["repertoire-scope"]
    assert left.position == right.position
    assert left.fen == right.fen
    assert left.route != right.route
    # Exact scheduling scope includes repertoire even when normalized position matches.
    assert next(r.repertoire for r in ROUTES if r.label == left.route) != next(
        r.repertoire for r in ROUTES if r.label == right.route
    )
    short, long, _ = RELATIONSHIPS["clocks-ignored"]
    assert short.fen.split()[4:] != long.fen.split()[4:]
    ep, expired, _ = RELATIONSHIPS["legal-en-passant"]
    assert " d6 " in ep.fen and " - " in expired.fen
    castle, lost, _ = RELATIONSHIPS["castling-rights"]
    assert " Kk " in castle.fen and " - " in lost.fen


def test_decision_values_and_conflict_are_explicit_and_stably_ordered():
    assert set(EXPECTED_DECISION_COUNTS) == {route.label for route in ROUTES}
    assert sum(EXPECTED_DECISION_COUNTS.values()) == len(DECISIONS)
    for label, expected_count in EXPECTED_DECISION_COUNTS.items():
        assert sum(decision.route == label for decision in DECISIONS) == expected_count
    assert STABLE_DECISIONS == tuple(
        sorted(
            DECISIONS,
            key=lambda item: (
                item.repertoire,
                item.position,
                item.expected_uci,
                item.route,
                item.ply,
            ),
        )
    )
    decisions_at = defaultdict(set)
    for decision in DECISIONS:
        assert decision.variant == "standard"
        assert decision.expected_uci in {
            move.uci()
            for move in chess.Board(
                FRAMES[decision.route][decision.ply].fen
            ).legal_moves
        }
        decisions_at[(decision.repertoire, decision.position)].add(
            decision.expected_uci
        )
    labels = SOURCE_CASES["selected-response-conflict"]
    by_label = {route.label: route for route in ROUTES}
    conflict_routes = [by_label[label] for label in labels]
    root_keys = {
        (route.repertoire, FRAMES[route.label][0].position) for route in conflict_routes
    }
    assert len(root_keys) == 1
    assert {route.moves[0] for route in conflict_routes} == {"e2e4", "d2d4"}
    assert len(decisions_at[next(iter(root_keys))]) == 2
    with pytest.raises(FrozenInstanceError):
        conflict_routes[0].label = "changed"


def test_source_invalid_case_is_diagnostic_and_never_a_legal_route():
    label, uci, diagnostic = SOURCE_CASES["invalid-source"]
    board = chess.Board()
    assert diagnostic == "illegal_move"
    assert uci not in {move.uci() for move in board.legal_moves}
    assert label not in {route.label for route in ROUTES}


def test_evidence_scenarios_cover_due_partial_retry_and_duplicate_receipts():
    assert set(POSITION_REFS) == {case.position_ref for case in EVIDENCE_CASES}
    cases = {case.label: case for case in EVIDENCE_CASES}
    assert cases["due-first-clean"].expected_grades == ("good",)
    assert cases["nondue-clean-observation"].expected_grades == ("observe",)
    partial = cases["partial-first-response-error"]
    assert partial.expected_grades == ("again",) and partial.terminal_complete == (
        False,
    )
    later_wrong = cases["good-then-fresh-wrong"]
    assert later_wrong.expected_grades == ("good", "again")
    duplicate = cases["duplicate-offline-receipt"]
    assert duplicate.duplicate_receipt
    assert duplicate.expected_grades == ("again", "duplicate-no-grade")
    for case in EVIDENCE_CASES:
        assert (
            len(
                {
                    len(case.responses),
                    len(case.expected_grades),
                    len(case.saved),
                    len(case.terminal_complete),
                }
            )
            == 1
        )
        assert all(
            response
            in {
                move.uci()
                for move in chess.Board(
                    POSITION_REFS[case.position_ref].fen
                ).legal_moves
            }
            for response in case.responses
        )
