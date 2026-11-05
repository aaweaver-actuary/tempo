from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
import json

import chess

from app.study.position_learning.contracts import (
    DueAtIssuance,
    Hold,
    PositionDefinition,
)
from app.study.position_learning.contracts import PositionKey
from app.study.position_learning.prefix_eligibility import (
    CleanFirstResponse,
    CleanPresentationProof,
)
from app.study.position_learning.issued_plan_contracts import (
    IssuedPlan,
    SelectionRequest,
    decode_issued_record,
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


def request_for(
    route, target_ply=6, *, due=True, proofs=(), attempt="attempt-original"
):
    target_key = target_after(route, target_ply)
    target_occurrence = next(d for d in route.decisions if d.ply == target_ply)
    # The selected suffix includes every learner decision; witnesses retain
    # all due/nondue statuses, including positions before the requested target.
    planned = choose_plan(
        target_key, (route,), clean_proofs=proofs, study_day=DAY, owner_epoch=EPOCH
    )
    assert not isinstance(planned, Hold)
    due_by_position = {}
    for item in planned.decision_occurrences:
        due_by_position.setdefault(
            item.definition.key,
            DueAtIssuance(item.definition, due, DAY, EPOCH, 7, NOW),
        )
    due_rows = tuple(due_by_position.values())
    return SelectionRequest(
        PositionDefinition(
            target_key, target_occurrence.definition.expected_uci, "policy-v1"
        ),
        (route,),
        due_rows,
        tuple(proofs),
        "legacy-card-1",
        4,
        41,
        attempt,
    )


def test_v1_freeze_preserves_original_attempt_and_exposes_strict_wire():
    route = option("italian-knight-attack")
    issued = select_and_freeze(request_for(route))
    assert not isinstance(issued, Hold)
    assert (
        issued.original_card_id,
        issued.original_card_revision,
        issued.queue_entry_id,
        issued.attempt_id,
    ) == ("legacy-card-1", 4, 41, "attempt-original")
    assert issued.version == 1
    assert (
        issued.plan_id
        == validate_wire_v1(issued.to_wire(), expected_plan=issued)["plan_id"]
    )
    assert all(
        "due" not in row and "position_revision" not in row
        for row in issued.to_wire()["decision_slots"]
    )
    assert len(issued.plan_id) == 64
    changed_digest = dict(issued.to_wire(), plan_id="0" * 64)
    assert isinstance(validate_wire_v1(changed_digest, expected_plan=issued), Hold)


def test_server_record_roundtrips_after_json_restart_and_binds_all_due_and_route_fields():
    issued = select_and_freeze(request_for(option("italian-knight-attack"), due=False))
    assert not isinstance(issued, Hold)
    raw = json.loads(json.dumps(issued.to_record(), sort_keys=True))
    decoded = decode_issued_record(raw)
    assert not isinstance(decoded, Hold)
    assert decoded == issued
    assert decoded.to_record() == raw
    assert len(raw["due_witnesses"]) == len(issued.decision_slots)
    assert all(row["due"] is False for row in raw["due_witnesses"])
    assert (
        raw["route"]["decision_slots"][0]["original_ply"]
        == issued.decision_slots[0].original_ply
    )
    assert "due_witnesses" not in issued.to_wire()


def test_server_record_rejects_unknown_fields_and_altered_digest_or_typed_payload():
    issued = select_and_freeze(request_for(option("italian-knight-attack")))
    assert not isinstance(issued, Hold)
    record = issued.to_record()
    mutations = []
    mutations.append({**record, "unexpected": 1})
    mutations.append({**record, "plan_id": "0" * 64})
    changed_due = json.loads(json.dumps(record))
    changed_due["due_witnesses"][0]["due"] = not changed_due["due_witnesses"][0]["due"]
    mutations.append(changed_due)
    changed_epoch = json.loads(json.dumps(record))
    changed_epoch["due_witnesses"][0]["owner_epoch"] = "other-epoch"
    mutations.append(changed_epoch)
    changed_day = json.loads(json.dumps(record))
    changed_day["due_witnesses"][0]["study_day"] = "2026-11-03"
    mutations.append(changed_day)
    changed_definition = json.loads(json.dumps(record))
    changed_definition["due_witnesses"][0]["definition"]["policy_version"] = (
        "other-policy"
    )
    mutations.append(changed_definition)
    changed_slot = json.loads(json.dumps(record))
    changed_slot["route"]["decision_slots"][0]["original_ply"] += 1
    mutations.append(changed_slot)
    wrong_type = json.loads(json.dumps(record))
    wrong_type["original_card_revision"] = True
    mutations.append(wrong_type)
    for mutation in mutations:
        assert isinstance(decode_issued_record(mutation), Hold)


def test_authored_owner_token_is_scope_bound_immutable_and_digest_bound():
    route = option("italian-knight-attack")
    request = request_for(route)
    token = {
        "original_snapshot_id": route.certificate.snapshot_id,
        "original_source_revision": route.certificate.source_revision,
        "original_source_digest": route.certificate.source_digest,
        "repertoire_id": "rep-italian",
        "authored_digest": "authored-content-digest",
        "header": {"id": "rep-italian", "source_revision": "r1"},
        "integrity": {"status": "complete", "scan_generation": 7},
    }
    issued = select_and_freeze(replace(request, authored_source_token=token))
    assert not isinstance(issued, Hold)
    token["header"]["source_revision"] = "mutated-after-selection"
    record = issued.to_record()
    assert record["authored_source_token"]["header"]["source_revision"] == "r1"
    restored = decode_issued_record(json.loads(json.dumps(record)))
    assert not isinstance(restored, Hold)
    assert restored.to_record() == record
    changed_scope = json.loads(json.dumps(record))
    changed_scope["authored_source_token"]["repertoire_id"] = "rep-other"
    assert isinstance(decode_issued_record(changed_scope), Hold)
    changed_payload = json.loads(json.dumps(record))
    changed_payload["authored_source_token"]["integrity"]["scan_generation"] = 8
    assert isinstance(decode_issued_record(changed_payload), Hold)


def test_repeated_legal_position_keeps_occurrence_slots_but_one_due_witness_on_restart():
    route = option("clock-long")
    repeated = [
        d
        for d in route.decisions
        if d.definition.key == route.decisions[0].definition.key
    ]
    assert tuple(d.ply for d in repeated) == (0, 4)
    issued = select_and_freeze(request_for(route, target_ply=4))
    assert not isinstance(issued, Hold)
    assert tuple(slot.original_ply for slot in issued.decision_slots) == (0, 2, 4, 6)
    assert issued.decision_slots[0].definition == issued.decision_slots[2].definition
    assert issued.decision_slots[1].definition == issued.decision_slots[3].definition
    assert len(issued.due_positions) == len(
        {slot.definition.key for slot in issued.decision_slots}
    )
    record = json.loads(json.dumps(issued.to_record()))
    assert len(record["due_witnesses"]) == len(
        {slot["definition"]["position"] for slot in record["route"]["decision_slots"]}
    )
    restored = decode_issued_record(record)
    assert not isinstance(restored, Hold)
    assert tuple(slot.original_ply for slot in restored.decision_slots) == (0, 2, 4, 6)
    assert len(restored.due_positions) == 2


def test_server_record_roundtrips_prefix_receipt_identity_and_opponent_cue():
    route = option("italian-knight-attack")
    played = route.moves_uci[:6]
    decisions = tuple(d for d in route.decisions if d.ply < len(played))
    proof = CleanPresentationProof(
        "saved-prefix",
        route.route_id,
        route.start_fen,
        played,
        decisions,
        tuple(
            CleanFirstResponse(
                d.ply, d.definition, played[d.ply], False, f"event-{d.ply}"
            )
            for d in decisions
        ),
        route.certificate.snapshot_id,
        route.certificate.source_revision,
        route.certificate.source_digest,
        DAY,
        EPOCH,
        True,
        "terminal-proof",
        "review-proof",
    )
    issued = select_and_freeze(request_for(route, proofs=(proof,)))
    assert not isinstance(issued, Hold)
    decoded = IssuedPlan.from_record(json.loads(json.dumps(issued.to_record())))
    assert not isinstance(decoded, Hold)
    assert decoded.opponent_cue_uci == route.moves_uci[5]
    assert decoded.skipped_prefix_proof_ids == (
        "saved-prefix",
        "terminal-proof",
        "review-proof",
    )


def test_digest_changes_with_queue_identity_due_state_and_authored_move_order():
    route = option("italian-knight-attack")
    base = select_and_freeze(request_for(route))
    other_attempt = select_and_freeze(request_for(route, attempt="attempt-other"))
    nondue = select_and_freeze(request_for(route, due=False))
    assert (
        not isinstance(base, Hold)
        and not isinstance(other_attempt, Hold)
        and not isinstance(nondue, Hold)
    )
    assert len({base.plan_id, other_attempt.plan_id, nondue.plan_id}) == 3
    reordered = dict(base.to_wire())
    reordered["moves_uci"] = list(reordered["moves_uci"])
    reordered["moves_uci"][0], reordered["moves_uci"][1] = (
        reordered["moves_uci"][1],
        reordered["moves_uci"][0],
    )
    assert isinstance(validate_wire_v1(reordered), Hold)


def test_opponent_cue_is_not_a_learner_slot_and_original_ply_is_retained():
    route = option("italian-knight-attack")
    played = route.moves_uci[:6]
    decisions = tuple(d for d in route.decisions if d.ply < len(played))
    proof = CleanPresentationProof(
        "saved-prefix",
        route.route_id,
        route.start_fen,
        played,
        decisions,
        tuple(
            CleanFirstResponse(
                d.ply, d.definition, played[d.ply], False, f"event-{d.ply}"
            )
            for d in decisions
        ),
        route.certificate.snapshot_id,
        route.certificate.source_revision,
        route.certificate.source_digest,
        DAY,
        EPOCH,
        True,
        "terminal-proof",
        "review-proof",
    )
    result = select_and_freeze(request_for(route, proofs=(proof,)))
    assert not isinstance(result, Hold)
    assert result.start_ply == 5
    assert result.moves_uci[0] == route.moves_uci[5]  # opponent cue
    assert all(
        slot.original_ply >= 6 and slot.move_offset == slot.original_ply - 5
        for slot in result.decision_slots
    )
    assert "terminal-proof" in result.skipped_prefix_proof_ids


def test_mismatched_due_scope_definition_or_position_holds():
    route = option("italian-knight-attack")
    request = request_for(route)
    assert len(request.dues) > 1
    first, *rest = request.dues
    changed = (
        replace(first, owner_epoch="other-epoch"),
        replace(first, study_day=date(2026, 11, 3)),
        replace(
            first, definition=replace(first.definition, policy_version="other-policy")
        ),
        replace(
            first,
            definition=replace(
                first.definition,
                key=PositionKey(
                    first.definition.key.repertoire_id, "8/8/8/8/8/8/8/K6k w - -"
                ),
            ),
        ),
    )
    for witness in changed:
        result = select_and_freeze(replace(request, dues=(witness, *rest)))
        assert isinstance(result, Hold)


def test_prefix_proof_for_another_route_cannot_suppress_or_skip():
    route = option("italian-knight-attack")
    unrelated = option("london-order-a")
    moves = unrelated.moves_uci
    decisions = unrelated.decisions
    proof = CleanPresentationProof(
        "wrong-route-attempt",
        unrelated.route_id,
        unrelated.start_fen,
        moves,
        decisions,
        tuple(
            CleanFirstResponse(
                d.ply, d.definition, moves[d.ply], False, f"wrong-event-{d.ply}"
            )
            for d in decisions
        ),
        unrelated.certificate.snapshot_id,
        unrelated.certificate.source_revision,
        unrelated.certificate.source_digest,
        DAY,
        EPOCH,
        True,
        "wrong-terminal",
        "wrong-review",
    )
    issued = select_and_freeze(replace(request_for(route), clean_proofs=(proof,)))
    assert not isinstance(issued, Hold)
    assert issued.start_ply == 0
    assert issued.skipped_prefix_proof_ids == ()


def test_italian_knight_and_giuoco_share_only_c3_verified_clean_prefix():
    def whole_clean(route, attempt_id):
        moves = route.moves_uci
        decisions = route.decisions
        return CleanPresentationProof(
            attempt_id,
            route.route_id,
            route.start_fen,
            moves,
            decisions,
            tuple(
                CleanFirstResponse(
                    d.ply,
                    d.definition,
                    moves[d.ply],
                    False,
                    f"{attempt_id}-event-{d.ply}",
                )
                for d in decisions
            ),
            route.certificate.snapshot_id,
            route.certificate.source_revision,
            route.certificate.source_digest,
            DAY,
            EPOCH,
            True,
            f"{attempt_id}-terminal",
            f"{attempt_id}-review",
        )

    attack = option("italian-knight-attack")
    giuoco = option("italian-giuoco")
    for selected, prior in ((giuoco, attack), (attack, giuoco)):
        proof = whole_clean(prior, f"saved-{prior.route_id}")
        issued = select_and_freeze(request_for(selected, target_ply=6, proofs=(proof,)))
        assert not isinstance(issued, Hold)
        assert issued.route_id == selected.route_id
        assert issued.start_ply == 5
        assert issued.opponent_cue_uci == selected.moves_uci[5]
        assert issued.to_wire()["moves_uci"][0] == selected.moves_uci[5]
        assert tuple(slot.original_ply for slot in issued.decision_slots) == tuple(
            decision.ply for decision in selected.decisions if decision.ply >= 6
        )
        assert issued.skipped_prefix_proof_ids == (
            proof.attempt_id,
            proof.terminal_receipt_id,
            proof.review_receipt_id,
        )


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
    assert isinstance(
        validate_wire_v1({**wire, "moves_uci": wire["moves_uci"] + ["e2e4"] * 40}), Hold
    )
    assert isinstance(select_and_freeze(replace(request, dues=())), Hold)
    one_wire = dict(wire)
    first_slot = wire["decision_slots"][0]
    one_wire["decision_slots"] = [first_slot]
    one_wire["moves_uci"] = wire["moves_uci"][: first_slot["move_offset"] + 1]
    assert not isinstance(validate_wire_v1(one_wire), Hold)
    assert isinstance(validate_wire_v1({**one_wire, "decision_slots": []}), Hold)

    # Strict cross-language contract permits its maximum of 20 decisions.
    max_wire = dict(wire)
    max_wire["moves_uci"] = ["e2e4"] * 20
    max_wire["decision_slots"] = [
        {
            "move_offset": index,
            "position_key": f"position-{index}",
            "expected_uci": "e2e4",
        }
        for index in range(20)
    ]
    assert not isinstance(validate_wire_v1(max_wire), Hold)


def test_presentation_end_freezes_authored_prefix_and_replays_exactly():
    route = option("italian-knight-attack")
    request = request_for(route, target_ply=2)
    full = select_and_freeze(request)
    shortened = select_and_freeze(
        replace(
            request,
            end_ply=5,
            dues=tuple(
                w
                for w in request.dues
                if w.definition.key
                in {d.definition.key for d in route.decisions if d.ply < 5}
            ),
        )
    )
    assert not isinstance(full, Hold) and full.moves_uci == route.moves_uci
    assert not isinstance(shortened, Hold)
    assert shortened.moves_uci == route.moves_uci[:5]
    assert tuple(s.original_ply for s in shortened.decision_slots) == (0, 2, 4)
    assert (
        IssuedPlan.from_record(json.loads(json.dumps(shortened.to_record())))
        == shortened
    )
    for end in (2, 4, 6, len(route.moves_uci), True):
        assert isinstance(select_and_freeze(replace(request, end_ply=end)), Hold)
    missing_before_target = replace(
        request,
        end_ply=5,
        dues=tuple(
            w
            for w in request.dues
            if w.definition.key != route.decisions[0].definition.key
        ),
    )
    assert isinstance(select_and_freeze(missing_before_target), Hold)
