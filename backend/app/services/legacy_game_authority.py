"""Backend-owned capture and exact decision facts for legacy evidence intake.

Reads are paged and closed before replay/hash work. The command receiver fences
these facts before borrowing its transaction to shared remediation. This module
does not classify games, create cards, schedule, admit or settle work.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json

import chess
from tempo_chess_core import canonical_fen_key
from tempo_game_evidence_contracts.legacy import (LegacyAuthorityToken, fingerprint,
    authority_source_digest, authority_graph_digest, decision_identity)
from tempo_learning_remediation import LegacyAcceptedDecision, RemediationError
from tempo_game_evidence_contracts.legacy_scope import LegacyScopedCatalogueProof

from ..database import background_read_connection
from .legacy_game_evidence_scope import LEGACY_WORKSPACE_ID

MAX_DECISION_ROUTES = 64
_REPERTOIRE_COLUMNS = "id,name,is_main,scope_source_revision,canonical_prefix_revision,canonical_prefix_moves_json,canonical_prefix_preview_id"
_CARD_COLUMNS = "id,revision,start_fen,moves_json,trained_color,content_type,archived,pending_validation,superseded_by,repertoire_id,canonical_route_source,introduced_at"
_GAME_COLUMNS = "id,provider,provider_game_id,content_hash,played_at,color,result,start_fen,moves_json,adaptive_excluded"
_GAME_HEADER_COLUMNS = "id,provider,provider_game_id,content_hash"


@dataclass(frozen=True)
class Capture:
    target_id: str
    metadata: dict
    tables: dict
    graphs: list
    game: dict
    catalogue_proof: LegacyScopedCatalogueProof | None = None
    digests: dict | None = None


@dataclass(frozen=True)
class WithdrawalCapture:
    target_id: str
    game: dict | None


def capture_withdrawal_header(delivery):
    """No graph/card authority is needed to retract an exact retained incident."""
    identity = read_rows("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1")
    if (len(identity) != 1 or identity[0]["workspace_id"] != LEGACY_WORKSPACE_ID
            or identity[0]["target_id"] != delivery.authority.target_id):
        raise RemediationError("terminal_failure", "legacy_withdrawal_target_mismatch")
    games = read_rows("SELECT " + _GAME_HEADER_COLUMNS + " FROM imported_games WHERE id=%s", (delivery.game_id,))
    game = {key: games[0][key] for key in _GAME_HEADER_COLUMNS.split(",")} if games else None
    if game is not None and (game["provider"], game["provider_game_id"]) != (
            delivery.game_source.provider, delivery.game_source.provider_game_id):
        raise RemediationError("terminal_failure", "legacy_withdrawal_canonical_game_mismatch")
    return WithdrawalCapture(identity[0]["target_id"], game)


def validate_withdrawal_lineage(delivery, withdrawal, retained):
    """The exact accepted contribution owns source lineage even after deletion.

    Withdrawal envelopes identify that accepted source header. A corrected live
    content hash may differ; correction across hashes needs a separate explicit
    withdrawal envelope before new miss acceptance, not a mixed-source batch.
    """
    if retained is None:
        raise RemediationError("conflict", "legacy_withdrawal_lineage_missing")
    if retained["obligation_revision"] != withdrawal.expected_obligation_revision:
        raise RemediationError("stale_revision", "legacy_withdrawal_revision_superseded")
    try:
        payload = json.loads(retained["payload_json"])
        original = payload.get("authority", {})
        source = payload.get("game_source", {})
        if ((retained["evidence_id"], retained["game_id"], payload.get("game_id"),
                payload.get("obligation", {}).get("source_evidence_id")) != (
                delivery.evidence_id, delivery.game_id, delivery.game_id, delivery.evidence_id)
                or (original.get("kind"), original.get("target_id"), original.get("workspace_id"),
                    original.get("repertoire_id")) != ("legacy_graph", delivery.authority.target_id,
                    LEGACY_WORKSPACE_ID, delivery.authority.repertoire_id)
                or (source.get("provider"), source.get("provider_game_id"), source.get("content_hash")) != (
                    delivery.game_source.provider, delivery.game_source.provider_game_id,
                    delivery.game_source.content_hash)):
            raise RemediationError("terminal_failure", "legacy_withdrawal_lineage_mismatch")
    except (ValueError, TypeError, AttributeError) as error:
        raise RemediationError("terminal_failure", "legacy_withdrawal_lineage_invalid") from error


def read_rows(statement, parameters=()):
    with background_read_connection() as database:
        return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]


def metadata(repertoire_id):
    rows = read_rows("""SELECT repertoire.scope_source_revision,repertoire.canonical_prefix_revision,
        scope.generation catalogue_generation,graph.generation graph_generation,graph.state graph_state,
        integrity.status integrity_status,integrity.scan_status,integrity.scan_generation
        FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
        LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=repertoire.id
        LEFT JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
        WHERE repertoire.id=%s AND scope.id=1""", (repertoire_id,))
    if len(rows) != 1:
        raise RemediationError("stale_revision", "legacy_repertoire_authority_missing")
    from .postgres_integrity import current_integrity_certificate
    with background_read_connection() as database:
        certificate = current_integrity_certificate(database, repertoire_id, rows[0]["graph_generation"])
    return {**rows[0], "integrity_current": certificate is not None, "integrity_certificate": certificate}


def capture(delivery):
    from .legacy_game_capture import capture_scope
    return capture_scope(delivery, read_rows, metadata)


def current_token(captured, repertoire_id):
    if any((row["repertoire_id"], row["generation"]) != (
            repertoire_id, captured.metadata["graph_generation"]) for row in captured.graphs):
        raise RemediationError("stale_revision", "legacy_mixed_graph_capture")
    return LegacyAuthorityToken(kind="legacy_graph", target_id=captured.target_id,
        workspace_id=LEGACY_WORKSPACE_ID, repertoire_id=repertoire_id,
        scope_source_revision=captured.metadata["scope_source_revision"],
        canonical_prefix_revision=captured.metadata["canonical_prefix_revision"],
        catalogue_generation=captured.metadata["catalogue_generation"],
        graph_generation=captured.metadata["graph_generation"], graph_state=captured.metadata["graph_state"],
        source_digest=(captured.digests["source_digest"] if captured.digests else authority_source_digest(captured.tables)),
        graph_digest=(captured.digests["graph_digest"] if captured.digests else authority_graph_digest(captured.graphs)),
        catalogue_digest=(captured.digests["catalogue_digest"] if captured.digests else fingerprint([dict(repertoire_id=row["repertoire_id"], route_id=row["id"],
            policy_id=row["repertoire_id"], learner_color=row["trained_color"], starting_fen=row["start_fen"],
            moves=json.loads(row["moves_json"])) for row in sorted(
                captured.tables["repertoire_lines"], key=lambda row: row["id"])])))


def positions(starting_fen, moves):
    board = chess.Board(starting_fen)
    if not board.is_valid():
        raise RemediationError("stale_revision", "legacy_invalid_source_position")
    result = [board.copy(stack=False)]
    for move in moves:
        board.push_uci(move)
        result.append(board.copy(stack=False))
    return result


def comparison_key(board):
    return canonical_fen_key(board.fen(en_passant="legal"))


def introduced_before_game(value, game_time):
    if value is None:
        return False
    try:
        if len(value) == 10:
            # Unknown intraday time: only a later UTC day is established.
            latest = datetime.fromisoformat(value).replace(tzinfo=timezone.utc) + timedelta(days=1)
            return latest <= game_time
        known = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return known.tzinfo is not None and known <= game_time
    except (ValueError, TypeError, OverflowError):
        return False


def completed_game_time(game):
    if game["result"] not in {"win", "loss", "draw", "1-0", "0-1", "1/2-1/2"}:
        raise RemediationError("terminal_failure", "legacy_completed_game_required")
    try:
        actual = datetime.fromisoformat(game["played_at"].replace("Z", "+00:00"))
        if actual.tzinfo is None or actual.utcoffset() is None:
            raise ValueError("unknown game time")
        return actual
    except (ValueError, TypeError, AttributeError) as error:
        raise RemediationError("stale_revision", "legacy_game_time_unknown_or_changed") from error


def prepare_decisions(delivery, captured):
    """Verify full current source bindings; never choose among game findings."""
    token = current_token(captured, delivery.authority.repertoire_id)
    if not captured.metadata.get("integrity_current"):
        raise RemediationError("stale_revision", "legacy_completed_current_integrity_required")
    game = captured.game
    source = delivery.game_source
    if (game["provider"], game["provider_game_id"], game["content_hash"]) != (
            source.provider, source.provider_game_id, source.content_hash) or game["adaptive_excluded"]:
        raise RemediationError("stale_revision", "legacy_game_source_changed")
    actual_time = completed_game_time(game)
    expected_time = datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00"))
    if actual_time != expected_time:
        raise RemediationError("stale_revision", "legacy_game_time_unknown_or_changed")
    selected = next((row for row in captured.tables["repertoires"] if row["id"] == token.repertoire_id), None)
    if selected is None or json.loads(selected.get("canonical_prefix_moves_json", "[]")):
        raise RemediationError("stale_revision", "legacy_canonical_prefix_adapter_unsupported")
    game_moves = json.loads(game["moves_json"])
    if captured.catalogue_proof is not None:
        proof = captured.catalogue_proof
        if (proof.authority_id != token.storage_identity()[0]
                or proof.source_identity_digests() != captured.digests
                or proof.catalogue_generation != token.catalogue_generation
                or datetime.fromisoformat(proof.scope.occurred_at.replace("Z", "+00:00")) != actual_time
                or (proof.scope.game_id, proof.scope.content_hash, proof.scope.learner_color,
                    proof.scope.moves, proof.scope.decision_plies) != (
                    delivery.game_id, source.content_hash, game["color"], game_moves,
                    sorted({item.ply for item in delivery.encounters}))
                or set(proof.candidate_route_ids) != {row["id"] for row in captured.tables["repertoire_lines"]}):
            raise RemediationError("stale_revision", "legacy_scoped_capture_mismatch")
    game_boards = positions(game["start_fen"], game_moves)
    cards = {row["id"]: row for row in captured.tables["cards"]}
    lines = captured.tables["repertoire_lines"]
    if any(row["trained_color"] not in {"white", "black"} for row in lines):
        raise RemediationError("stale_revision", "legacy_catalogue_color_unknown")
    encounters = {row.encounter_id: row for row in delivery.encounters}
    decisions = {}
    for comparison in delivery.comparisons:
        encounter = encounters[comparison.encounter_id]
        ply = encounter.ply
        if (ply >= len(game_moves) or encounter.observed_move != game_moves[ply]
                or ("white" if game_boards[ply].turn else "black") != game["color"]):
            raise RemediationError("terminal_failure", "legacy_encounter_not_in_current_game")
        if captured.catalogue_proof is not None:
            # The bounded candidate list omits routes with no executable move
            # at this ply. Its complete prefix proof still includes their
            # policy, exactly as the read-only comparator's authority anchor.
            anchor = proof.authority_anchor(token.repertoire_id)
            exclusive = (anchor is not None and anchor <= ply
                and proof.prefix_policies[ply] == [token.repertoire_id])
        else:
            exclusive = {row["repertoire_id"] for row in lines
                if row["trained_color"] == game["color"]
                and len(json.loads(row["moves_json"])) >= ply
                and json.loads(row["moves_json"])[:ply] == game_moves[:ply]
                and comparison_key(chess.Board(row["start_fen"])) == comparison_key(game_boards[0])
            } == {token.repertoire_id}
        if not exclusive:
            raise RemediationError("terminal_failure", "legacy_route_policy_ambiguous")
        matching = []
        for row in lines:
            moves = json.loads(row["moves_json"])
            if (row["trained_color"] == game["color"] and ply < len(moves)
                    and moves[:ply] == game_moves[:ply]
                    and comparison_key(chess.Board(row["start_fen"])) == comparison_key(game_boards[0])):
                matching.append((row, moves))
        if not matching or len(matching) > MAX_DECISION_ROUTES or {
                row["repertoire_id"] for row, _ in matching} != {token.repertoire_id}:
            raise RemediationError("terminal_failure", "legacy_route_policy_ambiguous")
        all_mapped = True
        mapped_decisions = set()
        selected_binding = None
        for row, moves in matching:
            route_boards = positions(row["start_fen"], moves)
            route_positions = [comparison_key(board) for board in route_boards]
            candidates = set()
            for graph in captured.graphs:
                if graph["line_id"] != row["id"]:
                    continue
                card = cards.get(graph["card_id"])
                if (card is None or card["revision"] != 1 or card["trained_color"] != game["color"]
                        or card.get("content_type", "opening") != "opening"
                        or card["archived"] or card["pending_validation"] or card.get("deleted")
                        or card.get("integrity_blocked") or card.get("superseded_by")
                        or not chess.Board(card["start_fen"]).is_valid() or not chess.Board(graph["starting_fen"]).is_valid()
                        or comparison_key(chess.Board(card["start_fen"])) != comparison_key(chess.Board(graph["starting_fen"]))
                        or json.loads(card["moves_json"]) != json.loads(graph["moves_json"])):
                    continue
                memberships = {link["repertoire_id"] for link in captured.tables["repertoire_cards"]
                               if link["card_id"] == card["id"]}
                physical_moves = json.loads(card["moves_json"])
                if memberships != {token.repertoire_id} or not 1 <= len(physical_moves) <= 40:
                    continue
                physical_position = comparison_key(chess.Board(card["start_fen"]))
                roots = [root for root, position in enumerate(route_positions) if
                    position == physical_position
                    and moves[root:root + len(physical_moves)] == physical_moves]
                if len(roots) != 1 or not 0 <= ply - roots[0] < len(physical_moves):
                    continue
                candidates.add((card["id"], ply - roots[0]))
            if len(candidates) != 1:
                all_mapped = False
            mapped_decisions.update(candidates)
            if row["id"] == comparison.route_id and len(candidates) == 1:
                selected_binding = (row, moves, route_boards[ply], next(iter(candidates)))
        if selected_binding is None:
            raise RemediationError("stale_revision", "legacy_physical_binding_unavailable")
        row, moves, board, (card_id, offset) = selected_binding
        card = cards[card_id]
        physical_moves = json.loads(card["moves_json"])
        if comparison_key(board) != comparison_key(game_boards[ply]):
            raise RemediationError("terminal_failure", "legacy_game_route_position_changed")
        decisions[(comparison.encounter_id, card_id, decision_identity(card_id, offset, physical_moves[:offset]))] = LegacyAcceptedDecision(
            card_id, card["revision"], decision_identity(card_id, offset, physical_moves[:offset]), token,
            canonical_fen_key(board.fen(en_passant="fen")), (physical_moves[offset],), LEGACY_WORKSPACE_ID,
            row["id"], ply, offset, fingerprint(dict(starting_fen=card["start_fen"], moves=physical_moves,
                trained_color=card["trained_color"])), tuple(sorted({moves[ply] for _, moves in matching})),
            all_mapped and len(mapped_decisions) == 1, card.get("introduced_at") is not None,
            introduced_before_game(card.get("introduced_at"), actual_time),
            scope_proof=captured.catalogue_proof)
    return token, decisions
