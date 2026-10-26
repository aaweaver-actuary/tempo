"""Read-only projection of an existing backend-owned bounded game capture.

Composition supplies capture_game bound to the selected game. This adapter has no
SQL, connection, transaction, graph migration or provider lifecycle authority.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from typing import Callable, Mapping

import chess
from tempo_evidence_math import opening_position_key
from tempo_game_evidence_contracts.legacy import authority_source_digest, authority_graph_digest, decision_identity, fingerprint
from tempo_game_review_contracts import AuthoredRoute, RepertoireSnapshot as MembershipSnapshot

from app.contracts.product_v02.repertoire import (
    DecisionPresentation, RepertoireSnapshot, RouteBoundary,
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


def _mappings(capture, routes, revision):
    cards = capture["tables"]["cards"]
    counts = Counter(row["id"] for row in cards)
    cards_by_id = {row["id"]: row for row in cards if counts[row["id"]] == 1}
    mappings = []
    bound_occurrences = {}
    for graph in capture["graphs"]:
        card = cards_by_id.get(graph["card_id"])
        route = routes.get((graph["repertoire_id"], graph["line_id"]))
        if card is None or route is None:
            continue
        if (graph["generation"] != capture["authority"]["graph_generation"]
                or graph["trained_color"] != route.learner_color
                or card.get("trained_color") != route.learner_color
                or card.get("content_type") != "opening"
                or any(card.get(flag) for flag in ("archived", "pending_validation", "superseded_by", "integrity_blocked", "deleted"))):
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
                     and route.moves[ply:ply + len(executable)] == executable]
            # Explicit graph membership + exact executable/history is required.
            # Matching FEN/SAN alone never creates a physical binding.
            if len(roots) != 1:
                continue
        except (ValueError, TypeError, KeyError):
            continue
        introduction, introduced_at = _introduction(card)
        for offset, uci in enumerate(executable):
            frame = card_frames[offset]
            if frame.turn != (route.learner_color == "white"):
                continue
            mapping = DecisionPresentation(
                decision_id=decision_identity(card["id"], offset, executable[:offset]),
                repertoire_id=route.repertoire_id,
                position_key=opening_position_key(frame.fen()), selected_response_uci=uci,
                presentation_id=card["id"], presentation_revision=card["revision"],
                repertoire_revision=revision, introduction=introduction, introduced_at=introduced_at)
            mappings.append(mapping)
            key = route.repertoire_id, mapping.position_key, uci
            bound_occurrences.setdefault(key, set()).add((route.route_id, roots[0] + offset))
    # The first T01 freeze has position-wide mappings. Until the owner adds
    # route occurrence scope, withhold a mapping if any transposed occurrence
    # lacks explicit graph evidence. FEN coincidence cannot bind another route.
    required_occurrences = {}
    for route in routes.values():
        try:
            frames = _frames(route.starting_fen, route.moves)
        except (ValueError, TypeError):
            continue
        for ply, uci in enumerate(route.moves):
            if frames[ply].turn == (route.learner_color == "white"):
                key = route.repertoire_id, opening_position_key(frames[ply].fen()), uci
                required_occurrences.setdefault(key, set()).add((route.route_id, ply))
    mappings = [item for item in mappings if bound_occurrences.get(
        (item.repertoire_id, item.position_key, item.selected_response_uci)) == required_occurrences.get(
        (item.repertoire_id, item.position_key, item.selected_response_uci))]
    # Repeated graph uses of one explicitly mapped decision are not extra cards.
    return tuple({item.model_dump_json(): item for item in mappings}.values())


def project_capture(capture: Mapping) -> RepertoireSnapshot:
    """Project a retained owner capture; stale/oversized evidence is rejected."""
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
    tables = source["tables"]
    if (len(tables["repertoire_lines"]) > 64 or len(tables["cards"]) > 128
            or len(source["graphs"]) > 256):
        raise ValueError("snapshot_capture_exceeds_bound")
    proof = source.get("catalogue_scope")
    if proof is not None:
        from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof
        from tempo_game_evidence_contracts.legacy import LegacyAuthorityToken
        token = LegacyAuthorityToken.model_validate(authority)
        validated = LegacyScopedCatalogueProof.model_validate(proof)
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
            {(route.repertoire_id, route.route_id): route for route in routes}, revision))


class CapturedSnapshotAdapter:
    """Implement RepertoireSnapshotPort with one injected owner capture call."""
    def __init__(self, capture: Callable[[str], Mapping]):
        self._capture = capture

    def snapshot(self, repertoire_id: str) -> RepertoireSnapshot:
        captured = self._capture(repertoire_id)
        if captured["authority"]["repertoire_id"] != repertoire_id:
            raise ValueError("snapshot_scope_mismatch")
        return project_capture(captured)
