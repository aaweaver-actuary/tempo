"""Pure preparation for the reviews/schedules leaf of #267.

Consumes explicitly keyed, approved source facts. It has no database, scheduler,
exporter, network, file-writing or replay capability. A successful synthetic
projection is not the installed-version/golden approval required by #389.
"""

from copy import deepcopy
import math
from typing import Callable


class ReviewConversionError(ValueError):
    """The bounded packet cannot be converted without losing retained facts."""


# Exact source columns described in MIGRATION-MAP.md, not an installed schema
# assertion. The source packet must include every table, including empty tables.
SCHEDULE_COLUMNS = (
    "id", "state", "due_date", "interval_days", "ease", "repetitions", "lapses",
    "fsrs_card_json", "first_correct_at", "reinforcement_pending", "stability",
    "guided_review", "maximum_interval", "scheduling_mode", "hard_correct_streak",
    "recent_attempts_json",
)
CARD_CONTENT_COLUMNS = (
    "repertoire_id", "kind", "start_fen", "moves_json", "unlock_after_card_id",
    "content_type", "archived", "superseded_by", "source_ref", "source_fen",
    "revision", "trained_color", "pending_validation",
    "prefix_split_rejected_after_review_id", "study_exercise_id",
    "canonical_route_source",
)
SETTINGS_SERVING_COLUMNS = (
    "id", "timezone", "light_first_interval_days", "new_cards_per_day",
    "study_new_per_day", "tactics_new_per_day",
    "include_defensive_cards_in_daily_stack", "study_only_mode",
)
SETTINGS_COLD_COLUMNS = (
    "initial_depth", "lichess_username", "chesscom_username", "auto_sync_minutes",
    "engine_line_window_cp", "major_mistake_cp", "draw_hold_user_moves",
    "coverage_reply_denominator", "coverage_cumulative_target",
    "coverage_horizon_fullmoves", "coverage_path_floor", "coverage_maia_elo",
    "defense_new_cards_per_day", "discovery_window_days", "defensive_analysis_enabled",
)
TABLE_COLUMNS = {
    "cards": SCHEDULE_COLUMNS + CARD_CONTENT_COLUMNS + ("introduced_at",),
    "reviews": ("id", "card_id", "rating", "reviewed_at", "previous_interval",
                "next_interval", "internal_rating", "guided", "source_kind",
                "source_ref", "invalidated_at", "invalidation_reason"),
    "opening_evidence_observations": ("attempt_id", "decision_index", "decision_id",
                                      "observed_at", "failure_at", "observation_json"),
    "opening_evidence_summaries": ("decision_id", "first_responses", "clean_successes",
                                   "incorrect_responses", "assisted_responses",
                                   "manual_failures", "corrections", "distinct_clean_days"),
    "opening_evidence_clean_days": ("decision_id", "study_day"),
    "remediation_settlements": ("workspace_id", "review_id", "attempt_id", "card_id",
                                "reviewed_at", "invalidated", "invalidation_reason"),
    "opening_card_schedule_seeds": ("card_id", "source_card_id", "baseline_successful_days",
                                     "baseline_recent_clean", "verification_due", "created_at"),
    "review_schedule_snapshots": ("review_id", "state_json"),
    "study_sibling_burials": ("exercise_id", "study_day", "reason"),
    "settings": SETTINGS_SERVING_COLUMNS + SETTINGS_COLD_COLUMNS,
    "tactic_progress": ("puzzle_id", "deck_id", "card_id", "clean_pass_at", "admitted_at",
                        "admission_mode"),
    "tactic_introductions": ("puzzle_id", "pack_id", "introduction_date"),
    "tactic_pack_activation": ("pack_id", "active"),
    "tactic_rotation": ("id", "last_pack_id"),
    "frozen_opening_runs": ("run_id", "plan_checksum", "plan_json", "previous_run_id",
                            "created_at", "reader_version", "invalidated"),
    "frozen_opening_ranks": ("run_id", "card_id", "ordinal", "fingerprint",
                             "memberships_json", "definition_json"),
    "frozen_opening_pending": ("run_id",),
    "frozen_opening_queue_changes": ("run_id", "queue_id", "queue_date", "card_id",
                                     "before_position", "after_position", "definition_json"),
    "frozen_opening_activation": ("id", "run_id", "epoch"),
}
PRIMARY_KEYS = {
    "cards": ("id",), "reviews": ("id",),
    "opening_evidence_observations": ("attempt_id", "decision_index"),
    "opening_evidence_summaries": ("decision_id",),
    "opening_evidence_clean_days": ("decision_id", "study_day"),
    "remediation_settlements": ("workspace_id", "review_id"),
    "opening_card_schedule_seeds": ("card_id",),
    "review_schedule_snapshots": ("review_id",),
    "study_sibling_burials": ("exercise_id", "study_day"),
    "settings": ("id",), "tactic_progress": ("puzzle_id",),
    "tactic_introductions": ("puzzle_id",), "tactic_pack_activation": ("pack_id",),
    "tactic_rotation": ("id",), "frozen_opening_runs": ("run_id",),
    "frozen_opening_ranks": ("run_id", "card_id"),
    "frozen_opening_pending": ("run_id",),
    "frozen_opening_queue_changes": ("run_id", "queue_id"),
    "frozen_opening_activation": ("id",),
}
REVIEW_TABLES = (
    "reviews", "opening_evidence_observations", "opening_evidence_summaries",
    "opening_evidence_clean_days", "remediation_settlements",
)
SCHEDULE_TABLES = (
    "opening_card_schedule_seeds", "review_schedule_snapshots", "study_sibling_burials",
)
COLD_INTRODUCTION_TABLES = ("tactic_introductions", "tactic_pack_activation", "tactic_rotation")
INTEGER_COLUMNS = frozenset((
    "interval_days", "repetitions", "lapses", "reinforcement_pending", "guided_review",
    "maximum_interval", "hard_correct_streak", "archived", "revision", "pending_validation",
    "prefix_split_rejected_after_review_id", "canonical_route_source", "previous_interval",
    "next_interval", "guided", "decision_index", "first_responses", "clean_successes",
    "incorrect_responses", "assisted_responses", "manual_failures", "corrections",
    "distinct_clean_days", "baseline_successful_days", "baseline_recent_clean",
    "light_first_interval_days", "new_cards_per_day", "study_new_per_day", "tactics_new_per_day",
    "include_defensive_cards_in_daily_stack", "study_only_mode", "initial_depth",
    "auto_sync_minutes", "engine_line_window_cp", "major_mistake_cp", "draw_hold_user_moves",
    "coverage_reply_denominator", "coverage_cumulative_target", "coverage_horizon_fullmoves",
    "coverage_maia_elo", "defense_new_cards_per_day", "discovery_window_days",
    "defensive_analysis_enabled", "active", "ordinal", "queue_id", "before_position",
    "after_position", "epoch",
))
NULLABLE_COLUMNS = frozenset((
    "repertoire_id", "unlock_after_card_id", "fsrs_card_json", "first_correct_at",
    "superseded_by", "source_ref", "source_fen", "trained_color", "study_exercise_id",
    "introduced_at", "invalidated_at", "invalidation_reason", "failure_at",
    "clean_pass_at", "admitted_at", "previous_run_id",
))


def _fail(message):
    # Diagnostics identify the contract, never the captured values or row keys.
    raise ReviewConversionError(message)


def _json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json(item)
        return
    _fail("source must contain only finite JSON values")


def _validate_row(table, row):
    if type(row) is not dict or set(row) != set(TABLE_COLUMNS[table]):
        _fail(f"{table}: exact complete source columns required")
    for column, value in row.items():
        if value is None and (column in NULLABLE_COLUMNS or
                              column == "card_id" and table == "tactic_progress" or
                              column == "run_id" and table == "frozen_opening_activation"):
            continue
        if column == "invalidated":
            expected = bool if table == "remediation_settlements" else int
            valid = type(value) is expected
        elif column in INTEGER_COLUMNS or (column == "id" and table != "cards") or (
                column == "review_id" and table == "review_schedule_snapshots"):
            valid = type(value) is int
        elif column in ("ease", "stability", "coverage_path_floor"):
            valid = type(value) in (int, float)
        else:
            valid = type(value) is str
        if not valid:
            _fail(f"{table}: source column types must be retained")
    for column in PRIMARY_KEYS[table]:
        if row[column] == "" or row[column] is None:
            _fail(f"{table}: nonempty complete primary key required")


def _validate_closure(tables):
    cards = {entry["row"]["id"] for entry in tables["cards"]}
    reviews = {entry["row"]["id"] for entry in tables["reviews"]}
    runs = {entry["row"]["run_id"] for entry in tables["frozen_opening_runs"]}
    for table in ("reviews", "remediation_settlements", "opening_card_schedule_seeds",
                  "tactic_progress", "frozen_opening_ranks", "frozen_opening_queue_changes"):
        for entry in tables[table]:
            row = entry["row"]
            if row["card_id"] is not None and row["card_id"] not in cards:
                _fail(f"{table}: retained card identity is unresolved")
            if table == "opening_card_schedule_seeds" and row["source_card_id"] not in cards:
                _fail("opening_card_schedule_seeds: source card identity is unresolved")
    for entry in tables["review_schedule_snapshots"]:
        if entry["row"]["review_id"] not in reviews:
            _fail("review_schedule_snapshots: review identity is unresolved")
    review_sources = set()
    for entry in tables["reviews"]:
        row = entry["row"]
        if row["source_ref"] is not None:
            identity = (row["source_kind"], row["source_ref"])
            if identity in review_sources:
                _fail("reviews: duplicate original review source identity")
            review_sources.add(identity)
    decisions = {entry["row"]["decision_id"] for entry in tables["opening_evidence_summaries"]}
    for table in ("opening_evidence_observations", "opening_evidence_clean_days"):
        if any(entry["row"]["decision_id"] not in decisions for entry in tables[table]):
            _fail(f"{table}: retained decision identity is unresolved")
    ordinals = set()
    for table in ("frozen_opening_runs", "frozen_opening_ranks",
                  "frozen_opening_queue_changes", "frozen_opening_activation"):
        for entry in tables[table]:
            row = entry["row"]
            reference = row["previous_run_id"] if table == "frozen_opening_runs" else row["run_id"]
            if reference is not None and reference not in runs:
                _fail(f"{table}: frozen run identity is unresolved")
            if table == "frozen_opening_ranks":
                ordinal = (row["run_id"], row["ordinal"])
                if row["ordinal"] <= 0 or ordinal in ordinals:
                    _fail("frozen_opening_ranks: positive unique ordinal required")
                ordinals.add(ordinal)
    if tables["frozen_opening_pending"]:
        _fail("frozen_opening_pending: existing owner must reconcile pending apply")
    for entry in tables["settings"]:
        if entry["row"]["id"] != 1 or entry["row"]["timezone"] in ("", "local"):
            _fail("settings: explicit installed effective timezone required")
    for table in ("tactic_rotation", "frozen_opening_activation"):
        if any(entry["row"]["id"] != 1 for entry in tables[table]):
            _fail(f"{table}: singleton identity must be preserved")


def convert_reviews(source: object, *, approval: Callable[[dict], bool]) -> dict:
    """Project approved keyed facts into four disjoint #388 comparison groups.

    `approval` is injected by outer composition and must bind the complete packet
    (authority receipt, installed schema/serializers, keys, rows and independently
    reviewed golden fixture). Exactly True releases this pure projection only.
    The caller owns acquisition, independent target observation, group assembly
    and all eventual writes. Exceptions return no partial projection.
    """
    _json(source)
    source = deepcopy(source)
    if type(source) is not dict or set(source) != {"schema_version", "authority", "tables", "unresolved"}:
        _fail("exact versioned source packet required")
    if type(source["schema_version"]) is not int or source["schema_version"] != 1:
        _fail("source schema_version must be 1")
    if type(source["authority"]) not in (str, dict) or not source["authority"]:
        _fail("approved authority receipt required")
    if type(source["unresolved"]) is not list or source["unresolved"]:
        _fail("source has unresolved facts")
    tables = source["tables"]
    if type(tables) is not dict or set(tables) != set(TABLE_COLUMNS):
        _fail("exact leaf table set required, with explicit empty tables")
    for table, entries in tables.items():
        if type(entries) is not list:
            _fail(f"{table}: keyed source rows must be a list")
        primary_keys = set()
        for entry in entries:
            if type(entry) is not dict or set(entry) != {"key", "row"}:
                _fail(f"{table}: rows require exactly key and row")
            if type(entry["key"]) is not str or not entry["key"]:
                _fail(f"{table}: reviewer supplied opaque key required")
            _validate_row(table, entry["row"])
            primary_key = tuple(entry["row"][column] for column in PRIMARY_KEYS[table])
            if primary_key in primary_keys:
                _fail(f"{table}: duplicate complete primary key")
            primary_keys.add(primary_key)
    _validate_closure(tables)
    try:
        approved = approval(deepcopy(source))
    except Exception:
        _fail("approval could not authorize the complete source packet")
    if approved is not True:
        _fail("complete source packet requires independent mapping and golden approval")
    tables = deepcopy(tables)
    groups = {group: [] for group in ("reviews", "schedules", "introductions", "accepted_definitions")}

    record_keys = {group: set() for group in groups}

    def add(group, entry, data):
        if entry["key"] in record_keys[group]:
            _fail(f"{group}: duplicate approved record key")
        record_keys[group].add(entry["key"])
        groups[group].append({"key": entry["key"], "data": data})

    for table in REVIEW_TABLES:
        for entry in tables[table]:
            add("reviews", entry, {table: entry["row"]})
    for table in SCHEDULE_TABLES:
        for entry in tables[table]:
            add("schedules", entry, {table: entry["row"]})
    for entry in tables["cards"]:
        row = entry["row"]
        add("schedules", entry, {"cards": {column: row[column] for column in SCHEDULE_COLUMNS}})
        add("introductions", entry, {"cards": {"id": row["id"], "introduced_at": row["introduced_at"]}})
    for entry in tables["settings"]:
        row = entry["row"]
        serving = {column: row[column] for column in SETTINGS_SERVING_COLUMNS}
        serving["tactic_new_per_day"] = serving.pop("tactics_new_per_day")
        add("schedules", entry, {"settings": serving, "cold": {"settings": {
            column: row[column] for column in SETTINGS_COLD_COLUMNS}}})
    for entry in tables["tactic_progress"]:
        add("introductions", entry, {"tactic_progress": entry["row"]})
    for table in COLD_INTRODUCTION_TABLES:
        for entry in tables[table]:
            add("introductions", entry, {"cold": {table: entry["row"]}})
    for table in ("frozen_opening_ranks", "frozen_opening_activation"):
        for entry in tables[table]:
            add("accepted_definitions", entry, {table: entry["row"]})
    for entry in tables["frozen_opening_runs"]:
        row = entry["row"]
        add("accepted_definitions", entry, {
            "frozen_opening_runs": {column: row[column] for column in ("run_id", "reader_version")},
            "cold": {"frozen_opening_runs": {column: value for column, value in row.items()
                                             if column not in ("run_id", "reader_version")}},
        })
    for entry in tables["frozen_opening_queue_changes"]:
        add("accepted_definitions", entry, {"cold": {"frozen_opening_queue_changes": entry["row"]}})
    for records in groups.values():
        records.sort(key=lambda record: record["key"])
    return groups
