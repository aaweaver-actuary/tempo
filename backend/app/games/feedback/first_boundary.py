"""One pure action boundary across the complete configured authored scope.

The richer per-repertoire comparison remains diagnostic. This policy traverses
the actual prefix once, never resumes after the first boundary, and never uses
introduction chronology to decide authored membership or a physical target.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from tempo_game_classification import resolve_equivalent_mapping
from tempo_game_review_contracts import CanonicalGame, replay_line
from tempo_repertoire_comparison import RouteFrontier, TraversalRoute

from app.contracts.product_v02.games import (
    FirstDepartureRoute, FirstDepartureScope, FirstDepartureSelection,
)
from app.contracts.product_v02.repertoire import IntroductionGameScope, RepertoireSnapshot

__all__ = ["select_first_boundary"]


def _current_mapping(mappings):
    """Reuse owner equivalence on detached current facts; retain an original.

    Historical introduction chronology does not establish physical identity.
    Current introduced state, availability, revision and content facts still
    have to agree. No normalized copy becomes the emitted observation.
    """
    originals = {}
    for mapping in mappings:
        current = mapping.model_copy(update={"introduced_at": None,
            "introduced_before_game": None, "introduction_game": None})
        originals.setdefault(current.model_dump_json(), []).append(mapping)
    values = [items[0].model_copy(update={"introduced_at": None,
        "introduced_before_game": None, "introduction_game": None}) for items in originals.values()]
    chosen = resolve_equivalent_mapping(values)
    return None if chosen is None else min(originals[chosen.model_dump_json()], key=lambda item: item.model_dump_json())


def _snapshot_key(snapshot: RepertoireSnapshot) -> str:
    # Capture clocks do not change the authored authority or route identity.
    value = snapshot.model_dump(mode="json", exclude={"membership": {"captured_at"}})
    for owner, field in ((value["membership"], "routes"), (value, "boundaries"), (value, "decision_presentations")):
        owner[field].sort(key=lambda item: json.dumps(item, sort_keys=True))
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def select_first_boundary(game: CanonicalGame, *, snapshots: Iterable[RepertoireSnapshot],
                          source: IntroductionGameScope,
                          selected_repertoire_ids: Iterable[str] | None = None,
                          unavailable_repertoire_ids: Iterable[str] = ()) -> FirstDepartureSelection:
    """Select supplied facts only; the Study owner still revalidates authority.

    Pass the original configured selection and every failed read explicitly.
    Known opposite-colour snapshots remain scope evidence, without joining the
    frontier. Unknown failed selections cannot be assumed opposite-colour.
    """
    game = CanonicalGame.model_validate(game.model_dump(mode="python"))
    source = IntroductionGameScope.model_validate(source.model_dump(mode="python"))
    if (source.game_id, source.source_revision, source.source_digest) != (
            game.game_id, game.source_revision, game.source_digest):
        raise ValueError("first_boundary_source_mismatch")
    positions = replay_line(game.starting_fen, game.moves)
    supplied = {}
    for item in snapshots:
        snapshot = RepertoireSnapshot.model_validate(item.model_dump(mode="python"))
        if snapshot.membership.workspace_id != game.workspace_id:
            raise ValueError("first_boundary_workspace_mismatch")
        supplied[_snapshot_key(snapshot)] = snapshot
    snapshots = tuple(supplied[key] for key in sorted(supplied))
    unavailable = set(unavailable_repertoire_ids)
    selected = (set(selected_repertoire_ids) if selected_repertoire_ids is not None else
                {repertoire for snapshot in snapshots for repertoire in snapshot.membership.scope_repertoire_ids})
    selected.update(unavailable)
    scope = []
    contradictory = False
    for repertoire_id in sorted(selected):
        values = [snapshot for snapshot in snapshots if repertoire_id in snapshot.membership.scope_repertoire_ids]
        if values and all(item.learner_color != game.learner_color for item in values) and repertoire_id not in unavailable:
            continue
        facts = {FirstDepartureScope(repertoire_id=repertoire_id, learner_color=item.learner_color,
            snapshot_id=item.membership.snapshot_id, source_revision=item.membership.source_revision,
            revision=item.membership.revision, digest=item.membership.digest,
            complete=item.membership.complete and item.membership.available,
            available=item.membership.available) for item in values}
        versions = set()
        for item in values:
            route_ids = {route.route_id for route in item.membership.routes if route.repertoire_id == repertoire_id}
            payload = dict(
                routes=[route.model_dump(mode="json") for route in item.membership.routes if route.repertoire_id == repertoire_id],
                boundaries=[boundary.model_dump(mode="json") for boundary in item.boundaries if boundary.route_id in route_ids],
                mappings=[mapping.model_dump(mode="json") for mapping in item.decision_presentations if mapping.repertoire_id == repertoire_id])
            for values_for_field in payload.values():
                values_for_field.sort(key=lambda value: json.dumps(value, sort_keys=True))
            versions.add(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        if repertoire_id in unavailable or len(facts) != 1 or len(versions) > 1:
            colours = {item.learner_color for item in values}
            scope.append(FirstDepartureScope(repertoire_id=repertoire_id,
                learner_color=game.learner_color if colours == {game.learner_color} else None,
                snapshot_id=None, source_revision=None, revision=None, digest=None,
                complete=False, available=False))
            contradictory = contradictory or len(facts) > 1 or len(versions) > 1
        else:
            scope.append(next(iter(facts)))
    scope = tuple(scope)
    relevant = [item for item in scope if item.learner_color in {None, game.learner_color}]
    complete = bool(relevant) and all(item.complete and item.available for item in relevant)
    base = dict(workspace_id=game.workspace_id, source=source, learner_color=game.learner_color,
                selected_scope=scope)

    def result(status, *, position=None, expected=(), decision=None, routes=(), diagnostic=None):
        return FirstDepartureSelection(**base, status=status,
            ply=None if position is None else position["ply"] + 1,
            actor=None if position is None else ("learner" if position["turn"] == game.learner_color else "opponent"),
            position_key=None if position is None else position["comparison_key"],
            played_uci=None if position is None else position["move"], expected_uci=tuple(expected),
            decision=decision, surviving_routes=tuple(routes), diagnostic=diagnostic)

    if not scope:
        return result("unresolved", diagnostic="no_compatible_repertoire")
    if any(snapshot.variant != "standard" for snapshot in snapshots
           if snapshot.learner_color == game.learner_color):
        return result("unresolved", diagnostic="unsupported_selected_variant")
    routes, traversals, seen = [], [], set()
    try:
        for snapshot in snapshots:
            if snapshot.learner_color != game.learner_color:
                continue
            boundaries = {item.route_id: item for item in snapshot.boundaries}
            for route in sorted(snapshot.membership.routes, key=lambda item: (item.repertoire_id, item.route_id)):
                if route.repertoire_id not in selected or route.learner_color != game.learner_color:
                    continue
                if route.repertoire_id not in snapshot.membership.scope_repertoire_ids:
                    return result("unresolved", diagnostic="route_outside_selected_snapshot_scope")
                identity = (route.repertoire_id, route.route_id, snapshot.membership.source_revision,
                            route.model_dump_json())
                if identity in seen:
                    continue
                seen.add(identity)
                frames = replay_line(route.starting_fen, route.moves)
                routes.append((snapshot, route, boundaries[route.route_id], frames))
                traversals.append(TraversalRoute(tuple(frame["comparison_key"] for frame in frames), tuple(route.moves)))
    except (ValueError, KeyError):
        return result("unresolved", diagnostic="invalid_authored_scope")
    frontier = RouteFrontier(traversals, positions[0]["comparison_key"])
    # An intentionally ended alternative remains an authority limit when a
    # longer competing route happens to cover more of the actual game.
    ended = set()

    def route_facts(candidates):
        facts = []
        for index, ply in candidates:
            snapshot, route, boundary, _ = routes[index]
            if not any(item.repertoire_id == route.repertoire_id and
                       item.source_revision == snapshot.membership.source_revision for item in scope):
                continue
            facts.append(FirstDepartureRoute(repertoire_id=route.repertoire_id,
                route_id=route.route_id, route_ply=ply, policy_id=route.policy_id,
                source_revision=snapshot.membership.source_revision,
                at_deliberate_end=ply == len(route.moves) and boundary.deliberate))
        return tuple(sorted(set(facts), key=lambda item: (
            item.repertoire_id, item.route_id, item.route_ply, item.source_revision)))

    for position in positions[:-1]:
        candidates = frontier.candidates(position["comparison_key"])
        terminal = {(index, ply) for index, ply in candidates if ply == len(routes[index][1].moves)}
        supported = {(index, ply) for index, ply in candidates
                     if ply < len(routes[index][1].moves) and routes[index][1].moves[ply] == position["move"]}
        if supported:
            ended.update(terminal)
            frontier.advance(supported, position["move"])
            continue
        expected = tuple(sorted({routes[index][1].moves[ply] for index, ply in candidates
                                 if ply < len(routes[index][1].moves)}))
        facts = route_facts(candidates | ended)
        if not complete:
            return result("unresolved", position=position, expected=expected, routes=facts,
                          diagnostic="selected_scope_conflicting" if contradictory else "selected_scope_incomplete_or_unavailable")
        if any(routes[index][2].deliberate for index, _ in terminal | ended):
            if not expected and facts and all(item.at_deliberate_end for item in facts):
                return result("coverage_end", position=position, routes=facts,
                              diagnostic="deliberate_authored_endpoint")
            return result("unresolved", position=position, expected=expected, routes=facts,
                          diagnostic="ended_competing_route")
        if ended or terminal and expected:
            return result("unresolved", position=position, expected=expected, routes=facts,
                          diagnostic="authored_continuation_unavailable")
        if not candidates:
            return result("unresolved", position=position, routes=facts, diagnostic="no_applicable_authored_route")
        if position["turn"] != game.learner_color:
            return result("opponent_gap", position=position, expected=expected, routes=facts,
                          diagnostic="opponent_first_departure")
        if not expected:
            return result("missing_own_continuation", position=position, routes=facts,
                          diagnostic="missing_authored_own_continuation")
        if len(expected) != 1:
            return result("own_unmapped", position=position, expected=expected, routes=facts,
                          diagnostic="ambiguous_expected_response")
        policies = {}
        for index, _ in candidates:
            route = routes[index][1]
            policies.setdefault(route.repertoire_id, set()).add(route.policy_id)
        if any(len(values) > 1 for values in policies.values()):
            return result("own_unmapped", position=position, expected=expected, routes=facts,
                          diagnostic="ambiguous_authored_policy")
        # Preserve P02's own-response policy conflict at this exact position,
        # including another authored occurrence in the same repertoire.
        active_repertoires = {routes[index][1].repertoire_id for index, _ in candidates}
        own_responses = {}
        for _, route, _, frames in routes:
            if route.repertoire_id not in active_repertoires:
                continue
            for frame in frames[:-1]:
                if frame["comparison_key"] == position["comparison_key"] and frame["turn"] == game.learner_color:
                    own_responses.setdefault(route.repertoire_id, set()).add(frame["move"])
        if any(len(values) > 1 for values in own_responses.values()):
            return result("own_unmapped", position=position, expected=expected, routes=facts,
                          diagnostic="conflicting_authored_own_response")
        mappings = {}
        missing_occurrence = False
        for index, ply in candidates:
            snapshot, route, _, _ = routes[index]
            applicable = [item for item in snapshot.decision_presentations
                if (item.repertoire_id, item.repertoire_revision, item.position_key) == (
                    route.repertoire_id, snapshot.membership.revision, position["comparison_key"])
                and (item.route_id is None or (item.route_id, item.route_ply) == (route.route_id, ply))]
            missing_occurrence = missing_occurrence or not applicable
            for item in applicable:
                mappings[item.model_dump_json()] = item
        decision = _current_mapping(mappings.values())
        if missing_occurrence or decision is None or decision.selected_response_uci != expected[0]:
            return result("own_unmapped", position=position, expected=expected, routes=facts,
                          diagnostic="missing_or_ambiguous_physical_target")
        if not decision.available:
            return result("own_unmapped", position=position, expected=expected, routes=facts,
                          diagnostic="current_presentation_unavailable")
        if decision.route_id is None:
            if len(candidates) != 1:
                return result("own_unmapped", position=position, expected=expected, routes=facts,
                              diagnostic="physical_target_occurrence_unbound")
            index, ply = next(iter(candidates))
            decision = decision.model_copy(update={"route_id": routes[index][1].route_id, "route_ply": ply})
        return result("own_actionable", position=position, expected=expected, decision=decision, routes=facts)
    return result("covered", diagnostic=None if complete else "positive_coverage_with_incomplete_scope")
