"""Auditable repertoire suggestions derived from gameplay, coverage, and findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import sqlite3

import chess

from ..database import connection, read_connection
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task, enqueue_task_in_transaction


RECENT_DAYS = 90
MIN_ROUTE_GAMES = 5
MIN_REACH_GAMES = 20
MIN_ROUTE_SUCCESS_RATE = 0.8
MIN_TARGET_MISSES = 3
MIN_TARGET_MISS_RATE = 0.5
MIN_PERSONAL_GAP_GAMES = 3
MIN_COHORT_PROBABILITY = 0.05
MIN_EXPLORER_GAMES = 200
MAX_RECENT_DECISION_GAMES = 200
SCORING_VERSION = 1


def _window_days(database: sqlite3.Connection) -> int:
    row = database.execute("SELECT discovery_window_days FROM settings WHERE id=1").fetchone()
    return int(row[0]) if row else RECENT_DAYS


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(repertoire_id: str, kind: str, fen_key: str, target: str) -> str:
    return hashlib.sha256(
        f"{repertoire_id}\0{kind}\0{fen_key}\0{target}".encode()
    ).hexdigest()


def _fingerprint(evidence: dict) -> str:
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def _load_decision_summary(database: sqlite3.Connection, repertoire_id: str,
                           fen_key: str, expected_uci: str) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    row = database.execute(
        """SELECT COUNT(DISTINCT event.game_id) encounter_count,
                  COUNT(DISTINCT CASE WHEN event.outcome='success' THEN event.game_id END) success_count,
                  COUNT(DISTINCT CASE WHEN event.outcome='miss' THEN event.game_id END) miss_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? THEN event.game_id END) recent_encounter_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? AND event.outcome='success' THEN event.game_id END) recent_success_count,
                  COUNT(DISTINCT CASE WHEN event.played_at>=? AND event.outcome='miss' THEN event.game_id END) recent_miss_count,
                  MAX(event.played_at) last_encountered
           FROM repertoire_decision_events event JOIN imported_games game ON game.id=event.game_id
           WHERE event.repertoire_id=? AND event.fen_key=? AND event.expected_uci=?
             AND game.adaptive_excluded=0""",
        (cutoff, cutoff, cutoff, repertoire_id, fen_key, expected_uci),
    ).fetchone()
    return {"fen_key": fen_key, "expected_uci": expected_uci, **dict(row)}


def _publish_decision_summary(database: sqlite3.Connection, repertoire_id: str, summary: dict) -> None:
    database.execute(
        """INSERT INTO repertoire_decision_gameplay_summaries(
            repertoire_id,fen_key,expected_uci,encounter_count,success_count,miss_count,
            recent_encounter_count,recent_success_count,recent_miss_count,last_encountered,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key,expected_uci)
            DO UPDATE SET encounter_count=excluded.encounter_count,
             success_count=excluded.success_count,miss_count=excluded.miss_count,
             recent_encounter_count=excluded.recent_encounter_count,
             recent_success_count=excluded.recent_success_count,
             recent_miss_count=excluded.recent_miss_count,
             last_encountered=excluded.last_encountered,updated_at=excluded.updated_at""",
        (repertoire_id, summary["fen_key"], summary["expected_uci"],
         summary["encounter_count"], summary["success_count"], summary["miss_count"],
         summary["recent_encounter_count"], summary["recent_success_count"],
         summary["recent_miss_count"], summary["last_encountered"], _now()),
    )


def _materially_new(evidence: dict, dismissed: dict) -> bool:
    return (
        evidence.get("supporting_games", 0) >= dismissed.get("supporting_games", 0) + 3
        or (bool(evidence.get("validated_recommendation"))
            and evidence.get("validated_recommendation") != dismissed.get("validated_recommendation"))
    )


def _publish(database: sqlite3.Connection, *, repertoire_id: str, kind: str,
             fen_key: str, target: str, card_id: str | None,
             opponent_move_uci: str | None, score: float, evidence: dict) -> None:
    opportunity_id = _stable_id(repertoire_id, kind, fen_key, target)
    previous = database.execute(
        "SELECT status,dismissed_evidence_json FROM repertoire_opportunities WHERE id=?",
        (opportunity_id,),
    ).fetchone()
    previous_dismissal = (
        json.loads(previous["dismissed_evidence_json"])
        if previous and previous["dismissed_evidence_json"] else {}
    )
    status = "dismissed" if previous and previous["status"] == "dismissed" and not _materially_new(evidence, previous_dismissal) else "active"
    database.execute(
        """INSERT INTO repertoire_opportunities(
             id,repertoire_id,kind,fen_key,card_id,opponent_move_uci,status,score,
             evidence_json,evidence_fingerprint,dismissed_evidence_json,created_at,updated_at,resolved_at
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
           ON CONFLICT(id) DO UPDATE SET card_id=excluded.card_id,
             opponent_move_uci=excluded.opponent_move_uci,status=excluded.status,
             score=excluded.score,evidence_json=excluded.evidence_json,
             evidence_fingerprint=excluded.evidence_fingerprint,
             dismissed_evidence_json=CASE WHEN excluded.status='active' THEN NULL
               ELSE repertoire_opportunities.dismissed_evidence_json END,
             seen_at=CASE WHEN repertoire_opportunities.status='dismissed'
                AND excluded.status='active' THEN NULL ELSE repertoire_opportunities.seen_at END,
             updated_at=excluded.updated_at,resolved_at=NULL""",
        (opportunity_id, repertoire_id, kind, fen_key, card_id,
         opponent_move_uci, status, score, json.dumps(evidence, sort_keys=True),
         _fingerprint(evidence), json.dumps(previous_dismissal) if previous_dismissal else None,
         _now(), _now()),
    )
    if kind in {"post_gap_weakness", "missing_response"} and card_id is None and status == "active":
        enqueue_task_in_transaction(
            database, "discovery_recommendation", opportunity_id,
            {"opportunity_id": opportunity_id}, priority=128,
        )


def _resolve(database: sqlite3.Connection, opportunity_id: str) -> None:
    database.execute(
        """UPDATE repertoire_opportunities SET status='resolved',resolved_at=?,updated_at=?
           WHERE id=? AND status!='resolved'""",
        (_now(), _now(), opportunity_id),
    )


def _route_keys(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> list[list[str]]:
    rows = database.execute(
        """SELECT step.line_id,step.card_id,step.decision_fen_keys_json,step.parent_card_id
           FROM opening_graph_steps step JOIN opening_graph_publications publication
             ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
           WHERE step.repertoire_id=? AND step.card_id=?""",
        (repertoire_id, card_id),
    ).fetchall()
    routes: list[list[str]] = []
    for row in rows:
        if row["card_id"] != card_id or not row["parent_card_id"]:
            continue
        keys: list[str] = []
        parent_id = row["parent_card_id"]
        seen: set[str] = set()
        complete = True
        while parent_id and parent_id not in seen:
            seen.add(parent_id)
            parent = database.execute(
                """SELECT step.decision_fen_keys_json,step.parent_card_id
                   FROM opening_graph_steps step JOIN opening_graph_publications publication
                     ON publication.repertoire_id=step.repertoire_id AND publication.generation=step.generation
                   WHERE step.repertoire_id=? AND step.line_id=? AND step.card_id=?""",
                (repertoire_id, row["line_id"], parent_id),
            ).fetchone()
            if not parent:
                complete = False
                break
            try:
                keys.extend(json.loads(parent["decision_fen_keys_json"]))
            except (TypeError, ValueError):
                complete = False
                break
            parent_id = parent["parent_card_id"]
        if complete and not parent_id and keys:
            routes.append(keys)
    return routes


def card_evidence(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> dict | None:
    inputs = _load_card_inputs(database, repertoire_id, card_id)
    return _calculate_card_evidence(inputs) if inputs else None


def _load_card_inputs(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> dict | None:
    card = database.execute(
        """SELECT id,state,introduced_at,archived,pending_validation FROM cards
           WHERE id=? AND content_type='opening'""", (card_id,),
    ).fetchone()
    if not card or card["archived"] or card["pending_validation"]:
        return None
    routes = _route_keys(database, repertoire_id, card_id)
    if not routes:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    target_rows = database.execute(
        """SELECT event.game_id FROM repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           WHERE event.repertoire_id=? AND event.card_id=? AND event.played_at>=?
             AND game.adaptive_excluded=0
           GROUP BY event.game_id ORDER BY MAX(event.played_at) DESC,event.game_id
           LIMIT ?""",
        (repertoire_id, card_id, cutoff, MAX_RECENT_DECISION_GAMES),
    ).fetchall()
    game_ids = [row["game_id"] for row in target_rows]
    if not game_ids:
        return {"card_id": card_id, "card_state": card["state"], "routes": routes, "events": []}
    events = [dict(row) for row in database.execute(
        f"""SELECT event.game_id,event.fen_key,event.outcome,event.played_at,event.ply,event.card_id
            FROM repertoire_decision_events event
            WHERE event.repertoire_id=? AND event.game_id IN ({','.join('?' for _ in game_ids)})
            ORDER BY event.game_id,event.ply""",
        (repertoire_id, *game_ids),
    ).fetchall()]
    return {"card_id": card_id, "card_state": card["state"], "routes": routes, "events": events}


def _calculate_card_evidence(inputs: dict) -> dict:
    by_game: dict[str, list[dict]] = {}
    for event in inputs["events"]:
        by_game.setdefault(event["game_id"], []).append(event)
    target_games: dict[str, dict] = {}
    route_success_count = 0
    for game_id, game_events in by_game.items():
        targets = [event for event in game_events if event["card_id"] == inputs["card_id"]]
        if not targets:
            continue
        target = targets[0]
        target_games[game_id] = target
        earlier_success_keys = {
            event["fen_key"] for event in game_events
            if event["ply"] < target["ply"] and event["outcome"] == "success"
        }
        earlier_events = [event for event in game_events if event["ply"] < target["ply"]]
        exact_route_success = any(
            route and set(route).issubset(earlier_success_keys)
            and all(event["outcome"] == "success" for event in earlier_events)
            for route in inputs["routes"]
        )
        shortest_route = min(len(route) for route in inputs["routes"])
        transposed_route_success = (
            earlier_events and all(event["outcome"] == "success" for event in earlier_events)
            and len(earlier_success_keys) >= max(1, math.ceil(shortest_route / 2))
        )
        if exact_route_success or transposed_route_success:
            route_success_count += 1
    encounter_count = len(target_games)
    miss_count = sum(event["outcome"] == "miss" for event in target_games.values())
    success_count = encounter_count - miss_count
    route_success_rate = route_success_count / encounter_count if encounter_count else 0.0
    return {
        "version": SCORING_VERSION,
        "encounter_count": encounter_count,
        "success_count": success_count,
        "miss_count": miss_count,
        "success_rate": success_count / encounter_count if encounter_count else None,
        "route_success_count": route_success_count,
        "route_success_rate": route_success_rate,
        "last_encountered": max((event["played_at"] for event in target_games.values()), default=None),
        "supporting_games": encounter_count,
        "game_ids": sorted(target_games),
        "card_state": inputs["card_state"],
        "sample_limit_games": MAX_RECENT_DECISION_GAMES,
    }


def refresh_card_opportunity(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> None:
    evidence = card_evidence(database, repertoire_id, card_id)
    _apply_card_opportunity(database, repertoire_id, card_id, evidence)
    recurring_rows = _load_recurring_decisions(database, repertoire_id, card_id)
    _apply_recurring_decisions(database, repertoire_id, card_id,
                               _calculate_recurring_decisions(recurring_rows, _window_days(database)))


def _load_recurring_decisions(database: sqlite3.Connection, repertoire_id: str,
                              card_id: str) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    return [dict(row) for row in database.execute(
        """SELECT event.game_id,event.fen_key,event.expected_uci,event.ply,
                  game.color,analysis.loss_cp,analysis.eval_after_cp,
                  analysis.mate_before,analysis.mate_after,
                  previous.eval_after_cp previous_eval_cp,previous.mate_after previous_mate,
                  following.eval_after_cp following_eval_cp,following.mate_after following_mate,
                  (SELECT COUNT(*) FROM game_move_analysis earlier
                   WHERE earlier.game_id=event.game_id AND earlier.ply<event.ply
                     AND (event.ply-earlier.ply)%2=0 AND earlier.loss_cp IS NOT NULL)
                     previous_analyzed_count,
                  (SELECT COUNT(*) FROM game_move_analysis earlier
                   WHERE earlier.game_id=event.game_id AND earlier.ply<event.ply
                     AND (event.ply-earlier.ply)%2=0 AND earlier.loss_cp>30)
                     previous_weak_count
           FROM repertoire_decision_events event
           JOIN imported_games game ON game.id=event.game_id
           LEFT JOIN game_move_analysis analysis
             ON analysis.game_id=event.game_id AND analysis.ply=event.ply
           LEFT JOIN game_move_analysis previous
             ON previous.game_id=event.game_id AND previous.ply=event.ply-2
           LEFT JOIN game_move_analysis following
             ON following.game_id=event.game_id AND following.ply=event.ply+6
           WHERE event.repertoire_id=? AND event.card_id=? AND event.played_at>=?
             AND game.adaptive_excluded=0
           ORDER BY event.fen_key,event.game_id,event.ply""",
        (repertoire_id, card_id, cutoff),
    )]


def _calculate_recurring_decisions(rows: list[dict], window_days: int) -> list[dict]:
    grouped: dict[tuple[str, str], dict[str, dict]] = {}
    for row in rows:
        grouped.setdefault((row["fen_key"], row["expected_uci"]), {})[row["game_id"]] = row
    findings = []
    for (fen_key, expected_uci), game_rows in grouped.items():
        encounters = list(game_rows.values())
        analyzed = [row for row in encounters if row["loss_cp"] is not None
                    or row["mate_after"] is not None]
        mistakes = [row for row in analyzed if
                    (row["mate_after"] is not None
                     and int(row["mate_after"]) * (1 if row["color"] == "white" else -1) < 0
                     and (row["mate_before"] is None or int(row["mate_before"]) * (1 if row["color"] == "white" else -1) >= 0))
                    or (row["loss_cp"] is not None and row["mate_after"] is None
                        and int(row["loss_cp"]) >= 100)]
        if len(encounters) < 5 or len(mistakes) < 3 or len(mistakes) * 2 < len(analyzed):
            continue
        sufficiently_analyzed = [row for row in analyzed if row["ply"] >= 2
                                 and row["previous_analyzed_count"] >= row["ply"] // 2]
        strong_prefix = [row for row in sufficiently_analyzed
                         if row["previous_weak_count"] == 0]
        complete_horizons = [row for row in mistakes if row["previous_eval_cp"] is not None
                             and row["following_eval_cp"] is not None
                             and row["previous_mate"] is None and row["following_mate"] is None]
        centipawn_mistakes = [row for row in mistakes if row["mate_after"] is None
                              and row["loss_cp"] is not None]
        mate_outcomes = [
            {"game_id": row["game_id"],
             "preceding_move_mate": row["previous_mate"],
             "third_later_turn_mate": row["following_mate"]}
            for row in mistakes if row["previous_mate"] is not None
            or row["following_mate"] is not None
        ]
        delayed_losses = [
            (int(row["previous_eval_cp"]) - int(row["following_eval_cp"]))
            * (1 if row["color"] == "white" else -1)
            for row in complete_horizons
        ]
        findings.append({
            "fen_key": fen_key, "expected_uci": expected_uci,
            "score": len(mistakes) / len(analyzed),
            "evidence": {
                "version": SCORING_VERSION, "window_days": window_days,
                "supporting_games": len(mistakes), "encounter_count": len(encounters),
                "analyzed_count": len(analyzed), "miss_count": len(mistakes),
                "strong_prefix_count": len(strong_prefix),
                "sufficient_prefix_count": len(sufficiently_analyzed),
                "strong_prefix_rate": len(strong_prefix) / len(sufficiently_analyzed) if sufficiently_analyzed else None,
                "strong_prefix_qualifies": len(sufficiently_analyzed) >= 3
                    and len(strong_prefix) / len(sufficiently_analyzed) >= 0.8,
                "immediate_average_loss_cp": round(sum(int(row["loss_cp"]) for row in centipawn_mistakes) / len(centipawn_mistakes)) if centipawn_mistakes else None,
                "immediate_cp_sample_count": len(centipawn_mistakes),
                "later_average_change_cp": round(sum(delayed_losses) / len(delayed_losses)) if delayed_losses else None,
                "later_sample_count": len(delayed_losses),
                "mate_outcomes": mate_outcomes,
                "game_ids": sorted(row["game_id"] for row in mistakes),
            },
        })
    return findings


def _apply_recurring_decisions(database: sqlite3.Connection, repertoire_id: str,
                               card_id: str, findings: list[dict]) -> None:
    active_ids = set()
    for finding in findings:
        opportunity_id = _stable_id(repertoire_id, "weak_known_decision",
                                    finding["fen_key"], f"engine:{card_id}")
        active_ids.add(opportunity_id)
        _publish(database, repertoire_id=repertoire_id, kind="weak_known_decision",
                 fen_key=finding["fen_key"], target=f"engine:{card_id}", card_id=card_id,
                 opponent_move_uci=None, score=finding["score"],
                 evidence={**finding["evidence"], "analysis_based": True})
    for existing in database.execute(
        """SELECT id FROM repertoire_opportunities WHERE repertoire_id=?
           AND kind='weak_known_decision' AND card_id=? AND status='active'
           AND json_extract(evidence_json,'$.analysis_based')=1""",
        (repertoire_id, card_id),
    ).fetchall():
        if existing["id"] not in active_ids:
            _resolve(database, existing["id"])


def _apply_card_opportunity(database: sqlite3.Connection, repertoire_id: str,
                            card_id: str, evidence: dict | None) -> bool:
    target = database.execute(
        """SELECT fen_key,expected_uci FROM repertoire_decision_events
           WHERE repertoire_id=? AND card_id=? ORDER BY played_at DESC LIMIT 1""",
        (repertoire_id, card_id),
    ).fetchone()
    if evidence and target:
        database.execute(
            """UPDATE repertoire_decision_gameplay_summaries SET route_success_count=?,updated_at=?
               WHERE repertoire_id=? AND fen_key=? AND expected_uci=?""",
            (evidence["route_success_count"], _now(), repertoire_id,
             target["fen_key"], target["expected_uci"]),
        )
    existing = database.execute(
        """SELECT id FROM repertoire_opportunities WHERE repertoire_id=?
           AND kind='weak_known_decision' AND card_id=?
           AND json_extract(evidence_json,'$.analysis_based') IS NULL""", (repertoire_id, card_id),
    ).fetchall()
    qualifies = bool(evidence and evidence["card_state"] == "locked"
                     and evidence["encounter_count"] >= MIN_ROUTE_GAMES
                     and evidence["route_success_rate"] >= MIN_ROUTE_SUCCESS_RATE
                     and evidence["miss_count"] >= MIN_TARGET_MISSES
                     and evidence["miss_count"] / evidence["encounter_count"] >= MIN_TARGET_MISS_RATE)
    if not qualifies:
        for row in existing:
            _resolve(database, row["id"])
        return False
    if not target:
        return False
    fen_key = target["fen_key"]
    last_encountered = datetime.fromisoformat(evidence["last_encountered"].replace("Z", "+00:00"))
    age_days = max(0.0, (datetime.now(timezone.utc) - last_encountered).total_seconds() / 86400)
    score = min(1.0, evidence["route_success_count"] / 20) * (
        evidence["miss_count"] / evidence["encounter_count"]
    ) * (2 ** (-age_days / RECENT_DAYS))
    _publish(database, repertoire_id=repertoire_id, kind="weak_known_decision",
             fen_key=fen_key, target=card_id, card_id=card_id,
             opponent_move_uci=None, score=score, evidence=evidence)
    for row in existing:
        if row["id"] != _stable_id(repertoire_id, "weak_known_decision", fen_key, card_id):
            _resolve(database, row["id"])
    active = database.execute(
        """SELECT 1 FROM repertoire_opportunities WHERE id=? AND status='active'""",
        (_stable_id(repertoire_id, "weak_known_decision", fen_key, card_id),),
    ).fetchone()
    return bool(active)


def refresh_node_opportunities(database: sqlite3.Connection, repertoire_id: str, node_id: str) -> None:
    inputs = _load_node_inputs(database, repertoire_id, node_id)
    if inputs:
        _apply_node_opportunities(database, repertoire_id, _calculate_node_opportunities(inputs))


def _load_node_inputs(database: sqlite3.Connection, repertoire_id: str, node_id: str) -> dict | None:
    node = database.execute(
        """SELECT n.*,r.settings_json,r.status run_status,r.created_at run_created_at FROM repertoire_coverage_nodes n
           JOIN repertoire_coverage_runs r ON r.id=n.run_id WHERE n.id=? AND n.repertoire_id=?""",
        (node_id, repertoire_id),
    ).fetchone()
    if not node:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    personal = database.execute(
        """SELECT occurrence.move_uci,COUNT(DISTINCT occurrence.game_id) encounters
           FROM game_position_occurrences occurrence
           JOIN imported_games game ON game.id=occurrence.game_id
           JOIN game_repertoire_matches match ON match.game_id=game.id
             AND match.repertoire_id=?
           WHERE occurrence.fen_key=? AND occurrence.move_uci IS NOT NULL
             AND game.color=? AND game.adaptive_excluded=0 AND game.played_at>=?
           GROUP BY occurrence.move_uci""",
        (repertoire_id, node["fen_key"], node["trained_color"], cutoff),
    ).fetchall()
    relevant_games = database.execute(
        """SELECT COUNT(DISTINCT game.id) FROM game_repertoire_matches match
           JOIN imported_games game ON game.id=match.game_id
           WHERE match.repertoire_id=? AND game.color=? AND game.adaptive_excluded=0
             AND game.played_at>=?""",
        (repertoire_id, node["trained_color"], cutoff),
    ).fetchone()[0]
    consequences = database.execute(
        """SELECT json_extract(evidence_json,'$.opponent_gap_move_uci') move_uci,
                  COUNT(DISTINCT game_id) game_count,
                  MAX(CAST(json_extract(evidence_json,'$.mistake_loss_cp') AS INTEGER)) max_loss_cp
           FROM game_findings WHERE repertoire_id=? AND kind='repertoire gap'
             AND status!='ignored' AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
           GROUP BY move_uci""",
        (repertoire_id, node["fen_key"]),
    ).fetchall()
    rows = [dict(row) for row in database.execute(
        "SELECT * FROM repertoire_coverage_candidates WHERE node_id=?", (node_id,),
    ).fetchall()]
    return {"node": dict(node), "personal_counts": {row["move_uci"]: row["encounters"] for row in personal},
            "personal_repertoire_games": relevant_games,
            "consequences": {row["move_uci"]: {"game_count": row["game_count"],
                                                "max_loss_cp": row["max_loss_cp"]} for row in consequences},
            "candidates": rows}


def _calculate_node_opportunities(inputs: dict) -> list[dict]:
    node = inputs["node"]
    settings = json.loads(node["settings_json"])
    source_is_fresh = node["run_created_at"] >= (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    personal_counts = inputs["personal_counts"]
    candidates = {row["move_uci"]: row for row in inputs["candidates"]}
    position_encounters = sum(inputs["personal_counts"].values())
    repertoire_game_count = int(inputs["personal_repertoire_games"])
    position_reach = (
        min(1.0, position_encounters / repertoire_game_count)
        if repertoire_game_count >= MIN_REACH_GAMES else None
    )
    covered_moves = set(json.loads(node["covered_replies_json"]))
    decisions: list[dict] = []
    for move_uci in set(candidates) | set(personal_counts):
        candidate = candidates.get(move_uci)
        explorer_ready = source_is_fresh and node["explorer_status"] == "complete" and node["explorer_games"] >= MIN_EXPLORER_GAMES
        maia_ready = source_is_fresh and node["maia_status"] == "complete"
        explorer_probability = candidate["explorer_probability"] if candidate and explorer_ready else None
        maia_probability = candidate["maia_probability"] if candidate and maia_ready else None
        personal_count = int(personal_counts.get(move_uci, 0))
        qualifying_sources = sorted(source for source, qualifies in (
            ("explorer", explorer_probability is not None and explorer_probability >= MIN_COHORT_PROBABILITY),
            ("maia", maia_probability is not None and maia_probability >= MIN_COHORT_PROBABILITY),
            ("personal", personal_count >= MIN_PERSONAL_GAP_GAMES),
        ) if qualifies)
        below_reach_floor = (position_reach is not None and
                             position_reach < float(settings.get("path_floor", 0.0005)) and
                             personal_count < MIN_PERSONAL_GAP_GAMES)
        if move_uci in covered_moves or (candidate and candidate["covered"]) or not qualifying_sources or below_reach_floor:
            decisions.append({"fen_key": node["fen_key"], "move_uci": move_uci, "active": False})
            continue
        practical_probability = max((value for value in (explorer_probability, maia_probability) if value is not None), default=0.0)
        personal_frequency = personal_count / position_encounters if position_encounters else None
        consequence = inputs["consequences"].get(move_uci)
        consequence_factor = 1 + min(1.0, (int(consequence["max_loss_cp"] or 0) / 250)) if consequence else 1.0
        evidence = {
            "version": SCORING_VERSION, "supporting_games": personal_count,
            "personal_count": personal_count, "personal_frequency": personal_frequency,
            "personal_position_encounters": position_encounters,
            "personal_repertoire_games": repertoire_game_count,
            "position_reach_probability": position_reach,
            "position_reach_status": "personal-estimate" if position_reach is not None else "unknown",
            "explorer_probability": explorer_probability,
            "maia_probability": maia_probability, "explorer_games": node["explorer_games"],
            "explorer_status": "stale" if not source_is_fresh and node["explorer_status"] == "complete" else node["explorer_status"],
            "maia_status": "stale" if not source_is_fresh and node["maia_status"] == "complete" else node["maia_status"],
            "cohort": {key: settings.get(key) for key in ("explorer_rating", "maia_elo", "speed_weights")},
            "qualifying_sources": qualifying_sources, "coverage_run_id": node["run_id"],
            "coverage_node_id": node["id"],
            "coverage_path_floor": float(settings.get("path_floor", 0.0005)),
            "run_status": node["run_status"],
            "consequence": consequence,
        }
        score = (position_reach if position_reach is not None else 1.0) * max(
            practical_probability, personal_frequency or 0.0
        ) * (1 + min(1.0, personal_count / 10)) * consequence_factor
        decisions.append({"fen_key": node["fen_key"], "move_uci": move_uci,
                          "active": True, "score": score, "evidence": evidence})
    return decisions


def _apply_node_opportunities(database: sqlite3.Connection, repertoire_id: str,
                              decisions: list[dict]) -> None:
    for decision in decisions:
        move_uci = decision["move_uci"]
        fen_key = decision["fen_key"]
        if not decision["active"]:
            _resolve(database, _stable_id(repertoire_id, "missing_response", fen_key, move_uci))
            continue
        _publish(database, repertoire_id=repertoire_id, kind="missing_response",
                 fen_key=fen_key, target=move_uci, card_id=None,
                 opponent_move_uci=move_uci, score=decision["score"],
                 evidence=decision["evidence"])


def refresh_post_gap_opportunity(database: sqlite3.Connection, repertoire_id: str, finding_id: str) -> None:
    inputs = _load_post_gap_inputs(database, repertoire_id, finding_id)
    if inputs:
        _apply_post_gap_opportunity(database, repertoire_id, _calculate_post_gap_opportunity(inputs))


def _load_post_gap_inputs(database: sqlite3.Connection, repertoire_id: str, finding_id: str) -> dict | None:
    finding = database.execute(
        "SELECT * FROM game_findings WHERE id=? AND repertoire_id=? AND kind='repertoire gap'",
        (finding_id, repertoire_id),
    ).fetchone()
    if not finding:
        return None
    evidence = json.loads(finding["evidence_json"])
    fen_key = evidence.get("opponent_gap_fen_key")
    move_uci = evidence.get("opponent_gap_move_uci")
    if not fen_key or not move_uci:
        return None
    findings = [dict(row) for row in database.execute(
        """SELECT id,game_id,analysis_version,card_id,evidence_json FROM game_findings
           WHERE repertoire_id=? AND kind='repertoire gap' AND status!='ignored'
             AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
             AND json_extract(evidence_json,'$.opponent_gap_move_uci')=?
           ORDER BY updated_at DESC,id LIMIT 200""",
        (repertoire_id, fen_key, move_uci),
    ).fetchall()]
    return {"fen_key": fen_key, "move_uci": move_uci, "findings": findings}


def _calculate_post_gap_opportunity(inputs: dict) -> dict:
    fen_key = inputs["fen_key"]
    move_uci = inputs["move_uci"]
    findings = inputs["findings"]
    supporting = []
    for row in findings:
        item = json.loads(row["evidence_json"])
        if item.get("opponent_gap_fen_key") == fen_key and item.get("opponent_gap_move_uci") == move_uci:
            supporting.append({"finding_id": row["id"], "game_id": row["game_id"],
                               "analysis_version": row["analysis_version"],
                               "mistake_ply": item.get("mistake_ply"),
                               "mistake_loss_cp": item.get("mistake_loss_cp")})
    if not supporting:
        return {"fen_key": fen_key, "move_uci": move_uci, "active": False}
    supporting_ids = {item["finding_id"] for item in supporting}
    linked_card = next((row["card_id"] for row in findings
                        if row["id"] in supporting_ids and row["card_id"]), None)
    result = {"version": SCORING_VERSION, "supporting_games": len({item["game_id"] for item in supporting}),
              "findings": supporting, "opponent_gap_fen_key": fen_key,
              "opponent_gap_move_uci": move_uci,
              "max_loss_cp": max(int(item["mistake_loss_cp"] or 0) for item in supporting),
              "finding_ids": sorted(supporting_ids)}
    score = min(1.0, result["supporting_games"] / 5) * max(1, result["max_loss_cp"] / 100)
    return {"fen_key": fen_key, "move_uci": move_uci, "active": True,
            "card_id": linked_card, "score": score, "evidence": result}


def _apply_post_gap_opportunity(database: sqlite3.Connection, repertoire_id: str, decision: dict) -> None:
    fen_key = decision["fen_key"]
    move_uci = decision["move_uci"]
    if not decision["active"]:
        _resolve(database, _stable_id(repertoire_id, "post_gap_weakness", fen_key, move_uci))
        return
    _publish(database, repertoire_id=repertoire_id, kind="post_gap_weakness",
             fen_key=fen_key, target=move_uci, card_id=decision["card_id"],
             opponent_move_uci=move_uci, score=decision["score"], evidence=decision["evidence"])


def enqueue_opportunity_refresh(repertoire_id: str, *, background: bool = False) -> None:
    enqueue_task("repertoire_opportunity", repertoire_id,
                 {"repertoire_id": repertoire_id, "phase": "summaries", "cursor": ""},
                 priority=130, delay_seconds=5 if background else 0,
                 foreground=not background)


def _advance_slice(database: sqlite3.Connection, task: dict, phase: str, cursor: str) -> None:
    database.execute(
        """UPDATE background_tasks SET generation=generation+1,state='queued',phase='queued',
             payload_json=?,attempt_count=0,next_attempt_at=?,lease_token=NULL,
             lease_expires_at=NULL,updated_at=?
           WHERE id=? AND generation=? AND lease_token=? AND state='leased'""",
        (json.dumps({"repertoire_id": task["payload"]["repertoire_id"],
                     "phase": phase, "cursor": cursor}), _now(), _now(),
         task["id"], task["generation"], task["lease_token"]),
    )


def _cleanup_opportunity(database: sqlite3.Connection, repertoire_id: str, opportunity_id: str) -> None:
    opportunity = database.execute(
        "SELECT * FROM repertoire_opportunities WHERE id=? AND repertoire_id=?",
        (opportunity_id, repertoire_id),
    ).fetchone()
    if not opportunity or opportunity["status"] == "resolved":
        return
    if opportunity["admission_state"] == "preparing":
        return
    if opportunity["kind"] == "weak_known_decision" and json.loads(opportunity["evidence_json"]).get("analysis_based"):
        return
    if opportunity["kind"] == "weak_known_decision":
        card = database.execute("SELECT state,archived FROM cards WHERE id=?", (opportunity["card_id"],)).fetchone()
        if not card or card["state"] != "locked" or card["archived"]:
            _resolve(database, opportunity_id)
    elif opportunity["kind"] == "missing_response":
        node = database.execute(
            """SELECT covered_replies_json FROM repertoire_coverage_nodes WHERE repertoire_id=?
               AND fen_key=? AND run_id=(SELECT id FROM repertoire_coverage_runs
               WHERE repertoire_id=? ORDER BY CASE WHEN status='complete' THEN 0 ELSE 1 END,
               created_at DESC LIMIT 1)""",
            (repertoire_id, opportunity["fen_key"], repertoire_id),
        ).fetchone()
        if not node or opportunity["opponent_move_uci"] in json.loads(node["covered_replies_json"]):
            _resolve(database, opportunity_id)
    else:
        if opportunity["card_id"]:
            linked_card = database.execute(
                "SELECT introduced_at FROM cards WHERE id=?", (opportunity["card_id"],),
            ).fetchone()
            if linked_card and linked_card["introduced_at"]:
                _resolve(database, opportunity_id)
                return
        covered_node = database.execute(
            """SELECT covered_replies_json FROM repertoire_coverage_nodes WHERE repertoire_id=?
               AND fen_key=? AND run_id=(SELECT id FROM repertoire_coverage_runs
               WHERE repertoire_id=? ORDER BY CASE WHEN status='complete' THEN 0 ELSE 1 END,
               created_at DESC LIMIT 1)""",
            (repertoire_id, opportunity["fen_key"], repertoire_id),
        ).fetchone()
        if covered_node and opportunity["opponent_move_uci"] in json.loads(covered_node["covered_replies_json"]):
            _resolve(database, opportunity_id)
            return
        finding = database.execute(
            """SELECT 1 FROM game_findings WHERE repertoire_id=? AND kind='repertoire gap'
               AND status!='ignored' AND json_extract(evidence_json,'$.opponent_gap_fen_key')=?
               AND json_extract(evidence_json,'$.opponent_gap_move_uci')=? LIMIT 1""",
            (repertoire_id, opportunity["fen_key"], opportunity["opponent_move_uci"]),
        ).fetchone()
        if not finding:
            _resolve(database, opportunity_id)


def execute_opportunity_slice(task: dict) -> bool:
    """Process exactly one decision, card, coverage node, finding, or cleanup item."""
    repertoire_id = task["payload"]["repertoire_id"]
    phase = task["payload"].get("phase", "cards")
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    with read_connection() as database:
        if phase == "summaries":
            fen_cursor, _, move_cursor = cursor.partition("\0")
            item = database.execute(
                """SELECT fen_key,expected_uci FROM repertoire_decision_events
                   WHERE repertoire_id=? AND (fen_key>? OR (fen_key=? AND expected_uci>?))
                   GROUP BY fen_key,expected_uci ORDER BY fen_key,expected_uci LIMIT 1""",
                (repertoire_id, fen_cursor, fen_cursor, move_cursor),
            ).fetchone()
        elif phase == "cards":
            item = database.execute(
                """SELECT DISTINCT step.card_id id FROM opening_graph_steps step
                   JOIN opening_graph_publications publication ON publication.repertoire_id=step.repertoire_id
                    AND publication.generation=step.generation
                   WHERE step.repertoire_id=? AND step.card_id>? ORDER BY step.card_id LIMIT 1""",
                (repertoire_id, cursor),
            ).fetchone()
        elif phase == "nodes":
            item = database.execute(
                """SELECT id FROM repertoire_coverage_nodes WHERE repertoire_id=? AND run_id=(
                    SELECT id FROM repertoire_coverage_runs WHERE repertoire_id=?
                    ORDER BY CASE WHEN status='complete' THEN 0 ELSE 1 END,created_at DESC LIMIT 1)
                   AND id>? ORDER BY id LIMIT 1""", (repertoire_id, repertoire_id, cursor),
            ).fetchone()
        elif phase == "findings":
            item = database.execute(
                """SELECT id FROM game_findings WHERE repertoire_id=? AND kind='repertoire gap'
                   AND id>? ORDER BY id LIMIT 1""", (repertoire_id, cursor),
            ).fetchone()
        elif phase == "cleanup":
            item = database.execute(
                """SELECT id FROM repertoire_opportunities WHERE repertoire_id=? AND id>?
                   AND status!='resolved' ORDER BY id LIMIT 1""", (repertoire_id, cursor),
            ).fetchone()
        else:
            fen_cursor, _, move_cursor = cursor.partition("\0")
            item = database.execute(
                """SELECT fen_key,expected_uci FROM repertoire_decision_gameplay_summaries
                   WHERE repertoire_id=? AND (fen_key>? OR (fen_key=? AND expected_uci>?))
                   ORDER BY fen_key,expected_uci LIMIT 1""",
                (repertoire_id, fen_cursor, fen_cursor, move_cursor),
            ).fetchone()
        item_id = (
            f"{item['fen_key']}\0{item['expected_uci']}"
            if item and phase in {"summaries", "summary_cleanup"}
            else item["id"] if item else None
        )
    if not item_id:
        next_phase = {"summaries": "cards", "cards": "nodes", "nodes": "findings",
                      "findings": "cleanup", "cleanup": "summary_cleanup"}.get(phase)
        if next_phase:
            activity_gate.wait_for_foreground()
            with connection(background=True) as database:
                _advance_slice(database, task, next_phase, "")
            return True
        return False
    with read_connection() as database:
        if phase == "summaries":
            fen_key, expected_uci = item_id.split("\0", 1)
            inputs = _load_decision_summary(database, repertoire_id, fen_key, expected_uci)
        elif phase == "cards":
            inputs = _load_card_inputs(database, repertoire_id, item_id)
            recurring_rows = _load_recurring_decisions(database, repertoire_id, item_id)
            window_days = _window_days(database)
        elif phase == "nodes":
            inputs = _load_node_inputs(database, repertoire_id, item_id)
        elif phase == "findings":
            inputs = _load_post_gap_inputs(database, repertoire_id, item_id)
        else:
            inputs = None
    evidence = _calculate_card_evidence(inputs) if phase == "cards" and inputs else None
    recurring_decisions = _calculate_recurring_decisions(recurring_rows, window_days) if phase == "cards" else []
    node_decisions = _calculate_node_opportunities(inputs) if phase == "nodes" and inputs else []
    post_gap_decision = _calculate_post_gap_opportunity(inputs) if phase == "findings" and inputs else None
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        current = database.execute(
            "SELECT generation,lease_token FROM background_tasks WHERE id=?",
            (task["id"],),
        ).fetchone()
        if not current or current["generation"] != task["generation"] or current["lease_token"] != task["lease_token"]:
            return True
        if phase == "summaries" and inputs:
            _publish_decision_summary(database, repertoire_id, inputs)
        elif phase == "cards":
            card_became_trainable = _apply_card_opportunity(database, repertoire_id, item_id, evidence)
            _apply_recurring_decisions(database, repertoire_id, item_id, recurring_decisions)
        elif phase == "nodes" and inputs:
            _apply_node_opportunities(database, repertoire_id, node_decisions)
        elif phase == "findings" and post_gap_decision:
            _apply_post_gap_opportunity(database, repertoire_id, post_gap_decision)
        elif phase == "cleanup":
            _cleanup_opportunity(database, repertoire_id, item_id)
        elif phase == "summary_cleanup":
            fen_key, expected_uci = item_id.split("\0", 1)
            database.execute(
                """DELETE FROM repertoire_decision_gameplay_summaries WHERE repertoire_id=?
                   AND fen_key=? AND expected_uci=? AND NOT EXISTS(
                       SELECT 1 FROM repertoire_decision_events event
                       WHERE event.repertoire_id=? AND event.fen_key=? AND event.expected_uci=?)""",
                (repertoire_id, fen_key, expected_uci, repertoire_id, fen_key, expected_uci),
            )
        _advance_slice(database, task, phase, item_id)
    if phase == "cards" and card_became_trainable:
        from datetime import date
        today = date.today().isoformat()
        enqueue_task("daily_queue", "current", {"queue_date": today}, priority=10, foreground=False)
    return True


def list_opportunities(database: sqlite3.Connection, repertoire_id: str,
                       opportunity_ids: list[str] | None = None) -> list[dict]:
    result = []
    if opportunity_ids is not None and not opportunity_ids:
        return result
    accepted_by_position: dict[str, set[str]] = {}
    for line in database.execute(
        "SELECT start_fen,moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=?",
        (repertoire_id,),
    ):
        board = chess.Board(line["start_fen"])
        learner_turn = line["trained_color"] == "white"
        for move_uci in json.loads(line["moves_json"]):
            try:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                if board.turn == learner_turn:
                    accepted_by_position.setdefault(" ".join(board.fen().split()[:4]), set()).add(move_uci)
                board.push(move)
            except ValueError:
                break
    identifier_clause = (
        f" AND opportunity.id IN ({','.join('?' for _ in opportunity_ids)})"
        if opportunity_ids is not None else ""
    )
    limit_clause = "" if opportunity_ids is not None else " LIMIT 100"
    for row in database.execute(
                f"""SELECT opportunity.*,card.trained_color card_trained_color
                   FROM repertoire_opportunities opportunity
                   LEFT JOIN cards card ON card.id=opportunity.card_id
                   WHERE opportunity.repertoire_id=? AND opportunity.status='active'
                   {identifier_clause}
                   ORDER BY opportunity.score DESC,opportunity.id{limit_clause}""",
                (repertoire_id, *(opportunity_ids or [])),
            ):
        fen_key = row["fen_key"]
        evidence = json.loads(row["evidence_json"])
        finding_plies = {finding.get("game_id"): finding.get("mistake_ply")
                         for finding in evidence.get("findings", []) if finding.get("game_id")}
        supporting_game_ids = evidence.get("game_ids") or list(finding_plies)
        source_games = []
        distinct_routes: list[str] = []
        for game_id in supporting_game_ids[:5]:
            game = database.execute(
                "SELECT id,game_url,start_fen,moves_json FROM imported_games WHERE id=?",
                (game_id,),
            ).fetchone()
            event = database.execute(
                """SELECT ply FROM repertoire_decision_events WHERE repertoire_id=?
                   AND game_id=? AND fen_key=? ORDER BY ply LIMIT 1""",
                (repertoire_id, game_id, fen_key),
            ).fetchone()
            if not game:
                continue
            target_ply = event["ply"] if event else finding_plies.get(game_id)
            if target_ply is None:
                continue
            board = chess.Board(game["start_fen"])
            notation: list[str] = []
            for move_uci in json.loads(game["moves_json"])[:target_ply + 1]:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                notation.append(board.san(move))
                board.push(move)
            route = " ".join(notation)
            if route and route not in distinct_routes:
                distinct_routes.append(route)
            source_games.append({"id": game["id"], "url": game["game_url"], "route": route})
        decision_fen = f"{fen_key} 0 1"
        decision_start_fen = decision_fen
        decision_route_uci: list[str] = []
        trained_color = row["card_trained_color"] or ("white" if fen_key.split()[1] == "w" else "black")
        if row["kind"] == "missing_response" and row["opponent_move_uci"]:
            node = database.execute(
                "SELECT fen,routes_json,trained_color FROM repertoire_coverage_nodes WHERE id=?",
                (evidence.get("coverage_node_id"),),
            ).fetchone()
            try:
                decision_board = chess.Board(node["fen"] if node else decision_fen)
                decision_board.push_uci(row["opponent_move_uci"])
                decision_fen = decision_board.fen()
                trained_color = node["trained_color"] if node else ("white" if decision_board.turn else "black")
                routes_uci = json.loads(node["routes_json"]) if node else []
                for route_uci in sorted(routes_uci, key=lambda route: (len(route), route)):
                    for line in database.execute(
                        "SELECT start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=? ORDER BY id",
                        (repertoire_id,),
                    ):
                        if json.loads(line["moves_json"])[:len(route_uci)] != route_uci:
                            continue
                        route_board = chess.Board(line["start_fen"])
                        try:
                            for move_uci in [*route_uci, row["opponent_move_uci"]]:
                                route_board.push_uci(move_uci)
                        except ValueError:
                            continue
                        if " ".join(route_board.fen().split()[:4]) == " ".join(decision_fen.split()[:4]):
                            decision_start_fen = line["start_fen"]
                            decision_route_uci = [*route_uci, row["opponent_move_uci"]]
                            break
                    if decision_route_uci:
                        break
            except ValueError:
                pass
        elif evidence.get("findings"):
            for finding in evidence["findings"]:
                game_id, mistake_ply = finding.get("game_id"), finding.get("mistake_ply")
                if game_id is None or not isinstance(mistake_ply, int):
                    continue
                game = database.execute(
                    "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,),
                ).fetchone()
                if not game:
                    continue
                route_uci = json.loads(game["moves_json"])[:mistake_ply]
                route_board = chess.Board(game["start_fen"])
                try:
                    for move_uci in route_uci:
                        route_board.push_uci(move_uci)
                except ValueError:
                    continue
                if " ".join(route_board.fen().split()[:4]) == fen_key:
                    decision_start_fen = game["start_fen"]
                    decision_route_uci = route_uci
                    decision_fen = route_board.fen()
                    break
        result.append({
            "id": row["id"], "repertoire_id": repertoire_id,
            "kind": row["kind"], "status": row["status"],
            "fen_key": fen_key, "fen": f"{fen_key} 0 1",
            "card_id": row["card_id"], "opponent_move_uci": row["opponent_move_uci"],
            "trained_color": trained_color,
            "decision_fen": decision_fen,
            "decision_start_fen": decision_start_fen,
            "decision_route_uci": decision_route_uci,
            "accepted_moves_uci": sorted(accepted_by_position.get(
                " ".join(decision_fen.split()[:4]), set())),
            "score": row["score"], "evidence": evidence,
            "evidence_fingerprint": row["evidence_fingerprint"],
            "seen_at": row["seen_at"], "snoozed_until": row["snoozed_until"],
            "admission_state": row["admission_state"], "admitted_card_id": row["admitted_card_id"],
            "unread": (row["seen_at"] is None and not row["snoozed_until"])
                or bool(row["snoozed_until"] and row["snoozed_until"] <= _now()),
            "source_games": source_games, "routes": distinct_routes,
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })
    return result


def dismiss_opportunity(database: sqlite3.Connection, repertoire_id: str, opportunity_id: str) -> bool:
    row = database.execute(
        "SELECT evidence_json FROM repertoire_opportunities WHERE id=? AND repertoire_id=? AND status='active'",
        (opportunity_id, repertoire_id),
    ).fetchone()
    if not row:
        return False
    database.execute(
        """UPDATE repertoire_opportunities SET status='dismissed',dismissed_evidence_json=?,updated_at=?
           WHERE id=?""", (row["evidence_json"], _now(), opportunity_id),
    )
    return True


def acknowledge_opportunity(database: sqlite3.Connection, repertoire_id: str,
                            opportunity_id: str) -> bool:
    result = database.execute(
        """UPDATE repertoire_opportunities SET seen_at=?,snoozed_until=NULL,updated_at=?
           WHERE id=? AND repertoire_id=? AND status='active'""",
        (_now(), _now(), opportunity_id, repertoire_id),
    )
    return result.rowcount > 0


def snooze_opportunity(database: sqlite3.Connection, repertoire_id: str,
                       opportunity_id: str) -> bool:
    until = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    result = database.execute(
        """UPDATE repertoire_opportunities SET seen_at=COALESCE(seen_at,?),
             snoozed_until=?,updated_at=?
           WHERE id=? AND repertoire_id=? AND status='active'""",
        (_now(), until, _now(), opportunity_id, repertoire_id),
    )
    return result.rowcount > 0


def admit_existing_decision(database: sqlite3.Connection, repertoire_id: str,
                            opportunity_id: str) -> dict:
    """Explicitly queue a saved decision without changing ancestor mastery or reviews."""
    opportunity = database.execute(
        """SELECT * FROM repertoire_opportunities WHERE id=? AND repertoire_id=?""",
        (opportunity_id, repertoire_id),
    ).fetchone()
    if not opportunity:
        raise KeyError("Discovery not found")
    if opportunity["admission_state"] == "queued" and opportunity["admitted_card_id"]:
        return {"card_id": opportunity["admitted_card_id"], "queued": True,
                "idempotent": True}
    if opportunity["status"] != "active":
        raise ValueError("Discovery is no longer actionable")
    if not opportunity["card_id"]:
        raise ValueError("This continuation is not saved in the repertoire")
    card = database.execute(
        """SELECT * FROM cards WHERE id=? AND content_type='opening'
           AND archived=0 AND COALESCE(pending_validation,0)=0""",
        (opportunity["card_id"],),
    ).fetchone()
    if not card:
        raise ValueError("The saved card is unavailable or awaiting validation")
    if database.execute(
        """SELECT 1 FROM repertoire_integrity_card_blocks
           WHERE repertoire_id=? AND card_id=?""",
        (repertoire_id, card["id"]),
    ).fetchone():
        raise ValueError("Repair this repertoire before training the decision")
    if not database.execute(
        """SELECT 1 FROM repertoire_cards WHERE repertoire_id=? AND card_id=?""",
        (repertoire_id, card["id"]),
    ).fetchone() and card["repertoire_id"] != repertoire_id:
        raise ValueError("The card is no longer in this repertoire")
    target_card_id = card["id"]
    if card["kind"] == "prefix":
        from .prefix_split import apply_prefix_split, preview_prefix_split
        preview = preview_prefix_split(database, card["id"])
        continuation_board = chess.Board(preview["continuation"]["starting_fen"])
        learner_color = chess.WHITE if card["trained_color"] == "white" else chess.BLACK
        for move_uci in preview["continuation"]["moves"]:
            if continuation_board.turn == learner_color:
                break
            continuation_board.push_uci(move_uci)
        continuation_key = " ".join(continuation_board.fen().split()[:4])
        if continuation_key != opportunity["fen_key"]:
            raise ValueError("The target decision cannot be isolated from this prefix card")
        split = apply_prefix_split(database, card["id"], int(card["revision"]))
        target_card_id = split["continuation"]["card_id"]
    else:
        board = chess.Board(card["start_fen"])
        trained_color = chess.WHITE if card["trained_color"] == "white" else chess.BLACK
        learner_decisions = 0
        decision_fen_key = None
        for move_uci in json.loads(card["moves_json"]):
            if board.turn == trained_color:
                learner_decisions += 1
                decision_fen_key = " ".join(board.fen().split()[:4])
            board.push_uci(move_uci)
        if learner_decisions != 1:
            raise ValueError("This card contains more than one decision")
        if decision_fen_key != opportunity["fen_key"]:
            raise ValueError("The saved card no longer tests this decision")
    from .review_service import ensure_card_queued_after
    from datetime import date
    today = date.today().isoformat()
    database.execute(
        """UPDATE cards SET state=CASE WHEN state IN ('locked','new') THEN 'learning' ELSE state END,
             introduced_at=COALESCE(introduced_at,?) WHERE id=?""",
        (today, target_card_id),
    )
    ensure_card_queued_after(database, target_card_id, after_cards=0,
                             attempt_state="guided", priority_reason="Discovery · train this decision")
    database.execute(
        """UPDATE daily_queue SET admission_kind='explicit',admission_source=?
           WHERE queue_date=? AND card_id=? AND status='queued'""",
        (f"discovery:{opportunity_id}", today, target_card_id),
    )
    database.execute(
        """UPDATE repertoire_opportunities SET admission_state='queued',
             admitted_card_id=?,seen_at=COALESCE(seen_at,?),updated_at=? WHERE id=?""",
        (target_card_id, _now(), _now(), opportunity_id),
    )
    return {"card_id": target_card_id, "queued": True, "idempotent": False}
