"""Translate an owner-captured legacy scope into a read-only GE observation.

No capture, service calls, acceptance or activation happens here. Callers supply
the backend owner's before/after tokens and complete competing-route catalogue.
The existing translator remains the exact physical/ordered-prefix mapping owner.
"""
import json

from .legacy import fingerprint, translate
from tempo_game_evidence_contracts.legacy import (
    authority_source_projection, authority_source_digest, authority_graph_digest, decision_identity)
from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof


def comparison_snapshot(tables, graphs, *, authority, observed_after, captured_at, catalogue_scope=None):
    repertoire_id = authority["repertoire_id"]
    workspace_id = authority["workspace_id"]
    selected = next((row for row in tables["repertoires"] if row["id"] == repertoire_id), None)
    if selected is None or json.loads(selected.get("canonical_prefix_moves_json", "[]")):
        raise ValueError("legacy_canonical_prefix_adapter_unsupported")
    selected_graphs = [row for row in graphs if row["repertoire_id"] == repertoire_id]
    proof = LegacyScopedCatalogueProof.model_validate(catalogue_scope) if catalogue_scope is not None else None
    if proof is not None:
        from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken
        token = LegacyAuthorityToken.model_validate(authority)
        if (proof.authority_id != token.storage_identity()[0]
                or proof.catalogue_generation != token.catalogue_generation
                or proof.integrity_certificate.graph_task_generation != token.graph_generation
                or any(authority[key] != value for key, value in proof.source_identity_digests().items())):
            raise ValueError("legacy_scoped_authority_digest_mismatch")
    if ((proof is None and (authority_source_digest(tables) != authority["source_digest"]
            or authority_graph_digest(selected_graphs) != authority["graph_digest"]))
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
    if proof is None and fingerprint(catalogue) != authority["catalogue_digest"]:
        raise ValueError("legacy_capture_catalogue_token_mismatch")
    if proof is not None and set(proof.candidate_route_ids) != {row["route_id"] for row in catalogue}:
        raise ValueError("legacy_scoped_candidate_inventory")
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
            "introduced": card.get("introduced_at") is not None,
            **({"introduced_before_game": card["introduced_before_game"]} if proof is not None else {})})
    physical = {card["card_id"]: card for card in cards}
    exercises = {exercise["key"]: exercise for exercise in source["exercises"]}
    routes = []
    for route in source["routes"]:
        if proof is not None:
            route["authority_start_ply"] = proof.authority_anchor(repertoire_id)
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
    document = {"kind": "legacy_authority_snapshot", "schema_version": 2 if proof is not None else 1,
        "captured_at": captured_at, "authority": authority, "observed_after": observed_after,
        **({"catalogue_complete": True} if proof is None else {"catalogue_scope": proof.model_dump(mode="json")}),
        "catalogue": catalogue, "cards": cards, "routes": routes}
    if proof is not None:
        report["policy_authority_anchors"] = {route["route_id"]: route.get("authority_start_ply", 0) for route in routes}
    document["capture_digest"] = fingerprint(document)
    return document, report


def observe_capture(capture):
    """File-only projection of one backend-owned read capture; no service import."""
    from tempo_game_evidence_contracts import v3
    if (capture.get('kind') != 'legacy_game_capture' or type(capture.get('schema_version')) is not int
            or capture['schema_version'] != 1):
        raise ValueError('legacy_game_capture_required')
    if capture.get('export_digest') != fingerprint({key: value for key, value in capture.items() if key != 'export_digest'}):
        raise ValueError('legacy_capture_file_changed')
    snapshot, mapping = comparison_snapshot(**{key: capture[key] for key in
        ('tables', 'graphs', 'authority', 'observed_after', 'captured_at', 'catalogue_scope')})
    raw = capture['game']
    source = v3.LegacyGameSource(provider=raw['provider'], provider_game_id=raw['provider_game_id'],
        content_hash=raw['content_hash'], raw_source_digest=fingerprint(raw))
    game = dict(workspace_id=capture['authority']['workspace_id'], game_id=raw['id'], revision=1,
        starting_fen=raw['start_fen'], moves=json.loads(raw['moves_json']), observed_color=raw['color'],
        player='legacy-import:' + raw['provider'] + ':' + raw['color'], played_at=raw['played_at'],
        source_digest=source.raw_source_digest)
    return dict(kind='legacy_game_observation', schema_version=1, game=game,
                game_source=source.model_dump(mode='json'), snapshot=snapshot, mapping=mapping,
                input_digest=capture['export_digest'])


def main(arguments=None):
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description='Project one retained read-only legacy game capture')
    parser.add_argument('--capture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args(arguments)
    if options.capture.stat().st_size > 8 * 1024 * 1024: raise ValueError('legacy_capture_file_exceeds_bound')
    result = observe_capture(json.loads(options.capture.read_bytes()))
    with options.output.open('x') as output: json.dump(result, output, indent=2)
    print(json.dumps(dict(output=str(options.output), read_only=True)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
