"""Owner-generated synthetic proofs; filtered candidates are not global source."""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from tempo_game_evidence import (LegacyScopedComparisonSnapshot, normalize_pgn,
                                 replay_positions, compare_game)
from tempo_game_evidence.legacy_authority import authority_digest
from tempo_game_evidence.snapshot import RouteV2

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def document(kind=None):
    suffix = f"-{kind}" if kind else ""
    return json.loads((FIXTURES / f"legacy-scoped{suffix}-snapshot.json").read_text())


def observed():
    game = normalize_pgn((FIXTURES / "legacy-authority-miss.pgn").read_bytes(), "Learner")
    game.update(workspace_id="legacy", game_id="legacy-game", revision=1)
    # Canonical import identity in the hand-authored delivery fixture.
    game["game_id"] = document()["catalogue_scope"]["scope"]["game_id"]
    return game


def seal(value):
    value["routes"] = [RouteV2.model_validate(route).model_dump(mode="json") for route in value["routes"]]
    value["capture_digest"] = authority_digest({key: item for key, item in value.items() if key != "capture_digest"})
    return value


def test_scoped_original_card_miss_uses_complete_4102_row_manifest_and_preserves_the_prior_policy_anchor():
    snapshot = LegacyScopedComparisonSnapshot.model_validate(document())
    game = observed()
    result = compare_game(game, replay_positions(game), snapshot)
    assert [item["status"] for item in result] == ["unknown", "unknown", "missed"]
    assert result[-1]["card_id"] == "physical-black" and result[-1]["expected_moves"] == ["a7a6"]
    assert snapshot.catalogue_scope.stream("repertoire_lines").visited_rows == 4102
    assert len(snapshot.catalogue) == 1 and snapshot.routes[0].authority_start_ply == 5
    assert "catalogue_complete" not in snapshot.model_dump()
    assert snapshot.routes[0].bindings[-1].exercise_ply == 5


@pytest.mark.parametrize("kind", ["unbound", "foreign"])
def test_scoped_competitors_after_4096_veto_false_physical_misses(kind):
    snapshot = LegacyScopedComparisonSnapshot.model_validate(document(kind))
    game = observed()
    result = compare_game(game, replay_positions(game), snapshot)
    assert "zz-competitor" in snapshot.catalogue_scope.candidate_route_ids
    assert result[-1]["status"] == "unknown" and result[-1]["card_id"] is None


@pytest.mark.parametrize("ended_ply,status", [(4, "missed"), (5, "unknown")])
def test_ended_foreign_route_requires_later_exclusive_policy_anchor(ended_ply, status):
    value = document()
    for ply in range(ended_ply + 1):
        value["catalogue_scope"]["prefix_policies"][ply] = ["italian", "ruy"]
    value["routes"][0]["authority_start_ply"] = 5 if ended_ply == 4 else None
    snapshot = LegacyScopedComparisonSnapshot.model_validate(seal(value))
    game = observed()
    result = compare_game(game, replay_positions(game), snapshot)
    assert result[-1]["status"] == status
    if status == "unknown": assert result[-1]["card_id"] is None


@pytest.mark.parametrize("fault,reason", [("eof", "stream_not_exhausted"), ("identity", "authority_digest"),
    ("candidate", "candidate_inventory"), ("policy", "policy_anchor"), ("global_complete", "Extra inputs"),
    ("integrity", "integrity_status"), ("graph", "authority_digest")])
def test_scoped_snapshot_rejects_forged_shape_incomplete_proof_or_mixed_authority(fault, reason):
    value = document()
    if fault == "eof": value["catalogue_scope"]["streams"][1]["visited_rows"] -= 1
    if fault == "identity": value["catalogue_scope"]["authority_id"] = "legacy-graph:" + "0" * 64
    if fault == "candidate": value["catalogue_scope"]["candidate_route_ids"] = []
    if fault == "policy": value["routes"][0]["authority_start_ply"] = 0
    if fault == "global_complete": value["catalogue_complete"] = True
    if fault == "integrity": value["catalogue_scope"]["integrity_certificate"]["integrity_status"] = "unchecked"
    if fault == "graph": value["catalogue_scope"]["integrity_certificate"]["graph_task_generation"] += 1
    with pytest.raises(ValueError, match=reason): LegacyScopedComparisonSnapshot.model_validate(seal(value))


def test_game_specific_proof_cannot_be_reused_for_a_different_game_or_outside_its_decision_scope():
    snapshot = LegacyScopedComparisonSnapshot.model_validate(document())
    game = observed()
    result = compare_game(game, replay_positions(game), snapshot)
    assert result[0]["explanation"] == result[1]["explanation"] == "legacy_decision_outside_scope"
    game["game_id"] = "other-game"
    with pytest.raises(ValueError, match="outside_decision_scope"):
        compare_game(game, replay_positions(game), snapshot)


def test_matching_move_has_its_own_proof_without_changing_reusable_authority():
    value = document()
    before = deepcopy(value["authority"])
    value["catalogue_scope"]["scope"]["moves"][-1] = "a7a6"
    game = observed()
    game["moves"][-1] = "a7a6"
    result = compare_game(game, replay_positions(game), LegacyScopedComparisonSnapshot.model_validate(seal(value)))
    assert result[-1]["status"] == "matched" and value["authority"] == before


def test_read_only_scope_does_not_offer_a_card_with_unknown_introduction_before_game():
    value = document()
    value["cards"][0]["introduced_before_game"] = False
    game = observed()
    result = compare_game(game, replay_positions(game), LegacyScopedComparisonSnapshot.model_validate(seal(value)))
    assert result[-1]["status"] == "unknown" and result[-1]["card_id"] is None
    assert result[-1]["explanation"] == "legacy_introduction_chronology_unknown"
