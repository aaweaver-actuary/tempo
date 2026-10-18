from copy import deepcopy
import json

import pytest

from tempo_a_bridge.legacy import fingerprint
from tempo_a_bridge.legacy_observation import comparison_snapshot, authority_source_digest, authority_graph_digest
from test_legacy import captured, graphs


def inputs():
    tables = captured()
    tables["cards"][0]["introduced_at"] = "2026-10-01"
    graph = graphs()
    graph[0]["generation"] = 7
    catalogue = [{"repertoire_id": "italian", "route_id": "line", "policy_id": "italian",
        "learner_color": "white", "starting_fen": tables["repertoire_lines"][0]["start_fen"],
        "moves": json.loads(tables["repertoire_lines"][0]["moves_json"])}]
    token = dict(kind="legacy_graph", target_id="synthetic", workspace_id="test", repertoire_id="italian",
        scope_source_revision=0, canonical_prefix_revision=0, source_digest=authority_source_digest(tables),
        catalogue_digest=fingerprint(catalogue), catalogue_generation=3,
        graph_generation=7, graph_digest=authority_graph_digest(graph), graph_state="ready")
    return dict(tables=tables, graphs=graph, authority=token, observed_after=deepcopy(token),
                captured_at="2026-10-18T18:13:07Z")


def test_observed_legacy_adapter_reuses_mapping_without_content_or_activation():
    arguments = inputs()
    original = deepcopy(arguments)
    document, report = comparison_snapshot(**arguments)
    assert arguments == original
    assert report["mapped_identities"] == ["physical"]
    assert document["cards"][0]["card_id"] == "physical"
    assert document["cards"][0]["moves"] == ["e2e4", "e7e5", "g1f3"]
    assert document["cards"][0]["introduced"] is True
    assert document["routes"][0]["bindings"][-1]["exercise_ply"] == 2
    assert "publication" not in document and "activation" not in document
    assert comparison_snapshot(**arguments) == (document, report)


@pytest.mark.parametrize("fault", ["source", "graph", "generation", "catalogue"])
def test_observed_legacy_adapter_rejects_mixed_owner_capture(fault):
    arguments = inputs()
    if fault == "source":
        arguments["tables"]["cards"][0]["revision"] = 2
    elif fault in {"graph", "generation"}:
        arguments["graphs"][0]["generation"] = 8
        if fault == "generation":
            arguments["authority"]["graph_digest"] = authority_graph_digest(arguments["graphs"])
    else:
        arguments["authority"]["catalogue_digest"] = "0" * 64
    with pytest.raises(ValueError, match="token_mismatch"):
        comparison_snapshot(**arguments)


@pytest.mark.parametrize("unsupported", ["ambiguous_physical_overlap", "higher_revision"])
def test_observed_legacy_adapter_leaves_unsupported_physical_authority_unbound(unsupported):
    arguments = inputs()
    if unsupported == "ambiguous_physical_overlap":
        duplicate = {**arguments["tables"]["cards"][0], "id": "second-physical"}
        arguments["tables"]["cards"].append(duplicate)
        arguments["tables"]["repertoire_cards"].append(
            {"repertoire_id": "italian", "card_id": duplicate["id"]})
        arguments["graphs"].append({**arguments["graphs"][0], "card_id": duplicate["id"]})
    else:
        arguments["tables"]["cards"][0]["revision"] = 2
    arguments["authority"].update(source_digest=authority_source_digest(arguments["tables"]),
                                   graph_digest=authority_graph_digest(arguments["graphs"]))
    arguments["observed_after"] = deepcopy(arguments["authority"])
    document, report = comparison_snapshot(**arguments)
    assert document["cards"] == [] and document["routes"][0]["bindings"] == []
    assert report["mapped_identities"] == [] and report["diagnostics"]


@pytest.mark.parametrize("mapping", ["missing", "unsupported_revision"])
def test_unmapped_same_policy_alternative_stays_in_complete_authored_inventory(mapping):
    arguments = inputs()
    alternative = {**arguments["tables"]["repertoire_lines"][0], "id": "alternative",
                   "moves_json": '["e2e4","e7e5","b1c3"]'}
    arguments["tables"]["repertoire_lines"].append(alternative)
    if mapping == "unsupported_revision":
        card = {**arguments["tables"]["cards"][0], "id": "unsupported-card", "revision": 2,
                "moves_json": alternative["moves_json"]}
        arguments["tables"]["cards"].append(card)
        arguments["tables"]["repertoire_cards"].append({"repertoire_id": "italian", "card_id": card["id"]})
        arguments["graphs"].append({**arguments["graphs"][0], "line_id": "alternative",
                                    "card_id": card["id"], "moves_json": card["moves_json"]})
    catalogue = [{"repertoire_id": row["repertoire_id"], "route_id": row["id"],
        "policy_id": row["repertoire_id"], "learner_color": row["trained_color"],
        "starting_fen": row["start_fen"], "moves": json.loads(row["moves_json"])}
        for row in sorted(arguments["tables"]["repertoire_lines"], key=lambda row: row["id"])]
    arguments["authority"].update(source_digest=authority_source_digest(arguments["tables"]),
        graph_digest=authority_graph_digest(arguments["graphs"]), catalogue_digest=fingerprint(catalogue))
    arguments["observed_after"] = deepcopy(arguments["authority"])
    document, report = comparison_snapshot(**arguments)
    assert [card["card_id"] for card in document["cards"]] == ["physical"]
    route = next(route for route in document["routes"] if route["route_id"] == "alternative")
    assert route["bindings"] == [] and route["moves"][-1] == "b1c3"
    assert document["catalogue"] == catalogue
    if mapping == "unsupported_revision":
        assert any(item.get("reason") == "historical_revision_requires_explicit_successor" for item in report["diagnostics"])


def test_authority_source_identity_survives_ordinary_study_and_harmless_recapture():
    arguments = inputs()
    before = authority_source_digest(arguments["tables"])
    tables = deepcopy(arguments["tables"])
    tables["cards"][0].update(due_date="2026-10-20", interval_days=99, stability=50,
        fsrs_card_json='{"changed":true}', state="mature", recent_attempts_json='["correct"]',
        first_correct_at="2026-10-18", introduced_at="2026-10-02")
    tables["reviews"] = [{"id": 900, "rating": "correct"}]
    tables["daily_queue"] = [{"id": 999, "status": "complete"}]
    tables["repertoires"][0]["name"] = "Renamed"
    tables["repertoire_lines"][0]["moves_json"] = '[ "e2e4", "e7e5", "g1f3" ]'
    assert authority_source_digest(tables) == before
    arguments["tables"] = tables
    snapshot, _ = comparison_snapshot(**arguments)
    assert snapshot["authority"]["source_digest"] == before


@pytest.mark.parametrize("field,value", [("revision", 2), ("moves_json", '["d2d4"]'),
    ("trained_color", "black"), ("archived", 1), ("pending_validation", 1),
    ("superseded_by", "successor")])
def test_authority_source_identity_still_fences_true_content_or_eligibility_changes(field, value):
    arguments = inputs()
    before = authority_source_digest(arguments["tables"])
    arguments["tables"]["cards"][0][field] = value
    assert authority_source_digest(arguments["tables"]) != before


def test_ordinary_introduction_changes_eligibility_without_changing_source_identity():
    arguments = inputs()
    before = authority_source_digest(arguments["tables"])
    arguments["tables"]["cards"][0]["introduced_at"] = None
    assert authority_source_digest(arguments["tables"]) == before
    snapshot, _ = comparison_snapshot(**arguments)
    assert snapshot["cards"][0]["introduced"] is False


def test_nonempty_canonical_prefix_is_an_explicit_unsupported_boundary():
    arguments = inputs()
    arguments["tables"]["repertoires"][0]["canonical_prefix_moves_json"] = '["e2e4"]'
    with pytest.raises(ValueError, match="canonical_prefix_adapter_unsupported"):
        comparison_snapshot(**arguments)
