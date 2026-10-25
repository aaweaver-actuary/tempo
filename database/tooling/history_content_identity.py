"""Pure content/identity projection for explicitly approved history packets.

This source-only preparation has no database, exporter, writer, alias resolver or
default approval. Input is the content partition of an already mapped capture,
not an arbitrary legacy dump. See history_content_identity/README.md. Actual
installed-data use remains gated by #389's independently approved map/fixture.
"""

import copy
import json
import math
from types import MappingProxyType


class ContentIdentityError(ValueError):
    """The supplied content partition cannot be projected without guessing."""


# Fixed field partitions from the merged MIGRATION-MAP.md, sections 1 and 5.
# (normalized group, complete source PK, mapped fields, cold-preserved fields).
# No runtime SQL introspection, heuristic aliasing or new target storage model.
TABLES = {
    "cards": ("cards", "id",
        "id repertoire_id kind start_fen moves_json unlock_after_card_id content_type "
        "archived superseded_by source_ref source_fen revision trained_color "
        "pending_validation prefix_split_rejected_after_review_id study_exercise_id",
        "canonical_route_source"),
    "repertoire_cards": ("cards", "repertoire_id card_id",
        "repertoire_id card_id", "canonical_route_source"),
    "repertoire_integrity_card_blocks": ("cards", "repertoire_id card_id issue_id",
        "repertoire_id card_id", "issue_id scan_generation published_at"),
    "repertoires": ("accepted_definitions", "id",
        "id name source_name created_at is_main new_cards_per_day",
        "canonical_prefix_moves_json canonical_prefix_revision canonical_prefix_preview_id scope_source_revision"),
    "repertoire_lines": ("accepted_definitions", "id",
        "id repertoire_id trained_color created_at", "name start_fen moves_json"),
    "opening_graph_publications": ("accepted_definitions", "repertoire_id",
        "repertoire_id generation state", "last_error published_at"),
    "opening_graph_steps": ("accepted_definitions", "repertoire_id generation line_id decision_index",
        "repertoire_id generation line_id decision_index card_id parent_card_id starting_fen moves_json trained_color",
        "decision_fen_key segment_kind first_decision_index last_decision_index decision_fen_keys_json"),
    "studies": ("accepted_definitions", "id", "id title archived",
        "description source_json created_at updated_at"),
    "study_positions": ("accepted_definitions", "id", "id fen",
        "source_id parent_id child_index move_uci history_json comment starting_comment "
        "nags_json arrows_json squares_json node_path valid"),
    "study_exercises": ("accepted_definitions", "id",
        "id study_id position_id current_revision status sibling_group",
        "source_json point_value created_at updated_at"),
    "study_exercise_revisions": ("accepted_definitions", "exercise_id revision",
        "exercise_id revision specification_json specification_hash created_at", ""),
    "endgame_templates": ("accepted_definitions", "id",
        "id card_id name white_material black_material trained_color goal_mix enabled created_at", ""),
}

# Cold preservation is an explicit ledger disposition, never historical identity
# adoption, accepted-defense construction, proposal activation or serving proof.
_COLD_TABLES = {
    "opening_graph_legacy_mappings": ("repertoire_id generation legacy_card_id decision_card_id",
        "repertoire_id generation legacy_card_id decision_card_id"),
    "card_revisions": ("id", "id card_id revision start_fen moves_json history_mode created_at"),
    "prefix_splits": ("source_card_id",
        "source_card_id source_revision shortened_card_id continuation_card_id created_at"),
    "repertoire_line_training_depths": ("line_id", "line_id learner_decision_count"),
    "study_chapters": ("id", "id study_id title description position archived"),
    "study_sources": ("id", "id chapter_id source_group_id version raw_pgn sha256 filename "
        "record_index headers_json diagnostics_json valid created_at"),
    "study_links": ("id", "id study_id source_position_id target_position_id target_exercise_id relation"),
    "puzzles": ("id", "id deck_id source_fen start_fen moves_json rating popularity themes_json "
        "game_url state due_date interval_days"),
    "puzzle_decks": ("id", "id name themes_json min_rating max_rating daily_limit created_at"),
    "threat_training_candidates": ("id", "id finding_id game_id analysis_version incident_id player_ply "
        "validation_state diagnostic evidence_json validation_json source_fingerprint detector_version "
        "policy_json exercise_revision dismissed_at dismissed_evidence_fingerprint approved_at card_id "
        "superseded_at created_at updated_at paused_at admission_mode"),
    "tactic_captures": ("id", "id card_id source_kind source_ref source_url note captured_at request_json result_json"),
    "repertoire_card_introduction_priorities": ("repertoire_id card_id",
        "repertoire_id card_id scoring_version completed_line_ids_json completion_mass "
        "frontier_decisions_json frontier_reach priority_score evidence_json updated_at"),
    "repertoire_card_priority_generations": ("repertoire_id generation card_id",
        "repertoire_id generation card_id scoring_version completed_line_ids_json completion_mass "
        "frontier_decisions_json frontier_reach priority_score evidence_json updated_at"),
    "opening_segmentation_runs": ("id", "id repertoire_id"),
    "opening_segmentation_state": ("repertoire_id",
        "repertoire_id content_version graph_generation run_id state published_at"),
    "opening_segmentation_occurrences": ("run_id card_id decision_index",
        "run_id card_id decision_index trunk_key suffix_key occurrence_json"),
    "opening_segmentation_groups": ("run_id kind group_key",
        "run_id kind group_key source_count decisions_before segment_count decisions_after "
        "first_discriminator diverse source_fingerprint"),
    "opening_segmentation_parts": ("run_id recommendation_id segment_id",
        "run_id recommendation_id segment_id tested_decisions segment_json"),
    "opening_segmentation_sources": ("run_id recommendation_id card_id",
        "run_id recommendation_id card_id decision_count discriminator"),
    "opening_segmentation_recommendations": ("run_id id",
        "run_id id repertoire_id kind decisions_before decisions_after decisions_avoided "
        "additional_starts segment_count source_fingerprint"),
    "opening_segmentation_preferences": ("repertoire_id recommendation_id",
        "repertoire_id recommendation_id choice source_fingerprint updated_at"),
    "canonical_prefix_previews": ("id",
        "id repertoire_id moves_json expected_revision source_revision state suggestion_json last_error created_at"),
    "canonical_prefix_results": ("preview_id item_id",
        "preview_id item_id name status reason disagreement_ply origin_json scope_start_ply"),
    "canonical_prefix_positions": ("preview_id fen_key in_scope",
        "preview_id fen_key in_scope fen route_json ply"),
}
for _table, (_key, _fields) in _COLD_TABLES.items():
    TABLES[_table] = ("accepted_definitions", _key, "", _fields)
TABLES = MappingProxyType(TABLES)

# These facts cannot be converted from source evidence alone. Requiring an
# explicit empty partition prevents treating an omitted table as observed empty.
BLOCKED_TABLES = MappingProxyType({"deleted_cards": ("card_id", "card_id deleted_at")})
REQUIRED_TABLES = tuple(TABLES) + tuple(BLOCKED_TABLES)


def _validate_json(value):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_json(item)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise ContentIdentityError("content packet must contain only finite JSON values")


def _primary_key(table, row, fields):
    values = []
    for field in fields.split():
        value = row[field]
        integer_key = field in {"generation", "decision_index", "revision", "in_scope"} or (
            table == "card_revisions" and field == "id"
        )
        expected_type = int if integer_key else str
        if type(value) is not expected_type or value == "":
            raise ContentIdentityError("source primary key types must match the declared partition")
        # There is deliberately no delimiter-based key construction or coercion.
        values.append(value)
    return tuple(values)


def convert_content_identity(source, *, approval):
    """Return the two #388 content groups from a supplied approved partition.

    ``approval`` is a required composition-root capability. It receives a deep
    copy of the whole source packet and must return exactly True after validating
    the installed authority/fingerprint, packet digest and approved mapping. It must
    reject unavailable approval; this module has no permissive fallback.

    Reviewer-supplied record keys are retained verbatim. Data, IDs, TEXT JSON,
    nulls and numeric types are copied losslessly. Neither input nor callback
    arguments share mutable objects with returned groups. Nothing is written.
    """
    try:
        return _convert_content_identity(source, approval)
    except RecursionError:
        raise ContentIdentityError("content packet is too deeply nested") from None


def _convert_content_identity(source, approval):
    if type(source) is not dict or set(source) != {"schema_version", "authority", "tables", "unresolved"}:
        raise ContentIdentityError("expected exactly the content partition version 1 fields")
    if type(source["schema_version"]) is not int or source["schema_version"] != 1:
        raise ContentIdentityError("content partition schema_version must be integer 1")
    if type(source["unresolved"]) is not list or source["unresolved"]:
        raise ContentIdentityError("unresolved content mappings block conversion")
    if type(source["authority"]) is not dict or not source["authority"]:
        raise ContentIdentityError("content source authority is required")
    tables = source["tables"]
    if type(tables) is not dict or set(tables) != set(REQUIRED_TABLES):
        raise ContentIdentityError("content partition requires exactly every declared source table")
    _validate_json(source)
    try:
        approved = callable(approval) and approval(copy.deepcopy(source)) is True
    except Exception:
        raise ContentIdentityError("content authority and mapping approval are required") from None
    if not approved:
        raise ContentIdentityError("content authority and mapping approval are required")

    result = {"cards": [], "accepted_definitions": []}
    group_keys = {group: set() for group in result}
    for table, (group, key_fields, mapped, cold) in TABLES.items():
        rows = tables[table]
        if type(rows) is not list:
            raise ContentIdentityError("each content table must be an explicit list")
        source_keys = set()
        expected_fields = set((mapped + " " + cold).split())
        for entry in rows:
            if type(entry) is not dict or set(entry) != {"key", "row"}:
                raise ContentIdentityError("content rows require exactly key and row")
            key, row = entry["key"], entry["row"]
            if type(key) is not str or not key:
                raise ContentIdentityError("approved record keys must be nonempty strings")
            if key in group_keys[group]:
                raise ContentIdentityError("content group contains a duplicate approved record key")
            if type(row) is not dict or set(row) != expected_fields:
                raise ContentIdentityError("content row fields must match the approved source partition")
            primary_key = _primary_key(table, row, key_fields)
            if primary_key in source_keys:
                raise ContentIdentityError("content table contains a duplicate source primary key")
            source_keys.add(primary_key)
            group_keys[group].add(key)
            if table == "cards":
                repertoire, exercise = row["repertoire_id"], row["study_exercise_id"]
                if (repertoire is None) == (exercise is None):
                    raise ContentIdentityError("physical card must retain exactly one original owner")
                if exercise is not None and (row["kind"] != "exercise" or row["content_type"] != "study_exercise"):
                    raise ContentIdentityError("study exercise card must retain its original owner constraint")
            data = {}
            if mapped:
                data[table] = {field: copy.deepcopy(row[field]) for field in mapped.split()}
            if cold:
                data["cold"] = {table: {field: copy.deepcopy(row[field]) for field in cold.split()}}
            result[group].append({"key": key, "data": data})

    for table in BLOCKED_TABLES:
        if type(tables[table]) is not list:
            raise ContentIdentityError("each blocked content table must be an explicit list")
        if tables[table]:
            raise ContentIdentityError("deletion timestamp conversion requires an approved installed-version reader")
    for rows in result.values():
        rows.sort(key=lambda entry: entry["key"])
    return result


def load_content_identity_source(path):
    """Read supplied UTF-8 JSON only; reject duplicate members without payloads."""
    def unique_members(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ContentIdentityError("content JSON object contains duplicate member names")
            result[key] = value
        return result

    def finite_number(_value):
        raise ContentIdentityError("content JSON numbers must be finite")

    try:
        with open(path, encoding="utf-8") as document:
            return json.load(document, object_pairs_hook=unique_members, parse_constant=finite_number)
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise ContentIdentityError("content input must be readable finite UTF-8 JSON with unique object members") from None
