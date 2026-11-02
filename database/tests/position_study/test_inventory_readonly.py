from types import SimpleNamespace

from dataclasses import dataclass
from tooling.position_study_inventory import (
    DonorFact, InventoryHold, InventoryRequest, SourceFacts, build_inventory, read_card_page,
)


@dataclass(frozen=True, order=True)
class PositionKey:
    repertoire_id: str
    variant: str
    position: str


def occurrence(route_id, bindings=()):
    return SimpleNamespace(route_id=route_id, bindings=bindings)


def binding(card_id):
    return SimpleNamespace(presentation_id=card_id, presentation_revision=2)


def index(nodes, *, complete=True):
    return SimpleNamespace(
        scope_repertoire_ids=("r1",),
        nodes=nodes,
        available=True,
        complete=complete,
    )


def donor(card_id, repertoire_id, owners=("r1",)):
    return DonorFact(
        card_id=card_id, repertoire_id=repertoire_id, revision=2,
        state="learning", due_date="2026-11-03", fsrs_card_json='{"card_id":1,"state":2,"step":null,"stability":1,"difficulty":1,"due":"2026-11-03T00:00:00+00:00","last_review":null}',
        introduced_at="2026-10-01T12:00:00+00:00", archived=False,
        pending_validation=False, superseded_by=None, integrity_blocked=False,
        owner_repertoire_ids=owners,
    )


def test_same_physical_card_in_two_repertoires_is_excluded_for_both():
    shared = binding("shared")
    one = PositionKey("r1", "standard", "fen-one")
    two = PositionKey("r2", "standard", "fen-two")
    first = index({one: SimpleNamespace(occurrences=(occurrence("a", (shared,)),),
                                       conflict=False, selected_response="e2e4")})
    second = SimpleNamespace(scope_repertoire_ids=("r2",), available=True, complete=True,
        nodes={two: SimpleNamespace(occurrences=(occurrence("b", (shared,)),),
                                    conflict=False, selected_response="e2e4")})
    result = build_inventory(InventoryRequest(
        (SourceFacts(first, object()), SourceFacts(second, object())),
        (donor("shared", "r1", ("r1", "r2")),),
    ))
    assert [row["shared_card_exclusions"] for row in result["repertoires"]] == [1, 1]
    assert result["repertoires"][0]["positions"][0]["donor_candidates"][0]["status"] == "shared_across_repertoires"
    assert result["repertoires"][1]["positions"][0]["donor_candidates"][0]["status"] == "shared_across_repertoires"


def test_one_hundred_routes_count_one_exact_position_once():
    key = PositionKey("r1", "standard", "same-position")
    node = SimpleNamespace(occurrences=tuple(occurrence(f"route-{i}") for i in range(100)),
                           conflict=False, selected_response="e2e4")
    result = build_inventory(InventoryRequest((SourceFacts(index({key: node}), object()),), ()))
    repertoire = result["repertoires"][0]
    assert repertoire["distinct_exact_positions"] == 1
    assert repertoire["distinct_routes"] == 100
    assert repertoire["position_route_occurrences"] == 100
    assert repertoire["metrics"]["distinct_positions_tested_per_minute"] is None


def test_stale_generation_source_is_held_and_omitted_from_counts():
    key = PositionKey("r1", "standard", "stale-position")
    node = SimpleNamespace(occurrences=(occurrence("stale-route"),),
                           conflict=False, selected_response="e2e4")
    result = build_inventory(InventoryRequest((SourceFacts(
        index({key: node}), None, "stale_graph_generation"),), ()))
    repertoire = result["repertoires"][0]
    assert repertoire["source_status"] == "held"
    assert repertoire["source_hold"] == "stale_graph_generation"
    assert repertoire["distinct_exact_positions"] == 0


def test_bad_source_holds_and_missing_donor_is_reported():
    key = PositionKey("r1", "standard", "current-position")
    node = SimpleNamespace(occurrences=(occurrence("route", (binding("missing"),)),),
                           conflict=False, selected_response="e2e4")
    result = build_inventory(InventoryRequest((SourceFacts(
        index({key: node}), None, "authored_route_certificate_unavailable"),), ()))
    repertoire = result["repertoires"][0]
    assert repertoire["source_hold"] == "authored_route_certificate_unavailable"
    assert repertoire["distinct_exact_positions"] == 0
    # Source failure is surfaced at repertoire level, and no unproven card rows
    # leak into a partial denominator.
    assert repertoire["positions"] == []


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


def test_card_page_requires_reader_role_repeatable_read_and_bounded_keyset():
    reader = FakeReader(("tempo_reader", "on", "repeatable read"))
    assert read_card_page(reader, candidate_card_ids=("card-01", "card-02")) == [("card",)]
    assert reader.calls[1][1] == (["card-01", "card-02"],)
    assert "LEFT JOIN cards c ON c.id = requested.card_id" in reader.calls[1][0]
    try:
        read_card_page(FakeReader(("tempo_writer", "off", "read committed")), candidate_card_ids=("card",))
    except InventoryHold as error:
        assert str(error) == "postgres_read_only_role_required"
    else:
        raise AssertionError("writer connection was accepted")


def test_invalid_donor_state_is_a_hold_with_measured_source_counts():
    key = PositionKey("r1", "standard", "position")
    node = SimpleNamespace(occurrences=(occurrence("route", (binding("card"),)),),
                           conflict=False, selected_response="e2e4")
    invalid = donor("card", "r1")
    invalid = DonorFact(**{**invalid.__dict__, "fsrs_card_json": "not-json"})
    result = build_inventory(InventoryRequest(
        (SourceFacts(index({key: node}), object()),), (invalid,)))
    repertoire = result["repertoires"][0]
    assert repertoire["distinct_exact_positions"] == 1
    assert repertoire["donor_status_counts"] == {"invalid_fsrs_state_shape": 1}
    assert repertoire["positions"][0]["source_eligible_donor_card_ids"] == []
