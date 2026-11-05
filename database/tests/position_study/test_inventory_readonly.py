from types import SimpleNamespace

import chess
from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import SourceCertificate
from tempo_game_review_contracts import AuthoredRoute, RepertoireSnapshot as MembershipSnapshot

from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot, RouteBoundary
from app.repertoire import build_index
from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot
from tempo_legacy_database.adapters.study.position_seed_inventory import (
    DonorFact, InventoryHold, InventoryRequest, SourceFacts, build_inventory, read_card_page,
)


def authored(route_id, repertoire_id="r1", moves=("e2e4",), **changes):
    return dict(repertoire_id=repertoire_id, route_id=route_id, learner_color="white",
                starting_fen=chess.STARTING_FEN, moves=list(moves), policy_id=repertoire_id,
                reentries=[]) | changes


def snapshot(*routes, bindings=()):
    authored_routes = [AuthoredRoute(**{k: v for k, v in route.items() if k != "complete"}) for route in routes]
    membership = MembershipSnapshot(
        workspace_id="fixture", snapshot_id="snapshot", revision=1, digest="a" * 64,
        source_revision="source-1", captured_at="2026-11-02T12:00:00Z", scope_id="fixture",
        scope_repertoire_ids=sorted({route["repertoire_id"] for route in routes}),
        complete=True, available=True, routes=authored_routes)
    boundaries = tuple(RouteBoundary(route_id=route["route_id"], terminal_ply=len(route["moves"]),
                                     deliberate=route.get("complete", True)) for route in routes)
    mappings = []
    for value in bindings:
        route = next(r for r in routes if r["route_id"] == value["route_id"]
                     and r["repertoire_id"] == value["repertoire_id"])
        board = chess.Board(route["starting_fen"])
        for move in route["moves"][:value["ply"]]:
            board.push_uci(move)
        mappings.append(DecisionPresentation(
            decision_id=value["decision_id"], repertoire_id=value["repertoire_id"],
            position_key=opening_position_key(board.fen()),
            selected_response_uci=value.get("selected_response") or route["moves"][value["ply"]],
            presentation_id=value["card_id"], presentation_revision=value["revision"],
            repertoire_revision=membership.revision, introduction="unknown", introduced_at=None,
            available=value.get("available", True)))
    return RepertoireSnapshot(membership=membership, variant="standard", learner_color="white",
                              boundaries=boundaries, decision_presentations=tuple(mappings))


def card_binding(card_id, route_id="route", repertoire_id="r1", ply=0, revision=2,
                 selected_response=None):
    return {"card_id": card_id, "route_id": route_id, "repertoire_id": repertoire_id,
            "ply": ply, "revision": revision, "decision_id": f"decision-{card_id}",
            "selected_response": selected_response}


def source(snapshot_value, certificate=None):
    return SourceFacts(snapshot_value, inventory_from_snapshot(
        snapshot_value, source_certificate=certificate or certificate_for(snapshot_value)))


def certificate_for(snapshot_value):
    membership = snapshot_value.membership
    return SourceCertificate(membership.snapshot_id, membership.source_revision,
                             membership.digest, True, True)


def donor(card_id, repertoire_id, owners=None):
    return DonorFact(
        card_id=card_id, repertoire_id=repertoire_id, revision=2, state="learning",
        due_date="2026-11-03", fsrs_card_json='{"card_id":1,"state":2,"step":null,"stability":1,"difficulty":1,"due":"2026-11-03T00:00:00+00:00","last_review":null}',
        introduced_at="2026-10-01T12:00:00+00:00", archived=False,
        pending_validation=False, superseded_by=None, integrity_blocked=False,
        owner_repertoire_ids=tuple(owners if owners is not None else (repertoire_id,)), deleted=False)


def test_legacy_introduction_day_is_valid_evidence_without_inventing_an_instant():
    from dataclasses import replace
    value = snapshot(authored("route"), bindings=(card_binding("card"),))
    for introduced, valid in (("2026-10-01", True), ("2024-02-29", True),
                              ("2026-02-29", False), ("20261001", False),
                              ("2026-W40-4", False), ("2026-10-01T00:00:00", False),
                              ("2026-10-01T12:00:00+02:00", True)):
        fact = replace(donor("card", "r1"), introduced_at=introduced)
        result = build_inventory(InventoryRequest((source(value),), (fact,)))["repertoires"][0]
        status = result["positions"][0]["donor_candidates"][0]["status"]
        assert status == ("source_eligible_unvalidated_fsrs_runtime" if valid else "invalid_introduction_timestamp")
        assert result["due_introduction_distribution"] == {
            "introduced" if valid else "invalid_or_unknown_introduction": 1}
        assert fact.introduced_at == introduced


def test_same_physical_card_in_two_repertoires_is_excluded_for_both():
    value = snapshot(authored("a", "r1"), authored("b", "r2"), bindings=(
        card_binding("shared", route_id="a", repertoire_id="r1"),
        card_binding("shared", route_id="b", repertoire_id="r2")))
    result = build_inventory(InventoryRequest(
        (source(value),), (donor("shared", "r1", ("r1", "r2")),)))
    assert [row["shared_card_exclusions"] for row in result["repertoires"]] == [1, 1]
    assert [row["positions"][0]["donor_candidates"][0]["status"]
            for row in result["repertoires"]] == ["shared_across_repertoires"] * 2


def test_one_hundred_routes_count_one_exact_position_once():
    value = snapshot(*(authored(f"route-{i}") for i in range(100)))
    repertoire = build_inventory(InventoryRequest((source(value),), ())) ["repertoires"][0]
    assert repertoire["distinct_exact_positions"] == 1
    assert repertoire["distinct_routes"] == 100
    assert repertoire["position_route_occurrences"] == 100
    assert repertoire["metrics"]["distinct_positions_tested_per_minute"] is None


def test_arbitrary_and_stale_certificates_hold():
    value = snapshot(authored("route"))
    index = build_index(value)
    arbitrary = SourceFacts(value, object())
    stale = SourceCertificate("other", "source-1", "a" * 64, True, True)
    invalid_result = build_inventory(InventoryRequest((arbitrary,), ()))
    stale_result = build_inventory(InventoryRequest((SourceFacts(value, inventory_from_snapshot(value, source_certificate=stale)),), ()))
    assert invalid_result["repertoires"][0]["source_hold"] == "authored_route_certificate_unavailable"
    assert stale_result["repertoires"][0]["source_hold"] == "source_certificate_mismatch"
    assert invalid_result["repertoires"][0]["distinct_exact_positions"] == 0
    assert stale_result["repertoires"][0]["distinct_exact_positions"] == 0


def test_stale_generation_source_is_held_and_omitted_from_counts():
    value = snapshot(authored("stale-route"))
    cert = SourceCertificate("snapshot", "stale-generation", "a" * 64, True, True)
    result = build_inventory(InventoryRequest((SourceFacts(value, inventory_from_snapshot(value, source_certificate=cert)),), ()))
    repertoire = result["repertoires"][0]
    assert repertoire["source_status"] == "held"
    assert repertoire["source_hold"] == "source_certificate_mismatch"
    assert repertoire["distinct_exact_positions"] == 0


def test_ambiguous_physical_mapping_is_eligible_but_source_fault_holds():
    value = snapshot(authored("a"), authored("b"), bindings=(
        card_binding("one", "a"), card_binding("two", "a")))
    index = build_index(value)
    assert not index.complete
    result = build_inventory(InventoryRequest((SourceFacts(value, inventory_from_snapshot(value, source_certificate=certificate_for(value))),), ()))
    repertoire = result["repertoires"][0]
    assert repertoire["source_status"] == "source_facts_only"
    assert repertoire["distinct_exact_positions"] == 1
    assert "own_response_conflict" not in repertoire["positions"][0]["holds"]
    assert repertoire["donor_status_counts"] == {"missing_card_source": 2}

    invalid = snapshot(authored("bad", complete=False))
    held = build_inventory(InventoryRequest((source(invalid),), ())) ["repertoires"][0]
    assert held["source_status"] == "held"
    assert held["distinct_exact_positions"] == 0


def test_missing_donor_is_reported_without_partial_count_crash():
    value = snapshot(authored("route"), bindings=(card_binding("missing", route_id="route"),))
    repertoire = build_inventory(InventoryRequest((source(value),), ())) ["repertoires"][0]
    assert repertoire["distinct_exact_positions"] == 1
    assert repertoire["donor_status_counts"] == {"missing_card_source": 1}
    assert repertoire["physical_card_state_counts"] == {"missing_card_source": 1}
    assert repertoire["due_introduction_distribution"] == {"missing_card_source": 1}


def test_invalid_donor_state_is_a_hold_with_measured_source_counts():
    value = snapshot(authored("route"), bindings=(card_binding("card", route_id="route"),))
    invalid = donor("card", "r1")
    invalid = DonorFact(**{**invalid.__dict__, "fsrs_card_json": "not-json"})
    repertoire = build_inventory(InventoryRequest((source(value),), (invalid,))) ["repertoires"][0]
    assert repertoire["distinct_exact_positions"] == 1
    assert repertoire["donor_status_counts"] == {"invalid_fsrs_state_shape": 1}
    assert repertoire["positions"][0]["source_eligible_donor_card_ids"] == []


class FakeReader:
    read_only = True

    def __init__(self, identity):
        self.identity = identity
        self.calls = []
        self.raw = SimpleNamespace(info=SimpleNamespace(
            transaction_status=SimpleNamespace(name="INTRANS")))

    def execute_native(self, sql, params=()):
        self.calls.append((sql, params))
        if "current_user" in sql:
            return SimpleNamespace(fetchone=lambda: self.identity)
        return SimpleNamespace(fetchall=lambda: [("card",)])


def test_card_page_requires_reader_role_repeatable_read_and_bounded_batch():
    reader = FakeReader(("tempo_reader", "on", "repeatable read"))
    assert read_card_page(reader, candidate_card_ids=("card-01", "card-02")) == [("card",)]
    assert reader.calls[1][1] == (["card-01", "card-02"],)
    assert "LEFT JOIN cards c ON c.id = requested.card_id" in reader.calls[1][0]
    assert "FROM deleted_cards d" in reader.calls[1][0]
    try:
        read_card_page(FakeReader(("tempo_writer", "off", "read committed")), candidate_card_ids=("card",))
    except InventoryHold as error:
        assert str(error) == "postgres_read_only_role_required"
    else:
        raise AssertionError("writer connection was accepted")
