"""Pure retained server-work conversion preparation for #267.

No database, HTTP, filesystem, scheduler, queue writer or replay operation is
provided here. The table shapes describe reviewed source, not an assertion about
an installation. Source authority and reviewed identity are supplied by the
composition owner. Unknown/missing fields and unresolved recovery authority stop
the whole leaf; partial output must never look like accepted history.
"""

from copy import deepcopy
import math
from typing import Callable


class ServerReceiptError(ValueError):
    """The supplied server-work packet cannot be converted without guessing."""


# Each specification is group, complete primary key, complete captured columns.
# '?' permits SQL NULL; i/b distinguish BIGINT flags from BOOLEAN. TEXT JSON is
# deliberately plain text: parsing/re-emitting it would change historical bytes.
_TABLES = {
    "daily_queue": ("queue_attempts", "id", "id:i queue_date:s card_id:s cycle:i position:i status:s attempt_state:s review_result_json:s? attempt_failed:i card_bucket:s? admission_kind:s? gameplay_priority_reason:s? admission_repertoire_id:s? admission_source:s?"),
    "daily_queue_days": ("queue_attempts", "queue_date", "queue_date:s seed:i membership_hash:s generated_at:s"),
    "queue_attempt_origins": ("queue_attempts", "queue_entry_id card_id revision", "queue_entry_id:i card_id:s revision:i queue_date:s cycle:i admission_kind:s? admission_repertoire_id:s? attempt_failed:i last_status:s review_result_json:s? legacy:i start_fen:s moves_json:s trained_color:s? content_type:s issued_as_head:i"),
    "study_attempts": ("queue_attempts", "id", "id:s exercise_id:s revision:i card_id:s? queue_entry_id:i? cycle:i? context:s answer_json:s answer_hash:s assessment_json:s assessment_method:s grader_version:i hint_seen:i solution_seen_before_answer:i started_at:s committed_at:s finalized_at:s? result_json:s? retired_operation_id:s?"),
    "opening_evidence_attempts": ("queue_attempts", "attempt_id", "attempt_id:s manifest_id:s presentation_snapshot_id:i repertoire_id:s card_id:s card_revision:i trained_color:s context_json:s manifest_json:s started_at:s study_timezone:s queue_entry_id:i? contiguous_sequence:i state:s terminal_json:s? completed_at:s? review_id:i? review_result_json:s? retired_operation_id:s?"),
    "opening_evidence_presentations": ("queue_attempts", "id", "id:i content_fingerprint:s card_id:s revision:i start_fen:s moves_json:s trained_color:s? captured_at:s"),
    "opening_evidence_queue_contexts": ("queue_attempts", "queue_entry_id presentation_snapshot_id repertoire_id", "queue_entry_id:i presentation_snapshot_id:i repertoire_id:s effective_trained_color:s"),
    "opening_evidence_events": ("queue_attempts", "attempt_id sequence", "attempt_id:s sequence:i event_json:s"),
    "tactic_discovery_attempts": ("queue_attempts", "id", "id:s deck_id:s puzzle_id:s clean:i result_json:s"),
    "defense_attempts": ("queue_attempts", "attempt_id", "attempt_id:s candidate_id:s card_id:s queue_entry_id:i? exercise_revision:i move_uci:s grade_json:s reviewed_at:s"),
    "defense_recognition_submissions": ("queue_attempts", "attempt_id", "attempt_id:s candidate_id:s queue_entry_id:i exercise_revision:i answer_json:s recognition_correct:i created_at:s"),
    "endgame_attempts": ("queue_attempts", "id", "id:s template_id:s start_fen:s target:s status:s user_moves:i created_at:s completed_at:s?"),
    "queue_projections": ("queue_attempts", "queue_date", "queue_date:s state:s generation:i updated_at:s? refresh_pending:i last_error:s? blocked_count:i"),
    "queue_projection_diagnostics": ("queue_attempts", "queue_date card_id", "queue_date:s card_id:s message:s"),
    "gameplay_card_priorities": ("queue_attempts", "card_id", "card_id:s source_game_id:s finding_id:s priority_date:s reason:s created_at:s"),
    "teaching_states": ("introductions", "card_id revision ply", "card_id:s revision:i ply:i taught_at:s"),
    "remediation_attempts": ("queue_attempts", "workspace_id attempt_id", "workspace_id:s attempt_id:s card_id:s card_revision:i publication_id:s publication_generation:i issued_at:s frontier_sequence:i"),
    "remediation_cards": ("queue_attempts", "workspace_id card_id", "workspace_id:s card_id:s sequence:i work_state:s reason:s? queue_entry_id:i?"),
    "remediation_incident_versions": ("queue_attempts", "workspace_id incident_id logical_hash", "workspace_id:s incident_id:s logical_hash:s card_id:s work_sequence:i accepted_at:s"),
    "remediation_contributions": ("queue_attempts", "workspace_id obligation_id obligation_revision", "workspace_id:s obligation_id:s obligation_revision:i evidence_id:s evidence_revision:i game_id:s incident_id:s logical_hash:s card_id:s card_revision:i publication_id:s publication_generation:i kind:s occurred_at:s? payload_json:s game_revision:i"),
    "remediation_obligation_heads": ("queue_attempts", "workspace_id obligation_id", "workspace_id:s obligation_id:s obligation_revision:i evidence_id:s game_id:s incident_id:s card_id:s active:b"),
    "remediation_evidence": ("queue_attempts", "workspace_id evidence_id evidence_revision", "workspace_id:s evidence_id:s evidence_revision:i game_id:s game_revision:i payload_hash:s learning_revision:i"),
    "remediation_source_heads": ("queue_attempts", "workspace_id evidence_id", "workspace_id:s evidence_id:s game_id:s evidence_revision:i game_revision:i"),
    "remediation_game_heads": ("queue_attempts", "workspace_id game_id", "workspace_id:s game_id:s game_revision:i"),
    "remediation_workspaces": ("queue_attempts", "workspace_id", "workspace_id:s revision:i"),
    "operation_receipts": ("operation_receipts", "operation_id", "operation_id:s command_name:s request_hash:s payload_json:s? state:s response_json:s? error_json:s? next_retry_at:s? created_at:s updated_at:s background:b attempt_count:i lease_expires_at:s? last_error_json:s? retry_cycle:i cycle_attempt_count:i attempt_token:s?"),
    "review_attempt_receipts": ("operation_receipts", "attempt_id", "attempt_id:s card_id:s queue_entry_id:i? outcome:s guided:i completed_at:s? review_id:i? scheduling_status:s warning:s? result_json:s request_json:s?"),
    "remediation_deliveries": ("operation_receipts", "workspace_id delivery_id", "workspace_id:s delivery_id:s payload_hash:s response_json:j"),
    "remediation_review_receipts": ("operation_receipts", "workspace_id review_id", "workspace_id:s review_id:s payload_json:s settled:b"),
    "prefix_transition_applications": ("operation_receipts", "operation_id", "operation_id:s repertoire_id:s plan_id:s plan_json:s graph_generation:i state:s staging_task_id:s? graph_task_id:s? integrity_task_id:s? integrity_generation:i? queue_task_id:s? queue_generation:i? queue_date:s? result_json:s? last_error:s? created_at:s updated_at:s"),
    "prefix_transition_card_fences": ("operation_receipts", "card_id", "card_id:s operation_id:s"),
    "prefix_transition_repertoire_fences": ("operation_receipts", "repertoire_id", "repertoire_id:s operation_id:s"),
    "discovery_admission_intents": ("operation_receipts", "id", "id:s opportunity_id:s repertoire_id:s evidence_fingerprint:s starting_fen:s selected_move_uci:s preview_moves_json:s recommendation_json:s line_id:s state:s card_id:s? last_error:s? created_at:s updated_at:s"),
    "background_tasks": ("operation_receipts", "id", "id:s kind:s deduplication_key:s generation:i priority:i state:s phase:s payload_version:i payload_json:s attempt_count:i max_attempts:i next_attempt_at:s lease_token:s? lease_expires_at:s? last_error:s? created_at:s started_at:s? completed_at:s? updated_at:s replaced_pending_generation:i pending_since:s? generation_started_at:s? age_origin_estimated:i transaction_timeout_count:i transaction_timeout_checkpoint:s?"),
    "background_task_events": ("operation_receipts", "id", "id:i task_id:s generation:i event:s phase:s? detail:s? created_at:s"),
}

TABLE_NAMES = tuple(_TABLES)
OUTPUT_GROUPS = ("queue_attempts", "operation_receipts", "introductions")
_RECOVERY_FIELDS = frozenset("background attempt_count lease_expires_at last_error_json retry_cycle cycle_attempt_count attempt_token".split())
_COLD_TABLES = frozenset({"gameplay_card_priorities", "prefix_transition_applications", "prefix_transition_card_fences", "prefix_transition_repertoire_fences", "discovery_admission_intents", "background_tasks", "background_task_events"})

_UNIQUE_KEYS = {
    "daily_queue": (("queue_date", "card_id", "cycle"),),
    "study_attempts": (("queue_entry_id",),),
    "opening_evidence_presentations": (("content_fingerprint",),),
    "background_tasks": (("kind", "deduplication_key"),),
    "discovery_admission_intents": (("opportunity_id", "selected_move_uci", "evidence_fingerprint"),),
    "remediation_incident_versions": (("workspace_id", "card_id", "work_sequence"),),
}


def _fields(table):
    return {name: kind for name, kind in (field.split(":") for field in _TABLES[table][2].split())}


def _is_json(value):
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) is list:
        return all(_is_json(item) for item in value)
    if type(value) is dict:
        return all(type(key) is str and _is_json(item) for key, item in value.items())
    return False


def _validate_row(table, row):
    fields = _fields(table)
    if type(row) is not dict or set(row) != set(fields):
        raise ServerReceiptError("server_row_columns_unreviewed:" + table)
    for name, kind in fields.items():
        value = row[name]
        if value is None and kind.endswith("?"):
            continue
        required = kind.rstrip("?")
        valid = ((required == "s" and type(value) is str)
                 or (required == "i" and type(value) is int)
                 or (required == "b" and type(value) is bool))
        if required == "j":
            valid = _is_json(value)
        if not valid:
            raise ServerReceiptError("server_row_type_unreviewed:" + table + ":" + name)
    if any(row[name] is None or row[name] == "" for name in _TABLES[table][1].split()):
        raise ServerReceiptError("server_primary_key_missing:" + table)
    for field in {"attempt_failed", "legacy", "issued_as_head", "clean", "hint_seen", "solution_seen_before_answer", "recognition_correct", "guided", "refresh_pending", "replaced_pending_generation", "age_origin_estimated"} & set(row):
        if row[field] not in (0, 1):
            raise ServerReceiptError("server_flag_unreviewed:" + table + ":" + field)
    if table == "opening_evidence_events" and not 1 <= row["sequence"] <= 256:
        raise ServerReceiptError("server_event_sequence_unreviewed")


def _blockers(table, row):
    if table == "operation_receipts":
        if row["payload_json"] is None:
            yield "server_receipt_payload_missing"
        if row["state"] not in {"complete", "failed"}:
            yield "server_receipt_original_recovery_authority_required"
    if table == "defense_attempts" and row["queue_entry_id"] is None:
        yield "server_defense_queue_identity_missing"
    if table.startswith("remediation_"):
        yield "server_remediation_authority_transfer_unapproved"
    if table == "gameplay_card_priorities":
        yield "server_effective_gameplay_priority_disposition_unapproved"
    if table == "prefix_transition_applications" and row["state"] not in {"complete", "rejected"}:
        yield "server_transition_original_owner_required"
    if table in {"prefix_transition_card_fences", "prefix_transition_repertoire_fences"}:
        yield "server_outstanding_transition_fence"
    if table in {"discovery_admission_intents", "background_tasks", "background_task_events"}:
        # Even a terminal-looking row needs its owning recovery receipt, not a
        # guessed list of terminal states from a different source version.
        yield "server_background_terminal_disposition_unapproved"
    if table == "queue_projections" and (row["state"] != "ready" or row["refresh_pending"] != 0):
        yield "server_queue_projection_reconciliation_required"


def convert_server_receipts(source: dict, *, approval: Callable[[dict], bool]) -> dict:
    """Convert one fully supplied, externally approved source-only packet.

    ``approval`` is the composition owner's mandatory validator. It must reject
    any source/version/authority/table/PK/key not released by #389. It runs before
    row conversion; this leaf neither reads nor manufactures approval evidence.
    It is deliberately injected so this module imports no sibling converter or
    concrete authority service. Unit-test fakes establish only synthetic proof.

    Source is exactly schema_version=1, authority (opaque approval evidence),
    tables, and unresolved=[]. Every TABLE_NAMES entry must be present, including
    captured empty tables. Each row is exactly {key, row}; key is the reviewed
    opaque identity, and row is the complete captured row. No keys are derived.
    Any unresolved semantic blocker raises before returning any output.
    """
    if (type(source) is not dict or set(source) != {"schema_version", "authority", "tables", "unresolved"}
            or type(source["schema_version"]) is not int or source["schema_version"] != 1
            or type(source["unresolved"]) is not list or source["unresolved"]):
        raise ServerReceiptError("server_source_contract_unreviewed")
    if not callable(approval) or approval(deepcopy(source)) is not True:
        raise ServerReceiptError("server_implementation_release_approval_required")
    tables = source["tables"]
    if type(tables) is not dict or set(tables) != set(TABLE_NAMES):
        raise ServerReceiptError("server_table_inventory_unreviewed")
    entities = {group: [] for group in OUTPUT_GROUPS}
    unresolved = set()
    all_keys = set()
    for table in TABLE_NAMES:
        rows = tables[table]
        if type(rows) is not list:
            raise ServerReceiptError("server_table_rows_required:" + table)
        seen_primary_keys = set()
        seen_unique_keys = {fields: set() for fields in _UNIQUE_KEYS.get(table, ())}
        for record in rows:
            if (type(record) is not dict or set(record) != {"key", "row"}
                    or type(record["key"]) is not str or not record["key"]):
                raise ServerReceiptError("server_record_identity_required:" + table)
            row = record["row"]
            _validate_row(table, row)
            primary_key = tuple(row[name] for name in _TABLES[table][1].split())
            if primary_key in seen_primary_keys or record["key"] in all_keys:
                raise ServerReceiptError("server_duplicate_identity:" + table)
            seen_primary_keys.add(primary_key)
            for fields, seen in seen_unique_keys.items():
                unique = tuple(row[name] for name in fields)
                # PostgreSQL UNIQUE allows multiple SQL NULLs; do not invent an
                # identity for practice attempts that have no queue entry.
                if None not in unique:
                    if unique in seen:
                        raise ServerReceiptError("server_unique_identity_conflict:" + table)
                    seen.add(unique)
            all_keys.add(record["key"])
            unresolved.update(_blockers(table, row))
            if table == "operation_receipts":
                data = {table: {name: deepcopy(value) for name, value in row.items() if name not in _RECOVERY_FIELDS},
                        "cold": {table: {name: deepcopy(row[name]) for name in sorted(_RECOVERY_FIELDS)}}}
            elif table in _COLD_TABLES:
                data = {"cold": {table: deepcopy(row)}}
            else:
                data = {table: deepcopy(row)}
            if table == "queue_attempt_origins":
                data["first_issued_provenance"] = "unknown"
            entities[_TABLES[table][0]].append({"key": record["key"], "data": data})
    if unresolved:
        raise ServerReceiptError("server_semantics_unresolved:" + ",".join(sorted(unresolved)))
    for rows in entities.values():
        rows.sort(key=lambda record: record["key"])
    return entities
