"""Engine-backed repertoire continuation previews and durable card admission."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
from functools import lru_cache
import hashlib
import json

import chess

from tempo_legacy_database.connections import background_read_connection, connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.discovery_admission import storage
from .activity_gate import activity_gate
from . import canonical_scope_freshness
from .canonical_scope_freshness import opportunity_is_current, coverage_run_is_current, coverage_scope_predicate
from app.services.canonical_prefix import read_prefix, game_in_scope, line_origin, validate_scoped_line, store_positions
from app.chess.position_identity import position_key
from app.chess.cards import card_id
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
    prefix = read_prefix(database, opportunity["repertoire_id"])
    evidence = json.loads(opportunity["evidence_json"])
    for support in evidence.get("findings", []):
        game_id = support.get("game_id")
        mistake_ply = support.get("mistake_ply")
        if game_id is None or mistake_ply is None:
            continue
        game = storage.analyzed_source_game(database, (game_id,))
        if not game or not game_in_scope(game["start_fen"], json.loads(game["moves_json"]), prefix["moves"]):
            continue
        return {**dict(game), "ply": int(mistake_ply)}
    if opportunity["kind"] == "missing_response" and opportunity["opponent_move_uci"]:
        node_id = evidence.get("coverage_node_id")
        node = storage.coverage_source_node(database, (node_id, opportunity["repertoire_id"]))
        if not coverage_run_is_current(database, node, opportunity["repertoire_id"]):
            return None
        routes = json.loads(node["routes_json"])
        lines = storage.coverage_source_lines(database, (opportunity["repertoire_id"],))
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
            game = storage.background_analyzed_source_game(database, (game_id,))
        if game:
            return {**dict(game), "ply": int(mistake_ply)}
    if opportunity["kind"] != "missing_response" or not opportunity["opponent_move_uci"]:
        return None
    node_id = evidence.get("coverage_node_id")
    with background_read_connection() as database:
        node = storage.coverage_source_node(database, (node_id, opportunity["repertoire_id"]))
    if node is None:
        return None
    with background_read_connection() as database:
        if not coverage_run_is_current(database, node, opportunity["repertoire_id"]):
            return None
    with background_read_connection() as database:
        lines = storage.coverage_source_lines(database, (opportunity["repertoire_id"],))
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
    read_section = background_read_connection if storage.configured() else read_connection
    with read_section() as database:
        opportunity = storage.recommendation_opportunity(database, (task["payload"]["opportunity_id"],))
        if opportunity and not opportunity_is_current(database, opportunity):
            return
        game = _source_game(database, opportunity) if opportunity and not storage.configured() else None
    if opportunity and storage.configured():
        game = _background_source_game(opportunity)
    if not game:
        return
    try:
        _, request = _full_history_request(game)
    except (ValueError, KeyError, TypeError):
        return
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if opportunity:
            read_prefix(database, opportunity["repertoire_id"], lock=True)
            canonical_scope_freshness.game_scope_generation(database, lock=True)
        if "id" in task:
            if storage.configured():
                if not lock_current_slice(database, task):
                    return
            else:
                lease = storage.task_lease(database, (task["id"],))
                if (not lease or lease["generation"] != task["generation"]
                        or lease["lease_token"] != task["lease_token"]):
                    return
        current = storage.opportunity(database, (task["payload"]["opportunity_id"],))
        if (not current or current["status"] != "active" or current["card_id"]
                or not opportunity_is_current(database, current, lock=True)):
            return
        storage.insert_analysis_request(database, (request.request_id, json.dumps(asdict(request)), _now(), _now()))
        if game.get("source_kind") == "coverage":
            storage.upsert_coverage_request(
                database,
                (task["payload"]["opportunity_id"], request.request_id,
                 game["coverage_node_id"], game["moves_json"], _now(), _now()),
            )
        else:
            storage.upsert_game_request(
                database,
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
        opportunity = storage.active_opportunity(database, (opportunity_id,))
        if not opportunity or not opportunity_is_current(database, opportunity):
            raise KeyError("Active discovery not found")
        if opportunity["card_id"]:
            raise ValueError("This discovery already has a saved decision card")
        game = _source_game(database, opportunity)
        lines = [dict(row) for row in storage.repertoire_lines(database, (opportunity["repertoire_id"],))]
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
        saved_report = storage.saved_recommendation(database, game, opportunity_id, request)
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


def admission_intent_id(opportunity_id: str, selected_move_uci: str, evidence_fingerprint: str) -> str:
    """Retry identity includes the evidence the learner actually accepted."""
    return hashlib.sha256(f"{opportunity_id}\0{selected_move_uci}\0{evidence_fingerprint}".encode()).hexdigest()


def create_admission_intent(opportunity_id: str, selected_move_uci: str,
                            expected_fingerprint: str) -> dict:
    with read_connection() as database:
        prior = storage.matching_intent(database, (opportunity_id, selected_move_uci, expected_fingerprint))
    if prior:
        if prior["state"] != "queued":
            with read_connection() as database:
                existing_task = storage.admission_task_state(database, (prior["id"],))
            if not existing_task or existing_task["state"] == "failed":
                with connection() as database:
                    enqueue_task_in_transaction(
                        database, "discovery_admission", prior["id"],
                        {"intent_id": prior["id"]}, priority=DISCOVERY_ADMISSION_PRIORITY,
                    )
            elif (existing_task["state"] in {"queued", "retrying"}
                  and existing_task["priority"] > DISCOVERY_ADMISSION_PRIORITY):
                with connection() as database:
                    storage.promote_admission_task(database, (DISCOVERY_ADMISSION_PRIORITY, prior["id"], DISCOVERY_ADMISSION_PRIORITY))
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
    intent_id = admission_intent_id(opportunity_id, selected_move_uci, expected_fingerprint)
    with connection() as database:
        storage.begin_write(database)
        current_opportunity = storage.opportunity(database, (opportunity_id,))
        if (not current_opportunity or not opportunity_is_current(database, current_opportunity) or current_opportunity["status"] != "active"
                or current_opportunity["evidence_fingerprint"] != expected_fingerprint):
            raise ValueError("Discovery evidence changed; refresh the preview")
        inserted = storage.insert_intent(
            database,
            (intent_id, opportunity_id, repertoire_id, expected_fingerprint,
             starting_fen, selected_move_uci, json.dumps(preview_moves),
             json.dumps(selected), line_id, _now(), _now()),
        )
        if inserted:
            storage.mark_preparing(database, (_now(), _now(), opportunity_id, expected_fingerprint))
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
    read_section = background_read_connection if storage.configured() else read_connection
    with read_section() as database:
        intent = storage.intent(database, (task["payload"]["intent_id"],))
        if not intent or intent["state"] == "queued":
            return
        if storage.source_line_exists(database, (intent["line_id"],)):
            return
        prefix = read_prefix(database, intent["repertoire_id"])
        origin = line_origin(database, prefix["preview_id"], intent["starting_fen"]) if prefix["moves"] else None
        opportunity_snapshot = storage.opportunity(database, (intent["opportunity_id"],)) if prefix["moves"] and "opportunity_id" in dict(intent) else None
        if opportunity_snapshot and opportunity_snapshot["canonical_prefix_revision"] != prefix["revision"]:
            raise ValueError("The canonical prefix changed; refresh this discovery before adding it")
    if prefix["moves"] and origin is None and opportunity_snapshot:
        source = _background_source_game(opportunity_snapshot)
        if source:
            source_board, _ = _full_history_request(source)
            if position_key(source_board.fen()) == position_key(intent["starting_fen"]):
                with read_section() as database:
                    source_origin = line_origin(database, prefix["preview_id"], source["start_fen"])
                if source_origin is not None:
                    origin = [*source_origin, *json.loads(source["moves_json"])[:source["ply"]]]
    preview_moves = json.loads(intent["preview_moves_json"])
    board = chess.Board(intent["starting_fen"])
    learner_color = "white" if board.turn else "black"
    for move_uci in preview_moves:
        board.push_uci(move_uci)
    scoped_line = validate_scoped_line(intent["starting_fen"], preview_moves, prefix["moves"], origin)
    if scoped_line["status"] != "valid":
        raise ValueError("This continuation is outside the canonical prefix. " + scoped_line["reason"])
    activity_gate.wait_for_foreground()
    from .repertoire_game_refresh import refreshing_game_scope
    with connection(background=True) as database, refreshing_game_scope(database):
        if storage.configured():
            read_prefix(database, intent["repertoire_id"], lock=True)
            canonical_scope_freshness.game_scope_generation(database, lock=True)
            if not lock_current_slice(database, task):
                return
        else:
            storage.begin_write(database)
            lease = storage.task_lease(database, (task["id"],))
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return
        current_prefix = read_prefix(database, intent["repertoire_id"], lock=True)
        if current_prefix["revision"] != prefix["revision"]:
            raise ValueError("The canonical prefix changed; refresh this discovery before adding it")
        if current_prefix["source_revision"] != prefix["source_revision"]:
            raise ValueError("The repertoire routes changed; refresh this discovery before adding it")
        opportunity = storage.opportunity(database, (intent["opportunity_id"],)) if "opportunity_id" in dict(intent) else None
        if opportunity and not opportunity_is_current(database, opportunity):
            raise ValueError("Opening routes or game scope changed; refresh this discovery before adding it")
        inserted = storage.insert_source_line(
            database,
            (intent["line_id"], intent["repertoire_id"], learner_color,
             intent["starting_fen"], json.dumps(preview_moves), _now()),
        )
        if inserted:
            if current_prefix["moves"] and current_prefix["preview_id"]:
                # The source check above precedes this additive write. Re-establish
                # only the verified admitted route against its committed source version.
                store_positions(database, current_prefix["preview_id"], scoped_line["positions"],
                                source_revision=read_prefix(database, intent["repertoire_id"])["source_revision"])
            depth = storage.initial_depth(database)
            storage.insert_training_depth(database, (intent["line_id"], depth))
            enqueue_in_transaction = (enqueue_compact_postgres_task_in_transaction
                                      if storage.configured() else enqueue_task_in_transaction)
            enqueue_in_transaction(
                database, "opening_graph_rebuild", intent["repertoire_id"],
                {"repertoire_id": intent["repertoire_id"], "local_day": date.today().isoformat()},
                priority=40,
            )


def _ensure_admission_coverage_refresh(task: dict) -> None:
    """Resume the existing coverage pipeline after branch materialization."""
    read_section = background_read_connection if storage.configured() else read_connection
    with read_section() as database:
        intent = storage.coverage_intent(database, (task["payload"]["intent_id"],))
        if not intent or intent["state"] == "queued":
            return
        line = storage.source_line_created_at(database, (intent["line_id"],))
        if not line:
            return
        run = storage.coverage_started_since_line(database, intent, line)
        lease = None if storage.configured() else storage.task_lease(database, (task["id"],))
    if run:
        return
    if storage.configured():
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
    read_section = background_read_connection if storage.configured() else read_connection
    with read_section() as database:
        intent = storage.intent(database, (task["payload"]["intent_id"],))
        if not intent or intent["state"] == "queued":
            return False
        step = storage.published_line_card(database, (intent["repertoire_id"], intent["line_id"]))
        card_id_value = step["card_id"] if step else None
        integrity = storage.integrity_publication(database, (intent["repertoire_id"],))
        integrity_published = bool(integrity and integrity["scan_status"] == "idle"
                                   and integrity["checked_at"]
                                   and integrity["checked_at"] >= intent["created_at"])
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if storage.configured():
            if not lock_current_slice(database, task):
                return False
        else:
            storage.begin_write(database)
            lease = storage.task_lease(database, (task["id"],))
            if (not lease or lease["generation"] != task["generation"]
                    or lease["lease_token"] != task["lease_token"]):
                return True
        current = storage.intent(database, (task["payload"]["intent_id"],))
        if not current or current["state"] == "queued":
            return False
        if card_id_value and integrity_published:
            card = storage.available_card(database, (card_id_value,))
            blocked = storage.card_integrity_block(database, (current["repertoire_id"], card_id_value))
            if card and not card["archived"] and not card["pending_validation"] and not blocked:
                today = date.today().isoformat()
                storage.introduce_card(database, (today, card_id_value))
                ensure_card_queued_after(database, card_id_value, after_cards=0,
                                         attempt_state="guided", priority_reason="Discovery · added continuation")
                storage.mark_explicit_admission(database, (f"discovery:{current['opportunity_id']}", today, card_id_value))
                storage.mark_intent_queued(database, (card_id_value, _now(), current["id"]))
                storage.complete_opportunity_admission(database, (card_id_value, card_id_value, _now(), current["opportunity_id"], current["evidence_fingerprint"]))
                if storage.configured():
                    complete_task_slice_in_transaction(database, task)
                return False
        if storage.configured():
            enqueue_compact_postgres_task_in_transaction(
                database, "discovery_admission", task["payload"]["intent_id"],
                task["payload"], priority=DISCOVERY_ADMISSION_PRIORITY,
                delay_seconds=10,
            )
            return True
        retry_at = (datetime.now(timezone.utc) + timedelta(seconds=10)).isoformat()
        storage.requeue_task(
            database,
            (json.dumps(task["payload"]), retry_at, _now(), task["id"],
             task["generation"], task["lease_token"]),
        )
    return True
