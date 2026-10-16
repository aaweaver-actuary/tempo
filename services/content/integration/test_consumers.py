"""Actual installed service-local consumers, selected only by consumer qualification."""

import importlib.util
import json
import os
from pathlib import Path


HERE = Path(__file__).resolve().parent


def consumer_export():
    from tempo_content.domain import build_publication, stable_id
    from tempo_content.models import SourceSnapshot
    fixture = json.loads((HERE.parent / "fixtures" / "source.json").read_text())
    snapshot = SourceSnapshot.model_validate(fixture)
    publication = build_publication(snapshot, 1, 1, "2026-10-16T12:00:00Z", {item.key: 1 for item in snapshot.exercises})
    large_source = snapshot.model_dump(mode="json")
    prototype = next(item for item in large_source["exercises"] if item["specification"]["type"] == "explanation")
    large_source["bindings"] = []
    large_source["exercises"] = [{**prototype, "key": f"large-{index}", "decision_key": f"large-decision-{index}"} for index in range(129)]
    large = build_publication(SourceSnapshot.model_validate(large_source), 1, 1, "2026-10-16T12:00:00Z", {item["key"]: 1 for item in large_source["exercises"]})
    identify = lambda kind, key: stable_id(snapshot.workspace_id, snapshot.source_key, kind, key)
    routes = {route.key: route for route in snapshot.routes}
    repertoires = {item.key: item for item in snapshot.repertoires}
    exercises = {item.key: item for item in snapshot.exercises}
    comparison_routes = []
    for membership in snapshot.memberships:
        route = routes[membership.route_key]
        bindings = [item for item in snapshot.bindings if item.route_key == route.key and item.policy_key == membership.policy_key
                    and exercises[item.exercise_key].specification.type == "move_line"]
        if bindings:
            comparison_routes.append({
                "repertoire_id": identify("repertoire", membership.repertoire_key),
                "route_id": identify("route", route.key),
                "learner_color": repertoires[membership.repertoire_key].learner_color,
                "starting_fen": route.starting_fen,
                "moves": route.moves,
                "bindings": [{"ply": item.ply, "card_id": identify("card", item.exercise_key), "reentry": False} for item in bindings],
            })
    return {
        "source_id": identify("source", snapshot.source_key),
        "publication": publication.model_dump(mode="json"),
        "large_publication": large.model_dump(mode="json"),
        "snapshot": {"schema_version": 1, "complete": True,
                     "snapshot_id": identify("snapshot", "consumer-fixture"), "revision": 1,
                     "publication": publication.model_dump(mode="json"), "activation": None, "routes": comparison_routes},
    }


def test_installed_learning_and_game_evidence_consume_content_without_activation(tmp_path):
    spec = importlib.util.spec_from_file_location("content_consumer_probe", HERE / "consumer_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    exported = (json.loads(Path(os.environ["TEMPO_CONTENT_CONSUMER_EXPORT"]).read_text())
                if "TEMPO_CONTENT_CONSUMER_EXPORT" in os.environ else consumer_export())
    export_path = tmp_path / "content-consumer-export.json"
    export_path.write_text(json.dumps(exported, sort_keys=True))
    result = module.probe(json.loads(export_path.read_text()))
    assert result["identities_preserved"]
    assert result["learning_study_bundle"] == "compatible"
    assert result["game_evidence_snapshot"] == "compatible"
    assert result["learning_large_bundle"] == "learning_bundle_definition_limit_exceeded"


if __name__ == "__main__":
    print(json.dumps(consumer_export(), sort_keys=True))
