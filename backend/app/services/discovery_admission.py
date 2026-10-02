"""Engine-backed repertoire continuation previews and durable card admission."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json

import chess

from ..database import background_read_connection, connection, read_connection
from .. import postgres_store
from .activity_gate import activity_gate
from .cards import card_id
from .durable_tasks import (
    complete_task_slice_in_transaction,
    enqueue_compact_postgres_task_in_transaction,
    enqueue_task,
    enqueue_task_in_transaction,
    lock_current_slice,
)
from .review_service import ensure_card_queued_after
from .threat_pipeline import ENGINE_VERSION, NETWORK_VERSION, report_from_json, validate_analysis_report
from .threat_validation import AnalysisRequest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _position_key(board: chess.Board) -> tuple:
    # python-chess is pinned; this avoids serializing every repertoire position
    # and retains the same castling and legal en-passant distinctions as EPD.
    return board._transposition_key()


def _pawn_signature(board: chess.Board) -> tuple[int, int]:
    return tuple(board.pawns & board.occupied_co[color] for color in (chess.BLACK, chess.WHITE))


def _piece_signature(board: chess.Board) -> tuple[int, ...]:
    non_pawn_boards = (board.knights, board.bishops, board.rooks, board.queens, board.kings)
    return tuple(pieces & board.occupied_co[color]
                 for color in (chess.BLACK, chess.WHITE) for pieces in non_pawn_boards)


def _overlapping_pieces(first: tuple[int, ...], second: tuple[int, ...]) -> int:
    return sum((left & right).bit_count() for left, right in zip(first, second))


def _learner_pawns(board: chess.Board, learner_is_white: bool) -> int:
    return board.pawns & board.occupied_co[learner_is_white]


def _rank_candidates(candidates: list[dict]) -> list[dict]:
    """Keep engine quality as the gate, then choose the strongest familiar move."""
    eligible = [candidate for candidate in candidates
                if candidate["loss_cp"] is None
                or candidate["loss_cp"] <= (100 if candidate["familiar"] else 30)]
    eligible.sort(key=lambda candidate: (
        not candidate["familiar"],
        candidate["loss_cp"] if candidate["loss_cp"] is not None else 0,
        candidate["move_uci"],
    ))
    return eligible


def _comparable_move_examples(examples: list[dict], board: chess.Board,
                              learner_color: str, move_uci: str) -> list[dict]:
    learner_pawns = _learner_pawns(board, learner_color == "white")
    piece_positions = _piece_signature(board)
    return [example for example in examples
            if example["learner_turn"] and example["next_move"] == move_uci
            and example["learner_pawns"] == learner_pawns
            and _overlapping_pieces(example["pieces"], piece_positions) >= 8]


def _comparable_move_example(examples: list[dict], board: chess.Board,
                             learner_color: str, move_uci: str) -> dict | None:
    return next(iter(_comparable_move_examples(examples, board, learner_color, move_uci)), None)


def _source_game(database, opportunity) -> dict | None:
    evidence = json.loads(opportunity["evidence_json"])
    for support in evidence.get("findings", []):
        game_id = support.get("game_id")
        mistake_ply = support.get("mistake_ply")
        if game_id is None or mistake_ply is None:
            continue
        game = database.execute(
            """SELECT id,start_fen,moves_json,color,analysis_version FROM imported_games
               WHERE id=? AND adaptive_excluded=0 AND analysis_state='ready'""",
            (game_id,),
        ).fetchone()
        if not game:
            continue
        return {**dict(game), "ply": int(mistake_ply)}
    if opportunity["kind"] == "missing_response" and opportunity["opponent_move_uci"]:
        node_id = evidence.get("coverage_node_id")
        node = database.execute(
            "SELECT fen_key,routes_json FROM repertoire_coverage_nodes WHERE id=? AND repertoire_id=?",
            (node_id, opportunity["repertoire_id"]),
        ).fetchone()
        if not node:
            return None
        routes = json.loads(node["routes_json"])
        lines = database.execute(
            "SELECT start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
            (opportunity["repertoire_id"],),
        ).fetchall()
        return _coverage_source_game(opportunity, node_id, node, lines, routes)
    return None


def _coverage_source_game(opportunity, node_id: str, node, lines, routes: list) -> dict | None:
    """Resolve a coverage route from its supplied source snapshot."""
    for route in sorted(routes, key=lambda moves: (len(moves), moves)):
        for line in lines:
            if json.loads(line["moves_json"])[:len(route)] != route:
                continue
            board = chess.Board(line["start_fen"])
            try:
                for move_uci in route:
                    board.push_uci(move_uci)
                if _key(board) != node["fen_key"]:
                    continue
                board.push_uci(opportunity["opponent_move_uci"])
            except ValueError:
                continue
            if board.turn != (line["trained_color"] == "white"):
                continue
            decision_route = [*route, opportunity["opponent_move_uci"]]
            return {"id": f"coverage:{node_id}", "source_kind": "coverage",
                    "coverage_node_id": node_id, "start_fen": line["start_fen"],
                    "moves_json": json.dumps(decision_route),
                    "color": line["trained_color"], "analysis_version": 0,
                    "ply": len(decision_route)}
    return None


def _background_source_game(opportunity) -> dict | None:
    """Read bounded source snapshots, then traverse coverage routes outside PostgreSQL."""
    evidence = json.loads(opportunity["evidence_json"])
    for support in evidence.get("findings", []):
        game_id = support.get("game_id")
        mistake_ply = support.get("mistake_ply")
        if game_id is None or mistake_ply is None:
            continue
        with background_read_connection() as database:
            game = database.execute(
                """SELECT id,start_fen,moves_json,color,analysis_version FROM imported_games
                   WHERE id=? AND adaptive_excluded=0 AND analysis_state='ready'""",
                (game_id,),
            ).fetchone()
        if game:
            return {**dict(game), "ply": int(mistake_ply)}
    if opportunity["kind"] != "missing_response" or not opportunity["opponent_move_uci"]:
        return None
    node_id = evidence.get("coverage_node_id")
    with background_read_connection() as database:
        node = database.execute(
            "SELECT fen_key,routes_json FROM repertoire_coverage_nodes WHERE id=? AND repertoire_id=?",
            (node_id, opportunity["repertoire_id"]),
        ).fetchone()
    if node is None:
        return None
    with background_read_connection() as database:
        lines = database.execute(
            "SELECT start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
            (opportunity["repertoire_id"],),
        ).fetchall()
    return _coverage_source_game(opportunity, node_id, node, lines, json.loads(node["routes_json"]))


def _full_history_request(game: dict) -> tuple[chess.Board, AnalysisRequest]:
    board = chess.Board(game["start_fen"])
    all_moves = json.loads(game["moves_json"])
    if game["ply"] < 0 or game["ply"] > len(all_moves):
        raise ValueError("Source game does not contain the discovered decision")
    prefix = tuple(all_moves[:game["ply"]])
    for move_uci in prefix:
        board.push_uci(move_uci)
    if board.turn != (game["color"] == "white"):
        raise ValueError("The analyzed decision is not the learner's turn")
    return board, AnalysisRequest(
        position_start_fen=game["start_fen"], position_prefix_uci=prefix,
        engine_version=ENGINE_VERSION, network_version=NETWORK_VERSION,
        depth=14, multipv=5,
    )


def execute_recommendation_request_slice(task: dict) -> None:
    """Persist one full-history engine request in a bounded database slice."""
    activity_gate.wait_for_foreground()
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        opportunity = database.execute(
            """SELECT * FROM repertoire_opportunities WHERE id=? AND status='active'
               AND kind IN ('post_gap_weakness','missing_response') AND card_id IS NULL""",
            (task["payload"]["opportunity_id"],),
        ).fetchone()
        game = _source_game(database, opportunity) if opportunity and not postgres_store.configured() else None
    if opportunity and postgres_store.configured():
        game = _background_source_game(opportunity)
    if not game:
        return
    try:
        _, request = _full_history_request(game)
    except (ValueError, KeyError, TypeError):
        return
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if "id" in task:
            if postgres_store.configured():
                if not lock_current_slice(database, task):
                    return
            else:
                lease = database.execute(
                    "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
                ).fetchone()
                if (not lease or lease["generation"] != task["generation"]
                        or lease["lease_token"] != task["lease_token"]):
                    return
        current = database.execute(
            "SELECT status,card_id FROM repertoire_opportunities WHERE id=?",
            (task["payload"]["opportunity_id"],),
        ).fetchone()
        if not current or current["status"] != "active" or current["card_id"]:
            return
        database.execute(
            """INSERT OR IGNORE INTO threat_analysis_requests(
                 id,request_json,created_at,updated_at) VALUES(?,?,?,?)""",
            (request.request_id, json.dumps(asdict(request)), _now(), _now()),
        )
        if game.get("source_kind") == "coverage":
            database.execute(
                """INSERT INTO coverage_discovery_recommendation_requests(
                     opportunity_id,request_id,coverage_node_id,route_json,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(opportunity_id) DO UPDATE SET request_id=excluded.request_id,
                     coverage_node_id=excluded.coverage_node_id,route_json=excluded.route_json,
                     updated_at=excluded.updated_at""",
                (task["payload"]["opportunity_id"], request.request_id,
                 game["coverage_node_id"], game["moves_json"], _now(), _now()),
            )
        else:
            database.execute(
                """INSERT INTO discovery_recommendation_requests(
                 opportunity_id,request_id,source_game_id,source_ply,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(opportunity_id) DO UPDATE SET
                 request_id=excluded.request_id,source_game_id=excluded.source_game_id,
                 source_ply=excluded.source_ply,updated_at=excluded.updated_at""",
                (task["payload"]["opportunity_id"], request.request_id, game["id"],
                 game["ply"], _now(), _now()),
            )


def _repertoire_positions(lines: list[dict], learner_color: str) -> tuple[list[dict], set[str]]:
    examples = []
    accepted_moves = set()
    for line in lines:
        if line.get("trained_color", learner_color) != learner_color:
            continue
        board = chess.Board(line["start_fen"])
        for move_uci in json.loads(line["moves_json"]):
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                break
            examples.append({"position_key": _position_key(board), "pawn": _pawn_signature(board),
                             "learner_pawns": _learner_pawns(board, learner_color == "white"),
                             "pieces": _piece_signature(board), "line_id": line["id"],
                             "line_name": line["name"], "next_move": move_uci,
                             "learner_turn": board.turn == (learner_color == "white")})
            board.push(move)
        examples.append({"position_key": _position_key(board), "pawn": _pawn_signature(board),
                         "learner_pawns": _learner_pawns(board, learner_color == "white"),
                         "pieces": _piece_signature(board), "line_id": line["id"],
                         "line_name": line["name"], "next_move": None,
                         "learner_turn": board.turn == (learner_color == "white")})
    return examples, accepted_moves


@lru_cache(maxsize=8)
def _cached_repertoire_positions(
    line_snapshot: tuple[tuple[str, str, str, str, str], ...], learner_color: str,
) -> tuple[list[dict], set[str]]:
    fields = ("id", "name", "start_fen", "moves_json", "trained_color")
    return _repertoire_positions(
        [dict(zip(fields, values)) for values in line_snapshot], learner_color,
    )


def recommend_missing_continuations(opportunity_id: str) -> dict:
    with read_connection() as database:
        opportunity = database.execute(
            """SELECT * FROM repertoire_opportunities WHERE id=? AND status='active'""",
            (opportunity_id,),
        ).fetchone()
        if not opportunity:
            raise KeyError("Active discovery not found")
        if opportunity["card_id"]:
            raise ValueError("This discovery already has a saved decision card")
        game = _source_game(database, opportunity)
        lines = [dict(row) for row in database.execute(
            "SELECT id,name,start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=?",
            (opportunity["repertoire_id"],),
        )]
        fingerprint = opportunity["evidence_fingerprint"]
        repertoire_id = opportunity["repertoire_id"]
    if not game:
        return {"state": "unavailable" if opportunity["kind"] == "missing_response" else "waiting",
                "opportunity_id": opportunity_id,
                "reason": ("No legal repertoire route reaches this gap. Rebuild coverage or inspect it in Builder"
                           if opportunity["kind"] == "missing_response"
                           else "An analyzed source game is not available yet"), "candidates": []}
    try:
        board, request = _full_history_request(game)
    except (ValueError, KeyError, TypeError):
        return {"state": "unavailable", "opportunity_id": opportunity_id,
                "reason": "The source game does not reach a legal learner decision; inspect it in Builder",
                "candidates": []}
    with read_connection() as database:
        relation_table = ("coverage_discovery_recommendation_requests"
                          if game.get("source_kind") == "coverage" else "discovery_recommendation_requests")
        source_constraint = ("recommendation.coverage_node_id=? AND recommendation.route_json=?"
                             if game.get("source_kind") == "coverage"
                             else "recommendation.source_game_id=? AND recommendation.source_ply=?")
        source_parameters = ((game["coverage_node_id"], game["moves_json"])
                             if game.get("source_kind") == "coverage" else (game["id"], game["ply"]))
        saved_report = database.execute(
            f"""SELECT request.state,request.report_json,request.last_error
               FROM {relation_table} recommendation
               JOIN threat_analysis_requests request ON request.id=recommendation.request_id
               WHERE recommendation.opportunity_id=? AND recommendation.request_id=?
                 AND {source_constraint}""",
            (opportunity_id, request.request_id, *source_parameters),
        ).fetchone()
    if saved_report and saved_report["state"] == "failed":
        return {"state": "waiting", "opportunity_id": opportunity_id,
                "reason": f"Engine search failed: {saved_report['last_error'] or 'retry it in Analysis activity'}",
                "candidates": []}
    if not saved_report or saved_report["state"] != "complete" or not saved_report["report_json"]:
        return {"state": "waiting", "opportunity_id": opportunity_id,
                "reason": "Docker Stockfish is preparing a full-history continuation search", "candidates": []}
    try:
        report = report_from_json(json.loads(saved_report["report_json"]))
        validate_analysis_report(request, report)
    except (TypeError, KeyError, ValueError):
        return {"state": "unavailable", "opportunity_id": opportunity_id,
                "reason": "Saved engine evidence is invalid; repair analysis and retry this discovery",
                "candidates": []}
    target_key = _position_key(board)
    best = report.lines[0]
    fields = ("id", "name", "start_fen", "moves_json", "trained_color")
    line_snapshot = tuple(tuple(line[field] for field in fields) for line in lines)
    examples, _ = _cached_repertoire_positions(line_snapshot, game["color"])
    accepted = sorted({example["next_move"] for example in examples
                       if example["position_key"] == target_key and example["learner_turn"]
                       and example["next_move"]})
    example_positions = {example["position_key"] for example in examples}
    sound_candidates = []
    learner_sign = 1 if game["color"] == "white" else -1
    for line in report.lines[:5]:
        try:
            root_move = chess.Move.from_uci(line.root_move_uci)
        except ValueError:
            continue
        if root_move not in board.legal_moves:
            continue
        if best.score.cp is not None and line.score.cp is not None:
            loss_cp = learner_sign * (best.score.cp - line.score.cp)
        elif best.score.mate is not None and line.score.mate is not None:
            if ((best.score.mate > 0) != (line.score.mate > 0)
                    or abs(line.score.mate) > abs(best.score.mate) + 2):
                continue
            loss_cp = None
        else:
            continue
        continuation = board.copy(stack=False)
        preview_moves = []
        learner_decisions = 0
        legal = True
        for move_uci in line.pv_uci:
            move = chess.Move.from_uci(move_uci)
            if move not in continuation.legal_moves:
                legal = False
                break
            if continuation.turn == (game["color"] == "white"):
                learner_decisions += 1
            preview_moves.append(move_uci)
            continuation.push(move)
            if (_position_key(continuation) in example_positions and preview_moves
                    or learner_decisions >= 4):
                break
        if not legal or not preview_moves or preview_moves[0] != line.root_move_uci:
            continue
        after_first = board.copy(stack=False)
        after_first.push_uci(line.root_move_uci)
        after_key = _position_key(after_first)
        pawn_signature = _pawn_signature(after_first)
        piece_signature = _piece_signature(after_first)
        ranked_examples = sorted(examples, key=lambda example: (
            example["position_key"] != after_key,
            example["pawn"] != pawn_signature,
            -_overlapping_pieces(example["pieces"], piece_signature),
            example["line_id"],
        ))
        nearest = ranked_examples[0] if ranked_examples else None
        comparable_examples = _comparable_move_examples(
            examples, board, game["color"], line.root_move_uci)
        same_move_example = comparable_examples[0] if comparable_examples else None
        transposition = nearest if nearest and nearest["position_key"] == after_key else None
        familiar_example = transposition or same_move_example
        similarity = ("exact transposition" if transposition else
                      "same move in a comparable repertoire position" if same_move_example else
                      "no supported similarity")
        sound_candidates.append({
            "move_uci": line.root_move_uci, "score": asdict(line.score),
            "loss_cp": loss_cp, "similarity": similarity, "familiar": bool(familiar_example),
            "repertoire_line_count": len({example["line_id"] for example in comparable_examples}),
            "exact_transposition": transposition is not None,
            "example_line_id": familiar_example["line_id"] if familiar_example else None,
            "example_line_name": familiar_example["line_name"] if familiar_example else None,
            "preview_moves_uci": preview_moves,
            "engine_version": request.engine_version, "network_version": request.network_version,
            "depth": line.depth, "report_id": report.report_id,
            "source_game_id": game["id"], "source_ply": game["ply"],
        })
    sound_candidates = _rank_candidates(sound_candidates)
    for candidate in sound_candidates:
        del candidate["familiar"]
    engine_lines = []
    for line in report.lines[:5]:
        loss_cp = (learner_sign * (best.score.cp - line.score.cp)
                   if best.score.cp is not None and line.score.cp is not None else None)
        engine_lines.append({"move_uci": line.root_move_uci,
                             "score": asdict(line.score), "loss_cp": loss_cp,
                             "depth": line.depth})
    return {"state": "ready" if sound_candidates else "unavailable",
            "opportunity_id": opportunity_id, "repertoire_id": repertoire_id,
            "evidence_fingerprint": fingerprint, "starting_fen": board.fen(),
            "accepted_moves_uci": accepted, "candidates": sound_candidates,
            "suggested_move_uci": sound_candidates[0]["move_uci"] if sound_candidates else None,
            "suggestion_reason": (sound_candidates[0]["similarity"] if sound_candidates else None),
            "engine_lines": engine_lines,
            "reason": None if sound_candidates else "No compatible sound engine continuation is available"}


DISCOVERY_ADMISSION_PRIORITY = 80


def create_admission_intent(opportunity_id: str, selected_move_uci: str,
                            expected_fingerprint: str) -> dict:
    with read_connection() as database:
        prior = database.execute(
            """SELECT * FROM discovery_admission_intents
               WHERE opportunity_id=? AND selected_move_uci=?""",
            (opportunity_id, selected_move_uci),
        ).fetchone()
    if prior:
        if prior["evidence_fingerprint"] != expected_fingerprint:
            raise ValueError("This continuation was accepted from a different evidence revision")
        if prior["state"] != "queued":
            with read_connection() as database:
                existing_task = database.execute(
                    "SELECT state,priority FROM background_tasks WHERE kind='discovery_admission' AND deduplication_key=?",
                    (prior["id"],),
                ).fetchone()
            if not existing_task or existing_task["state"] == "failed":
                with connection() as database:
                    enqueue_task_in_transaction(
                        database, "discovery_admission", prior["id"],
                        {"intent_id": prior["id"]}, priority=DISCOVERY_ADMISSION_PRIORITY,
                    )
            elif (existing_task["state"] in {"queued", "retrying"}
                  and existing_task["priority"] > DISCOVERY_ADMISSION_PRIORITY):
                with connection() as database:
                    database.execute(
                        """UPDATE background_tasks SET priority=? WHERE kind='discovery_admission'
                           AND deduplication_key=? AND state IN ('queued','retrying') AND priority>?""",
                        (DISCOVERY_ADMISSION_PRIORITY, prior["id"], DISCOVERY_ADMISSION_PRIORITY),
                    )
        return {"id": prior["id"], "line_id": prior["line_id"],
                "repertoire_id": prior["repertoire_id"],
                "starting_fen": prior["starting_fen"],
                "selected_move_uci": prior["selected_move_uci"],
                "preview_moves_uci": json.loads(prior["preview_moves_json"]),
                "learner_color": "white" if chess.Board(prior["starting_fen"]).turn else "black"}
    recommendation = recommend_missing_continuations(opportunity_id)
    if recommendation["state"] != "ready" or recommendation["evidence_fingerprint"] != expected_fingerprint:
        raise ValueError("Discovery evidence changed; refresh the preview")
    selected = next((item for item in recommendation["candidates"]
                     if item["move_uci"] == selected_move_uci), None)
    if not selected:
        raise ValueError("The chosen move is not a validated recommendation")
    starting_fen = recommendation["starting_fen"]
    repertoire_id = recommendation["repertoire_id"]
    preview_moves = selected["preview_moves_uci"]
    line_id = hashlib.sha256(
        f"{repertoire_id}\0{card_id(starting_fen, preview_moves)}".encode()
    ).hexdigest()
    intent_id = hashlib.sha256(f"{opportunity_id}\0{selected_move_uci}".encode()).hexdigest()
    with connection() as database:
        database.execute(
            """INSERT OR IGNORE INTO discovery_admission_intents(
                 id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,
                 selected_move_uci,preview_moves_json,recommendation_json,line_id,
                 created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (intent_id, opportunity_id, repertoire_id, expected_fingerprint,
             starting_fen, selected_move_uci, json.dumps(preview_moves),
             json.dumps(selected), line_id, _now(), _now()),
        )
        database.execute(
            """UPDATE repertoire_opportunities SET admission_state='preparing',
                 seen_at=COALESCE(seen_at,?),updated_at=? WHERE id=?""",
            (_now(), _now(), opportunity_id),
        )
        enqueue_task_in_transaction(
            database, "discovery_admission", intent_id, {"intent_id": intent_id},
            priority=DISCOVERY_ADMISSION_PRIORITY,
        )
    return {"id": intent_id, "line_id": line_id, "repertoire_id": repertoire_id,
            "starting_fen": starting_fen, "selected_move_uci": selected_move_uci,
            "preview_moves_uci": preview_moves,
            "learner_color": "white" if chess.Board(starting_fen).turn else "black"}


def enqueue_admission_intent(intent_id: str) -> None:
    enqueue_task("discovery_admission", intent_id, {"intent_id": intent_id},
                 priority=DISCOVERY_ADMISSION_PRIORITY, foreground=False)


def _materialize_admission_branch(task: dict) -> None:
    """Write one selected branch and its rebuild request in a short transaction."""
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        intent = database.execute(
            "SELECT * FROM discovery_admission_intents WHERE id=?",
            (task["payload"]["intent_id"],),
        ).fetchone()
        if not intent or intent["state"] == "queued":
            return
        if database.execute(
            "SELECT 1 FROM repertoire_lines WHERE id=?", (intent["line_id"],),
        ).fetchone():
            return
    preview_moves = json.loads(intent["preview_moves_json"])
    board = chess.Board(intent["starting_fen"])
    learner_color = "white" if board.turn else "black"
    for move_uci in preview_moves:
        board.push_uci(move_uci)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return
        else:
            lease = database.execute(
                "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
            ).fetchone()
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return
        inserted = database.execute(
            """INSERT OR IGNORE INTO repertoire_lines(
                 id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
               VALUES(?,?,'Discovery continuation',?,?,?,?)""",
            (intent["line_id"], intent["repertoire_id"], learner_color,
             intent["starting_fen"], json.dumps(preview_moves), _now()),
        ).rowcount
        if inserted:
            depth = database.execute(
                "SELECT initial_depth FROM settings WHERE id=1",
            ).fetchone()[0]
            database.execute(
                """INSERT OR IGNORE INTO repertoire_line_training_depths(
                     line_id,learner_decision_count) VALUES(?,?)""",
                (intent["line_id"], depth),
            )
            enqueue_in_transaction = (enqueue_compact_postgres_task_in_transaction
                                      if postgres_store.configured() else enqueue_task_in_transaction)
            enqueue_in_transaction(
                database, "opening_graph_rebuild", intent["repertoire_id"],
                {"repertoire_id": intent["repertoire_id"], "local_day": date.today().isoformat()},
                priority=40,
            )


def _ensure_admission_coverage_refresh(task: dict) -> None:
    """Resume the existing coverage pipeline after branch materialization."""
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        intent = database.execute(
            "SELECT repertoire_id,line_id,state FROM discovery_admission_intents WHERE id=?",
            (task["payload"]["intent_id"],),
        ).fetchone()
        if not intent or intent["state"] == "queued":
            return
        line = database.execute(
            "SELECT created_at FROM repertoire_lines WHERE id=?", (intent["line_id"],),
        ).fetchone()
        if not line:
            return
        run = database.execute(
            """SELECT 1 FROM repertoire_coverage_runs
               WHERE repertoire_id=? AND created_at>=? LIMIT 1""",
            (intent["repertoire_id"], line["created_at"]),
        ).fetchone()
        lease = None if postgres_store.configured() else database.execute(
            "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
    if run:
        return
    if postgres_store.configured():
        from .postgres_coverage_seed import request_coverage_seed_in_transaction

        activity_gate.wait_for_foreground()
        with connection(background=True) as database:
            if lock_current_slice(database, task):
                request_coverage_seed_in_transaction(
                    database, intent["repertoire_id"], automatic=True,
                )
        return
    if not lease or lease["generation"] != task["generation"] or lease["lease_token"] != task["lease_token"]:
        return
    activity_gate.wait_for_foreground()
    from .repertoire_coverage import enqueue_coverage_refresh

    enqueue_coverage_refresh(intent["repertoire_id"], automatic=True, background=True)


def execute_admission_intent_slice(task: dict) -> bool:
    """Materialize one branch or wait for publication, then admit one card."""
    activity_gate.wait_for_foreground()
    _materialize_admission_branch(task)
    _ensure_admission_coverage_refresh(task)
    activity_gate.wait_for_foreground()
    read_section = background_read_connection if postgres_store.configured() else read_connection
    with read_section() as database:
        intent = database.execute(
            "SELECT * FROM discovery_admission_intents WHERE id=?",
            (task["payload"]["intent_id"],),
        ).fetchone()
        if not intent or intent["state"] == "queued":
            return False
        step = database.execute(
            """SELECT step.card_id FROM opening_graph_steps step
               JOIN opening_graph_publications publication
                 ON publication.repertoire_id=step.repertoire_id
                AND publication.generation=step.generation
               WHERE step.repertoire_id=? AND step.line_id=? AND step.card_id IS NOT NULL
               ORDER BY step.decision_index LIMIT 1""",
            (intent["repertoire_id"], intent["line_id"]),
        ).fetchone()
        card_id_value = step["card_id"] if step else None
        integrity = database.execute(
            """SELECT checked_at,scan_status,status FROM repertoire_integrity_state
               WHERE repertoire_id=?""",
            (intent["repertoire_id"],),
        ).fetchone()
        integrity_published = bool(integrity and integrity["scan_status"] == "idle"
                                   and integrity["checked_at"]
                                   and integrity["checked_at"] >= intent["created_at"])
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if postgres_store.configured():
            if not lock_current_slice(database, task):
                return False
        else:
            lease = database.execute(
                "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
            ).fetchone()
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return True
        current = database.execute("SELECT * FROM discovery_admission_intents WHERE id=?",
                                   (task["payload"]["intent_id"],)).fetchone()
        if not current or current["state"] == "queued":
            return False
        if card_id_value and integrity_published:
            card = database.execute(
                "SELECT id,archived,pending_validation FROM cards WHERE id=?", (card_id_value,),
            ).fetchone()
            blocked = database.execute(
                """SELECT 1 FROM repertoire_integrity_card_blocks
                   WHERE repertoire_id=? AND card_id=?""",
                (current["repertoire_id"], card_id_value),
            ).fetchone()
            if card and not card["archived"] and not card["pending_validation"] and not blocked:
                today = date.today().isoformat()
                database.execute(
                    """UPDATE cards SET state=CASE WHEN state IN ('locked','new') THEN 'learning' ELSE state END,
                         introduced_at=COALESCE(introduced_at,?) WHERE id=?""",
                    (today, card_id_value),
                )
                ensure_card_queued_after(database, card_id_value, after_cards=0,
                                         attempt_state="guided", priority_reason="Discovery · added continuation")
                database.execute(
                    """UPDATE daily_queue SET admission_kind='explicit',admission_source=?
                       WHERE queue_date=? AND card_id=? AND status='queued'""",
                    (f"discovery:{current['opportunity_id']}", today, card_id_value),
                )
                database.execute(
                    """UPDATE discovery_admission_intents SET state='queued',card_id=?,updated_at=? WHERE id=?""",
                    (card_id_value, _now(), current["id"]),
                )
                database.execute(
                    """UPDATE repertoire_opportunities SET admission_state='queued',
                         handled_evidence_json=evidence_json,card_id=COALESCE(card_id,?),
                         admitted_card_id=?,updated_at=? WHERE id=?""",
                    (card_id_value, card_id_value, _now(), current["opportunity_id"]),
                )
                if postgres_store.configured():
                    complete_task_slice_in_transaction(database, task)
                return False
        if postgres_store.configured():
            enqueue_compact_postgres_task_in_transaction(
                database, "discovery_admission", task["payload"]["intent_id"],
                task["payload"], priority=DISCOVERY_ADMISSION_PRIORITY,
                delay_seconds=10,
            )
            return True
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        database.execute(
            """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
                 payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
                 lease_expires_at=NULL,updated_at=?
               WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
            (json.dumps(task["payload"]), retry_at, _now(), task["id"],
             task["generation"], task["lease_token"]),
        )
    return True
