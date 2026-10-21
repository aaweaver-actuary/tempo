"""Read-only, denominator-transparent statistics for one published repertoire."""

from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tempo_legacy_database.connections import read_connection
from tempo_legacy_database.adapters.projections import repertoire_statistics as storage


WINDOWS = {"30d": 30, "90d": 90, "all": None}


def _cutoff(window: str, configured_timezone: str) -> int | None:
    if window not in WINDOWS:
        raise ValueError("Unknown statistics window")
    days = WINDOWS[window]
    today = date.fromisoformat(_study_day(datetime.now(timezone.utc).isoformat(), configured_timezone))
    return today.toordinal() - days + 1 if days is not None else None


def _in_window(timestamp: str, cutoff_ordinal: int | None) -> bool:
    return cutoff_ordinal is None or date.fromisoformat(timestamp[:10]).toordinal() >= cutoff_ordinal


def _study_day(timestamp: str, configured_timezone: str) -> str:
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    if configured_timezone != "local":
        try:
            return moment.astimezone(ZoneInfo(configured_timezone)).date().isoformat()
        except ZoneInfoNotFoundError:
            pass
    return moment.astimezone().date().isoformat()


def repertoire_statistics(repertoire_id: str, window: str) -> dict:
    with read_connection() as database:
        repertoire = storage.repertoire_identity(database, repertoire_id)
        if not repertoire or repertoire_id.startswith("__"):
            raise KeyError("Repertoire not found")
        publication = storage.opening_publication(database, repertoire_id)
        task = storage.opening_graph_task(database, repertoire_id)
        refresh = storage.game_refresh_task(database)
        pending_derivations = storage.pending_derivation_count(database)
        failed_derivations = storage.failed_derivation_count(database)
        cards = storage.published_card_rows(database, repertoire_id)
        card_ids = {card["id"] for card in cards}
        blocked_ids = storage.blocked_card_ids(database, repertoire_id)
        review_rows = storage.valid_review_rows(database, repertoire_id)
        timezone_name = storage.study_timezone(database)
        cutoff = _cutoff(window, timezone_name)
        game_rows = storage.primary_game_rows(database, repertoire_id)
        decision_rows = storage.primary_decision_rows(database, repertoire_id)
        position_total = storage.published_position_count(database, repertoire_id)
        frontier = storage.frontier_rows(database, repertoire_id)

    valid_study_reviews = [row for row in review_rows if row["source_kind"] == "study" and row["card_id"] in card_ids]
    studied_ids = {row["card_id"] for row in valid_study_reviews}
    first_attempts: dict[tuple[str, str], dict] = {}
    for review in valid_study_reviews:
        study_day = _study_day(review["reviewed_at"], timezone_name)
        if _in_window(study_day, cutoff):
            first_attempts.setdefault((review["card_id"], study_day), review)
    study_attempts = len(first_attempts)
    study_correct = sum(row["rating"] == "correct" for row in first_attempts.values())
    current_games = [row for row in game_rows if _in_window(_study_day(row["played_at"], timezone_name), cutoff)]
    current_decisions = [row for row in decision_rows if row["card_id"] in card_ids
                         and _in_window(_study_day(row["played_at"], timezone_name), cutoff)]
    game_correct = sum(row["outcome"] == "success" for row in current_decisions)
    wins = sum((row["result"] == "1-0") == (row["color"] == "white") for row in current_games if row["result"] != "1/2-1/2")
    draws = sum(row["result"] == "1/2-1/2" for row in current_games)
    prefixes = [card for card in cards if card["kind"] == "prefix"]
    paused_ids = blocked_ids | {card["id"] for card in cards if card["pending_validation"]}
    due_today = date.today().isoformat()
    seven_days = date.fromordinal(date.today().toordinal() + 7).isoformat()
    upcoming_cards = []
    for candidate in frontier:
        parent_id = candidate["parent_card_id"]
        blocked = (candidate["card_id"] in paused_ids or parent_id in paused_ids
                   or candidate["pending_validation"] or candidate["child_pending_validation"])
        status = ("paused" if blocked else "unavailable" if candidate["parent_archived"]
                  else "ready" if candidate["parent_practiced"] else "waiting_practice")
        upcoming_cards.append({
            "card_id": candidate["card_id"], "parent_card_id": parent_id,
            "line_name": candidate["line_name"], "parent_due_date": candidate["due_date"],
            "earliest_unlock_date": due_today if status == "ready" else None,
            "status": status,
        })
    status_order = {"ready": 0, "waiting_practice": 1, "paused": 2, "unavailable": 3}
    upcoming_cards.sort(key=lambda item: (status_order[item["status"]], item["card_id"], item["parent_card_id"]))
    unique_upcoming_cards = {}
    for item in upcoming_cards:
        unique_upcoming_cards.setdefault(item["card_id"], item)
    return {
        "repertoire_id": repertoire_id, "window": window, "graph_updated_at": publication["published_at"] if publication else None,
        "graph_state": "failed" if task and task["state"] == "failed" else "refreshing" if not publication or task and task["state"] in {"queued", "leased", "retrying"} else "ready",
        "game_state": "failed" if refresh and refresh["state"] == "failed" or failed_derivations else "refreshing" if pending_derivations or refresh and refresh["state"] in {"queued", "leased", "retrying"} else "ready",
        "prefix": {"total": len(prefixes), "active": sum(card["state"] != "locked" and card["id"] not in paused_ids for card in prefixes),
                   "studied": sum(card["id"] in studied_ids for card in prefixes), "unseen": sum(card["id"] not in studied_ids for card in prefixes),
                   "locked": sum(card["state"] == "locked" for card in prefixes), "paused": sum(card["id"] in paused_ids for card in prefixes)},
        "cards": {"total": len(cards), **{state: sum(card["state"] == state for card in cards) for state in ("new", "learning", "mature", "locked")},
                  "difficult": sum(card["scheduling_mode"] == "hard" for card in cards),
                  "due_today": sum(card["state"] in {"learning", "mature"} and card["due_date"] <= due_today and card["id"] not in paused_ids for card in cards),
                  "due_next_seven_days": sum(card["state"] in {"learning", "mature"} and due_today < card["due_date"] <= seven_days and card["id"] not in paused_ids for card in cards)},
        "study": {"correct": study_correct, "attempts": study_attempts, "accuracy": study_correct / study_attempts if study_attempts else None},
        "games": {"matched": len(current_games), "correct": game_correct, "decisions": len(current_decisions),
                  "adherence": game_correct / len(current_decisions) if current_decisions else None,
                  "wins": wins, "draws": draws, "losses": len(current_games) - wins - draws,
                  "positions_seen": len({row["fen_key"] for row in current_decisions}), "positions_total": position_total},
        "unlocks": list(unique_upcoming_cards.values())[:3],
    }


def repertoire_positions(repertoire_id: str, window: str, sort: str, cursor: str | None, limit: int) -> dict:
    if sort not in {"attention", "frequency"}:
        raise ValueError("Unknown position sort")
    if not 1 <= limit <= 50:
        raise ValueError("Position limit must be between 1 and 50")
    with read_connection() as database:
        if not storage.repertoire_presence(database, repertoire_id):
            raise KeyError("Repertoire not found")
        timezone_name = storage.study_timezone(database)
        cutoff = _cutoff(window, timezone_name)
        known_positions = storage.known_position_keys(database, repertoire_id)
        current_card_ids = storage.published_card_ids(database, repertoire_id)
        events = storage.position_decision_rows(database, repertoire_id)
    positions: dict[str, dict] = {}
    for event in events:
        if event["fen_key"] not in known_positions or event["card_id"] not in current_card_ids or not _in_window(_study_day(event["played_at"], timezone_name), cutoff):
            continue
        position = positions.setdefault(event["fen_key"], {"fen_key": event["fen_key"], "games": set(), "encounters": 0, "correct": 0, "missed": 0,
            "last_seen_at": "", "expected_moves": set(), "played_moves": {}, "card_id": None, "sample_game_id": None, "sample_ply": None})
        position["games"].add(event["game_id"])
        position["encounters"] += 1
        position["correct" if event["outcome"] == "success" else "missed"] += 1
        position["expected_moves"].add(event["expected_uci"])
        position["played_moves"][event["actual_uci"]] = position["played_moves"].get(event["actual_uci"], 0) + 1
        if event["played_at"] >= position["last_seen_at"]:
            position["last_seen_at"] = event["played_at"]
            position["sample_game_id"] = event["game_id"]
            position["sample_ply"] = event["ply"]
        position["card_id"] = event["card_id"] or position["card_id"]
    rows = list(positions.values())
    if sort == "attention":
        rows.sort(key=lambda row: (-row["missed"], -row["encounters"], row["fen_key"]))
    else:
        rows.sort(key=lambda row: (-row["encounters"], -row["missed"], row["fen_key"]))
    try:
        offset = int(base64.urlsafe_b64decode(cursor.encode()).decode()) if cursor else 0
    except (ValueError, UnicodeDecodeError):
        raise ValueError("Invalid positions cursor") from None
    if offset < 0:
        raise ValueError("Invalid positions cursor")
    page = rows[offset:offset + limit]
    return {"positions": [{**row, "fen": row["fen_key"] + " 0 1", "games": len(row["games"]),
                             "expected_moves": sorted(row["expected_moves"]),
                             "played_moves": [{"move_uci": move, "count": count} for move, count in sorted(row["played_moves"].items(), key=lambda item: (-item[1], item[0]))]}
                            for row in page],
            "total": len(rows), "next_cursor": base64.urlsafe_b64encode(str(offset + limit).encode()).decode() if offset + limit < len(rows) else None}
