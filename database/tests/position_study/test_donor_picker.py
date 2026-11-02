import json

import chess

from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import SourceCertificate
from tempo_game_review_contracts import AuthoredRoute, RepertoireSnapshot as MembershipSnapshot
from app.contracts.product_v02.repertoire import (
    DecisionPresentation,
    RepertoireSnapshot,
    RouteBoundary,
)
from app.repertoire import build_index
from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_validated_index
from tooling.position_study_donors import select_donors
from tooling.position_study_inventory import DonorFact, InventoryRequest, SourceFacts


def _snapshot(*routes, bindings=()):
    authored = [AuthoredRoute(**route) for route in routes]
    membership = MembershipSnapshot(
        workspace_id="fixture", snapshot_id="snapshot", revision=1, digest="a" * 64,
        source_revision="source-1", captured_at="2026-11-02T12:00:00Z", scope_id="fixture",
        scope_repertoire_ids=sorted({route["repertoire_id"] for route in routes}),
        complete=True, available=True, routes=authored,
    )
    boundaries = tuple(RouteBoundary(route_id=route["route_id"], terminal_ply=len(route["moves"]),
                                     deliberate=True) for route in routes)
    presentations = []
    for binding in bindings:
        route = next(route for route in routes if route["route_id"] == binding["route_id"])
        board = chess.Board(route["starting_fen"])
        for move in route["moves"][:binding["ply"]]:
            board.push_uci(move)
        presentations.append(DecisionPresentation(
            decision_id=f"decision-{binding['card_id']}-{binding['route_id']}",
            repertoire_id=binding["repertoire_id"],
            position_key=opening_position_key(board.fen()),
            selected_response_uci=route["moves"][binding["ply"]],
            presentation_id=binding["card_id"], presentation_revision=binding["revision"],
            repertoire_revision=membership.revision, introduction="unknown", introduced_at=None,
            available=binding.get("available", True),
        ))
    return RepertoireSnapshot(membership=membership, variant="standard", learner_color="white",
                              boundaries=boundaries,
                              decision_presentations=tuple(presentations))


def _route(route_id="route", repertoire_id="r1", moves=("e2e4",)):
    return {"repertoire_id": repertoire_id, "route_id": route_id, "learner_color": "white",
            "starting_fen": chess.STARTING_FEN, "moves": list(moves), "policy_id": "policy-v1",
            "reentries": []}


def _binding(card_id, route_id="route", repertoire_id="r1", ply=0):
    return {"card_id": card_id, "route_id": route_id, "repertoire_id": repertoire_id,
            "ply": ply, "revision": 2}


def _source(snapshot, certificate=None):
    membership = snapshot.membership
    certificate = certificate or SourceCertificate(
        membership.snapshot_id, membership.source_revision, membership.digest, True, True)
    return SourceFacts(snapshot, inventory_from_validated_index(
        snapshot, build_index(snapshot), source_certificate=certificate))


def _fsrs(card_id, due="2026-11-03T00:00:00+00:00"):
    return json.dumps({"card_id": card_id, "state": 1, "step": 0, "stability": None,
                       "difficulty": None, "due": due, "last_review": None})


def _donor(card_id, *, due="2026-11-03T00:00:00+00:00", **changes):
    values = dict(
        card_id=card_id, repertoire_id="r1", revision=2, state="learning",
        due_date="2026-11-03", fsrs_card_json=_fsrs(int(card_id[-1])),
        introduced_at="2026-10-01T12:00:00+00:00", archived=False,
        pending_validation=False, superseded_by=None, integrity_blocked=False,
        owner_repertoire_ids=("r1",), card_exists=True, content_type="opening", deleted=False,
    )
    values.update(changes)
    if "fsrs_card_json" not in changes:
        values["fsrs_card_json"] = _fsrs(int(card_id[-1]), due)
    return DonorFact(**values)


def _request(*donors, aliases=1, certificate=None):
    routes = tuple(_route(f"route-{i}") for i in range(aliases))
    bindings = tuple(_binding(donor.card_id, f"route-{i}")
                     for donor in donors for i in range(aliases))
    source = _source(_snapshot(*routes, bindings=bindings), certificate)
    return InventoryRequest((source,), tuple(donors))


def _selected(request):
    decisions = select_donors(request)
    assert len(decisions) == 1
    return decisions[0]


def test_permutation_invariant_and_earliest_valid_due_then_stable_id():
    later = _donor("card-2", due="2026-11-06T00:00:00+00:00")
    earliest = _donor("card-1", due="2026-11-03T00:00:00+00:00")
    one = _selected(_request(later, earliest)).selected
    two = _selected(_request(earliest, later)).selected
    assert one == two
    assert one.donor_card_id == "card-1"
    assert one.fsrs_card_json == earliest.fsrs_card_json
    assert one.fsrs_fingerprint
    assert one.source_snapshot_id == "snapshot"
    assert one.source_revision == "source-1"
    assert one.source_digest == "a" * 64


def test_malformed_earliest_state_does_not_hide_later_valid_donor():
    malformed = _donor("card-1", due="2026-11-01T00:00:00+00:00",
                       fsrs_card_json='{"card_id":1,"state":999}')
    valid = _donor("card-2", due="2026-11-04T00:00:00+00:00")
    decision = _selected(_request(malformed, valid))
    assert decision.selected.donor_card_id == "card-2"


def test_duplicate_route_aliases_still_produce_one_donor_decision():
    donor = _donor("card-1")
    decision = select_donors(_request(donor, aliases=4))
    assert len(decision) == 1
    assert decision[0].selected.donor_card_id == "card-1"


def test_stale_source_certificate_holds_without_selecting_donor():
    stale = SourceCertificate("other", "source-1", "a" * 64, True, True)
    decision = _selected(_request(_donor("card-1"), certificate=stale))
    assert decision.selected is None
    assert decision.hold.reason.startswith("source_held:")


def test_shared_physical_card_across_repertoires_is_held():
    shared = _donor("card-1", owner_repertoire_ids=("r1", "r2"))
    decision = _selected(_request(shared))
    assert decision.selected is None
    assert decision.hold.candidate_reasons == (("card-1", "shared_across_repertoires"),)


def test_missing_state_archived_deleted_and_locked_candidates_are_held():
    for fact in (
        _donor("card-1", fsrs_card_json=None),
        _donor("card-1", archived=True),
        _donor("card-1", card_exists=False),
        _donor("card-1", state="locked"),
    ):
        decision = _selected(_request(fact))
        assert decision.selected is None
        assert decision.hold is not None


def test_native_validator_holds_unsupported_version_and_short_prefix_state():
    complete = _donor("card-1")
    unsupported = _donor("card-1", fsrs_card_json=_fsrs(1)[:-1] + ',"future_field":1}')
    shortened = _donor("card-1", fsrs_card_json='{"card_id":1,"state":1,"step":0}')
    for fact in (unsupported, shortened):
        decision = _selected(_request(fact))
        assert decision.selected is None
        assert decision.hold.candidate_reasons[0][0] == "card-1"
        assert decision.hold.candidate_reasons[0][1] in {
            "invalid_native_fsrs_state", "invalid_fsrs_state_shape"}
    assert _selected(_request(complete)).selected is not None


def test_response_policy_conflict_is_held_by_certified_inventory():
    routes = (_route("route-a"), _route("route-b", moves=("d2d4",)))
    bindings = (_binding("card-1", "route-a"), _binding("card-2", "route-b"))
    snapshot = _snapshot(*routes, bindings=bindings)
    request = InventoryRequest((_source(snapshot),), (_donor("card-1"), _donor("card-2")))
    decision = _selected(request)
    assert decision.selected is None
    assert decision.hold.reason.startswith("source_held:")


def test_selected_original_blob_is_not_mutated_by_native_validation():
    donor = _donor("card-1")
    original = donor.fsrs_card_json
    decision = _selected(_request(donor))
    assert decision.selected.fsrs_card_json == original
    assert donor.fsrs_card_json == original


def test_tombstoned_or_unknown_donor_is_ineligible_even_if_card_exists():
    for deleted in (True, None):
        result = _selected(_request(_donor('card-1', deleted=deleted)))
        assert result.selected is None
        assert result.hold.candidate_reasons == (('card-1', 'deleted_or_unknown'),)
    result = _selected(_request(_donor('card-1', deleted=True), _donor('card-2')))
    assert result.selected.donor_card_id == 'card-2'


def test_unavailable_binding_remains_visible_but_cannot_donate():
    unavailable = _binding('card-1') | {'available': False}
    snapshot = _snapshot(_route(), bindings=(unavailable,))
    decision = _selected(InventoryRequest((_source(snapshot),), (_donor('card-1'),)))
    assert decision.selected is None
    assert decision.hold.candidate_reasons == (('card-1', 'binding_unavailable_or_ambiguous'),)
    # A different valid binding can be selected without reviving the unavailable one.
    snapshot = _snapshot(_route(), bindings=(unavailable, _binding('card-2')))
    decision = _selected(InventoryRequest((_source(snapshot),), (_donor('card-1'), _donor('card-2'))))
    assert decision.selected.donor_card_id == 'card-2'


def test_same_card_with_conflicting_binding_availability_is_held():
    snapshot = _snapshot(_route(), bindings=(_binding('card-1'),
        _binding('card-1') | {'available': False}))
    decision = _selected(InventoryRequest((_source(snapshot),), (_donor('card-1'),)))
    assert decision.selected is None
    assert decision.hold.candidate_reasons == (('card-1', 'binding_unavailable_or_ambiguous'),)


def test_unavailable_reference_cannot_be_omitted_from_detached_projection():
    from dataclasses import replace
    unavailable = _binding('card-1') | {'available': False}
    snapshot = _snapshot(_route(), bindings=(unavailable, _binding('card-2')))
    source = _source(snapshot)
    position = source.inventory.positions[0]
    tampered = replace(position, physical_bindings=tuple(
        b for b in position.physical_bindings if b.card_id != 'card-1'))
    source = replace(source, inventory=replace(source.inventory, positions=(tampered,)))
    result = _selected(InventoryRequest((source,), (_donor('card-1'), _donor('card-2'))))
    assert result.selected is None
    assert result.hold.reason.startswith('source_held:')
