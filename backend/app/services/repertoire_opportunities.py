"""Auditable repertoire suggestions derived from gameplay, coverage, and findings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from functools import lru_cache
import math
import sqlite3

import chess

from tempo_legacy_database.connections import background_read_connection, connection, read_connection
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.authored_repertoires.opportunities import storage
from .activity_gate import activity_gate
from .durable_tasks import enqueue_task, enqueue_task_in_transaction, enqueue_compact_postgres_task_in_transaction, lock_current_slice
from .discovery_admission import _full_history_request, _source_game
from .canonical_scope_freshness import scope_identity, latest_coverage_run_id, opportunity_is_current, game_scope_generation, opportunity_scope_predicate
from .canonical_prefix import read_prefix, assumed_position_keys


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
SCORING_VERSION = 2


def _window_days(database: sqlite3.Connection) -> int:
    row = storage.discovery_window(database)
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
    row = storage.decision_game_summary(database, (cutoff, cutoff, cutoff, repertoire_id, fen_key, expected_uci))
    return {"fen_key": fen_key, "expected_uci": expected_uci, **dict(row)}


def _publish_decision_summary(database: sqlite3.Connection, repertoire_id: str, summary: dict) -> None:
    storage.upsert_decision_summary(
        database,
        (repertoire_id, summary["fen_key"], summary["expected_uci"],
         summary["encounter_count"], summary["success_count"], summary["miss_count"],
         summary["recent_encounter_count"], summary["recent_success_count"],
         summary["recent_miss_count"], summary["last_encountered"], _now()),
    )


def _materially_new(evidence: dict, handled_evidence: dict) -> bool:
    return evidence.get("supporting_games", 0) >= handled_evidence.get("supporting_games", 0) + 3


def _publish(database: sqlite3.Connection, *, repertoire_id: str, kind: str,
             fen_key: str, target: str, card_id: str | None,
             opponent_move_uci: str | None, score: float, evidence: dict) -> None:
    if storage.requires_write_start(database):
        storage.begin_write(database)
    identity = scope_identity(database, repertoire_id, lock=True)
    game_generation = game_scope_generation(database, lock=True)
    opportunity_id = _stable_id(repertoire_id, kind, fen_key, target)
    # Serialize publication with admission completion: the read must describe
    # the row whose handled/preparing state this transaction will replace.
    if storage.requires_write_start(database):
        storage.begin_write(database)
    read_previous = storage.publication_previous_reader(database)
    published_fingerprint = _fingerprint(evidence)
    previous = read_previous((opportunity_id,))
    previous_dismissal = (
        json.loads(previous["dismissed_evidence_json"])
        if previous and previous["dismissed_evidence_json"] is not None else None
    )
    handled_evidence = (json.loads(previous["handled_evidence_json"])
                        if previous and previous["handled_evidence_json"] else None)
    reopened = handled_evidence is not None and _materially_new(evidence, handled_evidence)
    preparing_revision_changed = bool(previous and previous["admission_state"] == "preparing"
                                      and previous["evidence_fingerprint"] != published_fingerprint)
    reset_admission = reopened or preparing_revision_changed
    handled_snapshot = None if reopened or handled_evidence is None else previous["handled_evidence_json"]
    dismissal_reopened = previous_dismissal is not None and _materially_new(evidence, previous_dismissal)
    status = "dismissed" if previous_dismissal is not None and not dismissal_reopened else "active"
    storage.upsert_opportunity(
        database,
        (opportunity_id, repertoire_id, kind, fen_key, card_id,
         opponent_move_uci, status, score, json.dumps(evidence, sort_keys=True),
         published_fingerprint, json.dumps(previous_dismissal) if previous_dismissal is not None else None,
         handled_snapshot, _now(), _now(), identity["canonical_prefix_revision"], identity["canonical_scope_source_revision"], identity["canonical_scope_preview_id"], game_generation, reset_admission, reset_admission, reset_admission, reset_admission or dismissal_reopened),
    )
    if kind in {"post_gap_weakness", "missing_response"} and card_id is None and status == "active" and handled_snapshot is None:
        enqueue_task_in_transaction(
            database, "discovery_recommendation", opportunity_id,
            {"opportunity_id": opportunity_id}, priority=128,
        )


def _resolve(database: sqlite3.Connection, opportunity_id: str) -> None:
    storage.resolve_opportunity(database, (_now(), _now(), opportunity_id))


def _route_keys(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> list[list[str]]:
    prefix = read_prefix(database, repertoire_id)
    assumed_positions = assumed_position_keys(prefix["moves"])
    rows = storage.card_graph_routes(database, (repertoire_id, card_id))
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
            parent = storage.parent_graph_route(database, (repertoire_id, row["line_id"], parent_id))
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
            routes.append([key for key in keys if key not in assumed_positions])
    return routes


def card_evidence(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> dict | None:
    inputs = _load_card_inputs(database, repertoire_id, card_id)
    return _calculate_card_evidence(inputs) if inputs else None


def _load_card_inputs(database: sqlite3.Connection, repertoire_id: str, card_id: str) -> dict | None:
    card = storage.available_card(database, (card_id,))
    if not card or card["archived"] or card["pending_validation"]:
        return None
    routes = _route_keys(database, repertoire_id, card_id)
    if not routes:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    target_rows = storage.recent_card_games(database, (repertoire_id, card_id, cutoff, MAX_RECENT_DECISION_GAMES))
    game_ids = [row["game_id"] for row in target_rows]
    if not game_ids:
        return {"card_id": card_id, "card_state": card["state"], "routes": routes, "events": []}
    events = [dict(row) for row in storage.card_game_events(database, repertoire_id, game_ids)]
    return {"card_id": card_id, "card_state": card["state"], "routes": routes, "events": events,
            "has_assumed_history": bool(read_prefix(database, repertoire_id)["moves"])}


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
            (route or inputs.get("has_assumed_history")) and set(route).issubset(earlier_success_keys)
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
    return [dict(row) for row in storage.recurring_decision_rows(database, repertoire_id, card_id, cutoff)]


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
    for existing in storage.active_analysis_opportunities(database, (repertoire_id, card_id)):
        if existing["id"] not in active_ids:
            _resolve(database, existing["id"])


def _apply_card_opportunity(database: sqlite3.Connection, repertoire_id: str,
                            card_id: str, evidence: dict | None) -> bool:
    target = storage.latest_card_decision(database, (repertoire_id, card_id))
    if evidence and target:
        storage.update_route_success(
            database,
            (evidence["route_success_count"], _now(), repertoire_id,
             target["fen_key"], target["expected_uci"]),
        )
    existing = storage.card_route_opportunities(database, (repertoire_id, card_id))
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
    active = storage.active_opportunity_exists(database, (_stable_id(repertoire_id, "weak_known_decision", fen_key, card_id),))
    return bool(active)


def refresh_node_opportunities(database: sqlite3.Connection, repertoire_id: str, node_id: str) -> None:
    inputs = _load_node_inputs(database, repertoire_id, node_id)
    if inputs:
        _apply_node_opportunities(database, repertoire_id, _calculate_node_opportunities(inputs))


def _latest_coverage_node(database, repertoire_id: str, fen_key: str):
    return storage.latest_coverage_node(database, (repertoire_id, fen_key, latest_coverage_run_id(database, repertoire_id)))


def _coverage_source_snapshot(database, repertoire_id: str, fen_key: str) -> dict:
    node = _latest_coverage_node(database, repertoire_id, fen_key)
    candidates = [dict(row) for row in storage.coverage_candidates(database, (node["id"],))] if node else []
    return {"node": dict(node) if node else None, "candidates": candidates}


def _load_node_inputs(database: sqlite3.Connection, repertoire_id: str, node_id: str) -> dict | None:
    requested_node = storage.coverage_node_key(database, (node_id, repertoire_id))
    if requested_node is None:
        return None
    return _load_position_inputs(database, repertoire_id, requested_node["fen_key"])


def _load_position_inputs(database, repertoire_id: str, fen_key: str) -> dict | None:
    source_snapshot = _coverage_source_snapshot(database, repertoire_id, fen_key)
    node = source_snapshot["node"]
    if node is None:
        return None
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_window_days(database))).isoformat()
    personal = storage.personal_position_moves(database, (repertoire_id, node["fen_key"], node["trained_color"], cutoff))
    relevant_games = storage.personal_repertoire_games(database, (repertoire_id, node["trained_color"], cutoff))
    consequences = storage.gap_consequences(database, (repertoire_id, node["fen_key"]))
    existing_moves = [row[0] for row in storage.existing_missing_moves(database, (repertoire_id, fen_key))]
    return {"node": node, "source_snapshot": source_snapshot,
            "existing_moves": existing_moves,
            "personal_counts": {row["move_uci"]: row["encounters"] for row in personal},
            "personal_repertoire_games": relevant_games,
            "consequences": {row["move_uci"]: {"game_count": row["game_count"],
                                                "max_loss_cp": row["max_loss_cp"]} for row in consequences},
            "candidates": source_snapshot["candidates"]}


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
    for move_uci in sorted(set(candidates) | set(personal_counts) | set(inputs.get("existing_moves", []))):
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
            "source_provenance": {
                source: {"coverage_run_id": node["run_id"], "coverage_node_id": node["id"],
                         "run_created_at": node["run_created_at"],
                         "status": "stale" if not source_is_fresh and node[f"{source}_status"] == "complete"
                         else node[f"{source}_status"]}
                for source in ("explorer", "maia")
            },
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
    finding = storage.gap_finding(database, (finding_id, repertoire_id))
    if not finding:
        return None
    if read_prefix(database, repertoire_id)["revision"] and not storage.game_matches_repertoire(database, (finding["game_id"], repertoire_id)):
        return None
    evidence = json.loads(finding["evidence_json"])
    fen_key = evidence.get("opponent_gap_fen_key")
    move_uci = evidence.get("opponent_gap_move_uci")
    if not fen_key or not move_uci:
        return None
    read_related_findings = storage.related_gap_reader(read_prefix(database, repertoire_id)["revision"])
    findings = [dict(row) for row in read_related_findings(database, (repertoire_id, fen_key, move_uci))]
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


def enqueue_opportunity_refresh_in_transaction(database, repertoire_id: str, *, quiet_seconds=5) -> None:
    from .refresh_requests import request_refresh
    scheduled_delay = request_refresh(database, 'repertoire_opportunity', repertoire_id,
                                      quiet_seconds=quiet_seconds)
    if scheduled_delay is None:
        return
    enqueue = (enqueue_compact_postgres_task_in_transaction
               if storage.supports_native_execution(database) else enqueue_task_in_transaction)
    enqueue(database, "repertoire_opportunity", repertoire_id,
            {"repertoire_id": repertoire_id, "phase": "summaries", "cursor": ""},
            priority=130, delay_seconds=scheduled_delay)


def enqueue_opportunity_refresh(repertoire_id: str, *, background: bool = False) -> None:
    from tempo_legacy_database.connections import connection
    with connection(background=background) as database:
        enqueue_opportunity_refresh_in_transaction(
            database, repertoire_id, quiet_seconds=5 if background else 0,
        )


def _advance_slice(database: sqlite3.Connection, task: dict, phase: str, cursor: str) -> None:
    storage.advance_slice(
        database,
        (json.dumps({"repertoire_id": task["payload"]["repertoire_id"],
                     "phase": phase, "cursor": cursor}), _now(), _now(),
         task["id"], task["generation"], task["lease_token"]),
    )


def _cleanup_opportunity(database: sqlite3.Connection, repertoire_id: str, opportunity_id: str,
                         *, node_decisions: list[dict] | None = None) -> None:
    opportunity = storage.opportunity(database, (opportunity_id, repertoire_id))
    if not opportunity or opportunity["status"] == "resolved":
        return
    if opportunity["admission_state"] == "preparing":
        return
    if opportunity["kind"] == "weak_known_decision" and json.loads(opportunity["evidence_json"]).get("analysis_based"):
        return
    if opportunity["kind"] == "weak_known_decision":
        card = storage.card_state(database, (opportunity["card_id"],))
        if not card or card["state"] != "locked" or card["archived"]:
            _resolve(database, opportunity_id)
    elif opportunity["kind"] == "missing_response":
        if node_decisions is None:
            inputs = _load_position_inputs(database, repertoire_id, opportunity["fen_key"])
            node_decisions = _calculate_node_opportunities(inputs) if inputs else []
        if not any(decision["move_uci"] == opportunity["opponent_move_uci"] and decision["active"]
                   for decision in node_decisions):
            _resolve(database, opportunity_id)
    else:
        if opportunity["card_id"]:
            linked_card = storage.card_introduced_at(database, (opportunity["card_id"],))
            if linked_card and linked_card["introduced_at"]:
                _resolve(database, opportunity_id)
                return
        covered_node = _latest_coverage_node(database, repertoire_id, opportunity["fen_key"])
        if covered_node and opportunity["opponent_move_uci"] in json.loads(covered_node["covered_replies_json"]):
            _resolve(database, opportunity_id)
            return
        finding = storage.gap_finding_exists(database, (repertoire_id, opportunity["fen_key"], opportunity["opponent_move_uci"]))
        if not finding:
            _resolve(database, opportunity_id)


def execute_opportunity_slice(task: dict) -> bool:
    """Process exactly one decision, card, coverage node, finding, or cleanup item."""
    repertoire_id = task["payload"]["repertoire_id"]
    phase = task["payload"].get("phase", "cards")
    cursor = task["payload"].get("cursor", "")
    activity_gate.wait_for_foreground()
    read_section = background_read_connection if storage.configured() else read_connection
    with read_section() as database:
        identity = scope_identity(database, repertoire_id)
        game_generation = game_scope_generation(database)
        if phase == "summaries":
            fen_cursor, _, move_cursor = cursor.partition("\0")
            item = storage.next_decision_summary(database, (repertoire_id, fen_cursor, fen_cursor, move_cursor))
        elif phase == "cards":
            item = storage.next_graph_card(database, (repertoire_id, cursor))
        elif phase == "nodes":
            item = storage.next_coverage_node(database, (repertoire_id, latest_coverage_run_id(database, repertoire_id), cursor))
        elif phase == "findings":
            item = storage.next_gap_finding(database, (repertoire_id, cursor))
        elif phase == "cleanup":
            item = storage.next_unresolved_opportunity(database, (repertoire_id, cursor))
        else:
            fen_cursor, _, move_cursor = cursor.partition("\0")
            item = storage.next_stored_summary(database, (repertoire_id, fen_cursor, fen_cursor, move_cursor))
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
                if not storage.configured() or lock_current_slice(database, task):
                    _advance_slice(database, task, next_phase, "")
            return True
        return False
    with read_section() as database:
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
        elif phase == "cleanup":
            cleanup_row = storage.cleanup_opportunity(database, (item_id, repertoire_id))
            cleanup_fen_key = cleanup_row["fen_key"] if cleanup_row and cleanup_row["kind"] == "missing_response" else None
            inputs = _load_position_inputs(database, repertoire_id, cleanup_fen_key) if cleanup_fen_key else None
            cleanup_snapshot = inputs["source_snapshot"] if inputs else {"node": None, "candidates": []}
        else:
            inputs = None
    evidence = _calculate_card_evidence(inputs) if phase == "cards" and inputs else None
    recurring_decisions = _calculate_recurring_decisions(recurring_rows, window_days) if phase == "cards" else []
    node_decisions = _calculate_node_opportunities(inputs) if phase in {"nodes", "cleanup"} and inputs else []
    post_gap_decision = _calculate_post_gap_opportunity(inputs) if phase == "findings" and inputs else None
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        # Prefix saves lock repertoire metadata before admitting refresh tasks.
        # Keep the same order so publication cannot hold a task while waiting
        # for the repertoire that a foreground save already holds.
        if storage.is_sqlite_connection(database):
            storage.begin_write(database)
        if (scope_identity(database, repertoire_id, lock=True) != identity
                or game_scope_generation(database, lock=True) != game_generation):
            return True
        if storage.configured():
            current_slice = lock_current_slice(database, task)
        else:
            current = storage.slice_lease(database, (task["id"],))
            current_slice = bool(current and current["generation"] == task["generation"]
                                 and current["lease_token"] == task["lease_token"])
        if not current_slice:
            return True
        source_fen_key = None
        expected_snapshot = None
        if phase == "nodes" and inputs:
            source_fen_key = inputs["node"]["fen_key"]
            expected_snapshot = inputs["source_snapshot"]
        elif phase == "cleanup":
            source_fen_key = cleanup_fen_key
            expected_snapshot = cleanup_snapshot
        if source_fen_key and _coverage_source_snapshot(database, repertoire_id, source_fen_key) != expected_snapshot:
            # Retry the same cursor; a newer attempt or source result invalidated calculation.
            _advance_slice(database, task, phase, cursor)
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
            _cleanup_opportunity(database, repertoire_id, item_id, node_decisions=node_decisions)
        elif phase == "summary_cleanup":
            fen_key, expected_uci = item_id.split("\0", 1)
            storage.delete_obsolete_summary(database, (repertoire_id, fen_key, expected_uci, repertoire_id, fen_key, expected_uci))
        _advance_slice(database, task, phase, item_id)
    if phase == "cards" and card_became_trainable:
        from datetime import date
        today = date.today().isoformat()
        enqueue_task("daily_queue", "current", {"queue_date": today}, priority=10, foreground=False)
    return True


@lru_cache(maxsize=8)
def _accepted_moves_by_line_snapshot(
    lines: tuple[tuple[str, str, str], ...],
) -> dict[str, set[str]]:
    accepted_by_position: dict[tuple, set[str]] = {}
    for start_fen, moves_json, trained_color in lines:
        board = chess.Board(start_fen)
        learner_turn = trained_color == "white"
        for move_uci in json.loads(moves_json):
            try:
                move = chess.Move.from_uci(move_uci)
                if move not in board.legal_moves:
                    break
                if board.turn == learner_turn:
                    accepted_by_position.setdefault(board._transposition_key(), set()).add(move_uci)
                board.push(move)
            except ValueError:
                break
    return accepted_by_position


def list_opportunities(database: sqlite3.Connection, repertoire_id: str,
                       opportunity_ids: list[str] | None = None) -> list[dict]:
    result = []
    if opportunity_ids is not None and not opportunity_ids:
        return result
    line_snapshot = tuple(
        (line["start_fen"], line["moves_json"], line["trained_color"])
        for line in storage.accepted_line_snapshot(database, (repertoire_id,))
    )
    accepted_by_position = _accepted_moves_by_line_snapshot(line_snapshot)
    read_active_feed = storage.active_feed_reader(opportunity_ids)

    for row in read_active_feed(database, repertoire_id):
        fen_key = row["fen_key"]
        evidence = json.loads(row["evidence_json"])
        finding_plies = {finding.get("game_id"): finding.get("mistake_ply")
                         for finding in evidence.get("findings", []) if finding.get("game_id")}
        supporting_game_ids = evidence.get("game_ids") or list(finding_plies)
        source_games = []
        distinct_routes: list[str] = []
        for game_id in supporting_game_ids[:5]:
            game = storage.source_game(database, (game_id,))
            event = storage.source_decision_ply(database, (repertoire_id, game_id, fen_key))
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
            node = storage.source_coverage_node(database, (evidence.get("coverage_node_id"),))
            try:
                decision_board = chess.Board(node["fen"] if node else decision_fen)
                decision_board.push_uci(row["opponent_move_uci"])
                decision_fen = decision_board.fen()
                trained_color = node["trained_color"] if node else ("white" if decision_board.turn else "black")
                routes_uci = json.loads(node["routes_json"]) if node else []
                for route_uci in sorted(routes_uci, key=lambda route: (len(route), route)):
                    for line in storage.source_line_routes(database, (repertoire_id,)):
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
        elif row["kind"] == "post_gap_weakness":
            source_game = _source_game(database, row)
            if source_game:
                try:
                    decision_board, _ = _full_history_request(source_game)
                    decision_fen = decision_board.fen()
                    decision_start_fen = source_game["start_fen"]
                    decision_route_uci = json.loads(source_game["moves_json"])[:source_game["ply"]]
                    trained_color = source_game["color"]
                except (ValueError, TypeError, KeyError):
                    pass
        elif evidence.get("findings"):
            for finding in evidence["findings"]:
                game_id, mistake_ply = finding.get("game_id"), finding.get("mistake_ply")
                if game_id is None or not isinstance(mistake_ply, int):
                    continue
                game = storage.game_route(database, (game_id,))
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
        try:
            accepted_position = chess.Board(decision_fen)._transposition_key()
        except ValueError:
            accepted_position = None
        result.append({
            "id": row["id"], "repertoire_id": repertoire_id,
            "kind": row["kind"], "status": row["status"],
            "fen_key": fen_key, "fen": f"{fen_key} 0 1",
            "card_id": row["card_id"], "opponent_move_uci": row["opponent_move_uci"],
            "trained_color": trained_color,
            "decision_fen": decision_fen,
            "decision_start_fen": decision_start_fen,
            "decision_route_uci": decision_route_uci,
            "accepted_moves_uci": sorted(accepted_by_position.get(accepted_position, set())),
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


def active_opportunity_for_state_change(database, repertoire_id: str, opportunity_id: str):
    """Fence a state action under the same scope/row locks as publication."""
    if storage.requires_write_start(database):
        storage.begin_write(database)
    read_prefix(database, repertoire_id, lock=True)
    game_scope_generation(database, lock=True)

    row = storage.active_state_opportunity(database, (opportunity_id, repertoire_id))
    if row is not None and not opportunity_is_current(database, row):
        raise ValueError("Opening routes or game scope changed; refresh this discovery before trying again.")
    return row


def dismiss_opportunity(database: sqlite3.Connection, repertoire_id: str, opportunity_id: str) -> bool:
    row = active_opportunity_for_state_change(database, repertoire_id, opportunity_id)
    if not row:
        return False
    storage.dismiss_opportunity(database, (row["evidence_json"], _now(), opportunity_id))
    return True


def acknowledge_opportunity(database: sqlite3.Connection, repertoire_id: str,
                            opportunity_id: str) -> bool:
    if active_opportunity_for_state_change(database, repertoire_id, opportunity_id) is None:
        return False
    result = storage.acknowledge_opportunity(database, (_now(), _now(), opportunity_id, repertoire_id))
    return result > 0


def snooze_opportunity(database: sqlite3.Connection, repertoire_id: str,
                       opportunity_id: str) -> bool:
    if active_opportunity_for_state_change(database, repertoire_id, opportunity_id) is None:
        return False
    until = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    result = storage.snooze_opportunity(database, (_now(), until, _now(), opportunity_id, repertoire_id))
    return result > 0


def _existing_decision_training_plan(database: sqlite3.Connection, repertoire_id: str,
                                     opportunity_id: str, expected_fingerprint: str | None = None,
                                     *, lock_for_write: bool = False) -> tuple[sqlite3.Row, sqlite3.Row | None]:
    """Validate direct training without writing, including the exact target position."""

    opportunity = storage.training_opportunity(database, (opportunity_id, repertoire_id), lock_for_write)
    if not opportunity:
        raise KeyError("Discovery not found")
    if expected_fingerprint is not None and opportunity["evidence_fingerprint"] != expected_fingerprint:
        raise ValueError("Discovery evidence changed; refresh before training")
    if opportunity["admission_state"] == "queued" and opportunity["admitted_card_id"]:
        return opportunity, None
    if not opportunity_is_current(database, opportunity):
        raise ValueError("Opening routes or game scope changed; refresh this discovery before training")
    if opportunity["status"] != "active":
        raise ValueError("Discovery is no longer actionable")
    if not opportunity["card_id"]:
        raise ValueError("This continuation is not saved in the repertoire")
    card = storage.trainable_card(database, (opportunity["card_id"],))
    if not card:
        raise ValueError("The saved card is unavailable or awaiting validation")
    if storage.card_integrity_block(database, (repertoire_id, card["id"])):
        raise ValueError("Repair this repertoire before training the decision")
    if not storage.card_membership(database, (repertoire_id, card["id"])) and card["repertoire_id"] != repertoire_id:
        raise ValueError("The card is no longer in this repertoire")
    if card["kind"] == "prefix":
        from .prefix_split import preview_prefix_split
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
    return opportunity, card


def existing_decision_training_eligibility(database: sqlite3.Connection, repertoire_id: str,
                                           opportunity_id: str) -> dict:
    """Read-only admission check for the Discoveries UI."""
    try:
        _existing_decision_training_plan(database, repertoire_id, opportunity_id)
    except ValueError as error:
        return {"eligible": False, "reason": str(error)}
    return {"eligible": True, "reason": None}


def admit_existing_decision(database: sqlite3.Connection, repertoire_id: str,
                            opportunity_id: str, expected_fingerprint: str) -> dict:
    """Explicitly queue a saved decision without changing ancestor mastery or reviews."""
    if not isinstance(expected_fingerprint, str) or not expected_fingerprint.strip():
        raise ValueError("Review the current evidence and train again with its evidence revision")
    if storage.requires_write_start(database):
        storage.begin_write(database)
    opportunity, card = _existing_decision_training_plan(
        database, repertoire_id, opportunity_id, expected_fingerprint, lock_for_write=True,
    )
    if card is None:
        return {"card_id": opportunity["admitted_card_id"], "queued": True,
                "idempotent": True}
    target_card_id = card["id"]
    if card["kind"] == "prefix":
        from .prefix_split import apply_prefix_split
        split = apply_prefix_split(database, card["id"], int(card["revision"]))
        target_card_id = split["continuation"]["card_id"]
    from .review_service import ensure_card_queued_after
    from datetime import date
    today = date.today().isoformat()
    storage.introduce_card(database, (today, target_card_id))
    ensure_card_queued_after(database, target_card_id, after_cards=0,
                             attempt_state="guided", priority_reason="Discovery · train this decision")
    storage.mark_explicit_queue_admission(database, (f"discovery:{opportunity_id}", today, target_card_id))
    storage.complete_opportunity_admission(database, (target_card_id, _now(), _now(), opportunity_id, opportunity["evidence_fingerprint"]))
    return {"card_id": target_card_id, "queued": True, "idempotent": False}
