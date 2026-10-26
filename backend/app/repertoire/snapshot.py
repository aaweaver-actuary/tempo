"""Pure repertoire projections over supplied bounded owner facts.

Game capture and repertoire-only reads retain separate scopes. SQL, connection,
transaction, graph migration and provider lifecycle authority stay in adapters.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from typing import Callable, Mapping

import chess
from tempo_evidence_math import opening_position_key
from tempo_game_evidence_contracts.legacy import authority_source_digest, authority_graph_digest, decision_identity, fingerprint
from tempo_game_review_contracts import AuthoredRoute, CanonicalGame, RepertoireSnapshot as MembershipSnapshot

from app.contracts.product_v02.repertoire import (
    DecisionPresentation, IntroductionGameScope, RepertoireSnapshot, RouteBoundary,
)

MAX_CAPTURE_BYTES = 32 * 1024 * 1024


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


def _comparison_scope(capture, comparison_game):
    if comparison_game is None:
        return None
    game = CanonicalGame.model_validate(comparison_game.model_dump(mode="python"))
    raw = capture["game"]
    if (game.game_id != raw.get("id") or game.source_id != raw.get("provider")
            or game.learner_color != raw.get("color")
            or list(game.moves) != json.loads(raw["moves_json"])
            or opening_position_key(game.starting_fen) != opening_position_key(raw["start_fen"])
            or game.played_at is None or datetime.fromisoformat(game.played_at.replace("Z", "+00:00"))
            != datetime.fromisoformat(raw["played_at"].replace("Z", "+00:00"))):
        raise ValueError("snapshot_comparison_game_mismatch")
    return IntroductionGameScope(provider=raw["provider"], game_id=game.game_id,
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
                    or opening_position_key(graph["starting_fen"]) != opening_position_key(card["start_fen"])):
                continue
            route_frames = _frames(route.starting_fen, route.moves)
            card_frames = _frames(card["start_fen"], executable)
            roots = [ply for ply, frame in enumerate(route_frames)
                     if opening_position_key(frame.fen()) == opening_position_key(graph["starting_fen"])
                     and tuple(route.moves[ply:ply + len(executable)]) == tuple(executable)]
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
            frame = card_frames[offset]
            if frame.turn != (route.learner_color == "white"):
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
                position_key=opening_position_key(frame.fen()), selected_response_uci=uci,
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


def project_repertoire(facts: Mapping, *, captured_at: str | None = None) -> RepertoireSnapshot:
    """Project the complete bounded repertoire-only owner read, including conflicts.

    No game/clean-integrity prerequisite and no inferred date-only chronology.
    The source digest excludes capture time and every schedule/review/queue field.
    """
    encoded = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode()) > MAX_CAPTURE_BYTES:
        raise ValueError("repertoire_snapshot_exceeds_bound")
    source = json.loads(encoded)
    header = source["repertoire"]
    repertoire_id = header["id"]
    if not header.get("workspace_id"):
        raise ValueError("repertoire_workspace_unknown")
    if json.loads(header.get("canonical_prefix_moves_json", "[]")):
        raise ValueError("canonical_prefix_snapshot_unsupported")
    if (len(source["lines"]) > 10000 or len(source["cards"]) > 10000
            or len(source["memberships"]) > 10000 or len(source["graphs"]) > 20000):
        raise ValueError("repertoire_snapshot_exceeds_bound")
    if any(row["repertoire_id"] != repertoire_id for name in ("lines", "memberships", "graphs")
           for row in source[name]):
        raise ValueError("repertoire_snapshot_scope_mismatch")
    routes = tuple(AuthoredRoute(repertoire_id=repertoire_id, route_id=row["id"],
        policy_id=repertoire_id, learner_color=row["trained_color"], starting_fen=row["start_fen"],
        moves=json.loads(row["moves_json"])) for row in sorted(source["lines"], key=lambda row: row["id"]))
    if sum(len(route.moves) for route in routes) > 100000:
        raise ValueError("repertoire_snapshot_exceeds_bound")
    colors = {route.learner_color for route in routes}
    if len(colors) != 1:
        raise ValueError("repertoire_color_unknown" if not colors else "repertoire_color_ambiguous")
    source["lines"].sort(key=lambda row: row["id"])
    source["cards"].sort(key=lambda row: row["id"])
    source["memberships"].sort(key=lambda row: (row["repertoire_id"], row["card_id"]))
    source["graphs"].sort(key=lambda row: (row["line_id"], row["decision_index"]))
    revision = header["scope_source_revision"] + 1
    digest = fingerprint({"kind": "current_repertoire_snapshot", "schema_version": 1, "facts": source})
    moment = captured_at or datetime.now(timezone.utc).isoformat()
    membership = MembershipSnapshot(workspace_id=header["workspace_id"],
        snapshot_id="repertoire:" + digest, revision=revision, digest=digest,
        source_revision=digest, captured_at=moment, scope_id="repertoire:" + repertoire_id,
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


class CurrentSnapshotAdapter:
    """RepertoireSnapshotPort over an injected bounded repertoire-only read."""
    def __init__(self, read: Callable[[str], Mapping]):
        self._read = read

    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot:
        facts = self._read(repertoire_id)
        if facts["repertoire"]["id"] != repertoire_id:
            raise ValueError("repertoire_snapshot_scope_mismatch")
        return project_repertoire(facts)
