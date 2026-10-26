"""Transport-free product adapter over pure ingestion/traversal/classification.

The caller supplies the captured snapshot and its matching immutable index.
This module acquires nothing and submits no learning command.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tempo_game_classification import classify_response
from tempo_game_ingestion import BytesInput, PgnGameIngester
from tempo_game_review_contracts import CanonicalGame, replay_line
from tempo_repertoire_comparison import RouteFrontier, TraversalRoute

from app.contracts.product_v02.games import Finding, GameRecord
from app.contracts.product_v02.learning import GameEffectKey
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot

__all__ = ["ReentryLookup", "normalize_record", "compare_record", "effect_key"]


class _ReentryOccurrence(Protocol):
    route_id: str
    ply: int
    source_revision: str
    route_complete: bool
    binding: DecisionPresentation | None
    reentry_authority_id: str | None


class ReentryLookup(Protocol):
    """Structural read seam implemented by #483's immutable PositionIndex.

    No concrete sibling implementation is imported. Composition supplies the
    index built from this snapshot; tests may supply a fake with the same seam.
    """
    snapshot_id: str
    source_revision: str
    revision: int
    digest: str

    def reentry_candidate(self, repertoire_id: str, fen: str,
                          variant: str = "standard") -> _ReentryOccurrence | None: ...


def normalize_record(record: GameRecord, *, provider: str, account: str,
                     workspace_id: str, game_id: str | None = None) -> CanonicalGame:
    """Reuse the strict ingester; the original PGN/time precision stay in record."""
    if provider not in {"lichess", "chesscom"}:
        raise ValueError("unsupported_provider")
    game = PgnGameIngester().ingest(
        BytesInput(record.pgn.encode("utf-8")), workspace_id=workspace_id,
        game_id=record.source_game_id if game_id is None else game_id,
        game_revision=record.source_revision, source_id=provider,
        source_revision=record.source_revision, player=account,
        played_at=record.played_at,
    )
    if game.source_digest != record.source_digest:
        raise ValueError("source_digest_does_not_match_supplied_pgn")
    # PGN UTC headers may contain a more precise time. The provider's explicit
    # precision remains authoritative; do not upgrade unknown to historical proof.
    return game.model_copy(update={"played_at": record.played_at}, deep=True)


def effect_key(finding: Finding, *, workspace_id: str) -> GameEffectKey:
    """Stable logical identity also usable for a mapped reclassified finding."""
    if finding.decision_id is None:
        raise ValueError("decision_identity_required_for_effect_key")
    return GameEffectKey(workspace_id=workspace_id, provider=finding.provider,
                         game_id=finding.game_id, decision_id=finding.decision_id,
                         occurrence_ply=finding.ply)


def compare_record(record: GameRecord, *, provider: str, account: str,
                   workspace_id: str, snapshot: RepertoireSnapshot,
                   reentry_lookup: ReentryLookup | None = None,
                   game_id: str | None = None) -> tuple[Finding, ...]:
    """Normalize one supplied provider record and classify against captured facts.

    After a departure only an exact authored anchor validated by the supplied
    index can resume traversal. A later reentry never rewrites earlier findings.
    """
    game = normalize_record(record, provider=provider, account=account,
                            workspace_id=workspace_id, game_id=game_id)
    # Existing membership models contain caller-mutable lists despite frozen
    # fields. Revalidate/copy the supplied capture before traversing it.
    snapshot = RepertoireSnapshot.model_validate(snapshot.model_dump(mode="python"))
    membership = snapshot.membership
    if membership.workspace_id != workspace_id:
        raise ValueError("game_snapshot_workspace_mismatch")
    if snapshot.variant != "standard":
        raise ValueError("unsupported_variant")
    if snapshot.learner_color != game.learner_color:
        raise ValueError("game_snapshot_learner_color_mismatch")
    scope = tuple(membership.scope_repertoire_ids)
    if len(scope) != len(set(scope)):
        raise ValueError("duplicate_scope_repertoire")
    routes = [route for route in membership.routes if route.learner_color == game.learner_color]
    if any(route.repertoire_id not in scope for route in routes):
        raise ValueError("route_outside_snapshot_scope")
    if len({(route.repertoire_id, route.route_id) for route in routes}) != len(routes):
        raise ValueError("duplicate_route_identity")
    route_positions = [replay_line(route.starting_fen, route.moves) for route in routes]
    own_responses_by_position = {}
    for route, frames in zip(routes, route_positions):
        for ply, frame in enumerate(frames[:-1]):
            if frame["turn"] == game.learner_color:
                own_responses_by_position.setdefault((route.repertoire_id, frame["comparison_key"]), set()).add(route.moves[ply])
    # No unconditional FEN-based anchors: only the supplied index may validate
    # one for the current game position, route and current physical mapping.
    traversal = [TraversalRoute(tuple(p["comparison_key"] for p in positions), tuple(route.moves))
                 for route, positions in zip(routes, route_positions)]
    positions = replay_line(game.starting_fen, game.moves)
    frontier = RouteFrontier(traversal, positions[0]["comparison_key"])
    boundaries = {item.route_id: item for item in snapshot.boundaries}
    findings = []
    departed = False
    for position in positions[:-1]:
        key = position["comparison_key"]
        authority = None
        candidates = frontier.candidates(key)
        if (departed and not candidates and reentry_lookup is not None
                and reentry_lookup.snapshot_id == membership.snapshot_id
                and reentry_lookup.source_revision == membership.source_revision
                and reentry_lookup.revision == membership.revision
                and reentry_lookup.digest == membership.digest):
            validated = []
            for repertoire_id in scope:
                candidate = reentry_lookup.reentry_candidate(repertoire_id, position["fen"], snapshot.variant)
                if candidate is None:
                    continue
                for index, route in enumerate(routes):
                    if (route.repertoire_id != repertoire_id or route.route_id != candidate.route_id
                            or candidate.source_revision != membership.source_revision
                            or not candidate.route_complete or not 0 <= candidate.ply < len(route.moves)
                            or traversal[index].position_keys[candidate.ply] != key):
                        continue
                    binding = candidate.binding
                    if binding is None:
                        continue
                    anchors = [item for item in route.reentries if item.ply == candidate.ply
                               and item.authority_id == candidate.reentry_authority_id]
                    mappings = [item for item in snapshot.decision_presentations
                                if item.repertoire_id == repertoire_id and item.position_key == key
                                and item.decision_id == binding.decision_id
                                and item.presentation_id == binding.presentation_id
                                and item.presentation_revision == binding.presentation_revision
                                and item.selected_response_uci == route.moves[candidate.ply]]
                    if len(anchors) == 1 and len(mappings) == 1:
                        validated.append((index, candidate.ply, anchors[0].authority_id))
            if len(validated) == 1:
                index, ply, authority = validated[0]
                candidates = {(index, ply)}
        if not candidates:
            if not departed:
                for repertoire_id in sorted(scope):
                    findings.append(_finding(game, provider, membership, position, repertoire_id, (),
                                             "known_unavailable", "no_applicable_authored_route", None, None))
            departed = True
            continue
        repertoires = {routes[index].repertoire_id for index, _ in candidates}
        if len(repertoires) > 1:
            for repertoire_id in sorted(repertoires):
                findings.append(_finding(game, provider, membership, position, repertoire_id, (),
                                         "ambiguous", "multiple_applicable_repertoires", None, None))
            frontier.advance((), position["move"])
            departed = True
            continue
        repertoire_id = sorted(repertoires)[0]
        learner_turn = position["turn"] == game.learner_color
        # P02 applies across every occurrence in this repertoire, including a
        # different authored route with the same exact transposed position.
        own_responses = own_responses_by_position.get((repertoire_id, key), set()) if any(
            ply < len(routes[index].moves) for index, ply in candidates) else set()
        expected = tuple(sorted(own_responses if learner_turn else {
            routes[index].moves[ply] for index, ply in candidates if ply < len(routes[index].moves)}))
        terminal = any(ply == len(routes[index].moves) and boundaries[routes[index].route_id].deliberate
                       for index, ply in candidates)
        mappings = tuple({item for item in snapshot.decision_presentations
                          if item.repertoire_id == repertoire_id and item.position_key == key
                          and item.selected_response_uci in expected})
        mapping = mappings[0] if len(mappings) == 1 else None
        result = classify_response(
            played_uci=position["move"], expected_uci=expected, learner_turn=learner_turn,
            complete=membership.complete and membership.available, terminal=terminal,
            ambiguous=len(repertoires) != 1, mapping_count=len(mappings),
            available=mapping is not None and getattr(mapping, "available", False),
            introduced=None if mapping is None or mapping.introduction == "unknown" else mapping.introduction == "introduced",
            introduced_at=_timestamp(None if mapping is None else mapping.introduced_at),
            occurred_at=_timestamp(record.played_at),
            occurrence_is_exact=record.time_precision in {"second", "millisecond"},
            introduction_matches_content=mapping is not None and getattr(mapping, "introduction_matches_current_content", False),
        )
        if result is not None:
            kind, diagnostic = result
            if kind in {"ambiguous", "conflict"}:
                mapping = None
            findings.append(_finding(game, provider, membership, position, repertoire_id,
                                     expected, kind, diagnostic, mapping, authority))
            # Every meaningful uncertainty/departure ends this traversal, even
            # if an old raw comparer would accept one of conflicting own moves.
            frontier.advance((), position["move"])
            departed = True
        else:
            frontier.advance(candidates, position["move"])
    return tuple(findings)


def _timestamp(value):
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _finding(game, provider, membership, position, repertoire_id, expected, kind,
             diagnostic, mapping, authority):
    if kind == "conflict":
        membership_fact = "conflict"
    elif kind == "ambiguous" or (kind == "known_unavailable" and not expected):
        membership_fact = "unknown"
    else:
        membership_fact = "present" if expected else "absent"
    return Finding(
        kind=kind, provider=provider, game_id=game.game_id,
        source_revision=game.source_revision, source_digest=game.source_digest,
        ply=position["ply"] + 1, position_key=position["comparison_key"],
        expected_uci=expected, played_uci=position["move"], repertoire_id=repertoire_id,
        repertoire_revision=membership.revision,
        decision_id=None if mapping is None else mapping.decision_id,
        presentation_id=None if mapping is None else mapping.presentation_id,
        presentation_revision=None if mapping is None else mapping.presentation_revision,
        introduced_before_game=True if kind == "confirmed_miss" else False if kind == "known_unintroduced" else None,
        membership=membership_fact,
        lapse_eligible=kind == "confirmed_miss", diagnostic=diagnostic,
        reentry_authority_id=authority,
    )
