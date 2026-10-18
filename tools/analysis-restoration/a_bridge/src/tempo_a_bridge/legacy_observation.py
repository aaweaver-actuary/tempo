"""Translate an owner-captured legacy scope into a read-only GE observation.

No capture, service calls, acceptance or activation happens here. Callers supply
the backend owner's before/after tokens and complete competing-route catalogue.
The existing translator remains the exact physical/ordered-prefix mapping owner.
"""
import json

from .legacy import fingerprint, translate


def comparison_snapshot(tables, graphs, *, authority, observed_after, captured_at):
    repertoire_id = authority["repertoire_id"]
    workspace_id = authority["workspace_id"]
    selected_graphs = [row for row in graphs if row["repertoire_id"] == repertoire_id]
    if (fingerprint(tables) != authority["source_digest"]
            or fingerprint(selected_graphs) != authority["graph_digest"]
            or any(row["generation"] != authority["graph_generation"] for row in selected_graphs)):
        raise ValueError("legacy_capture_source_or_graph_token_mismatch")
    source_bytes, manifest, report = translate(tables, graphs, workspace=workspace_id,
        repertoire_ids=[repertoire_id], source_key="legacy-observation",
        preserve_physical_execution=True)
    source = json.loads(source_bytes)
    catalogue = [{"repertoire_id": row["repertoire_id"], "route_id": row["id"],
        "policy_id": row["repertoire_id"], "learner_color": row["trained_color"],
        "starting_fen": row["start_fen"], "moves": json.loads(row["moves_json"])}
        for row in sorted(tables["repertoire_lines"], key=lambda row: row["id"])]
    if fingerprint(catalogue) != authority["catalogue_digest"]:
        raise ValueError("legacy_capture_catalogue_token_mismatch")
    mapped = set(report["mapped_identities"])
    raw_cards = {row["id"]: row for row in tables["cards"]}
    cards = []
    for card_id in sorted(mapped):
        card = raw_cards[card_id]
        cards.append({"card_id": card_id, "card_revision": card["revision"],
            "decision_id": decision_identity(card_id, 0, []), "policy_id": repertoire_id,
            "trained_color": card["trained_color"], "starting_fen": card["start_fen"],
            "moves": json.loads(card["moves_json"]),
            "repertoire_ids": sorted(row["repertoire_id"] for row in tables["repertoire_cards"]
                                     if row["card_id"] == card_id),
            "introduced": card.get("introduced_at") is not None})
    physical = {card["card_id"]: card for card in cards}
    exercises = {exercise["key"]: exercise for exercise in source["exercises"]}
    routes = []
    for route in source["routes"]:
        bindings = []
        for binding in source["bindings"]:
            card_id = binding["exercise_key"]
            if binding["route_key"] != route["key"] or card_id not in mapped:
                continue
            offset = binding.get("exercise_ply", 0) + len(
                exercises[card_id].get("execution_prelude", {}).get("moves", []))
            projected = {"ply": binding["ply"], "card_id": card_id, "reentry": False}
            if offset:
                projected.update(exercise_ply=offset, decision_id=decision_identity(
                    card_id, offset, physical[card_id]["moves"][:offset]))
            bindings.append(projected)
        result = {"repertoire_id": repertoire_id, "route_id": route["key"],
            "policy_id": repertoire_id, "learner_color": next(
                row["learner_color"] for row in source["repertoires"] if row["key"] == repertoire_id),
            "starting_fen": route["starting_fen"], "moves": route["moves"], "bindings": bindings}
        if route["authority_start_ply"] != 0:
            result["authority_start_ply"] = route["authority_start_ply"]
        routes.append(result)
    document = {"kind": "legacy_authority_snapshot", "schema_version": 1,
        "captured_at": captured_at, "authority": authority, "observed_after": observed_after,
        "catalogue_complete": True, "catalogue": catalogue, "cards": cards, "routes": routes}
    document["capture_digest"] = fingerprint(document)
    return document, report


def decision_identity(card_id, offset, prefix):
    return "legacy-decision:" + fingerprint([card_id, offset, prefix])
