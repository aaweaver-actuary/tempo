"""Policy-aware observations. A repertoire deviation is not an engine error."""
from tempo_chess_core import canonical_fen_key
from .source import identity
from .snapshot import projected_definition


def compare_game(game, positions, snapshot):
    # Both accepted Content publications and observed legacy graphs normalize
    # here. Neither adapter can replace route matching or decision projection.
    definitions = snapshot.comparison_definitions()
    context = snapshot.finding_context(game)
    routes = [item for item in snapshot.compiled_routes() if item[0].learner_color == game["observed_color"]]
    active = {(index, 0) for index, (_, keys, _) in enumerate(routes)
              if keys[0] == positions[0]["comparison_key"]}
    findings = []
    projections={}
    decision_positions={}
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
                if route.authority_start_ply is None or route_ply < route.authority_start_ply:
                    continue
                binding = bindings.get(route_ply)
                if binding is None:
                    continue
                definition = definitions[binding.card_id]
                spec = definition['specification']
                if spec['type'] != "move_line" or spec['grading_policy'] != "reference":
                    continue
                projection_key=(binding.card_id,binding.exercise_ply,binding.decision_id)
                if projection_key not in projections:
                    projections[projection_key]=projected_definition(definition,binding.model_dump(mode='json'))
                projected=projections[projection_key]
                expected = sorted({line[0] for line in projected['specification']['accepted_lines']})
                key = (definition['policy_id'], projected['decision_id'], definition['card_id'], tuple(expected))
                decision_positions[key]=projected['starting_fen']
                groups.setdefault(key, set()).add((route.repertoire_id,route.route_id))
            accepted = [key for key in groups if position["move"] in key[3]]
            selected = accepted[0] if len(accepted) == 1 else next(iter(groups)) if len(groups) == 1 else None
            finding = {**position, "status": "unknown", "expected_moves": [], "memberships": [],
                       "card_id": None, "decision_id": None, "policy_id": None,
                       "explanation": "missing_or_ambiguous_route_policy", **context}
            route_context = snapshot.finding_route_context(tuple(
                (routes[index][0], ply) for index, ply in sorted(candidates)))
            finding.update(route_context)
            if reason := snapshot.applicability_reason():
                finding["explanation"] = reason
            elif selected and (reason := snapshot.definition_unavailable_reason(selected[2])):
                finding["explanation"] = reason
            elif selected and (reason := snapshot.physical_comparison_reason(selected, position["move"], route_context)):
                finding["explanation"] = reason
            elif selected:
                policy_id, decision_id, card_id, expected = selected
                matched = position["move"] in expected
                finding.update(status="matched" if matched else "missed", expected_moves=list(expected),
                               decision_position_key=canonical_fen_key(decision_positions[selected]),
                               memberships=[{"repertoire_id":repertoire_id,"route_id":route_id} for repertoire_id,route_id in sorted(groups[selected])],
                               card_id=card_id, decision_id=decision_id, policy_id=policy_id,
                               explanation="accepted_repertoire_move" if matched else "defined_repertoire_move_not_played")
                finding.update(snapshot.finding_authority(card_id))
            finding["encounter_id"] = identity(game["workspace_id"], game["game_id"], game["player"],
                                                position["ply"], finding["decision_id"])
            findings.append(finding)
        active = {(index, ply + 1) for index, ply in candidates
                  if ply < len(routes[index][0].moves) and routes[index][0].moves[ply] == position["move"]}
    return findings
