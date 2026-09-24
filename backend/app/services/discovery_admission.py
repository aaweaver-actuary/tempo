"""Engine-backed repertoire continuation previews and durable card admission."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
import hashlib
import json

import chess

from ..database import connection, read_connection
from .activity_gate import activity_gate
from .cards import card_id
from .durable_tasks import enqueue_task
from .review_service import ensure_card_queued_after
from .threat_pipeline import ENGINE_VERSION, NETWORK_VERSION, report_from_json, validate_analysis_report
from .threat_validation import AnalysisRequest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(board: chess.Board) -> str:
    return " ".join(board.fen().split()[:4])


def _pawn_signature(board: chess.Board) -> tuple[tuple[bool, int], ...]:
    return tuple(sorted((piece.color, square) for square, piece in board.piece_map().items()
                        if piece.piece_type == chess.PAWN))


def _piece_signature(board: chess.Board) -> set[tuple[bool, int, int]]:
    return {(piece.color, piece.piece_type, square) for square, piece in board.piece_map().items()
            if piece.piece_type != chess.PAWN}


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
    return None


def _full_history_request(game: dict) -> tuple[chess.Board, AnalysisRequest]:
    board = chess.Board(game["start_fen"])
    all_moves = json.loads(game["moves_json"])
    if game["ply"] < 0 or game["ply"] >= len(all_moves):
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
    """Persist one full-history Docker engine request outside long SQLite work."""
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        opportunity = database.execute(
            """SELECT * FROM repertoire_opportunities WHERE id=? AND status='active'
               AND kind='post_gap_weakness' AND card_id IS NULL""",
            (task["payload"]["opportunity_id"],),
        ).fetchone()
        game = _source_game(database, opportunity) if opportunity else None
    if not game:
        return
    _, request = _full_history_request(game)
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if "id" in task:
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
        board = chess.Board(line["start_fen"])
        for move_uci in json.loads(line["moves_json"]):
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                break
            examples.append({"fen_key": _key(board), "pawn": _pawn_signature(board),
                             "pieces": _piece_signature(board), "line_id": line["id"],
                             "line_name": line["name"], "next_move": move_uci,
                             "learner_turn": board.turn == (learner_color == "white")})
            board.push(move)
        examples.append({"fen_key": _key(board), "pawn": _pawn_signature(board),
                         "pieces": _piece_signature(board), "line_id": line["id"],
                         "line_name": line["name"], "next_move": None,
                         "learner_turn": board.turn == (learner_color == "white")})
    return examples, accepted_moves


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
        return {"state": "waiting", "opportunity_id": opportunity_id,
                "reason": "An analyzed source game is not available yet", "candidates": []}
    board, request = _full_history_request(game)
    with read_connection() as database:
        saved_report = database.execute(
            """SELECT request.state,request.report_json,request.last_error
               FROM discovery_recommendation_requests recommendation
               JOIN threat_analysis_requests request ON request.id=recommendation.request_id
               WHERE recommendation.opportunity_id=? AND recommendation.request_id=?
                 AND recommendation.source_game_id=? AND recommendation.source_ply=?""",
            (opportunity_id, request.request_id, game["id"], game["ply"]),
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
        return {"state": "waiting", "opportunity_id": opportunity_id,
                "reason": "Engine evidence needs repair before this branch can be accepted", "candidates": []}
    target_key = _key(board)
    best = report.lines[0]
    examples, _ = _repertoire_positions(lines, game["color"])
    accepted = sorted({example["next_move"] for example in examples
                       if example["fen_key"] == target_key and example["learner_turn"]
                       and example["next_move"]})
    example_positions = {example["fen_key"] for example in examples}
    sound_candidates = []
    learner_sign = 1 if game["color"] == "white" else -1
    for line in report.lines[:5]:
        if best.score.cp is not None and line.score.cp is not None:
            loss_cp = learner_sign * (best.score.cp - line.score.cp)
            if loss_cp > 30:
                continue
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
            if (_key(continuation) in example_positions and preview_moves
                    or learner_decisions >= 4):
                break
        if not legal or not preview_moves or preview_moves[0] != line.root_move_uci:
            continue
        after_first = board.copy(stack=False)
        after_first.push_uci(line.root_move_uci)
        after_key = _key(after_first)
        pawn_signature = _pawn_signature(after_first)
        piece_signature = _piece_signature(after_first)
        ranked_examples = sorted(examples, key=lambda example: (
            example["fen_key"] != after_key,
            example["pawn"] != pawn_signature,
            -len(example["pieces"] & piece_signature),
            example["line_id"],
        ))
        nearest = ranked_examples[0] if ranked_examples else None
        similarity = ("exact transposition" if nearest and nearest["fen_key"] == after_key
                      else "matching pawn structure" if nearest and nearest["pawn"] == pawn_signature
                      else "familiar piece placement" if nearest and len(nearest["pieces"] & piece_signature) >= 8
                      else "no supported similarity")
        sound_candidates.append({
            "move_uci": line.root_move_uci, "score": asdict(line.score),
            "loss_cp": loss_cp, "similarity": similarity,
            "example_line_id": nearest["line_id"] if nearest and similarity != "no supported similarity" else None,
            "example_line_name": nearest["line_name"] if nearest and similarity != "no supported similarity" else None,
            "preview_moves_uci": preview_moves,
            "engine_version": request.engine_version, "network_version": request.network_version,
            "depth": line.depth, "report_id": report.report_id,
            "source_game_id": game["id"], "source_ply": game["ply"],
        })
    similarity_rank = {"exact transposition": 0, "matching pawn structure": 1,
                       "familiar piece placement": 2, "no supported similarity": 3}
    sound_candidates.sort(key=lambda row: (similarity_rank[row["similarity"]],
                                           row["loss_cp"] if row["loss_cp"] is not None else 0,
                                           row["move_uci"]))
    return {"state": "ready" if sound_candidates else "waiting",
            "opportunity_id": opportunity_id, "repertoire_id": repertoire_id,
            "evidence_fingerprint": fingerprint, "starting_fen": board.fen(),
            "accepted_moves_uci": accepted, "candidates": sound_candidates[:3],
            "reason": None if sound_candidates else "No compatible sound engine continuation is available"}


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
    return {"id": intent_id, "line_id": line_id, "repertoire_id": repertoire_id,
            "starting_fen": starting_fen, "selected_move_uci": selected_move_uci,
            "preview_moves_uci": preview_moves,
            "learner_color": "white" if chess.Board(starting_fen).turn else "black"}


def enqueue_admission_intent(intent_id: str) -> None:
    enqueue_task("discovery_admission", intent_id, {"intent_id": intent_id},
                 priority=125, foreground=False)


def execute_admission_intent_slice(task: dict) -> bool:
    """Wait for one published target card, then admit it in one short transaction."""
    activity_gate.wait_for_foreground()
    with read_connection() as database:
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
        lease = database.execute(
            "SELECT generation,lease_token FROM background_tasks WHERE id=?", (task["id"],),
        ).fetchone()
        if not lease or lease["generation"] != task["generation"] or lease["lease_token"] != task["lease_token"]:
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
                         admitted_card_id=?,updated_at=? WHERE id=?""",
                    (card_id_value, _now(), current["opportunity_id"]),
                )
                return False
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
