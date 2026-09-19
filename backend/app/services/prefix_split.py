"""Atomic splitting of an overlong opening prefix into two study cards."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3

import chess

from .cards import card_id
from .review_service import ensure_card_queued_after


def _trained_color_for_card(database: sqlite3.Connection, card: sqlite3.Row) -> str:
    if card["trained_color"] in {"white", "black"}:
        return card["trained_color"]
    line = database.execute(
        """SELECT trained_color FROM repertoire_lines
           WHERE repertoire_id=? ORDER BY created_at,id LIMIT 1""",
        (card["repertoire_id"],),
    ).fetchone()
    if not line or line["trained_color"] not in {"white", "black"}:
        raise ValueError("The trained color for this opening card is unknown")
    return line["trained_color"]


def _legal_split(card: sqlite3.Row, trained_color: str) -> dict:
    moves = json.loads(card["moves_json"])
    board = chess.Board(card["start_fen"])
    trained_chess_color = chess.WHITE if trained_color == "white" else chess.BLACK
    trained_move_indices: list[int] = []
    positions_before_moves: list[str] = []
    for move_index, move_uci in enumerate(moves):
        positions_before_moves.append(board.fen())
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            raise ValueError("The saved opening card contains an illegal move")
        if board.turn == trained_chess_color:
            trained_move_indices.append(move_index)
        board.push(move)
    if len(trained_move_indices) < 2 or trained_move_indices[-1] != len(moves) - 1:
        raise ValueError(
            "A prefix needs at least two completed player decisions before it can be shortened"
        )
    shortened_length = trained_move_indices[-2] + 1
    shortened_moves = moves[:shortened_length]
    continuation_moves = moves[shortened_length:]
    continuation_board = chess.Board(card["start_fen"])
    for move_uci in shortened_moves:
        continuation_board.push_uci(move_uci)
    tested_player_moves = 0
    test_board = continuation_board.copy()
    for move_uci in continuation_moves:
        if test_board.turn == trained_chess_color:
            tested_player_moves += 1
        test_board.push_uci(move_uci)
    if tested_player_moves != 1:
        raise ValueError("The continuation must test exactly one player decision")
    return {
        "shortened_card_id": card_id(card["start_fen"], shortened_moves),
        "shortened_starting_fen": card["start_fen"],
        "shortened_moves": shortened_moves,
        "parent_player_moves": len(trained_move_indices) - 1,
        "continuation_card_id": card_id(continuation_board.fen(), continuation_moves),
        "continuation_starting_fen": continuation_board.fen(),
        "continuation_moves": continuation_moves,
        "tested_player_moves": tested_player_moves,
    }


def _response(
    source_card_id: str,
    source_revision: int,
    split: dict,
    *,
    applied: bool,
    idempotent: bool = False,
) -> dict:
    return {
        "source_card_id": source_card_id,
        "source_revision": source_revision,
        "parent": {
            "card_id": split["shortened_card_id"],
            "starting_fen": split["shortened_starting_fen"],
            "moves": split["shortened_moves"],
            "tested_player_moves": max(1, split.get("parent_player_moves", 1)),
        },
        "continuation": {
            "card_id": split["continuation_card_id"],
            "starting_fen": split["continuation_starting_fen"],
            "moves": split["continuation_moves"],
            "tested_player_moves": split["tested_player_moves"],
        },
        "applied": applied,
        "idempotent": idempotent,
    }


def preview_prefix_split(database: sqlite3.Connection, source_card_id: str) -> dict:
    card = database.execute("SELECT * FROM cards WHERE id=?", (source_card_id,)).fetchone()
    if not card:
        raise KeyError("Card not found")
    if card["content_type"] != "opening" or card["kind"] != "prefix":
        raise ValueError("Only opening prefix cards can be shortened this way")
    trained_color = _trained_color_for_card(database, card)
    split = _legal_split(card, trained_color)
    return _response(source_card_id, int(card["revision"]), split, applied=False)


def _copy_card(
    database: sqlite3.Connection,
    source_card: sqlite3.Row,
    values: dict,
) -> None:
    columns = [row[1] for row in database.execute("PRAGMA table_info(cards)")]
    source_values = dict(source_card)
    source_values.update(values)
    database.execute(
        f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
        tuple(source_values.get(column) for column in columns),
    )


def apply_prefix_split(
    database: sqlite3.Connection,
    source_card_id: str,
    expected_revision: int,
) -> dict:
    prior = database.execute(
        "SELECT * FROM prefix_splits WHERE source_card_id=?", (source_card_id,)
    ).fetchone()
    source_card = database.execute(
        "SELECT * FROM cards WHERE id=?", (source_card_id,)
    ).fetchone()
    if not source_card:
        raise KeyError("Card not found")
    trained_color = _trained_color_for_card(database, source_card)
    split = _legal_split(source_card, trained_color)
    if prior:
        return _response(
            source_card_id,
            int(prior["source_revision"]),
            split,
            applied=True,
            idempotent=True,
        )
    if int(source_card["revision"]) != expected_revision:
        raise RuntimeError("The card changed after this split was previewed")

    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    shortened_card_id = split["shortened_card_id"]
    continuation_card_id = split["continuation_card_id"]
    linked_repertoire_ids = [
        row[0]
        for row in database.execute(
            "SELECT repertoire_id FROM repertoire_cards WHERE card_id=?",
            (source_card_id,),
        )
    ] or [source_card["repertoire_id"]]

    shortened_card = database.execute(
        "SELECT * FROM cards WHERE id=?", (shortened_card_id,)
    ).fetchone()
    if not shortened_card:
        _copy_card(
            database,
            source_card,
            {
                "id": shortened_card_id,
                "moves_json": json.dumps(split["shortened_moves"]),
                "revision": int(source_card["revision"]) + 1,
                "archived": 0,
                "superseded_by": None,
            },
        )
    database.execute(
        """INSERT OR IGNORE INTO card_revisions(
               card_id,revision,start_fen,moves_json,history_mode,created_at
           ) VALUES(?,?,?,?,?,?)""",
        (
            source_card_id,
            int(source_card["revision"]) + 1,
            source_card["start_fen"],
            source_card["moves_json"],
            "preserve",
            now,
        ),
    )
    database.execute(
        "UPDATE reviews SET card_id=? WHERE card_id=?",
        (shortened_card_id, source_card_id),
    )
    queued_entries = database.execute(
        "SELECT id,queue_date,cycle FROM daily_queue WHERE card_id=?",
        (source_card_id,),
    ).fetchall()
    for queue_entry in queued_entries:
        collision = database.execute(
            "SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=? AND cycle=?",
            (
                queue_entry["queue_date"],
                shortened_card_id,
                queue_entry["cycle"],
            ),
        ).fetchone()
        if collision:
            database.execute(
                "UPDATE daily_queue SET status='complete' WHERE id=?",
                (queue_entry["id"],),
            )
        else:
            database.execute(
                "UPDATE daily_queue SET card_id=? WHERE id=?",
                (shortened_card_id, queue_entry["id"]),
            )

    continuation_card = database.execute(
        "SELECT 1 FROM cards WHERE id=?", (continuation_card_id,)
    ).fetchone()
    if not continuation_card:
        _copy_card(
            database,
            source_card,
            {
                "id": continuation_card_id,
                "kind": "response",
                "start_fen": split["continuation_starting_fen"],
                "moves_json": json.dumps(split["continuation_moves"]),
                "state": "learning",
                "due_date": today,
                "interval_days": 0,
                "repetitions": 0,
                "lapses": 0,
                "fsrs_card_json": None,
                "first_correct_at": None,
                "reinforcement_pending": 0,
                "stability": 0,
                "guided_review": 1,
                "scheduling_mode": "normal",
                "hard_correct_streak": 0,
                "recent_attempts_json": "[]",
                "archived": 0,
                "superseded_by": None,
                "source_ref": f"prefix-split:{source_card_id}",
                "source_fen": source_card["start_fen"],
                "revision": 1,
                "introduced_at": now,
                "trained_color": trained_color,
            },
        )
    for repertoire_id in linked_repertoire_ids:
        database.execute(
            "INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
            (repertoire_id, shortened_card_id),
        )
        database.execute(
            "INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES(?,?)",
            (repertoire_id, continuation_card_id),
        )
    database.execute("DELETE FROM repertoire_cards WHERE card_id=?", (source_card_id,))
    database.execute(
        "UPDATE cards SET archived=1,superseded_by=? WHERE id=?",
        (shortened_card_id, source_card_id),
    )
    ensure_card_queued_after(
        database, continuation_card_id, after_cards=4, attempt_state="guided"
    )
    database.execute(
        """INSERT INTO prefix_splits(
               source_card_id,source_revision,shortened_card_id,
               continuation_card_id,created_at
           ) VALUES(?,?,?,?,?)""",
        (
            source_card_id,
            expected_revision,
            shortened_card_id,
            continuation_card_id,
            now,
        ),
    )
    return _response(
        source_card_id,
        expected_revision,
        split,
        applied=True,
        idempotent=False,
    )
