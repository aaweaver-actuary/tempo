"""Policy-aware observations. A repertoire deviation is not an engine error."""
from tempo_chess_core import canonical_fen_key
from .source import identity


def compare_game(game, positions, snapshot):
    definitions = {item.card_id: item for item in snapshot.publication.definitions}
    routes = [item for item in snapshot.compiled_routes() if item[0].learner_color == game["observed_color"]]
    active = {(index, 0) for index, (_, keys, _) in enumerate(routes)
              if keys[0] == positions[0]["comparison_key"]}
    findings = []
    for position in positions:
        if position["move"] is None:
            break
        candidates = set(active)
        # FEN overlap alone never authorizes re-entry after divergence.
        for index, (_, keys, bindings) in enumerate(routes):
            for ply, binding in bindings.items():
                if binding.reentry and keys[ply] == position["comparison_key"]:
                    candidates.add((index, ply))
        candidates = {(index, ply) for index, ply in candidates
                      if routes[index][1][ply] == position["comparison_key"]}
        if position["turn"] == game["observed_color"]:
            groups = {}
            for index, route_ply in candidates:
                route, _, bindings = routes[index]
                binding = bindings.get(route_ply)
                if binding is None:
                    continue
                definition = definitions[binding.card_id]
                spec = definition.specification
                if spec.type != "move_line" or spec.grading_policy != "reference":
                    continue
                expected = sorted({line[0] for line in spec.accepted_lines})
                key = (definition.policy_id, definition.decision_id, definition.card_id, tuple(expected))
                groups.setdefault(key, set()).add((route.repertoire_id,route.route_id))
            accepted = [key for key in groups if position["move"] in key[3]]
            selected = accepted[0] if len(accepted) == 1 else next(iter(groups)) if len(groups) == 1 else None
            finding = {**position, "status": "unknown", "expected_moves": [], "memberships": [],
                       "card_id": None, "decision_id": None, "policy_id": None,
                       "explanation": "missing_or_ambiguous_route_policy"}
            if not snapshot.serving_current():
                finding["explanation"] = "publication_acceptance_unknown" if snapshot.activation is None else "publication_not_current"
            elif selected:
                policy_id, decision_id, card_id, expected = selected
                matched = position["move"] in expected
                finding.update(status="matched" if matched else "missed", expected_moves=list(expected),
                               decision_position_key=canonical_fen_key(definitions[card_id].starting_fen),
                               memberships=[{"repertoire_id":repertoire_id,"route_id":route_id} for repertoire_id,route_id in sorted(groups[selected])],
                               card_id=card_id, decision_id=decision_id, policy_id=policy_id,
                               explanation="accepted_repertoire_move" if matched else "defined_repertoire_move_not_played")
            finding["encounter_id"] = identity(game["workspace_id"], game["game_id"], game["player"],
                                                position["ply"], finding["decision_id"])
            findings.append(finding)
        active = {(index, ply + 1) for index, ply in candidates
                  if ply < len(routes[index][0].moves) and routes[index][0].moves[ply] == position["move"]}
    return findings
