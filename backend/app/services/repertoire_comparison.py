"""Position-based repertoire matching with transposition-aware coverage."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import threading

import chess

from ..database import connection
from .activity_gate import activity_gate


_index_lock = threading.Lock()
_cached_signature = ""
_cached_repertoires: list[dict] = []
_cached_graphs_by_repertoire: dict[str, tuple[dict[str, set[str]], set[str]]] = {}
_cached_colors_by_repertoire: dict[str, set[str]] = {}
_cached_card_positions: dict[tuple[str, str, str], str] = {}


def canonical_fen(fen: str) -> str:
    return " ".join(chess.Board(fen).fen().split()[:4])


def _position_graph(lines: list[dict]) -> tuple[dict[str, set[str]], set[str]]:
    expected_moves: dict[str, set[str]] = defaultdict(set)
    known_positions: set[str] = set()
    for line in lines:
        try:
            board = chess.Board(line["start_fen"])
            known_positions.add(canonical_fen(board.fen()))
            for move_uci in line["moves"]:
                position = canonical_fen(board.fen())
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                expected_moves[position].add(move_uci)
                board.push(move)
                known_positions.add(canonical_fen(board.fen()))
        except (ValueError, TypeError):
            continue
    return expected_moves, known_positions


def _card_position_index(rows: list[dict]) -> dict[tuple[str, str, str], str]:
    positions: dict[tuple[str, str, str], str] = {}
    for row in rows:
        try:
            board = chess.Board(row["start_fen"])
            for move_uci in json.loads(row["moves_json"]):
                positions.setdefault(
                    (row["repertoire_id"], canonical_fen(board.fen()), move_uci),
                    row["id"],
                )
                board.push_uci(move_uci)
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return positions


def _load_repertoire_index(*, background: bool = False) -> tuple[list[dict], dict[str, tuple[dict[str, set[str]], set[str]]], dict[str, set[str]], dict[tuple[str, str, str], str]]:
    global _cached_signature, _cached_repertoires, _cached_graphs_by_repertoire
    global _cached_colors_by_repertoire, _cached_card_positions
    with connection(background=background) as database:
        repertoires = [dict(row) for row in database.execute(
            "SELECT id,is_main FROM repertoires WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__') ORDER BY id"
        )]
        line_rows = [dict(row) for row in database.execute(
            "SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines ORDER BY id"
        )]
        card_rows = [dict(row) for row in database.execute(
            """SELECT rc.repertoire_id,c.id,c.start_fen,c.moves_json FROM cards c
               JOIN repertoire_cards rc ON rc.card_id=c.id
               WHERE c.archived=0 AND c.content_type='opening'
               ORDER BY rc.repertoire_id,c.id"""
        )]
    signature = hashlib.sha256(
        json.dumps([repertoires, line_rows, card_rows], sort_keys=True).encode()
    ).hexdigest()
    with _index_lock:
        if signature == _cached_signature:
            return (
                _cached_repertoires,
                _cached_graphs_by_repertoire,
                _cached_colors_by_repertoire,
                _cached_card_positions,
            )
        lines_by_repertoire: dict[str, list[dict]] = defaultdict(list)
        colors_by_repertoire: dict[str, set[str]] = defaultdict(set)
        for row in line_rows:
            line = {**row, "moves": json.loads(row["moves_json"])}
            lines_by_repertoire[row["repertoire_id"]].append(line)
            colors_by_repertoire[row["repertoire_id"]].add(row["trained_color"])
        _cached_signature = signature
        _cached_repertoires = repertoires
        _cached_graphs_by_repertoire = {
            repertoire_id: _position_graph(lines)
            for repertoire_id, lines in lines_by_repertoire.items()
        }
        _cached_colors_by_repertoire = dict(colors_by_repertoire)
        _cached_card_positions = _card_position_index(card_rows)
        return (
            _cached_repertoires,
            _cached_graphs_by_repertoire,
            _cached_colors_by_repertoire,
            _cached_card_positions,
        )


def _compare_game_to_repertoire(game: dict, repertoire: dict, graph: tuple[dict[str, set[str]], set[str]], card_positions: dict[tuple[str, str, str], str]) -> dict:
    expected_by_position, known_positions = graph
    board = chess.Board(game["start_fen"])
    player_is_white = game["color"] == "white"
    matched = opportunities = deepest = 0
    player_deviation = opponent_gap = out_of_book = None
    timeline: list[dict] = []
    decision_events: list[dict] = []
    for ply, actual_uci in enumerate(game["moves"]):
        fen = board.fen()
        key = canonical_fen(fen)
        expected = expected_by_position.get(key, set())
        player_turn = board.turn == player_is_white
        if player_turn and expected:
            expected_move = actual_uci if actual_uci in expected else next(
                (move for move in sorted(expected) if (repertoire["id"], key, move) in card_positions),
                sorted(expected)[0],
            )
            decision_events.append({
                "ply": ply,
                "fen_key": key,
                "expected_uci": expected_move,
                "actual_uci": actual_uci,
                "outcome": "success" if actual_uci in expected else "miss",
                "card_id": card_positions.get((repertoire["id"], key, expected_move)),
            })
        if key in known_positions:
            if not expected:
                if out_of_book is None:
                    out_of_book = ply
                timeline.append({"ply": ply, "kind": "out of book"})
            elif actual_uci in expected:
                deepest = max(deepest, ply + 1)
                if player_turn:
                    opportunities += 1
                    matched += 1
                    timeline.append({"ply": ply, "kind": "followed"})
            elif player_turn:
                opportunities += 1
                if player_deviation is None:
                    player_deviation = {
                        "ply": ply,
                        "fen": fen,
                        "expected": sorted(expected),
                        "actual": actual_uci,
                    }
                timeline.append({"ply": ply, "kind": "player deviation"})
            else:
                if opponent_gap is None:
                    opponent_gap = ply
                timeline.append({"ply": ply, "kind": "opponent gap"})
        elif deepest > 0 or timeline:
            if out_of_book is None:
                out_of_book = ply
            timeline.append({"ply": ply, "kind": "out of book"})
        try:
            board.push_uci(actual_uci)
        except ValueError:
            break
    classification = (
        "player deviation"
        if player_deviation
        else "opponent repertoire gap"
        if opponent_gap is not None
        else "out of book"
        if out_of_book is not None
        else "covered"
        if matched or canonical_fen(game["start_fen"]) in known_positions
        else "no applicable repertoire"
    )
    expected = set(player_deviation["expected"]) if player_deviation else set()
    return {
        "game_id": game["id"],
        "repertoire_id": repertoire["id"],
        "is_main": repertoire["is_main"],
        "classification": classification,
        "matched": matched,
        "opportunities": opportunities,
        "deepest": deepest,
        "deviation": player_deviation,
        "deviation_card_id": next(
            (
                card_positions[(repertoire["id"], canonical_fen(player_deviation["fen"]), move)]
                for move in sorted(expected)
                if (repertoire["id"], canonical_fen(player_deviation["fen"]), move) in card_positions
            ),
            None,
        ) if player_deviation else None,
        "opponent_gap": opponent_gap,
        "out_of_book": out_of_book,
        "timeline": timeline,
        "decision_events": decision_events,
    }


def compare_games(
    game_ids: list[str] | None = None, *, background: bool = False
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    repertoires, graphs_by_repertoire, colors_by_repertoire, card_positions = _load_repertoire_index(background=background)
    with connection(background=background) as database:
        where = "" if game_ids is None else f" WHERE id IN ({','.join('?' for _ in game_ids)})"
        games = [
            {**dict(row), "moves": json.loads(row["moves_json"])}
            for row in database.execute(f"SELECT * FROM imported_games{where}", game_ids or [])
        ]
    computed: list[tuple[dict, list[dict]]] = []
    for game in games:
        matches = [
            _compare_game_to_repertoire(
                game,
                repertoire,
                graphs_by_repertoire.get(repertoire["id"], ({}, set())),
                card_positions,
            )
            for repertoire in repertoires
            if game["color"] in colors_by_repertoire.get(repertoire["id"], set())
        ]
        matches.sort(key=lambda item: (-item["matched"], -item["deepest"], -item["is_main"], item["repertoire_id"]))
        computed.append((game, matches))
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        for game, matches in computed:
            database.execute("DELETE FROM game_repertoire_matches WHERE game_id=?", (game["id"],))
            database.execute("DELETE FROM repertoire_decision_events WHERE game_id=?", (game["id"],))
            for index, match in enumerate(matches):
                deviation = match["deviation"]
                database.execute(
                    """INSERT INTO game_repertoire_matches VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        match["game_id"], match["repertoire_id"], int(index == 0), match["classification"],
                        match["matched"], match["opportunities"], match["deepest"],
                        deviation["ply"] if deviation else None, deviation["fen"] if deviation else None,
                        json.dumps(deviation["expected"] if deviation else []), deviation["actual"] if deviation else None,
                        match["deviation_card_id"], match["opponent_gap"], match["out_of_book"],
                        json.dumps(match["timeline"]), now,
                    ),
                )
            primary = matches[0] if matches else None
            if primary:
                for event in primary["decision_events"]:
                    event_id = hashlib.sha256(
                        f"{game['id']}\0{primary['repertoire_id']}\0{event['ply']}".encode()
                    ).hexdigest()
                    database.execute(
                        """INSERT INTO repertoire_decision_events(
                               id,game_id,repertoire_id,card_id,ply,fen_key,
                               expected_uci,actual_uci,outcome,played_at,updated_at
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            event_id, game["id"], primary["repertoire_id"],
                            event["card_id"], event["ply"], event["fen_key"],
                            event["expected_uci"], event["actual_uci"],
                            event["outcome"], game["played_at"], now,
                        ),
                    )
            deviation = primary["deviation"] if primary else None
            database.execute(
                """INSERT INTO repertoire_comparisons(game_id,repertoire_id,classification,divergence_ply,divergence_fen,expected_json,actual_uci,updated_at)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET repertoire_id=excluded.repertoire_id,
                   classification=excluded.classification,divergence_ply=excluded.divergence_ply,divergence_fen=excluded.divergence_fen,
                   expected_json=excluded.expected_json,actual_uci=excluded.actual_uci,updated_at=excluded.updated_at""",
                (
                    game["id"], primary["repertoire_id"] if primary else None,
                    primary["classification"] if primary else "no applicable repertoire",
                    deviation["ply"] if deviation else None, deviation["fen"] if deviation else None,
                    json.dumps(deviation["expected"] if deviation else []), deviation["actual"] if deviation else None, now,
                ),
            )


def compare_all_games() -> None:
    compare_games()
