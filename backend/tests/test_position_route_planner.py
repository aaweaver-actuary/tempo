from __future__ import annotations

from datetime import date

import chess
from tempo_evidence_math import opening_position_key

from app.study.position_learning.contracts import (
    DecisionOccurrence,
    Hold,
    PositionDefinition,
    PositionKey,
    RouteOption,
    SourceCertificate,
)
from app.study.position_learning.prefix_eligibility import (
    CleanFirstResponse,
    CleanPresentationProof,
)
from app.study.position_learning.route_planner import Plan, choose_plan
from tests.fixtures.position_study_cases import ROUTES

DAY = date(2026, 11, 2)
EPOCH = "epoch-1"
CERTIFICATE = SourceCertificate("snapshot-1", "revision-1", "digest-1", True, True)


def option(label: str, *, learner_color: str = "white") -> RouteOption:
    authored = next(route for route in ROUTES if route.label == label)
    board = chess.Board(authored.start_fen)
    decisions = []
    for ply, uci in enumerate(authored.moves):
        if board.turn == (learner_color == "white"):
            fen = board.fen()
            definition = PositionDefinition(
                PositionKey(authored.repertoire, opening_position_key(fen)),
                uci,
                "policy-v1",
            )
            decisions.append(DecisionOccurrence(ply, definition, fen))
        board.push_uci(uci)
    return RouteOption(
        authored.label,
        authored.start_fen,
        authored.moves,
        learner_color,
        tuple(decisions),
        CERTIFICATE,
        True,
    )


def clean_proof(
    route: RouteOption,
    *,
    moves: tuple[str, ...] | None = None,
    source_digest: str = "digest-1",
    attempt_id: str = "attempt-1",
    duplicate_response: bool = False,
) -> CleanPresentationProof:
    played = route.moves_uci if moves is None else moves
    decisions = tuple(d for d in route.decisions if d.ply < len(played))
    responses = tuple(
        CleanFirstResponse(d.ply, d.definition, played[d.ply], False, f"event-{d.ply}")
        for d in decisions
    )
    if duplicate_response and responses:
        responses += (responses[0],)
    return CleanPresentationProof(
        attempt_id=attempt_id,
        route_id=route.route_id,
        root_fen=route.start_fen,
        moves_uci=played,
        attempt_decisions=decisions,
        first_responses=responses,
        source_snapshot_id=CERTIFICATE.snapshot_id,
        source_revision=CERTIFICATE.source_revision,
        source_digest=source_digest,
        study_day=DAY,
        owner_epoch=EPOCH,
        terminal_complete=True,
        terminal_receipt_id=f"terminal-{attempt_id}",
        review_receipt_id=f"review-{attempt_id}",
    )


def target_after(route: RouteOption, ply: int) -> PositionKey:
    return route.decisions[
        next(i for i, d in enumerate(route.decisions) if d.ply == ply)
    ].definition.key


def plan(target: PositionKey, routes: tuple[RouteOption, ...], proofs=()):
    return choose_plan(
        target,
        routes,
        clean_proofs=proofs,
        study_day=DAY,
        owner_epoch=EPOCH,
    )


def test_italian_ng5_branch_chooses_one_stable_authored_route():
    attack = option("italian-knight-attack")
    d6 = option("italian-knight-d6")
    target = target_after(attack, 6)  # White's Ng5 decision after ...Nf6.
    assert target == target_after(d6, 6)

    selected = plan(target, (d6, attack))
    repeated = plan(target, (attack, d6))

    assert isinstance(selected, Plan)
    assert selected == repeated
    assert selected.route_id == "italian-knight-attack"
    assert selected.moves_uci == attack.moves_uci
    assert selected.target_ply == 6
    assert (
        next(d for d in selected.decision_occurrences if d.ply == 6).fen
        == next(d for d in attack.decisions if d.ply == 6).fen
    )


def test_london_transposition_selection_is_stable_and_fen_exact():
    first = option("london-order-a")
    second = option("london-order-b")
    target = target_after(first, 6)
    assert target == target_after(second, 6)

    selected = plan(target, (second, first))
    assert selected == plan(target, (first, second))
    assert isinstance(selected, Plan)
    assert selected.route_id == "london-order-a"
    assert selected.moves_uci == first.moves_uci

    # A proof for the transposed route shares the root and target FEN, but only
    # its truly identical leading move can earn credit on the selected route.
    credit = plan(target, (first,), (clean_proof(second),))
    assert isinstance(credit, Plan)
    assert credit.route_id == "london-order-a"
    assert credit.prefix_credit_attempt_id == "attempt-1"
    assert tuple(d.ply for d in credit.skipped_prefix_decisions) == (0,)
    assert credit.start_ply == 1
    assert credit.opponent_cue_uci == first.moves_uci[1]
    assert credit.moves_uci == first.moves_uci[1:]


def test_longest_clean_prefix_starts_with_opponent_cue_and_retains_occurrences():
    route = option("italian-knight-attack")
    target = target_after(route, 6)
    proof = clean_proof(route, moves=route.moves_uci[:6])

    result = plan(target, (route,), (proof,))

    assert isinstance(result, Plan)
    assert result.prefix_credit_attempt_id == proof.attempt_id
    assert tuple(d.ply for d in result.skipped_prefix_decisions) == (0, 2, 4)
    assert result.start_ply == 5
    assert result.opponent_cue_ply == 5
    assert result.opponent_cue_uci == route.moves_uci[5]  # ...Nf6
    cue_board = chess.Board(route.start_fen)
    for uci in route.moves_uci[:5]:
        cue_board.push_uci(uci)
    assert result.starting_fen == cue_board.fen()
    assert result.moves_uci == route.moves_uci[5:]
    assert tuple(d.ply for d in result.decision_occurrences) == tuple(
        d.ply for d in route.decisions if d.ply >= 6
    )
    assert all(
        result.decision_occurrences[i].fen
        == route.decisions[
            next(
                j
                for j, original in enumerate(route.decisions)
                if original.ply == result.decision_occurrences[i].ply
            )
        ].fen
        for i in range(len(result.decision_occurrences))
    )


def test_source_mismatch_and_malformed_whole_clean_proof_fall_back_to_full_route():
    route = option("italian-knight-attack")
    target = target_after(route, 6)
    wrong_source = clean_proof(
        route, moves=route.moves_uci[:6], source_digest="old-digest"
    )
    malformed = clean_proof(
        route,
        moves=route.moves_uci[:6],
        duplicate_response=True,
        attempt_id="duplicate",
    )

    for proof in (wrong_source, malformed):
        result = plan(target, (route,), (proof,))
        assert isinstance(result, Plan)
        assert result.start_ply == 0
        assert result.starting_fen == route.start_fen
        assert result.moves_uci == route.moves_uci
        assert result.prefix_credit_attempt_id is None


def test_target_already_whole_clean_today_has_no_new_test():
    route = option("italian-knight-attack")
    target = target_after(route, 6)
    result = plan(target, (route,), (clean_proof(route),))
    assert isinstance(result, Hold)
    assert result.reason == "position_already_cleanly_tested_today"


def test_missing_target_or_route_holds():
    route = option("italian-knight-attack")
    missing = PositionKey("rep-italian", "8/8/8/8/8/8/8/K6k w - -")
    assert plan(missing, (route,)).reason == "no_exact_target_route"
    assert plan(target_after(route, 6), ()).reason == "no_exact_target_route"


def test_clean_completed_root_prefix_does_not_credit_unplayed_target():
    route = option("italian-knight-attack")
    proof = clean_proof(route, moves=route.moves_uci[:5])
    chosen = plan(target_after(route, 6), (route,), (proof,))
    assert isinstance(chosen, Plan)
    assert chosen.start_ply == 5
    assert chosen.opponent_cue_uci == route.moves_uci[5]
    assert tuple(d.ply for d in chosen.skipped_prefix_decisions) == (0, 2, 4)
    assert chosen.moves_uci == route.moves_uci[5:]
