"""Position-level integrity checks and repairs for opening repertoires."""

from __future__ import annotations

import json

import chess
from backend.app.utils.hash import stable_key
from tempo_chess_core import card_id
from tempo_storage.authored_repertoires.integrity import storage
from tempo_storage.connections import read_connection
from tempo_storage.postgres import PostgresConnection

from app.chess.position_identity import position_key as canonical_fen


def _issue_id(
    repertoire_id: str,
    kind: str,
    fen_key: str | None,
    sources: list[dict] | None = None,
) -> str:
    source_key = "|".join(
        f"{source.get('type')}:{source.get('id')}" for source in (sources or [])
    )
    return stable_key("repertoire-issue", repertoire_id, kind, fen_key or source_key)


def _signature(issue: dict) -> str:
    payload = {
        "kind": issue["kind"],
        "fen_key": issue.get("fen_key"),
        "moves": sorted(issue.get("moves", [])),
        "sources": sorted(
            [
                (
                    source.get("type"),
                    source.get("id"),
                    source.get("move_index"),
                    source.get("move"),
                )
                for source in issue.get("sources", [])
            ],
            key=repr,
        ),
    }
    return stable_key(
        "repertoire-issue",
        issue["kind"],
        issue.get("fen_key"),
        *[json.dumps(payload, sort_keys=True)],
    )


def _scan_source(
    source: dict, repertoire_color: str | None
) -> tuple[dict[str, dict], list[dict]]:
    positions: dict[str, dict] = {}
    invalid: list[dict] = []
    source_label = f"{source['source_type']} {source['source_id']}"
    try:
        board = chess.Board(source["start_fen"])
        moves = json.loads(source["moves_json"])
        if not isinstance(moves, list):
            raise ValueError("moves_json must be a list")
        target = chess.WHITE if source["trained_color"] == "white" else chess.BLACK
        if source["trained_color"] not in {"white", "black"}:
            raise ValueError("trained color is unknown")
        if repertoire_color is not None and source["trained_color"] != repertoire_color:
            raise ValueError(
                f"trained color {source['trained_color']} does not match repertoire color {repertoire_color}"
            )
        last_moving_color = None
        for move_index, move_value in enumerate(moves):
            if board.turn == target:
                _record_position(positions, board, source, move_index, str(move_value))
            move = chess.Move.from_uci(str(move_value).lower())
            if move not in board.legal_moves:
                raise ValueError(f"illegal move at ply {move_index + 1}")
            last_moving_color = board.turn
            board.push(move)
        if (
            board.turn == target
            and any(board.legal_moves)
            and last_moving_color != target
        ):
            _record_position(positions, board, source, len(moves), None)
    except (TypeError, ValueError, json.JSONDecodeError, KeyError) as error:
        invalid.append(
            {
                "kind": "invalid_source",
                "fen_key": None,
                "fen": None,
                "trained_color": source.get("trained_color"),
                "moves": [],
                "sources": [
                    {
                        "type": source["source_type"],
                        "id": source["source_id"],
                        "label": source_label,
                        "error": str(error),
                    }
                ],
            }
        )
    return positions, invalid


def _record_position(
    positions: dict[str, dict],
    board: chess.Board,
    source: dict,
    move_index: int,
    move: str | None,
) -> None:
    fen_key = canonical_fen(board.fen())
    entry = positions.setdefault(
        fen_key,
        {
            "fen_key": fen_key,
            "fen": board.fen(),
            "trained_color": source["trained_color"],
            "moves": [],
            "sources": [],
        },
    )
    if move is not None:
        entry["moves"].append(move.lower())
    entry["sources"].append(
        {
            "type": source["source_type"],
            "id": source["source_id"],
            "move_index": move_index,
            "move": move.lower() if move else None,
        }
    )


def integrity_summary(database: PostgresConnection, repertoire_id: str) -> dict:
    row = storage.summary_state(database, (repertoire_id,))
    if not row:
        raise KeyError("Repertoire not found")
    issue = storage.first_issue(database, (repertoire_id,))
    count = storage.issue_count(database, (repertoire_id,))
    return {
        "status": row["integrity_status"],
        "issue_count": count,
        "first_issue_id": issue[0] if issue else None,
        "checked_at": row["integrity_checked_at"],
        "scan_status": row["scan_status"],
        "scan_generation": row["scan_generation"],
        "scan_progress": {
            "completed": row["scan_completed_sources"],
            "total": row["scan_total_sources"],
        },
        "last_scan_error": row["scan_error"],
    }


def list_integrity_issues(
    database: PostgresConnection, repertoire_id: str
) -> list[dict]:
    rows = storage.issue_details(database, (repertoire_id,))
    results = []
    for row in rows:
        sources = json.loads(row["sources_json"])
        moves = json.loads(row["moves_json"])
        stats = {
            move: {"uci": move, "line_count": 0, "card_count": 0, "review_count": 0}
            for move in moves
        }
        for source in sources:
            move = source.get("move")
            if move not in stats:
                continue
            bucket = stats[move]
            if source.get("type") == "line":
                bucket["line_count"] += 1
            elif source.get("type") == "card":
                bucket["card_count"] += 1
                bucket["review_count"] += storage.card_review_count(
                    database, (source["id"],)
                )
        results.append(
            {
                "id": row["id"],
                "kind": row["kind"],
                "fen_key": row["fen_key"],
                "fen": row["fen"],
                "trained_color": row["trained_color"],
                "signature": row["signature"],
                "moves": list(stats.values()),
                "sources": sources,
            }
        )
    return results


def _transform_source(
    start_fen: str, moves: list[str], trained_color: str, target_fen: str, selected: str
) -> tuple[list[str], bool]:
    board = chess.Board(start_fen)
    target = chess.WHITE if trained_color == "white" else chess.BLACK
    transformed = list(moves)
    for index, move_value in enumerate(list(moves)):
        if board.turn == target and canonical_fen(board.fen()) == target_fen:
            if move_value.lower() == selected:
                return transformed, False
            transformed = transformed[:index] + [selected]
            return transformed, True
        board.push_uci(move_value)
    if board.turn == target and canonical_fen(board.fen()) == target_fen:
        transformed.append(selected)
        return transformed, True
    return transformed, False


def _archive_unsupported_card(
    database: PostgresConnection, repertoire_id: str, card_identifier: str
) -> bool:
    """Detach an already-computed unsupported card without chess traversal."""

    card = storage.active_card_authority(database, (card_identifier,))
    if not card:
        return False
    membership = storage.membership_authority(
        database, (repertoire_id, card_identifier)
    )
    if membership is not None and membership["canonical_route_source"]:
        return False
    if membership is None and (
        card["repertoire_id"] != repertoire_id or card["canonical_route_source"]
    ):
        return False
    remaining = storage.remaining_membership(database, (card_identifier, repertoire_id))
    if remaining and card["repertoire_id"] == repertoire_id:
        storage.reassign_card_owner(
            database, (remaining["repertoire_id"], card_identifier)
        )
    elif not remaining and (
        not card["canonical_route_source"] or card["repertoire_id"] == repertoire_id
    ):
        storage.archive_unsupported_card(database, (card_identifier,))
    storage.delete_membership(database, (repertoire_id, card_identifier))
    return True


def prepare_issue_resolution(
    repertoire_id: str,
    issue_id: str,
    signature: str,
    selected_move: str,
) -> dict:
    """Read and compute a guided repair after closing its read connection."""

    with read_connection() as database:
        issue_row = storage.issue(database, (issue_id, repertoire_id))
        if not issue_row:
            raise KeyError("Integrity issue not found")
        issue = dict(issue_row)
        source_descriptors = json.loads(issue["sources_json"])
        line_rows = {
            row["id"]: dict(row)
            for row in storage.prepared_lines(database, (repertoire_id,))
        }
        card_rows = {
            row["id"]: dict(row)
            for row in storage.prepared_cards(database, (repertoire_id, repertoire_id))
        }
    if issue["signature"] != signature:
        raise RuntimeError("This integrity issue changed; refresh and try again")
    if not issue["fen"]:
        raise ValueError(
            "Invalid source issues must be repaired by editing or removing the source"
        )
    board = chess.Board(issue["fen"])
    selected = selected_move.lower()
    try:
        move = chess.Move.from_uci(selected)
    except ValueError as error:
        raise ValueError("Selected move is not valid UCI") from error
    if move not in board.legal_moves:
        raise ValueError("Selected move is illegal from this position")

    changed_lines: dict[str, list[str]] = {}
    changed_cards: dict[str, list[str]] = {}
    for source in source_descriptors:
        if source.get("type") == "line" and source.get("id") in line_rows:
            row = line_rows[source["id"]]
            moves, changed = _transform_source(
                row["start_fen"],
                json.loads(row["moves_json"]),
                row["trained_color"],
                issue["fen_key"],
                selected,
            )
            if changed:
                changed_lines[row["id"]] = moves
        elif source.get("type") == "card" and source.get("id") in card_rows:
            row = card_rows[source["id"]]
            moves, changed = _transform_source(
                row["start_fen"],
                json.loads(row["moves_json"]),
                row.get("trained_color") or issue["trained_color"],
                issue["fen_key"],
                selected,
            )
            if changed:
                changed_cards[row["id"]] = moves

    line_routes = [
        (
            canonical_fen(row["start_fen"]),
            changed_lines.get(row_identifier, json.loads(row["moves_json"])),
        )
        for row_identifier, row in line_rows.items()
    ]
    unsupported_card_ids: list[str] = []
    for row_identifier, row in card_rows.items():
        final_moves = changed_cards.get(row_identifier, json.loads(row["moves_json"]))
        final_card_identifier = (
            card_id(row["start_fen"], final_moves)
            if row_identifier in changed_cards
            else row_identifier
        )
        if not any(
            canonical_fen(row["start_fen"]) == line_start_fen
            and line_moves[: len(final_moves)] == final_moves
            for line_start_fen, line_moves in line_routes
        ):
            unsupported_card_ids.append(final_card_identifier)
    return {
        "repertoire_id": repertoire_id,
        "issue_id": issue_id,
        "signature": signature,
        "changed_lines": changed_lines,
        "changed_cards": changed_cards,
        "expected_line_moves": {
            source_id: line_rows[source_id]["moves_json"] for source_id in changed_lines
        },
        "expected_card_moves": {
            source_id: card_rows[source_id]["moves_json"] for source_id in changed_cards
        },
        "unsupported_card_ids": unsupported_card_ids,
    }
