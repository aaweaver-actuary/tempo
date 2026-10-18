"""Synthetic original-card authority; no Content publication or Study writes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tempo_game_evidence import LegacyComparisonSnapshot, compare_game, normalize_pgn, replay_positions
from tempo_game_evidence.legacy_authority import authority_digest
from tempo_game_evidence.snapshot import RouteV2, parse_snapshot
from .factories import observed


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def document():
    return json.loads((FIXTURES / "legacy-authority-snapshot.json").read_text())


def seal(value):
    value["routes"] = [RouteV2.model_validate(route).model_dump(mode="json") for route in value["routes"]]
    value["capture_digest"] = authority_digest({key: item for key, item in value.items()
                                               if key != "capture_digest"})
    return value


def findings(value=None, moves=None):
    if moves is None:
        game = normalize_pgn((FIXTURES / "legacy-authority-miss.pgn").read_bytes(), "Learner")
        game.update(workspace_id="fixture", game_id="synthetic-legacy-game", revision=1)
        positions = replay_positions(game)
    else:
        game, positions = observed(moves, color="black")
    snapshot = LegacyComparisonSnapshot.model_validate(value or document())
    return snapshot, compare_game(game, positions, snapshot)


def test_legacy_opponent_prelude_and_interior_miss_retain_original_physical_executable():
    snapshot, result = findings()
    assert [item["status"] for item in result] == ["unknown", "unknown", "missed"]
    miss = result[-1]
    assert (miss["ply"], miss["move"], miss["expected_moves"]) == (5, "g8f6", ["a7a6"])
    assert miss["card_id"] == miss["learning_card_id"] == "physical-black"
    assert miss["card_revision"] == 1 and miss["authority_kind"] == "legacy_graph"
    assert miss["target_id"] == "synthetic-target" and miss["workspace_id"] == "fixture"
    assert miss["applicability"] == "current_at_capture"
    assert snapshot.cards[0].moves == ["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"]
    assert snapshot.cards[0].starting_fen == snapshot.routes[0].starting_fen
    assert snapshot.routes[0].bindings[-1].exercise_ply == 5
    assert miss["executable_digest"] == authority_digest({"starting_fen": snapshot.cards[0].starting_fen,
        "moves": snapshot.cards[0].moves, "trained_color": "black"})
    assert "publication" not in snapshot.model_dump() and "activation" not in snapshot.model_dump()
    # This checkpoint cannot enter the existing publication-backed loader/outbox.
    with pytest.raises(ValueError):
        parse_snapshot(document())


def test_legacy_fixture_is_bound_to_synthetic_capture_and_exact_mapping_report():
    inputs = json.loads((FIXTURES / "legacy-authority-input.json").read_text())
    mapping = json.loads((FIXTURES / "legacy-authority-mapping.json").read_text())
    snapshot, _ = findings()
    assert inputs["provenance"].startswith("synthetic;")
    assert snapshot.authority.source_digest == authority_digest(inputs["tables"])
    assert snapshot.authority.graph_digest == authority_digest(inputs["graphs"])
    assert mapping["mapped_identities"] == ["physical-black"]
    assert mapping["historical_prelude_count"] == 1
    assert mapping["historical_interior_binding_count"] == 2
    assert mapping["policy_authority_anchors"] == {"ruy-route": 5}


@pytest.mark.parametrize("mutate,reason", [
    (lambda value: value["observed_after"].update(scope_source_revision=3), "legacy_authority_changed_during_capture"),
    (lambda value: value.update(catalogue_complete=False), "legacy_catalogue_incomplete"),
    (lambda value: [value[key].update(graph_state="building") for key in ("authority", "observed_after")], "legacy_graph_not_ready"),
    (lambda value: value["cards"][0].update(introduced=False), "legacy_card_not_introduced"),
])
def test_legacy_stale_incomplete_unready_or_unintroduced_is_unknown(mutate, reason):
    value = document()
    mutate(value)
    _, result = findings(seal(value))
    assert result[-1]["status"] == "unknown" and result[-1]["explanation"] == reason
    assert result[-1]["card_id"] is None


@pytest.mark.parametrize("mutate,reason", [
    (lambda value: value["routes"][0].update(authority_start_ply=0), "policy_anchor"),
    (lambda value: value["routes"][0]["bindings"][-1].update(exercise_ply=3), "physical_route_binding"),
    (lambda value: value["routes"][0]["bindings"][-1].update(reentry=True), "physical_route_binding"),
    (lambda value: value["cards"][0].update(trained_color="white"), "physical_route_binding"),
    (lambda value: value["cards"][0]["moves"].__setitem__(-1, "g8f6"), "physical_route_binding"),
    (lambda value: value["cards"][0].update(repertoire_ids=["ruy", "italian"]), "shared_physical_card"),
    (lambda value: value["cards"].append(deepcopy(value["cards"][0])), "duplicate_physical_card"),
    (lambda value: value["routes"].clear(), "route_inventory"),
    (lambda value: value["observed_after"].update(target_id="other-target"), "target_changed"),
])
def test_legacy_ambiguous_or_mixed_physical_authority_is_rejected_even_with_valid_capture_hash(mutate, reason):
    value = document()
    mutate(value)
    with pytest.raises(ValueError, match=reason):
        LegacyComparisonSnapshot.model_validate(seal(value))


def test_legacy_unknown_and_no_miss_are_valid_results_without_expanding_games():
    _, matching = findings(moves=["e2e4", "e7e5", "g1f3", "b8c6", "f1b5", "a7a6"])
    assert matching[-1]["status"] == "matched"
    _, divergent = findings(moves=["e2e4", "c7c5", "g1f3", "g8f6"])
    assert all(item["status"] == "unknown" for item in divergent)


def test_legacy_capture_hash_and_workspace_are_independently_checked():
    value = document()
    value["cards"][0]["card_revision"] = 2
    with pytest.raises(ValueError, match="capture_digest"):
        LegacyComparisonSnapshot.model_validate(value)
    snapshot = LegacyComparisonSnapshot.model_validate(document())
    game, positions = observed(["e2e4", "c7c5"], color="black")
    game["workspace_id"] = "other"
    with pytest.raises(ValueError, match="workspace_mismatch"):
        compare_game(game, positions, snapshot)
