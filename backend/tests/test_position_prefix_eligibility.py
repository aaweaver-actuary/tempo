"""Pure whole-clean prefix proof regressions for Position Study."""
from datetime import date

import chess
import pytest

from app.study.position_learning.contracts import (
    DecisionOccurrence,
    PositionDefinition,
    PositionKey,
    RouteOption,
    SourceCertificate,
)
from app.study.position_learning.prefix_eligibility import (
    CleanFirstResponse,
    CleanPresentationProof,
    verify_clean_prefix,
)


DAY = date(2026, 11, 2)
EPOCH = "owner-epoch-7"
CERTIFICATE = SourceCertificate("snapshot-1", "revision-3", "digest-3", True, True)


def _route(route_id: str, moves: tuple[str, ...]) -> RouteOption:
    board = chess.Board()
    decisions: list[DecisionOccurrence] = []
    for ply, uci in enumerate(moves):
        if board.turn == chess.WHITE:
            key = PositionKey("italian", " ".join(board.fen().split()[:4]))
            decisions.append(DecisionOccurrence(
                ply,
                PositionDefinition(key, uci, "reply-policy-v1"),
                board.fen(),
            ))
        board.push_uci(uci)
    return RouteOption(route_id, chess.Board().fen(), moves, "white", tuple(decisions), CERTIFICATE, True)


def _proof(route: RouteOption, *, moves: tuple[str, ...] | None = None,
           responses: tuple[CleanFirstResponse, ...] | None = None,
           day: date = DAY, epoch: str = EPOCH, root: str | None = None,
           source_digest: str = "digest-3", terminal: bool = True,
           terminal_receipt: str = "terminal-receipt-1",
           review_receipt: str = "review-receipt-1") -> CleanPresentationProof:
    actual_moves = moves if moves is not None else route.moves_uci
    decisions: list[DecisionOccurrence] = []
    board = chess.Board()
    for ply, uci in enumerate(actual_moves):
        if board.turn == chess.WHITE:
            key = PositionKey("italian", " ".join(board.fen().split()[:4]))
            decisions.append(DecisionOccurrence(ply, PositionDefinition(key, uci, "reply-policy-v1"), board.fen()))
        board.push_uci(uci)
    actual_responses = responses if responses is not None else tuple(
        CleanFirstResponse(d.ply, d.definition, d.definition.expected_uci, False, f"event-{d.ply}")
        for d in decisions
    )
    return CleanPresentationProof(
        "attempt-1", route.route_id, root or route.start_fen, actual_moves,
        tuple(decisions), actual_responses, "snapshot-1", "revision-3", source_digest,
        day, epoch, terminal, terminal_receipt, review_receipt,
    )


def _italian_routes() -> tuple[RouteOption, RouteOption]:
    # The branches share 1.e4 e5 2.Nf3 Nc6 and diverge at White's third move.
    return (
        _route("bishop", ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "f3g5", "d7d6")),
        _route("c3", ("e2e4", "e7e5", "g1f3", "b8c6", "c2c3", "g8f6")),
    )


@pytest.mark.parametrize("reverse", [False, True])
def test_full_clean_italian_prefix_proof_is_order_independent(reverse: bool) -> None:
    bishop, c3 = _italian_routes()
    selected, prior = (c3, bishop) if reverse else (bishop, c3)
    proof = _proof(prior)

    eligibility = verify_clean_prefix(selected, proof, target_ply=4, study_day=DAY, owner_epoch=EPOCH)

    assert eligibility is not None
    assert eligibility.attempt_id == "attempt-1"
    assert eligibility.shared_move_count == 4
    assert eligibility.skipped_positions == tuple(d.definition.key for d in selected.decisions[:2])
    assert eligibility.first_uncovered_decision == selected.decisions[2]


def test_failed_at_ng5_never_certifies_whole_clean_prefix() -> None:
    bishop, c3 = _italian_routes()
    # Represent an otherwise completed presentation whose Ng5 first response
    # was wrong; correctness is derived from the saved response row.
    bad = list(_proof(bishop).first_responses)
    ng5 = bad[-1]
    bad[-1] = CleanFirstResponse(ng5.ply, ng5.definition, "f3h4", False, "wrong-ng5")
    proof = _proof(bishop, responses=tuple(bad))

    assert verify_clean_prefix(c3, proof, target_ply=4, study_day=DAY, owner_epoch=EPOCH) is None


def test_missing_learner_cue_cannot_be_filled_by_move_or_fen_match() -> None:
    bishop, c3 = _italian_routes()
    clean = _proof(bishop)
    proof = CleanPresentationProof(
        clean.attempt_id, clean.route_id, clean.root_fen, clean.moves_uci,
        clean.attempt_decisions, clean.first_responses[:-1], clean.source_snapshot_id,
        clean.source_revision, clean.source_digest, clean.study_day, clean.owner_epoch,
        clean.terminal_complete, clean.terminal_receipt_id, clean.review_receipt_id,
    )

    assert verify_clean_prefix(c3, proof, target_ply=4, study_day=DAY, owner_epoch=EPOCH) is None


@pytest.mark.parametrize("change", ["source", "assistance", "offline", "duplicate", "day", "root"])
def test_untrusted_or_mismatched_proof_grants_no_prefix(change: str) -> None:
    bishop, c3 = _italian_routes()
    clean = _proof(bishop)
    responses = clean.first_responses
    day = DAY
    root = clean.root_fen
    source_digest = clean.source_digest
    terminal_receipt = clean.terminal_receipt_id
    if change == "source":
        source_digest = "old-source"
    elif change == "assistance":
        first = responses[0]
        responses = (CleanFirstResponse(first.ply, first.definition, first.actual_uci, True, first.event_id), *responses[1:])
    elif change == "offline":
        terminal_receipt = ""
    elif change == "duplicate":
        responses = (*responses, responses[0])
    elif change == "day":
        day = date(2026, 11, 1)
    elif change == "root":
        root = chess.Board("1rbqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3").fen()
    proof = _proof(
        bishop, responses=tuple(responses), day=day, root=root,
        source_digest=source_digest, terminal_receipt=terminal_receipt,
    )

    assert verify_clean_prefix(c3, proof, target_ply=4, study_day=DAY, owner_epoch=EPOCH) is None


def test_wrong_owner_epoch_and_different_authored_root_hold() -> None:
    bishop, c3 = _italian_routes()
    assert verify_clean_prefix(c3, _proof(bishop, epoch="old-epoch"), target_ply=4,
                               study_day=DAY, owner_epoch=EPOCH) is None
    changed_root = _route("other-root", ("d2d4", "d7d5", "g1f3", "g8f6", "c2c4"))
    assert verify_clean_prefix(changed_root, _proof(bishop), target_ply=4,
                               study_day=DAY, owner_epoch=EPOCH) is None
