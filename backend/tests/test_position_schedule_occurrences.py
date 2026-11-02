"""Native-chess source projection regressions; no source authority is fabricated."""
from dataclasses import replace

import chess
import pytest
from tempo_evidence_math.position_learning import Hold, PositionKey, SourceCertificate
from tempo_evidence_math import opening_position_key

from app.repertoire.position_index import build_index
from app.repertoire.interfaces.position_schedule_occurrences import (
    index_routes, options_for_target, routes_from_validated_index,
    inventory_from_snapshot, PositionInventoryFacts,
)
from test_product_position_index import binding, route, snapshot


def project(source, certificate=True):
    member = source.membership
    proof = SourceCertificate(member.snapshot_id, member.source_revision, member.digest, True, True) if certificate else None
    return routes_from_validated_index(source, build_index(source), source_certificate=proof)


def inventory(source, certificate=True):
    member = source.membership
    proof = SourceCertificate(member.snapshot_id, member.source_revision, member.digest, True, True) if certificate else None
    return inventory_from_snapshot(source, source_certificate=proof)


@pytest.mark.parametrize("count", [1, 2, 100])
def test_aliases_are_distinct_routes_for_one_position(count):
    source = snapshot(*(route(f"r{i:03}", ("d2d4",)) for i in range(count)))
    options = project(source)
    key = PositionKey("rep", opening_position_key(chess.STARTING_FEN))
    matches = options_for_target(index_routes(options), key)
    assert len(matches) == count
    assert [r.route_id for r in matches] == sorted(r.route_id for r in matches)
    assert all(r.decisions[0].definition.key == key for r in matches)
    facts = inventory(source)
    assert not isinstance(facts, Hold)
    assert (facts.distinct_position_count, facts.distinct_route_count, facts.route_occurrence_count) == (1, count, count)


def test_london_transposition_and_exact_repeated_ply():
    a = chess.Board(); a.push_uci("g1f3"); a.push_uci("d7d5")
    b = chess.Board(); b.push_uci("g2g3"); b.push_uci("c7c5")
    source = snapshot(route("a", ("g2g3", "c7c5", "f1g2"), starting_fen=a.fen()),
                      route("b", ("g1f3", "d7d5", "f1g2"), starting_fen=b.fen()))
    for m in ("g2g3", "c7c5"): a.push_uci(m)
    matches = options_for_target(index_routes(project(source)), PositionKey("rep", opening_position_key(a.fen())))
    assert [(r.route_id, r.decisions[-1].ply) for r in matches] == [("a", 2), ("b", 2)]
    repeat = snapshot(route("repeat", ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3")))
    option = project(repeat)[0]
    assert [d.ply for d in option.decisions if d.definition.key.position == opening_position_key(chess.STARTING_FEN)] == [0, 4]


def test_conflicts_illegal_incomplete_and_missing_authority_hold():
    conflict = project(snapshot(route("a", ("e2e4",)), route("b", ("d2d4",))))
    assert isinstance(conflict, Hold) and "own_response_conflict" in conflict.detail
    illegal = project(snapshot(route("bad", ("e2e5",))))
    assert isinstance(illegal, Hold) and "illegal_move" in illegal.detail
    assert isinstance(project(snapshot(route(complete=False))), Hold)
    source = snapshot(route())
    assert project(source, False).reason == "authored_source_unavailable_or_not_current"
    cert = SourceCertificate("other", "source-1", "a" * 64, True, True)
    assert routes_from_validated_index(source, build_index(source), source_certificate=cert).reason == "source_certificate_mismatch"
    assert routes_from_validated_index(source, replace(build_index(source), complete=False), source_certificate=SourceCertificate("snapshot", "source-1", "a"*64, True, True)).reason == "source_index_mismatch"


def test_p11_physical_ambiguity_does_not_veto_authored_route():
    source = snapshot(route("a", ("e2e4",), bindings=(binding("one"), binding("two"))),
                      route("b", ("e2e4",)))
    index = build_index(source)
    assert not index.complete and any(d.code == "ambiguous_physical_mapping" for d in index.diagnostics)
    options = project(source)
    assert not isinstance(options, Hold) and len(options) == 2
    assert options[0].decisions[0].definition.key == options[1].decisions[0].definition.key


def test_selected_mapping_conflict_is_not_hidden_by_index():
    source = snapshot(route("a", ("e2e4",), bindings=(binding(),)))
    mapping = source.decision_presentations[0].model_copy(update={"selected_response_uci": "d2d4"})
    source = source.model_copy(update={"decision_presentations": (mapping,)})
    assert project(source).reason == "selected_response_conflict"


def test_inventory_projects_exact_occurrences_and_counts_repeated_aliases():
    source = snapshot(route("a", ("g1f3", "g8f6", "f3g1", "f6g8", "g1f3")),
                      route("b", ("g1f3",)))
    facts = inventory(source)
    assert isinstance(facts, PositionInventoryFacts)
    start = next(row for row in facts.positions if row.definition.key.position == opening_position_key(chess.STARTING_FEN))
    assert [(item.route_id, item.route_ply) for item in start.occurrences] == [("a", 0), ("a", 4), ("b", 0)]
    assert facts.distinct_route_count == 2
    assert facts.route_occurrence_count == 4
    assert facts.distinct_position_count == 2
    assert [(item.route_id, item.route_ply) for item in next(
        row for row in facts.positions if row.definition.key.position == opening_position_key(chess.STARTING_FEN)
    ).occurrences] == [("a", 0), ("a", 4), ("b", 0)]
    assert sorted(item.route_ply for row in facts.positions for item in row.occurrences) == [0, 0, 2, 4]
    assert facts.source_certificate is not None
    assert facts.source_scopes.repertoire_ids == ("rep",)
    assert not hasattr(facts, "index") and not hasattr(facts, "nodes")
    with pytest.raises((AttributeError, TypeError)):
        facts.distinct_route_count = 50


def test_inventory_keeps_unavailable_physical_reference_as_explicit_fact():
    source = snapshot(route("a", ("e2e4",), bindings=(binding("card"),)))
    mapping = source.decision_presentations[0].model_copy(update={"available": False})
    source = source.model_copy(update={"decision_presentations": (mapping,)})
    facts = inventory(source)
    row = facts.positions[0]
    assert facts.physical_reference_count == 1
    assert (row.physical_bindings[0].card_id, row.physical_bindings[0].card_revision,
            row.physical_bindings[0].available, row.physical_bindings[0].route_id,
            row.physical_bindings[0].route_ply) == ("card", 3, False, None, None)


def test_inventory_preserves_ambiguous_physical_refs_and_holds_source_conflicts():
    source = snapshot(route("a", ("e2e4",), bindings=(binding("one"), binding("two"))),
                      route("b", ("e2e4",)))
    facts = inventory(source)
    assert not isinstance(facts, Hold)
    assert facts.physical_reference_count == 2
    assert {item.card_id for item in facts.positions[0].physical_bindings} == {"one", "two"}
    conflict = inventory(snapshot(route("a", ("e2e4",)), route("b", ("d2d4",))))
    assert isinstance(conflict, Hold) and "own_response_conflict" in conflict.detail
