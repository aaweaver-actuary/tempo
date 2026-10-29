"""Transport-free product adapter over pure ingestion/traversal/classification.

The caller supplies the captured snapshot and its matching immutable index.
This module acquires nothing and submits no learning command.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, get_args

from tempo_game_classification import classify_response, resolve_equivalent_mapping
from tempo_game_ingestion import BytesInput, PgnGameIngester
from tempo_game_review_contracts import CanonicalGame, replay_line
from tempo_repertoire_comparison import RouteFrontier, TraversalRoute

from app.contracts.product_v02.games import Finding, GameRecord, TimePrecision
from app.contracts.product_v02.learning import GameEffectKey
from app.contracts.product_v02.repertoire import DecisionPresentation, RepertoireSnapshot

__all__ = ["ReentryLookup", "normalize_record", "compare_record", "compare_game", "effect_key"]


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
    return compare_game(game, provider=provider, time_precision=record.time_precision,
                        snapshot=snapshot, reentry_lookup=reentry_lookup)


def compare_game(game: CanonicalGame, *, provider: str,
                 snapshot: RepertoireSnapshot,
                 time_precision: TimePrecision = "unknown",
                 reentry_lookup: ReentryLookup | None = None) -> tuple[Finding, ...]:
    """Compare a retained canonical game without requiring original PGN bytes.

    Preserve the supplied source identity, revision and digest; legal replay
    validates the canonical history but cannot authenticate unavailable original
    bytes. The caller supplies retained occurrence precision explicitly. Missing
    precision stays unknown even when the canonical game contains a timestamp.
    """
    if provider not in {"lichess", "chesscom"}:
        raise ValueError("unsupported_provider")
    if time_precision not in get_args(TimePrecision):
        raise ValueError("unsupported_time_precision")
    if time_precision != "unknown" and game.played_at is None:
        raise ValueError("known_time_precision_requires_occurrence_time")
    # The existing canonical contract has a caller-mutable move list.
    game = CanonicalGame.model_validate(game.model_dump(mode="python"))
    # Existing membership models contain caller-mutable lists despite frozen
    # fields. Revalidate/copy the supplied capture before traversing it.
    snapshot = RepertoireSnapshot.model_validate(snapshot.model_dump(mode="python"))
    membership = snapshot.membership
    if membership.workspace_id != game.workspace_id:
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
                    if binding is None or not _mapping_applies(binding, route.route_id, candidate.ply):
                        continue
                    anchors = [item for item in route.reentries if item.ply == candidate.ply
                               and item.authority_id == candidate.reentry_authority_id]
                    mappings = [item for item in snapshot.decision_presentations
                                if item.repertoire_id == repertoire_id and item.position_key == key
                                and item.decision_id == binding.decision_id
                                and item.presentation_id == binding.presentation_id
                                and item.presentation_revision == binding.presentation_revision
                                and item.selected_response_uci == route.moves[candidate.ply]
                                and _mapping_applies(item, route.route_id, candidate.ply)]
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
                          and item.selected_response_uci in expected
                          and any(_mapping_applies(item, routes[index].route_id, ply)
                                  for index, ply in candidates)})
        mapping = resolve_equivalent_mapping(mappings)
        active_occurrences = {(routes[index].route_id, ply) for index, ply in candidates}
        route_mapping_ambiguous = (learner_turn and len(expected) == 1 and len(active_occurrences) > 1
            and any(item.route_id is not None for item in mappings)
            and (mapping is None or any(not any(_mapping_applies(item, route_id, ply) for item in mappings)
                                       for route_id, ply in active_occurrences)))
        result = classify_response(
            played_uci=position["move"], expected_uci=expected, learner_turn=learner_turn,
            complete=membership.complete and membership.available, terminal=terminal,
            ambiguous=route_mapping_ambiguous, mapping_count=1 if mapping is not None else len(mappings),
            available=mapping is not None and getattr(mapping, "available", False),
            introduced=None if mapping is None or mapping.introduction == "unknown" else mapping.introduction == "introduced",
            introduced_at=_timestamp(None if mapping is None else mapping.introduced_at),
            introduced_before_game=_scoped_introduction(mapping, game, provider),
            occurred_at=_timestamp(game.played_at),
            occurrence_is_exact=time_precision in {"second", "millisecond"},
            introduction_matches_content=mapping is not None and getattr(mapping, "introduction_matches_current_content", False),
        )
        if result is not None:
            kind, diagnostic = result
            if kind in {"ambiguous", "conflict"}:
                mapping = None
            findings.append(_finding(game, provider, membership, position, repertoire_id,
                                     expected, kind, diagnostic, mapping, authority))
            if kind == "known_unintroduced" and position["move"] in expected:
                # A covered correct encounter is a frequency fact, not a
                # departure. Retain the fact and keep following the same route.
                frontier.advance(candidates, position["move"])
                continue
            # Every meaningful uncertainty/departure ends this traversal, even
            # if an old raw comparer would accept one of conflicting own moves.
            frontier.advance((), position["move"])
            departed = True
        else:
            frontier.advance(candidates, position["move"])
    return tuple(findings)


def _timestamp(value):
    return None if value is None else datetime.fromisoformat(value.replace("Z", "+00:00"))


def _mapping_applies(mapping, route_id, route_ply):
    return mapping.route_id is None or (mapping.route_id, mapping.route_ply) == (route_id, route_ply)


def _scoped_introduction(mapping, game, provider):
    if mapping is None or mapping.introduction_game is None:
        return None
    scope = mapping.introduction_game
    if (scope.provider, scope.game_id, scope.source_revision, scope.source_digest) != (
            provider, game.game_id, game.source_revision, game.source_digest):
        return None
    return mapping.introduced_before_game


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
