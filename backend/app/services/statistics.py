"""Reproducible chess-performance features and denominator-transparent KPIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import hashlib
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import chess

from ..database import connection
from .activity_gate import activity_gate


FEATURE_VERSION = 1
MATERIAL_PHASE_VALUES = {
    chess.PAWN: 0,
    chess.KNIGHT: 1,
    chess.BISHOP: 1,
    chess.ROOK: 2,
    chess.QUEEN: 4,
    chess.KING: 0,
}


def _player_score(result: str, color: str) -> float:
    if result == "1/2-1/2":
        return 0.5
    return 1.0 if (result == "1-0") == (color == "white") else 0.0


def _local_datetime(played_at: str, configured_timezone: str) -> datetime:
    moment = datetime.fromisoformat(played_at.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if configured_timezone == "local":
        return moment.astimezone()
    try:
        return moment.astimezone(ZoneInfo(configured_timezone))
    except ZoneInfoNotFoundError:
        return moment.astimezone()


def _evaluation_before_ply(rows_by_ply: dict[int, object], ply: int, color: str) -> int | None:
    row = rows_by_ply.get(ply)
    if not row or row["eval_before_cp"] is None:
        return None
    white_evaluation = int(row["eval_before_cp"])
    return white_evaluation if color == "white" else -white_evaluation


def refresh_game_features(game_id: str, *, background: bool = False) -> None:
    with connection() as database:
        game = database.execute("SELECT * FROM imported_games WHERE id=?", (game_id,)).fetchone()
        if not game:
            return
        analysis_rows = database.execute(
            "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply", (game_id,)
        ).fetchall()
        events = database.execute(
            "SELECT * FROM gameplay_events WHERE game_id=? AND classifier_version=1",
            (game_id,),
        ).fetchall()
        primary_match = database.execute(
            "SELECT * FROM game_repertoire_matches WHERE game_id=? AND is_primary=1",
            (game_id,),
        ).fetchone()
        configured_timezone = database.execute("SELECT timezone FROM settings WHERE id=1").fetchone()[0]

    moves = json.loads(game["moves_json"])
    player_rows = [row for row in analysis_rows if bool(row["is_player_move"])]
    rows_by_ply = {int(row["ply"]): row for row in analysis_rows}
    opening_exit_ply = (
        int(primary_match["out_of_book_ply"])
        if primary_match and primary_match["out_of_book_ply"] is not None
        else min(20, len(moves))
    )
    board = chess.Board(game["start_fen"])
    endgame_entry_ply: int | None = None
    for ply, move_uci in enumerate(moves):
        phase_score = sum(
            MATERIAL_PHASE_VALUES[piece.piece_type] for piece in board.piece_map().values()
        )
        if phase_score <= 6:
            endgame_entry_ply = ply
            break
        board.push_uci(move_uci)
    local_moment = _local_datetime(game["played_at"], configured_timezone)
    player_opportunities = [
        event for event in events
        if event["kind"] == "tactical opportunity"
        and event["beneficiary_color"] == game["color"] and event["confidence"] >= 0.8
    ]
    conceded_opportunities = [
        event for event in events
        if event["kind"] == "tactical opportunity"
        and event["created_by_color"] == game["color"] and event["confidence"] >= 0.8
    ]
    feature_values = (
        game_id, FEATURE_VERSION, local_moment.date().isoformat(), local_moment.hour,
        local_moment.weekday(), _player_score(game["result"], game["color"]),
        len(player_rows),
        (sum(max(0, int(row["loss_cp"] or 0)) for row in player_rows) / len(player_rows)) if player_rows else None,
        sum(int(row["loss_cp"] or 0) >= 100 for row in player_rows) if player_rows else None,
        opening_exit_ply, _evaluation_before_ply(rows_by_ply, opening_exit_ply, game["color"]),
        endgame_entry_ply,
        _evaluation_before_ply(rows_by_ply, endgame_entry_ply, game["color"]) if endgame_entry_ply is not None else None,
        len(player_opportunities), sum(event["outcome"] == "found" for event in player_opportunities),
        len(conceded_opportunities), primary_match["repertoire_id"] if primary_match else None,
        datetime.now(timezone.utc).isoformat(),
    )
    if background:
        activity_gate.wait_for_foreground()
    with connection(background=background) as database:
        database.execute(
            """INSERT INTO game_feature_rows(
                   game_id,feature_version,local_day,local_hour,local_weekday,outcome_score,
                   player_decisions,mean_loss_cp,major_mistakes,opening_exit_ply,
                   opening_exit_eval_cp,endgame_entry_ply,endgame_entry_eval_cp,
                   tactical_opportunities,tactical_found,tactical_conceded,
                   primary_repertoire_id,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET
                   feature_version=excluded.feature_version,local_day=excluded.local_day,
                   local_hour=excluded.local_hour,local_weekday=excluded.local_weekday,
                   outcome_score=excluded.outcome_score,player_decisions=excluded.player_decisions,
                   mean_loss_cp=excluded.mean_loss_cp,major_mistakes=excluded.major_mistakes,
                   opening_exit_ply=excluded.opening_exit_ply,
                   opening_exit_eval_cp=excluded.opening_exit_eval_cp,
                   endgame_entry_ply=excluded.endgame_entry_ply,
                   endgame_entry_eval_cp=excluded.endgame_entry_eval_cp,
                   tactical_opportunities=excluded.tactical_opportunities,
                   tactical_found=excluded.tactical_found,
                   tactical_conceded=excluded.tactical_conceded,
                   primary_repertoire_id=excluded.primary_repertoire_id,
                   updated_at=excluded.updated_at""",
            feature_values,
        )


def _confidence_interval(successes: float, denominator: int) -> list[float] | None:
    if denominator == 0:
        return None
    proportion = successes / denominator
    radius = 1.96 * math.sqrt(max(0, proportion * (1 - proportion) / denominator))
    return [max(0, proportion - radius), min(1, proportion + radius)]


def statistics_overview(window_days: int) -> dict:
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=window_days - 1)).isoformat()
    with connection() as database:
        rows = database.execute(
            """SELECT g.*,f.* FROM imported_games g
                 LEFT JOIN game_feature_rows f ON f.game_id=g.id
                WHERE g.adaptive_excluded=0 AND substr(g.played_at,1,10)>=?
                ORDER BY g.played_at""",
            (cutoff,),
        ).fetchall()
    total_games = len(rows)
    game_scores = [_player_score(row["result"], row["color"]) for row in rows]
    wins = sum(score == 1 for score in game_scores)
    draws = sum(score == 0.5 for score in game_scores)
    losses = sum(score == 0 for score in game_scores)
    analyzed = [row for row in rows if row["mean_loss_cp"] is not None]
    opportunities = sum(int(row["tactical_opportunities"] or 0) for row in analyzed)
    found = sum(int(row["tactical_found"] or 0) for row in analyzed)
    decisions = sum(int(row["player_decisions"] or 0) for row in analyzed)
    conceded = sum(int(row["tactical_conceded"] or 0) for row in analyzed)
    scored_games = wins + draws + losses
    score_points = wins + 0.5 * draws
    return {
        "window_days": window_days, "games": total_games, "analyzed_games": len(analyzed),
        "analysis_coverage": len(analyzed) / total_games if total_games else 0,
        "score": {"value": score_points / scored_games if scored_games else None,
                  "wins": wins, "draws": draws, "losses": losses,
                  "numerator": score_points, "denominator": scored_games,
                  "confidence_interval": _confidence_interval(score_points, scored_games)},
        "decision_quality": {
            "mean_loss_cp": sum(row["mean_loss_cp"] for row in analyzed) / len(analyzed) if analyzed else None,
            "major_mistakes_per_game": sum(int(row["major_mistakes"] or 0) for row in analyzed) / len(analyzed) if analyzed else None,
            "denominator": len(analyzed)},
        "tactical_performance": {
            "value": found / opportunities if opportunities else None,
            "found": found, "opportunities": opportunities,
            "conceded_per_100_decisions": conceded * 100 / decisions if decisions else None,
            "conceded": conceded, "player_decisions": decisions,
            "confidence_interval": _confidence_interval(found, opportunities)},
    }


DIMENSION_EXPRESSIONS = {
    "color": "g.color", "speed": "g.speed", "provider": "g.provider",
    "weekday": "f.local_weekday", "hour": "f.local_hour",
    "opening": "COALESCE(g.opening_name,'Unknown')",
    "repertoire": "COALESCE(r.name,'No repertoire')",
    "opponent_rating": "CASE WHEN g.opponent_rating IS NULL THEN 'Unknown' WHEN g.opponent_rating<1200 THEN '<1200' WHEN g.opponent_rating<1600 THEN '1200–1599' WHEN g.opponent_rating<2000 THEN '1600–1999' ELSE '2000+' END",
    "relative_rating": "CASE WHEN g.opponent_rating IS NULL OR g.player_rating IS NULL THEN 'Unknown' WHEN g.opponent_rating-g.player_rating<=-100 THEN '100+ lower' WHEN g.opponent_rating-g.player_rating>=100 THEN '100+ higher' ELSE 'Within 99' END",
}


def statistics_breakdown(dimension: str, window_days: int) -> dict:
    expression = DIMENSION_EXPRESSIONS.get(dimension)
    if not expression:
        raise ValueError("Unknown statistics dimension")
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=window_days - 1)).isoformat()
    with connection() as database:
        rows = database.execute(
            f"""SELECT {expression} AS segment,COUNT(*) AS games,
                       SUM(f.outcome_score) AS score_points,AVG(f.mean_loss_cp) AS mean_loss_cp,
                       SUM(f.tactical_found) AS tactical_found,
                       SUM(f.tactical_opportunities) AS tactical_opportunities
                  FROM imported_games g JOIN game_feature_rows f ON f.game_id=g.id
                  LEFT JOIN repertoires r ON r.id=f.primary_repertoire_id
                 WHERE g.adaptive_excluded=0 AND f.local_day>=?
                 GROUP BY segment ORDER BY games DESC,segment""",
            (cutoff,),
        ).fetchall()
    return {"dimension": dimension, "window_days": window_days, "segments": [
        {"segment": str(row["segment"]), "games": row["games"],
         "score": row["score_points"] / row["games"] if row["games"] else None,
         "mean_loss_cp": row["mean_loss_cp"], "tactical_found": row["tactical_found"] or 0,
         "tactical_opportunities": row["tactical_opportunities"] or 0}
        for row in rows
    ]}


def refresh_daily_snapshot(local_day: str) -> dict:
    """Materialize a day only after provider watermarks and analyses are settled."""
    datetime.fromisoformat(local_day)
    next_day = (datetime.fromisoformat(local_day).date() + timedelta(days=1)).isoformat()
    with connection() as database:
        configured_providers = database.execute(
            "SELECT provider FROM game_accounts WHERE trim(username)!=''"
        ).fetchall()
        missing_watermarks = []
        for provider in configured_providers:
            state = database.execute(
                "SELECT last_success_at FROM game_sync_state WHERE provider=?",
                (provider["provider"],),
            ).fetchone()
            if not state or not state["last_success_at"] or state["last_success_at"][:10] < next_day:
                missing_watermarks.append(provider["provider"])
        eligible_games = database.execute(
            """SELECT g.id,g.analysis_state,g.adaptive_excluded,f.game_id AS feature_game_id
                 FROM imported_games g LEFT JOIN game_feature_rows f ON f.game_id=g.id
                WHERE substr(g.played_at,1,10)=?""",
            (local_day,),
        ).fetchall()
        blocked_games = [
            row["id"] for row in eligible_games
            if not row["adaptive_excluded"]
            and row["analysis_state"] not in {"failed"}
            and row["feature_game_id"] is None
        ]
        now = datetime.now(timezone.utc).isoformat()
        if missing_watermarks or blocked_games:
            status = "waiting-sync" if missing_watermarks else "waiting-analysis"
            payload = {
                "local_day": local_day, "status": status,
                "missing_provider_watermarks": missing_watermarks,
                "blocked_game_ids": blocked_games,
            }
            database.execute(
                """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
                   VALUES(?,1,?,?,?) ON CONFLICT(local_day) DO UPDATE SET
                   metrics_json=excluded.metrics_json,status=excluded.status,updated_at=excluded.updated_at""",
                (local_day, json.dumps(payload), status, now),
            )
            return payload
        features = database.execute(
            "SELECT * FROM game_feature_rows WHERE local_day=?", (local_day,)
        ).fetchall()
        games = len(features)
        score_points = sum(row["outcome_score"] for row in features)
        opportunities = sum(row["tactical_opportunities"] for row in features)
        found = sum(row["tactical_found"] for row in features)
        payload = {
            "local_day": local_day, "status": "complete", "games": games,
            "score": score_points / games if games else None,
            "tactical_found": found, "tactical_opportunities": opportunities,
        }
        database.execute(
            """INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at)
               VALUES(?,1,?,'complete',?) ON CONFLICT(local_day) DO UPDATE SET
               metrics_json=excluded.metrics_json,status='complete',updated_at=excluded.updated_at""",
            (local_day, json.dumps(payload), now),
        )
        if games >= 20:
            insight_id = hashlib.sha256(f"{local_day}\0outcome-trend\01".encode()).hexdigest()
            database.execute(
                """INSERT OR IGNORE INTO daily_chess_insights(
                       id,local_day,kind,evidence_json,status,created_at,updated_at
                   ) VALUES(?,?, 'outcome trend',?,'pending',?,?)""",
                (insight_id, local_day, json.dumps(payload), now, now),
            )
        missed = opportunities - found
        if opportunities >= 10 and missed >= 3:
            insight_id = hashlib.sha256(f"{local_day}\0tactical-focus\01".encode()).hexdigest()
            database.execute(
                """INSERT OR IGNORE INTO daily_chess_insights(
                       id,local_day,kind,evidence_json,status,created_at,updated_at
                   ) VALUES(?,?, 'tactical focus',?,'pending',?,?)""",
                (insight_id, local_day, json.dumps(payload), now, now),
            )
        return payload
