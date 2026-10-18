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

from ..database import background_read_connection
from .legacy_game_evidence_scope import LEGACY_WORKSPACE_ID

PAGE_SIZE = 64
MAX_SOURCE_ROWS = 4096
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


def paged_rows(statement, parameters=(), *, composite=False):
    rows, cursor = [], ("", -1) if composite else ""
    while True:
        values = (*parameters, *cursor) if composite else (*parameters, cursor)
        page = read_rows(statement, values)
        rows.extend(page)
        if len(rows) > MAX_SOURCE_ROWS:
            raise RemediationError("stale_revision", "legacy_capture_scope_exceeds_bound")
        if len(page) < PAGE_SIZE:
            return rows
        cursor = (page[-1]["line_id"], page[-1]["decision_index"]) if composite else page[-1]["id"]


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
    return rows[0]


def capture(delivery):
    repertoire_id = delivery.authority.repertoire_id
    before = metadata(repertoire_id)
    if (before["graph_state"] != "ready" or before["integrity_status"] != "clean"
            or before["scan_status"] != "idle"):
        raise RemediationError("stale_revision", "legacy_graph_or_integrity_not_ready")
    identity = read_rows("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1")
    if len(identity) != 1 or identity[0]["workspace_id"] != LEGACY_WORKSPACE_ID:
        raise RemediationError("stale_revision", "legacy_backend_identity_missing")
    target_id = identity[0]["target_id"]
    repertoires = paged_rows("SELECT " + _REPERTOIRE_COLUMNS +
        " FROM repertoires WHERE left(id,2)<>'__' AND id>%s ORDER BY id LIMIT 64")
    lines = paged_rows("""SELECT id,repertoire_id,trained_color,start_fen,moves_json
        FROM repertoire_lines WHERE left(repertoire_id,2)<>'__' AND id>%s ORDER BY id LIMIT 64""")
    cards = paged_rows("SELECT " + ",".join("card." + column for column in _CARD_COLUMNS.split(",")) +
        " FROM cards card WHERE EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id"
        " AND link.repertoire_id=%s) AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)"
        " AND card.content_type='opening' AND card.id>%s ORDER BY card.id LIMIT 64",
        (repertoire_id,))
    links, link_cursor = [], ("", "")
    while True:
        page = read_rows("""SELECT link.repertoire_id,link.card_id,link.canonical_route_source
            FROM repertoire_cards link WHERE EXISTS(SELECT 1 FROM repertoire_cards selected
            WHERE selected.card_id=link.card_id AND selected.repertoire_id=%s)
            AND (link.repertoire_id,link.card_id)>(%s,%s)
            ORDER BY link.repertoire_id,link.card_id LIMIT 64""", (repertoire_id, *link_cursor))
        links.extend(page)
        if len(links) > MAX_SOURCE_ROWS:
            raise RemediationError("stale_revision", "legacy_capture_scope_exceeds_bound")
        if len(page) < PAGE_SIZE: break
        link_cursor = (page[-1]["repertoire_id"], page[-1]["card_id"])
    graphs = paged_rows("""SELECT * FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s
        AND (line_id,decision_index)>(%s,%s) ORDER BY line_id,decision_index LIMIT 64""",
        (repertoire_id, before["graph_generation"]), composite=True)
    games = read_rows("SELECT " + _GAME_COLUMNS + " FROM imported_games WHERE id=%s", (delivery.game_id,))
    if len(games) != 1:
        raise RemediationError("stale_revision", "legacy_game_source_missing")
    after = metadata(repertoire_id)
    if before != after:
        raise RemediationError("stale_revision", "legacy_source_changed_during_capture")
    return Capture(target_id, before, dict(repertoires=repertoires, repertoire_lines=lines,
                   cards=cards, repertoire_cards=links), graphs, games[0])


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
        source_digest=authority_source_digest(captured.tables), graph_digest=authority_graph_digest(captured.graphs),
        catalogue_digest=fingerprint([dict(repertoire_id=row["repertoire_id"], route_id=row["id"],
            policy_id=row["repertoire_id"], learner_color=row["trained_color"], starting_fen=row["start_fen"],
            moves=json.loads(row["moves_json"])) for row in sorted(
                captured.tables["repertoire_lines"], key=lambda row: row["id"])]))


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
            # A date-only marker has unknown intraday time. The end of that day
            # is a conservative bound; same-day games remain unsupported.
            latest = datetime.fromisoformat(value).replace(tzinfo=timezone.utc) + timedelta(days=1)
            return latest <= game_time
        known = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return known.tzinfo is not None and known <= game_time
    except (ValueError, TypeError, OverflowError):
        return False


def prepare_decisions(delivery, captured):
    """Verify full current source bindings; never choose among game findings."""
    token = current_token(captured, delivery.authority.repertoire_id)
    game = captured.game
    source = delivery.game_source
    if (game["provider"], game["provider_game_id"], game["content_hash"]) != (
            source.provider, source.provider_game_id, source.content_hash) or game["adaptive_excluded"]:
        raise RemediationError("stale_revision", "legacy_game_source_changed")
    if game["result"] not in {"win", "loss", "draw", "1-0", "0-1", "1/2-1/2"}:
        raise RemediationError("terminal_failure", "legacy_completed_game_required")
    actual_time = datetime.fromisoformat(game["played_at"].replace("Z", "+00:00"))
    expected_time = datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00"))
    if actual_time.tzinfo is None or actual_time != expected_time:
        raise RemediationError("stale_revision", "legacy_game_time_unknown_or_changed")
    selected = next((row for row in captured.tables["repertoires"] if row["id"] == token.repertoire_id), None)
    if selected is None or json.loads(selected.get("canonical_prefix_moves_json", "[]")):
        raise RemediationError("stale_revision", "legacy_canonical_prefix_adapter_unsupported")
    game_moves = json.loads(game["moves_json"])
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
            candidates = set()
            for graph in captured.graphs:
                if graph["line_id"] != row["id"]:
                    continue
                card = cards.get(graph["card_id"])
                if (card is None or card["revision"] != 1 or card["trained_color"] != game["color"]
                        or card.get("content_type", "opening") != "opening"
                        or card["archived"] or card["pending_validation"] or card.get("superseded_by")
                        or card["start_fen"] != graph["starting_fen"]
                        or json.loads(card["moves_json"]) != json.loads(graph["moves_json"])):
                    continue
                memberships = {link["repertoire_id"] for link in captured.tables["repertoire_cards"]
                               if link["card_id"] == card["id"]}
                physical_moves = json.loads(card["moves_json"])
                if memberships != {token.repertoire_id} or not 1 <= len(physical_moves) <= 40:
                    continue
                roots = [root for root in range(ply + 1) if
                    route_boards[root].fen(en_passant="fen") == card["start_fen"]
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
            introduced_before_game(card.get("introduced_at"), actual_time))
    return token, decisions
