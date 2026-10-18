from copy import deepcopy
import json

import pytest

from tempo_a_bridge.legacy import fingerprint
from tempo_a_bridge.legacy_observation import comparison_snapshot
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
        scope_source_revision=0, canonical_prefix_revision=0, source_digest=fingerprint(tables),
        catalogue_digest=fingerprint(catalogue), graph_generation=7, graph_digest=fingerprint(graph), graph_state="ready")
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
            arguments["authority"]["graph_digest"] = fingerprint(arguments["graphs"])
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
    arguments["authority"].update(source_digest=fingerprint(arguments["tables"]),
                                   graph_digest=fingerprint(arguments["graphs"]))
    arguments["observed_after"] = deepcopy(arguments["authority"])
    document, report = comparison_snapshot(**arguments)
    assert document["cards"] == [] and document["routes"][0]["bindings"] == []
    assert report["mapped_identities"] == [] and report["diagnostics"]
