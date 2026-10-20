"""Current authored membership, independent of introduction and remediation facts."""
import hashlib
import json

from tempo_game_review_contracts import (
    CanonicalGame, ComparisonReport, PositionFact, RepertoireSnapshot, RouteContext,
)
from tempo_game_review_contracts import replay_line
from .frontier import RouteFrontier, TraversalRoute


class CurrentRepertoireComparer:
    def compare(self, game: CanonicalGame, snapshot: RepertoireSnapshot) -> ComparisonReport:
        return compare(game, snapshot)


def compare(game: CanonicalGame, snapshot: RepertoireSnapshot) -> ComparisonReport:
    """Compare supplied captured values; never query live state or authorize effects."""
    if game.workspace_id != snapshot.workspace_id:
        raise ValueError("game_snapshot_workspace_mismatch")
    scope = set(snapshot.scope_repertoire_ids)
    if len(scope) != len(snapshot.scope_repertoire_ids):
        raise ValueError("duplicate_scope_repertoire")
    identities = [(route.repertoire_id, route.route_id) for route in snapshot.routes]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate_route_identity")
    if any(route.repertoire_id not in scope for route in snapshot.routes):
        raise ValueError("route_outside_snapshot_scope")
    positions = replay_line(game.starting_fen, game.moves)
    routes = [route for route in snapshot.routes if route.learner_color == game.learner_color]
    traversal_routes = []
    for route in routes:
        if any(authority.ply >= len(route.moves) for authority in route.reentries):
            raise ValueError("reentry_outside_route_moves")
        route_positions = replay_line(route.starting_fen, route.moves)
        traversal_routes.append(TraversalRoute(
            tuple(position["comparison_key"] for position in route_positions),
            tuple(route.moves), tuple(authority.ply for authority in route.reentries),
        ))
        if len({authority.ply for authority in route.reentries}) != len(route.reentries):
            raise ValueError("duplicate_reentry_anchor")
    frontier = RouteFrontier(traversal_routes, positions[0]["comparison_key"])
    facts = []
    departed = False
    matched_prefix = 0
    for position in positions[:-1]:
        candidates = frontier.candidates(position["comparison_key"])
        contexts = []
        groups = {}
        ended = False
        for index, route_ply in sorted(candidates):
            route = routes[index]
            authority = next((item.authority_id for item in route.reentries if item.ply == route_ply), None)
            contexts.append(RouteContext(repertoire_id=route.repertoire_id,
                route_id=route.route_id, policy_id=route.policy_id, route_ply=route_ply,
                reentry_authority_id=authority))
            if route_ply == len(route.moves):
                ended = True
            else:
                groups.setdefault((route.repertoire_id, route.policy_id), set()).add(route.moves[route_ply])
        expected = sorted(set().union(*groups.values())) if groups else []
        status, reason = "unknown", "no_applicable_route"
        if not snapshot.available or not snapshot.complete:
            reason = "unavailable_evidence"
        elif not groups:
            reason = "route_ended" if ended else "after_departure" if departed else "no_applicable_route"
        elif len({tuple(sorted(moves)) for moves in groups.values()}) > 1:
            reason = "ambiguous"
        elif position["move"] in expected:
            status, reason = "matched", "authored_move"
        elif ended:
            reason = "route_ended"
        else:
            status, reason = "different", "move_not_authored"
        fact = PositionFact(ply=position["ply"], fen=position["fen"],
            side="learner" if position["turn"] == game.learner_color else "opponent",
            actual_move=position["move"], expected_moves=expected, status=status,
            reason=reason, route_contexts=contexts)
        facts.append(fact)
        if status == "matched" and position["ply"] == matched_prefix:
            matched_prefix += 1
        if status == "different":
            departed = True
        frontier.advance(candidates, position["move"])
    payload = {"algorithm": "authored-route-frontier-v1", "game": game.model_dump(mode="json"),
               "snapshot": snapshot.model_dump(mode="json")}
    report_id = "comparison:" + hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode()).hexdigest()
    limitations = ["Snapshot is the explicitly supplied captured version; live freshness is unverified.",
                   "Authored membership does not establish personal recall, historical eligibility or action authority."]
    if snapshot.unavailable_reason:
        limitations.append(snapshot.unavailable_reason)
    return ComparisonReport(report_id=report_id, workspace_id=game.workspace_id,
        game_id=game.game_id, game_revision=game.game_revision, source_digest=game.source_digest,
        source_id=game.source_id, source_revision=game.source_revision,
        snapshot_id=snapshot.snapshot_id, snapshot_revision=snapshot.revision,
        snapshot_digest=snapshot.digest, snapshot_source_revision=snapshot.source_revision,
        snapshot_captured_at=snapshot.captured_at, scope_id=snapshot.scope_id,
        scope_repertoire_ids=snapshot.scope_repertoire_ids,
        snapshot_complete=snapshot.complete, snapshot_available=snapshot.available,
        matched_prefix_plies=matched_prefix, positions=facts,
        first_difference=next((fact for fact in facts if fact.status == "different"), None),
        limitations=limitations)
