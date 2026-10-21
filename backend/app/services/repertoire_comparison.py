"""Position-based repertoire matching with transposition-aware coverage."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import threading

import chess

from tempo_legacy_database.connections import background_read_connection, connection
from tempo_legacy_database.adapters.projections import repertoire_comparison as storage
from tempo_legacy_database.adapters import postgres_store
from .activity_gate import activity_gate
from .canonical_prefix import game_in_scope
from .canonical_scope_freshness import game_scope_generation


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


def _postgres_repertoire_source_signature(repertoires: list[dict]) -> str:
    with background_read_connection() as database:
        line_digest = storage.repertoire_line_digest(database)
    with background_read_connection() as database:
        card_digest = storage.opening_card_digest(database)
    return hashlib.sha256(
        json.dumps([repertoires, line_digest, card_digest], sort_keys=True).encode()
    ).hexdigest()


def _postgres_repertoire_input_rows() -> tuple[list[dict], list[dict], list[dict]]:
    with background_read_connection() as database:
        repertoires = storage.repertoire_rows(database)
    line_rows: list[dict] = []
    line_cursor = ""
    while True:
        with background_read_connection() as database:
            page = storage.line_page(database, line_cursor)
        line_rows.extend(page)
        if len(page) < 64:
            break
        line_cursor = page[-1]["id"]
    card_rows: list[dict] = []
    card_cursor = ("", "")
    while True:
        with background_read_connection() as database:
            page = storage.card_page(database, card_cursor)
        card_rows.extend(page)
        if len(page) < 64:
            break
        card_cursor = (page[-1]["repertoire_id"], page[-1]["id"])
    return repertoires, line_rows, card_rows


def _load_repertoire_index_snapshot(*, background: bool = False) -> tuple[str, list[dict], dict[str, tuple[dict[str, set[str]], set[str]]], dict[str, set[str]], dict[tuple[str, str, str], str]]:
    global _cached_signature, _cached_repertoires, _cached_graphs_by_repertoire
    global _cached_colors_by_repertoire, _cached_card_positions
    if background and postgres_store.configured():
        with background_read_connection() as database:
            repertoires = storage.repertoire_rows(database)
        signature = _postgres_repertoire_source_signature(repertoires)
        with _index_lock:
            if signature == _cached_signature:
                return (signature, _cached_repertoires, _cached_graphs_by_repertoire,
                        _cached_colors_by_repertoire, _cached_card_positions)
        repertoires, line_rows, card_rows = _postgres_repertoire_input_rows()
        if signature != _postgres_repertoire_source_signature(repertoires):
            raise RuntimeError("Repertoire changed during index loading; retry the slice")
    else:
        with connection(background=background) as database:
            repertoires = storage.repertoire_rows(database)
            line_rows = storage.all_line_rows(database)
            card_rows = storage.all_card_rows(database)
        signature = hashlib.sha256(
            json.dumps([repertoires, line_rows, card_rows], sort_keys=True).encode()
        ).hexdigest()
    with _index_lock:
        if signature == _cached_signature:
            return (
                signature,
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
            signature,
            _cached_repertoires,
            _cached_graphs_by_repertoire,
            _cached_colors_by_repertoire,
            _cached_card_positions,
        )


def _load_repertoire_index(*, background: bool = False) -> tuple[list[dict], dict[str, tuple[dict[str, set[str]], set[str]]], dict[str, set[str]], dict[tuple[str, str, str], str]]:
    _, repertoires, graphs, colors, card_positions = _load_repertoire_index_snapshot(
        background=background,
    )
    return repertoires, graphs, colors, card_positions


def _compare_game_to_repertoire(game: dict, repertoire: dict, graph: tuple[dict[str, set[str]], set[str]], card_positions: dict[tuple[str, str, str], str], *, include_assumed_decisions: bool = False) -> dict:
    expected_by_position, known_positions = graph
    board = chess.Board(game["start_fen"])
    player_is_white = game["color"] == "white"
    matched = opportunities = deepest = 0
    player_deviation = opponent_gap = out_of_book = None
    timeline: list[dict] = []
    decision_events: list[dict] = []
    prefix_moves = json.loads(repertoire.get("canonical_prefix_moves_json", "[]"))
    for ply, actual_uci in enumerate(game["moves"]):
        fen = board.fen()
        key = canonical_fen(fen)
        expected = expected_by_position.get(key, set())
        player_turn = board.turn == player_is_white
        if player_turn and expected and (include_assumed_decisions or ply >= len(prefix_moves)):
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
        if ply < len(prefix_moves):
            try:
                board.push_uci(actual_uci)
            except ValueError:
                break
            continue
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
        "canonical_prefix_revision": int(repertoire.get("canonical_prefix_revision", 0)),
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


def prepare_game_comparisons(game: dict, repertoires: list[dict], graphs: dict,
                             colors: dict, card_positions: dict) -> tuple[list[dict], dict | None]:
    """Reuse one traversal; classify scoped games separately from owned decisions."""
    all_comparisons = [
        _compare_game_to_repertoire(game, repertoire, graphs.get(repertoire["id"], ({}, set())),
                                   card_positions, include_assumed_decisions=True)
        for repertoire in repertoires if game["color"] in colors.get(repertoire["id"], set())
    ]
    all_comparisons.sort(key=lambda item: (-item["matched"], -item["deepest"], -item["is_main"], item["repertoire_id"]))
    prefixes = {repertoire["id"]: json.loads(repertoire.get("canonical_prefix_moves_json", "[]")) for repertoire in repertoires}
    scoped_comparisons = [item for item in all_comparisons
                          if game_in_scope(game["start_fen"], game["moves"], prefixes[item["repertoire_id"]])]
    evidence_owner = scoped_comparisons[0] if scoped_comparisons else next(
        (item for item in all_comparisons if any(event["card_id"] for event in item["decision_events"])), None,
    )
    return scoped_comparisons, evidence_owner


def compare_games(
    game_ids: list[str] | None = None, *, background: bool = False
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with connection(background=background) as database:
        scope_generation = game_scope_generation(database)
    repertoires, graphs_by_repertoire, colors_by_repertoire, card_positions = _load_repertoire_index(background=background)
    with connection(background=background) as database:
        games = storage.comparison_game_rows(database, game_ids)
    computed: list[tuple[dict, list[dict]]] = []
    for game in games:
        matches, evidence_owner = prepare_game_comparisons(
            game, repertoires, graphs_by_repertoire, colors_by_repertoire, card_positions,
        )
        game["decision_evidence_owner"] = evidence_owner
        computed.append((game, matches))
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        storage.begin_comparison_write(database)
        if game_scope_generation(database, lock=True) != scope_generation:
            raise RuntimeError("Repertoire membership changed during game comparison; retry")
        for game, matches in computed:
            storage.clear_game_comparison(database, game, scope_generation)
            for index, match in enumerate(matches):
                deviation = match["deviation"]
                storage.insert_repertoire_match(database, match, int(index == 0), deviation, now)
            primary = matches[0] if matches else None
            evidence_owner = game["decision_evidence_owner"]
            if evidence_owner:
                for event in evidence_owner["decision_events"]:
                    event_id = hashlib.sha256(
                        f"{game['id']}\0{evidence_owner['repertoire_id']}\0{event['ply']}".encode()
                    ).hexdigest()
                    storage.insert_decision_event(database, event_id, game, evidence_owner, event, now)
            deviation = primary["deviation"] if primary else None
            storage.save_primary_comparison(
                database, game["id"], primary["repertoire_id"] if primary else None,
                primary["classification"] if primary else "no applicable repertoire",
                deviation["ply"] if deviation else None, deviation["fen"] if deviation else None,
                deviation["expected"] if deviation else [], deviation["actual"] if deviation else None, now,
            )


def compare_all_games() -> None:
    compare_games()
