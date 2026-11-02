from dataclasses import FrozenInstanceError, replace
import json

import chess
import pytest

from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import (
    DecisionOccurrence, PositionDefinition, PositionKey, RouteOption, SourceCertificate,
)
from app.study.position_learning.source_change_policy import ScopedPositionSource, compare_source_change
from tests.fixtures.position_study_cases import ROUTES

CERT = SourceCertificate("original-full-snapshot", "revision-1", "full-mappings-digest", True, True)
NEXT = replace(CERT, snapshot_id="snapshot-2", source_revision="revision-2", source_digest="different-full-digest")


def option(label, certificate=CERT):
    authored = next(r for r in ROUTES if r.label == label)
    board = chess.Board(authored.start_fen)
    decisions = []
    for ply, uci in enumerate(authored.moves):
        if board.turn == chess.WHITE:
            definition = PositionDefinition(PositionKey(authored.repertoire, opening_position_key(board.fen())), uci, "policy-1")
            decisions.append(DecisionOccurrence(ply, definition, board.fen()))
        board.push_uci(uci)
    return RouteOption(label, authored.start_fen, authored.moves, "white", tuple(decisions), certificate, True)


def scope(routes, key=None, certificate=None, authored_digest="authored-only-digest"):
    key = key or routes[0].decisions[-1].definition.key
    return ScopedPositionSource(key, certificate or routes[0].certificate, authored_digest, routes)


def single(key, answer="g1f3", policy="policy-1", route_id="route", certificate=CERT):
    fen = chess.Board(key.position).fen()
    return RouteOption(route_id, fen, (answer,), "white",
                       (DecisionOccurrence(0, PositionDefinition(key, answer, policy), fen),), certificate, True)


def test_london_new_opponent_route_preserves_complete_schedule_identity():
    a, b = option("london-order-a"), option("london-order-b", NEXT)
    key = a.decisions[-1].definition.key
    assert key == b.decisions[-1].definition.key
    before = scope((a,), key)
    after = scope((replace(a, certificate=NEXT), b), key, authored_digest="edited-authored")
    decision = compare_source_change(before, after)
    assert decision.disposition == "preserve_schedule"
    assert decision.reuse_schedule and decision.allow_new_issuance
    assert not decision.requires_reauthorization
    assert decision.reprepare_presentation
    assert decision.added_route_ids == ("london-order-b",)
    assert decision.retired_route_ids == ()
    assert before.routes[0] is a  # No history/schedule/route mutation.


@pytest.mark.parametrize("answer,policy,reason", [
    ("b1c3", "policy-1", "selected_response_changed"),
    ("g1f3", "policy-2", "content_policy_changed"),
])
def test_changed_own_response_or_policy_holds_until_explicit_reauthorization(answer, policy, reason):
    key = PositionKey("rep", opening_position_key(chess.STARTING_FEN))
    decision = compare_source_change(scope((single(key),)), scope((single(key, answer, policy),)))
    assert decision.disposition == "hold_definition"
    assert decision.reason == reason
    assert not decision.allow_new_issuance and not decision.reuse_schedule
    assert decision.requires_reauthorization
    assert decision.as_record()["prior_attempt_rule"] == "hold_positive_until_explicit_reauthorization"
    assert decision.as_record()["retain_accepted_grade_history"]


def test_other_repertoire_is_independent_even_with_different_answer():
    key = PositionKey("rep", opening_position_key(chess.STARTING_FEN))
    other = replace(key, repertoire_id="other")
    decision = compare_source_change(scope((single(key),)), scope((single(other, "d2d4"),)))
    assert decision.disposition == "independent"
    assert decision.allow_new_issuance and not decision.reuse_schedule
    assert not decision.retired_route_ids


def test_route_gone_while_queued_retains_old_attempt_proof_without_fresh_issuance():
    a = option("london-order-a")
    before = scope((a,))
    after = replace(before, certificate=NEXT, authored_digest="removed-route", routes=())
    decision = compare_source_change(before, after)
    assert decision.disposition == "retire_new_issuance"
    assert not decision.allow_new_issuance
    assert decision.retired_route_ids == (a.route_id,)
    record = decision.as_record()
    assert record["retain_stored_schedule"]
    assert record["retain_original_attempt_proof"] and record["retain_accepted_grade_history"]
    assert record["prior_attempt_rule"] == "revalidate_original_attempt_proof"
    assert before.routes == (a,)


def test_one_alias_removed_keeps_schedule_for_remaining_legal_route():
    a, b = option("london-order-a"), option("london-order-b")
    decision = compare_source_change(scope((a, b)), scope((b,)))
    assert decision.disposition == "preserve_schedule"
    assert decision.reuse_schedule and decision.allow_new_issuance
    assert decision.retired_route_ids == (a.route_id,)


def test_unrelated_source_edit_does_not_globally_reset_untouched_position():
    route = option("london-order-a")
    before = scope((route,))
    after = scope((replace(route, certificate=NEXT),), authored_digest="unrelated-edit")
    decision = compare_source_change(before, after)
    assert decision.disposition == "preserve_schedule"
    assert decision.reuse_schedule and decision.allow_new_issuance
    assert not decision.requires_reauthorization
    assert decision.as_record()["prior_attempt_rule"] == "revalidate_original_attempt_proof"
    assert before.authored_digest != before.certificate.source_digest


def test_castling_rights_difference_is_independent_exact_position():
    key = PositionKey("rep", "4k2r/8/8/8/8/8/8/4K2R w Kk -")
    other = replace(key, position="4k2r/8/8/8/8/8/8/4K2R w k -")
    decision = compare_source_change(scope((single(key, "h1h2"),)), scope((single(other, "h1h2"),)))
    assert decision.disposition == "independent" and not decision.reuse_schedule


@pytest.mark.parametrize("cert", [replace(NEXT, complete=False), replace(NEXT, current_integrity=False)])
def test_bad_certificate_holds_even_empty_scope_cannot_prove_removal(cert):
    before = scope((option("london-order-a"),))
    decision = compare_source_change(before, replace(before, routes=(), certificate=cert))
    assert decision.disposition == "hold_definition"
    assert decision.reason == "source_integrity_unavailable"
    assert not decision.allow_new_issuance


def test_conflicting_source_responses_hold_instead_of_selecting_one():
    key = PositionKey("rep", opening_position_key(chess.STARTING_FEN))
    before = scope((single(key),))
    after = scope((single(key), single(key, "d2d4", route_id="conflict")))
    decision = compare_source_change(before, after)
    assert decision.reason == "conflicting_position_definitions"
    assert decision.disposition == "hold_definition" and decision.requires_reauthorization


def test_new_unchanged_and_mixed_source_contracts():
    route = option("london-order-a")
    facts = scope((route,))
    assert compare_source_change(None, facts).disposition == "new"
    decision = compare_source_change(facts, facts)
    assert decision.reuse_schedule and not decision.reprepare_presentation
    assert json.loads(json.dumps(decision.as_record())) == decision.as_record()
    with pytest.raises(FrozenInstanceError):
        decision.disposition = "new"
    mixed = replace(facts, certificate=NEXT)
    assert compare_source_change(facts, mixed).reason == "route_source_unproven"
    assert compare_source_change(facts, replace(facts, routes=(route, route))).reason == "ambiguous_route_identity"


def test_scoped_route_bound_holds_without_truncating_or_retiring_history():
    route = option("london-order-a")
    before = scope((route,))
    after = replace(before, routes=tuple(replace(route, route_id=f"route-{i}") for i in range(65)))
    decision = compare_source_change(before, after)
    assert decision.disposition == "hold_definition"
    assert decision.reason == "scoped_source_exceeds_bound"
    assert not decision.allow_new_issuance
    assert decision.as_record()["retain_stored_schedule"]
