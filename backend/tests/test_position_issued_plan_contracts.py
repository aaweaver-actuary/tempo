from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone

import chess

from app.study.position_learning.contracts import DueAtIssuance, Hold, PositionDefinition
from app.study.position_learning.contracts import PositionKey
from app.study.position_learning.prefix_eligibility import CleanFirstResponse, CleanPresentationProof
from app.study.position_learning.issued_plan_contracts import (
    SelectionRequest,
    select_and_freeze,
    validate_wire_v1,
)
from app.study.position_learning.route_planner import choose_plan
from tempo_evidence_math import opening_position_key
from tests.fixtures.position_study_cases import ROUTES
from tests.test_position_route_planner import option, target_after


DAY = date(2026, 11, 2)
NOW = datetime(2026, 11, 2, 9, tzinfo=timezone.utc)
EPOCH = "owner-epoch-1"


def request_for(route, target_ply=6, *, due=True, proofs=(), attempt="attempt-original"):
    target_key = target_after(route, target_ply)
    target_occurrence = next(d for d in route.decisions if d.ply == target_ply)
    # The selected suffix includes every learner decision; witnesses retain
    # all due/nondue statuses, including positions before the requested target.
    planned = choose_plan(target_key, (route,), clean_proofs=proofs,
                          study_day=DAY, owner_epoch=EPOCH)
    assert not isinstance(planned, Hold)
    due_rows = tuple(
        DueAtIssuance(item.definition, due, DAY, EPOCH, 7, NOW)
        for item in planned.decision_occurrences
    )
    return SelectionRequest(
        PositionDefinition(target_key, target_occurrence.definition.expected_uci, "policy-v1"),
        (route,), due_rows, tuple(proofs), "legacy-card-1", 4, 41, attempt,
    )


def test_v1_freeze_preserves_original_attempt_and_exposes_strict_wire():
    route = option("italian-knight-attack")
    issued = select_and_freeze(request_for(route))
    assert not isinstance(issued, Hold)
    assert (issued.original_card_id, issued.original_card_revision,
            issued.queue_entry_id, issued.attempt_id) == ("legacy-card-1", 4, 41, "attempt-original")
    assert issued.version == 1
    assert issued.plan_id == validate_wire_v1(issued.to_wire(), expected_plan=issued)["plan_id"]
    assert all("due" not in row and "position_revision" not in row for row in issued.to_wire()["decision_slots"])
    assert len(issued.plan_id) == 64
    changed_digest = dict(issued.to_wire(), plan_id="0" * 64)
    assert isinstance(validate_wire_v1(changed_digest, expected_plan=issued), Hold)


def test_digest_changes_with_queue_identity_due_state_and_authored_move_order():
    route = option("italian-knight-attack")
    base = select_and_freeze(request_for(route))
    other_attempt = select_and_freeze(request_for(route, attempt="attempt-other"))
    nondue = select_and_freeze(request_for(route, due=False))
    assert not isinstance(base, Hold) and not isinstance(other_attempt, Hold) and not isinstance(nondue, Hold)
    assert len({base.plan_id, other_attempt.plan_id, nondue.plan_id}) == 3
    reordered = dict(base.to_wire())
    reordered["moves_uci"] = list(reordered["moves_uci"])
    reordered["moves_uci"][0], reordered["moves_uci"][1] = reordered["moves_uci"][1], reordered["moves_uci"][0]
    assert isinstance(validate_wire_v1(reordered), Hold)


def test_opponent_cue_is_not_a_learner_slot_and_original_ply_is_retained():
    route = option("italian-knight-attack")
    played = route.moves_uci[:6]
    decisions = tuple(d for d in route.decisions if d.ply < len(played))
    proof = CleanPresentationProof(
        "saved-prefix", route.route_id, route.start_fen, played, decisions,
        tuple(CleanFirstResponse(d.ply, d.definition, played[d.ply], False, f"event-{d.ply}") for d in decisions),
        route.certificate.snapshot_id, route.certificate.source_revision, route.certificate.source_digest,
        DAY, EPOCH, True, "terminal-proof", "review-proof",
    )
    result = select_and_freeze(request_for(route, proofs=(proof,)))
    assert not isinstance(result, Hold)
    assert result.start_ply == 5
    assert result.moves_uci[0] == route.moves_uci[5]  # opponent cue
    assert all(slot.original_ply >= 6 and slot.move_offset == slot.original_ply - 5
               for slot in result.decision_slots)
    assert "terminal-proof" in result.skipped_prefix_proof_ids


def test_mismatched_due_scope_definition_or_position_holds():
    route = option("italian-knight-attack")
    request = request_for(route)
    assert len(request.dues) > 1
    first, *rest = request.dues
    changed = (
        replace(first, owner_epoch="other-epoch"),
        replace(first, study_day=date(2026, 11, 3)),
        replace(first, definition=replace(first.definition, policy_version="other-policy")),
        replace(first, definition=replace(first.definition, key=PositionKey(
            first.definition.key.repertoire_id, "8/8/8/8/8/8/8/K6k w - -"))),
    )
    for witness in changed:
        result = select_and_freeze(replace(request, dues=(witness, *rest)))
        assert isinstance(result, Hold)


def test_prefix_proof_for_another_route_cannot_suppress_or_skip():
    route = option("italian-knight-attack")
    moves = route.moves_uci
    decisions = route.decisions
    proof = CleanPresentationProof(
        "wrong-route-attempt", "some-other-route", route.start_fen, moves, decisions,
        tuple(CleanFirstResponse(d.ply, d.definition, moves[d.ply], False, f"wrong-event-{d.ply}")
              for d in decisions),
        route.certificate.snapshot_id, route.certificate.source_revision, route.certificate.source_digest,
        DAY, EPOCH, True, "wrong-terminal", "wrong-review",
    )
    issued = select_and_freeze(replace(request_for(route), clean_proofs=(proof,)))
    assert not isinstance(issued, Hold)
    assert issued.start_ply == 0
    assert issued.skipped_prefix_proof_ids == ()


def test_black_learner_and_wire_caps_and_unknown_fields():
    authored = next(r for r in ROUTES if r.label == "italian-knight-attack")
    board = chess.Board(authored.start_fen)
    decisions = []
    for ply, uci in enumerate(authored.moves):
        if board.turn == chess.BLACK:
            fen = board.fen()
            key = PositionKey(authored.repertoire, opening_position_key(fen))
            decisions.append((ply, PositionDefinition(key, uci, "policy-v1"), fen))
        board.push_uci(uci)
    assert decisions and decisions[0][0] == 1
    route = option("italian-knight-attack", learner_color="black")
    request = request_for(route, route.decisions[0].ply)
    result = select_and_freeze(request)
    assert not isinstance(result, Hold)
    assert result.decision_slots[0].original_ply == route.decisions[0].ply
    wire = result.to_wire()
    assert isinstance(validate_wire_v1({**wire, "trusted": True}), Hold)
    assert isinstance(validate_wire_v1({**wire, "moves_uci": wire["moves_uci"] + ["e2e4"] * 40}), Hold)
    assert isinstance(select_and_freeze(replace(request, dues=())), Hold)
    one_wire = dict(wire)
    first_slot = wire["decision_slots"][0]
    one_wire["decision_slots"] = [first_slot]
    one_wire["moves_uci"] = wire["moves_uci"][:first_slot["move_offset"] + 1]
    assert not isinstance(validate_wire_v1(one_wire), Hold)
    assert isinstance(validate_wire_v1({**one_wire, "decision_slots": []}), Hold)

    # Strict cross-language contract permits its maximum of 20 decisions.
    max_wire = dict(wire)
    max_wire["moves_uci"] = ["e2e4"] * 20
    max_wire["decision_slots"] = [
        {"move_offset": index, "position_key": f"position-{index}", "expected_uci": "e2e4"}
        for index in range(20)
    ]
    assert not isinstance(validate_wire_v1(max_wire), Hold)
