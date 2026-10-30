"""Pure repertoire projections over supplied bounded owner facts.

Game capture and repertoire-only reads retain separate scopes. SQL, connection,
transaction, graph migration and provider lifecycle authority stay in adapters.
"""
from __future__ import annotations

from collections import Counter, OrderedDict
from functools import lru_cache
from dataclasses import dataclass
from types import MappingProxyType
from datetime import datetime, timezone
import json
from typing import Callable, Mapping

import chess
from tempo_evidence_math import opening_position_key
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import authority_source_digest, authority_graph_digest, decision_identity, fingerprint
from tempo_game_review_contracts import AuthoredRoute, CanonicalGame, RepertoireSnapshot as MembershipSnapshot

from app.contracts.product_v02.repertoire import (
    DecisionPresentation, IntroductionGameScope, RepertoireSnapshot, RouteBoundary,
)

MAX_CAPTURE_BYTES = 32 * 1024 * 1024


class RepertoireSnapshotBoundExceeded(ValueError):
    """A proved budget refusal containing only public numeric diagnostics."""

    def __init__(self, *, stage, sample_complete, violations):
        dimensions = {"source_rows", "card_rows", "membership_rows", "graph_rows",
                      "row_bytes", "estimated_snapshot_bytes", "snapshot_bytes", "authored_moves"}
        observations = {"exact", "lower_bound", "conservative_estimate"}
        if stage not in {"read", "projection"} or type(sample_complete) is not bool:
            raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
        copied = []
        for violation in violations:
            if (set(violation) != {"dimension", "observed", "limit", "observation"}
                    or violation["dimension"] not in dimensions
                    or violation["observation"] not in observations
                    or type(violation["observed"]) is not int
                    or type(violation["limit"]) is not int
                    or violation["limit"] < 0 or violation["observed"] <= violation["limit"]):
                raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
            copied.append(tuple(violation[key] for key in ("dimension", "observed", "limit", "observation")))
        if not copied:
            raise ValueError("invalid_repertoire_snapshot_bound_diagnostic")
        self._stage, self._sample_complete, self._violations = stage, sample_complete, tuple(copied)
        super().__init__("repertoire_snapshot_exceeds_bound")

    def diagnostic(self):
        return {"code": "repertoire_snapshot_exceeds_bound", "stage": self._stage,
                "sample_complete": self._sample_complete,
                "violations": [dict(zip(("dimension", "observed", "limit", "observation"), values))
                               for values in self._violations]}


def _introduction(card):
    if "introduced_at" not in card:
        return "unknown", None
    value = card["introduced_at"]
    if value is None:
        return "unintroduced", None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if "T" in value and parsed.tzinfo is not None and parsed.utcoffset() == timezone.utc.utcoffset(parsed):
            return "introduced", value
    except (ValueError, TypeError, AttributeError):
        pass
    # A day-only or malformed timestamp cannot establish precise chronology.
    return "unknown", None


def _chronology_provider(value):
    # The retained importer uses "chess.com"; product contracts use "chesscom".
    # Normalize only those explicit aliases, never the source value or digest.
    if value in {"chess.com", "chesscom"}:
        return "chesscom"
    if value == "lichess":
        return "lichess"
    raise ValueError("unsupported_chronology_provider")


def _comparison_scope(capture, comparison_game):
    if comparison_game is None:
        return None
    game = CanonicalGame.model_validate(comparison_game.model_dump(mode="python"))
    raw = capture["game"]
    provider = _chronology_provider(raw.get("provider"))
    if (game.game_id != raw.get("id") or _chronology_provider(game.source_id) != provider
            or game.learner_color != raw.get("color")
            or list(game.moves) != json.loads(raw["moves_json"])
            or opening_position_key(game.starting_fen) != opening_position_key(raw["start_fen"])
            or game.played_at is None or datetime.fromisoformat(game.played_at.replace("Z", "+00:00"))
            != datetime.fromisoformat(raw["played_at"].replace("Z", "+00:00"))):
        raise ValueError("snapshot_comparison_game_mismatch")
    return IntroductionGameScope(provider=provider, game_id=game.game_id,
        source_revision=game.source_revision, source_digest=game.source_digest)


def _frames(starting_fen, moves):
    board = chess.Board(starting_fen)
    if not board.is_valid():
        raise ValueError("invalid_source_position")
    frames = [board.copy(stack=False)]
    for uci in moves:
        move = chess.Move.from_uci(uci)
        if move not in board.legal_moves:
            raise ValueError("illegal_source_move")
        board.push(move)
        frames.append(board.copy(stack=False))
    return frames


def _mappings(capture, routes, revision, scope_proof=None, comparison_scope=None, *, include_unavailable=False):
    cards = capture["tables"]["cards"]
    counts = Counter(row["id"] for row in cards)
    cards_by_id = {row["id"]: row for row in cards if counts[row["id"]] == 1}
    memberships = {(row["repertoire_id"], row["card_id"]) for row in capture["tables"]["repertoire_cards"]}
    mappings = []
    route_positions = {}
    # Graph aliases often reuse the same exact executable. Keep only detached
    # turn/position facts in this invocation, never mutable Boards or authority.
    # Bounds cap retained replay data independently of the number of graph uses.
    replayed = OrderedDict()
    position_key = lru_cache(maxsize=1024)(opening_position_key)
    def replay_positions(starting_fen, moves):
        key = (starting_fen, tuple(moves))
        if key not in replayed:
            replayed[key] = tuple((frame.turn, canonical_fen_key(frame.fen(en_passant='legal')))
                for frame in _frames(starting_fen, moves))
            if len(replayed) > 128:
                replayed.popitem(last=False)
        else:
            replayed.move_to_end(key)
        return replayed[key]
    for graph in capture["graphs"]:
        card = cards_by_id.get(graph["card_id"])
        route = routes.get((graph["repertoire_id"], graph["line_id"]))
        if card is None or route is None or (graph["repertoire_id"], graph["card_id"]) not in memberships:
            continue
        if (graph["generation"] != capture["authority"]["graph_generation"]
                or graph["trained_color"] != route.learner_color
                or card.get("trained_color") != route.learner_color
                or card.get("content_type") != "opening"
                or not {"archived", "pending_validation", "superseded_by"} <= card.keys()):
            continue
        available = not any(card.get(flag) for flag in (
            "archived", "pending_validation", "superseded_by", "integrity_blocked", "deleted"))
        if not available and not include_unavailable:
            continue
        try:
            executable = json.loads(graph["moves_json"])
            if (not executable or len(executable) > 1000
                    or executable != json.loads(card["moves_json"])
                    or position_key(graph["starting_fen"]) != position_key(card["start_fen"])):
                continue
            route_key = (route.repertoire_id, route.route_id)
            if route_key not in route_positions:
                try:
                    occurrences = {}
                    for ply, (_turn, key) in enumerate(replay_positions(route.starting_fen, route.moves)):
                        occurrences.setdefault(key, []).append(ply)
                    index = MappingProxyType({key: tuple(plies) for key, plies in occurrences.items()})
                    route_positions[route_key] = (route, index)
                except (ValueError, TypeError, KeyError):
                    route_positions[route_key] = (route, None)
            cached_route, positions = route_positions[route_key]
            if cached_route is not route or positions is None:
                continue
            card_frames = replay_positions(card["start_fen"], executable)
            roots = [ply for ply in positions.get(position_key(graph["starting_fen"]), ())
                     if tuple(route.moves[ply:ply + len(executable)]) == tuple(executable)]
            # Explicit graph membership + exact executable/history is required.
            # Matching FEN/SAN alone never creates a physical binding.
            if len(roots) != 1:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        introduction, introduced_at = _introduction(card)
        before = card.get("introduced_before_game")
        known_before = before if type(before) is bool and comparison_scope is not None else None
        if known_before is True:
            introduction = "introduced"
        for offset, uci in enumerate(executable):
            turn, key = card_frames[offset]
            if turn != (route.learner_color == "white"):
                continue
            # The bounded capture may omit an ended/diverged competing route,
            # but its public proof retains the exclusive-policy anchor. Never
            # turn that omission into physical miss authority before the anchor.
            if scope_proof is not None:
                anchor = scope_proof.authority_anchor(route.repertoire_id)
                if anchor is None or roots[0] + offset < anchor:
                    continue
            mapping = DecisionPresentation(
                decision_id=decision_identity(card["id"], offset, executable[:offset]),
                repertoire_id=route.repertoire_id,
                position_key=key, selected_response_uci=uci,
                presentation_id=card["id"], presentation_revision=card["revision"],
                repertoire_revision=revision, introduction=introduction, introduced_at=introduced_at,
                route_id=route.route_id, route_ply=roots[0] + offset,
                introduced_before_game=known_before,
                introduction_game=comparison_scope if known_before is not None else None,
                available=available,
                # Retain the donor's original-revision compatibility rule;
                # changed physical content needs explicit owner introduction proof.
                introduction_matches_current_content=(
                    card["introduction_matches_current_content"] is True
                    if "introduction_matches_current_content" in card else card["revision"] == 1))
            mappings.append(mapping)
    # Repeated graph uses of one explicitly mapped decision are not extra cards.
    return tuple({item.model_dump_json(): item for item in mappings}.values())


def project_capture(capture: Mapping, *, comparison_game: CanonicalGame | None = None) -> RepertoireSnapshot:
    """Project owner capture; optionally retain its exact comparison chronology.

    Composition supplies authenticated CanonicalGame source revision/digest;
    the legacy capture itself retains no original PGN bytes or revision label.
    Identity, normalized execution, color and occurrence time must still agree.
    """
    encoded = json.dumps(capture, separators=(",", ":"), sort_keys=True)
    if len(encoded.encode()) > MAX_CAPTURE_BYTES:
        raise ValueError("snapshot_capture_exceeds_bound")
    source = json.loads(encoded)  # Own every mutable byte before inspecting it.
    if (source.get("kind") != "legacy_game_capture" or source.get("schema_version") != 1
            or source.get("export_digest") != fingerprint({key: value for key, value in source.items() if key != "export_digest"})):
        raise ValueError("invalid_owner_capture")
    authority = source["authority"]
    if authority.get("graph_state") != "ready":
        raise ValueError("snapshot_graph_unavailable")
    if authority != source["observed_after"]:
        raise ValueError("snapshot_revision_changed")
    comparison_scope = _comparison_scope(source, comparison_game)
    tables = source["tables"]
    if (len(tables["repertoire_lines"]) > 64 or len(tables["cards"]) > 128
            or len(source["graphs"]) > 256):
        raise ValueError("snapshot_capture_exceeds_bound")
    proof = source.get("catalogue_scope")
    validated = None
    if proof is not None:
        from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof
        from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken
        token = LegacyAuthorityToken.model_validate(authority)
        validated = LegacyScopedCatalogueProof.model_validate(proof)
        game = source["game"]
        if (validated.scope.game_id != game.get("id")
                or validated.scope.content_hash != game.get("content_hash")
                or validated.scope.learner_color != game.get("color")
                or datetime.fromisoformat(validated.scope.occurred_at.replace("Z", "+00:00"))
                != datetime.fromisoformat(game["played_at"].replace("Z", "+00:00"))
                or list(validated.scope.moves) != json.loads(game["moves_json"])
                or validated.scope.starting_position_key != opening_position_key(game["start_fen"])):
            raise ValueError("snapshot_game_scope_mismatch")
        if (validated.authority_id != token.storage_identity()[0]
                or validated.integrity_certificate.graph_task_generation != token.graph_generation
                or validated.source_identity_digests() != {key: authority[key] for key in
                ("source_digest", "graph_digest", "catalogue_digest")}
                or validated.catalogue_generation != authority["catalogue_generation"]
                or set(validated.candidate_route_ids) != {row["id"] for row in tables["repertoire_lines"]}):
            raise ValueError("snapshot_scope_revision_mismatch")
    elif not source.get("catalogue_complete", False):
        raise ValueError("snapshot_scope_completeness_unknown")
    elif (authority_source_digest(tables) != authority["source_digest"]
            or authority_graph_digest(source["graphs"]) != authority["graph_digest"]):
        raise ValueError("snapshot_source_digest_mismatch")
    routes = tuple(AuthoredRoute(repertoire_id=row["repertoire_id"], route_id=row["id"],
        policy_id=row["repertoire_id"], learner_color=row["trained_color"],
        starting_fen=row["start_fen"], moves=json.loads(row["moves_json"]))
        for row in sorted(tables["repertoire_lines"], key=lambda row: (row["repertoire_id"], row["id"])))
    if len({(route.repertoire_id, route.route_id) for route in routes}) != len(routes):
        raise ValueError("duplicate_source_route")
    revision = authority["graph_generation"]
    selected = authority["repertoire_id"]
    repertoire = next((row for row in tables["repertoires"] if row["id"] == selected), None)
    if repertoire is None or repertoire.get("scope_source_revision") != authority["scope_source_revision"]:
        raise ValueError("snapshot_source_revision_mismatch")
    if json.loads(repertoire.get("canonical_prefix_moves_json", "[]")):
        raise ValueError("canonical_prefix_snapshot_unsupported")
    membership = MembershipSnapshot(workspace_id=authority["workspace_id"],
        snapshot_id="product:" + source["export_digest"], revision=revision,
        digest=source["export_digest"], source_revision=fingerprint(authority),
        captured_at=source["captured_at"], scope_id="capture:" + source["export_digest"],
        scope_repertoire_ids=sorted({selected, *(route.repertoire_id for route in routes)}),
        complete=True, routes=list(routes))
    return RepertoireSnapshot(membership=membership, variant="standard",
        learner_color=source["game"]["color"],
        boundaries=tuple(RouteBoundary(route_id=route.route_id, terminal_ply=len(route.moves), deliberate=True)
                         for route in routes),
        decision_presentations=_mappings(source,
            {(route.repertoire_id, route.route_id): route for route in routes}, revision, validated, comparison_scope))


class CapturedSnapshotAdapter:
    """Implement RepertoireSnapshotPort with one injected owner capture call."""
    def __init__(self, capture: Callable[[str], Mapping], *, comparison_game: CanonicalGame | None = None):
        self._capture = capture
        self._comparison_game = (CanonicalGame.model_validate(comparison_game.model_dump(mode="python"))
                                 if comparison_game is not None else None)

    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot:
        captured = self._capture(repertoire_id)
        if captured["authority"]["repertoire_id"] != repertoire_id:
            raise ValueError("snapshot_scope_mismatch")
        return project_capture(captured, comparison_game=self._comparison_game)


def _project_repertoire(facts: Mapping, *, captured_at: str | None = None, published: bool = False) -> RepertoireSnapshot:
    """Project the complete bounded repertoire-only owner read, including conflicts.

    No game/clean-integrity prerequisite and no inferred date-only chronology.
    The source digest excludes capture time and every schedule/review/queue field.
    """
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    source = json.loads(encoded)
    header = source["repertoire"]
    repertoire_id = header["id"]
    if not header.get("workspace_id"):
        raise ValueError("repertoire_workspace_unknown")
    if json.loads(header.get("canonical_prefix_moves_json", "[]")):
        raise ValueError("canonical_prefix_snapshot_unsupported")
    violations = [dict(dimension=dimension, observed=len(source[name]), limit=limit, observation="exact")
                  for name, dimension, limit in (("lines", "source_rows", 10000), ("cards", "card_rows", 10000),
                      ("memberships", "membership_rows", 10000))
                  if len(source[name]) > limit]
    if violations:
        raise RepertoireSnapshotBoundExceeded(stage="projection", sample_complete=True, violations=violations)
    if any(row["repertoire_id"] != repertoire_id for name in ("lines", "memberships", "graphs")
           for row in source[name]):
        raise ValueError("repertoire_snapshot_scope_mismatch")
    routes = tuple(AuthoredRoute(repertoire_id=repertoire_id, route_id=row["id"],
        policy_id=repertoire_id, learner_color=row["trained_color"], starting_fen=row["start_fen"],
        moves=json.loads(row["moves_json"])) for row in sorted(source["lines"], key=lambda row: row["id"]))
    authored_moves = sum(len(route.moves) for route in routes)
    if authored_moves > 100000:
        raise RepertoireSnapshotBoundExceeded(stage="projection", sample_complete=True, violations=[
            dict(dimension="authored_moves", observed=authored_moves, limit=100000, observation="exact")])
    colors = {route.learner_color for route in routes}
    if len(colors) != 1:
        raise ValueError("repertoire_color_unknown" if not colors else "repertoire_color_ambiguous")
    source["lines"].sort(key=lambda row: row["id"])
    source["cards"].sort(key=lambda row: row["id"])
    source["memberships"].sort(key=lambda row: (row["repertoire_id"], row["card_id"]))
    source["graphs"].sort(key=lambda row: (row["line_id"], row["decision_index"]))
    if published:
        generation = header.get("graph_generation")
        if header.get("graph_state") != "ready" or type(generation) is not int or generation < 1:
            raise ValueError("repertoire_publication_unavailable")
        revision = generation
    else:
        revision = header["scope_source_revision"] + 1
    kind = "current_published_repertoire_snapshot" if published else "current_repertoire_snapshot"
    digest = fingerprint({"kind": kind, "schema_version": 1, "facts": source})
    moment = captured_at or datetime.now(timezone.utc).isoformat()
    membership = MembershipSnapshot(workspace_id=header["workspace_id"],
        snapshot_id=("publication:" if published else "repertoire:") + digest,
        revision=revision, digest=digest, source_revision=digest, captured_at=moment,
        scope_id=("publication:" if published else "repertoire:") + repertoire_id,
        scope_repertoire_ids=[repertoire_id], complete=True, routes=list(routes))
    # Retain the existing exact graph/executable/card mapping law. A missing or
    # non-ready graph leaves physical mapping unknown but never hides source routes.
    mapping_source = {"tables": {"cards": source["cards"], "repertoire_cards": source["memberships"]},
        "graphs": source["graphs"] if header.get("graph_state") == "ready" else [],
        "authority": {"graph_generation": header.get("graph_generation")}}
    return RepertoireSnapshot(membership=membership, variant="standard", learner_color=next(iter(colors)),
        boundaries=tuple(RouteBoundary(route_id=route.route_id, terminal_ply=len(route.moves), deliberate=True)
                         for route in routes),
        decision_presentations=_mappings(mapping_source,
            {(route.repertoire_id, route.route_id): route for route in routes}, revision,
            include_unavailable=True))


def project_repertoire(facts: Mapping, *, captured_at: str | None = None) -> RepertoireSnapshot:
    """Audit source view: positive authored counter, independent of publication."""
    return _project_repertoire(facts, captured_at=captured_at)


def project_published_repertoire(facts: Mapping, *, captured_at: str | None = None) -> RepertoireSnapshot:
    """Feedback comparison view: explicit ready graph generation, never a guess.

    Current source facts remain in the freshness digest. This is detector input,
    not mutation authority; Study still captures/fences its current owner state.
    """
    return _project_repertoire(facts, captured_at=captured_at, published=True)


@dataclass(frozen=True, init=False)
class PreparedPublishedRepertoire:
    """One Repertoire-owned projection of detached complete publication facts.

    Construction accepts source facts only. Consumers cannot supply a snapshot
    or a readiness assertion in place of the owning projection. This ephemeral
    value grants no current database authority; receiving owners still select
    their policy and fence every prepared publication in their writer.
    """
    _source_json: str
    snapshot: RepertoireSnapshot

    def __init__(self, facts: Mapping, *, captured_at: str | None = None):
        encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        snapshot = project_published_repertoire(json.loads(encoded), captured_at=captured_at)
        object.__setattr__(self, '_source_json', encoded)
        object.__setattr__(self, 'snapshot', snapshot)

    def publication_facts(self) -> dict:
        """Return a detached complete read, never the capability's owned bytes."""
        return json.loads(self._source_json)


class CurrentSnapshotAdapter:
    """RepertoireSnapshotPort over an injected bounded repertoire-only read."""
    projection = staticmethod(project_repertoire)

    def __init__(self, read: Callable[[str], Mapping]):
        self._read = read

    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot:
        facts = self._read(repertoire_id)
        if facts["repertoire"]["id"] != repertoire_id:
            raise ValueError("repertoire_snapshot_scope_mismatch")
        return self.projection(facts)


class PublishedSnapshotAdapter(CurrentSnapshotAdapter):
    """Feedback comparison port; unavailable publication grants no view."""
    projection = staticmethod(project_published_repertoire)
