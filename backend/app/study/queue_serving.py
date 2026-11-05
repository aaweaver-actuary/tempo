"""Pure shaping of the existing flat Study queue response."""

import json


def assemble_queue_cards(rows):
    cards = [{**dict(row), "moves": json.loads(row["moves_json"])} for row in rows]
    for card in cards:
        card["prefix_split_offer_available"] = (
            card["latest_failed_review_id"]
            > card["prefix_split_rejected_after_review_id"]
        )
    return cards


def add_encounter_evidence(card, encounter_by_fen, recent_cutoff):
    card["encounter_evidence_status"] = (
        "available" if encounter_by_fen is not None else "unavailable"
    )
    fen_key = " ".join(card["start_fen"].split()[:4])
    encounters = encounter_by_fen.get(fen_key) if encounter_by_fen is not None else None
    encounter_count = int(encounters["encounter_count"]) if encounters else 0
    last_seen_at = encounters["last_seen_at"] if encounters else None
    if encounter_by_fen is not None:
        card["encounter_count_30d"] = encounter_count
    card["last_encountered_at"] = last_seen_at
    badges = []
    if last_seen_at and last_seen_at >= recent_cutoff:
        badges.append("Seen recently")
    if encounter_count >= 3:
        badges.append("Frequent")
    card["encounter_badges"] = badges


def finalize_queue_cards(cards):
    for card in cards:
        card.pop("moves_json", None)
        card["trained_color"] = card.pop("effective_trained_color")


def queue_payload(day, cards, count, diagnostic_rows, projection_row):
    return {
        "local_date": day,
        "cards": cards,
        "count": count,
        "diagnostics": [dict(row) for row in diagnostic_rows],
        "projection": (
            dict(projection_row)
            if projection_row
            else {
                "state": "refreshing",
                "generation": 0,
                "updated_at": None,
                "refresh_pending": 1,
                "last_error": None,
                "blocked_count": 0,
            }
        ),
    }


def exercise_snapshot(card, revision):
    return {
        "schema_version": 1,
        "grader_version": 1,
        "exercise_id": card["study_exercise_id"],
        "revision": revision["current_revision"],
        "fen": card["start_fen"],
        "specification": json.loads(revision["specification_json"]),
    }
