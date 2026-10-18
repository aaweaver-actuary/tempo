"""Worker-only legacy evidence receiver under the existing command receipt owner.

No HTTP route is exposed here. Authentication/producer operation polling and
queue issuance/settlement adoption remain separate integration gates.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from psycopg.rows import tuple_row
from tempo_game_evidence_contracts import v3
from tempo_learning_remediation import PostgresRepository, ReceiveContext, RemediationError
from tempo_learning_remediation.validation import digest

from .command_gateway import register_command
from .services import legacy_game_authority as authority_owner
from .services.legacy_game_evidence_scope import LEGACY_WORKSPACE_ID
from .services.redis_admission_gate import BackgroundAdmissionDeferred


@dataclass(frozen=True)
class PreparedDelivery:
    delivery: v3.EvidenceDelivery
    captured: authority_owner.Capture | authority_owner.WithdrawalCapture | None
    decisions: dict
    error: RemediationError | None = None
    replay: bool = False


class BorrowedTupleConnection:
    def __init__(self, database):
        if database.read_only:
            raise RuntimeError("Legacy evidence requires the command writer transaction")
        self.raw = database.raw
        self.info = self.raw.info

    def execute(self, statement, parameters=()):
        return self.raw.cursor(row_factory=tuple_row).execute(statement, parameters)


class CurrentLegacyAuthority:
    def __init__(self, prepared, error=None):
        self.prepared, self.error = prepared, error or prepared.error

    def legacy_decision(self, card_id, decision_id, authority, comparison):
        if self.error: raise self.error
        return self.prepared.decisions.get((comparison.encounter_id, card_id, decision_id))

    def validate_legacy_source(self, delivery):
        if self.error: raise self.error


def prepare_legacy_delivery(payload):
    if set(payload) != {"delivery"}:
        raise HTTPException(422, "A single immutable delivery envelope is required")
    try:
        delivery = v3.EvidenceDelivery.model_validate(payload["delivery"])
    except ValueError as error:
        raise HTTPException(422, "Invalid legacy evidence contract") from error
    if delivery.workspace_id != LEGACY_WORKSPACE_ID:
        raise HTTPException(403, "Evidence does not belong to this backend workspace")
    # A completed/stale module transport receipt wins even after source edits.
    # This read grants no permission; receive rechecks immutable identity under
    # its own lock in the command writer transaction.
    prior = authority_owner.read_rows("SELECT payload_hash,response_json FROM remediation_deliveries "
        "WHERE workspace_id=%s AND delivery_id=%s", (LEGACY_WORKSPACE_ID, delivery.delivery_id))
    if prior and prior[0]["response_json"]["status"] in {"completed", "stale_revision"}:
        error = None if prior[0]["payload_hash"] == digest(delivery.model_dump(mode="json")) else RemediationError(
            "conflict", "delivery_id_reused")
        return PreparedDelivery(delivery, None, {}, error, replay=True)
    try:
        if not delivery.obligations:
            return PreparedDelivery(delivery, authority_owner.capture_withdrawal_header(delivery), {})
        captured = authority_owner.capture(delivery)
        _, decisions = authority_owner.prepare_decisions(delivery, captured)
        return PreparedDelivery(delivery, captured, decisions)
    except RemediationError as error:
        return PreparedDelivery(delivery, None, {}, error)
    except (ValueError, KeyError) as error:
        return PreparedDelivery(delivery, None, {}, RemediationError("terminal_failure", "legacy_capture_invalid"))


def _lock_and_recheck(database, prepared):
    delivery, captured = prepared.delivery, prepared.captured
    if prepared.error:
        return prepared.error
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:imported-game:" + delivery.game_source.provider + ":" + delivery.game_source.provider_game_id,))
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))")
    affected = {item.card_id for item in delivery.obligations}
    for withdrawal in delivery.withdrawals:
        row = database.execute_native("""SELECT contribution.card_id,contribution.payload_json,
                contribution.evidence_id,contribution.game_id,contribution.obligation_revision
            FROM remediation_obligation_heads head JOIN remediation_contributions contribution
            USING(workspace_id,obligation_id,obligation_revision)
            WHERE head.workspace_id=%s AND head.obligation_id=%s""",
            (LEGACY_WORKSPACE_ID, withdrawal.obligation_id)).fetchone()
        try:
            authority_owner.validate_withdrawal_lineage(delivery, withdrawal, row)
        except RemediationError as error:
            return error
        affected.add(row["card_id"])
    for card_id in sorted(affected):
        database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:card-edit:" + card_id,))
        database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,))
    if captured is None:
        return RemediationError("terminal_failure", "legacy_capture_required")
    identity = database.execute_native("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1 FOR SHARE").fetchone()
    if (not identity or tuple(identity) != (captured.target_id, LEGACY_WORKSPACE_ID)
            or identity["target_id"] != delivery.authority.target_id):
        return RemediationError("stale_revision", "legacy_backend_identity_changed")
    if isinstance(captured, authority_owner.WithdrawalCapture):
        game = database.execute_native("SELECT " + authority_owner._GAME_HEADER_COLUMNS +
            " FROM imported_games WHERE id=%s FOR SHARE", (delivery.game_id,)).fetchone()
        current = {key: game[key] for key in authority_owner._GAME_HEADER_COLUMNS.split(",")} if game else None
        if current != captured.game:
            return RemediationError("stale_revision", "legacy_withdrawal_game_changed_before_acceptance")
        return None
    repertoire = database.execute_native("SELECT scope_source_revision,canonical_prefix_revision FROM repertoires WHERE id=%s FOR SHARE",
        (delivery.authority.repertoire_id,)).fetchone()
    scope = database.execute_native("SELECT generation FROM repertoire_game_scope WHERE id=1 FOR SHARE").fetchone()
    graph = database.execute_native("SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=%s FOR SHARE",
        (delivery.authority.repertoire_id,)).fetchone()
    integrity = database.execute_native("SELECT status,scan_status,scan_generation FROM repertoire_integrity_state WHERE repertoire_id=%s FOR SHARE",
        (delivery.authority.repertoire_id,)).fetchone()
    expected = captured.metadata
    if (not scope or not repertoire or not graph or not integrity or
            (scope[0], repertoire[0], repertoire[1], graph[0], graph[1], *tuple(integrity)) != (
                expected["catalogue_generation"], expected["scope_source_revision"], expected["canonical_prefix_revision"],
                expected["graph_generation"], expected["graph_state"], expected["integrity_status"],
                expected["scan_status"], expected["scan_generation"])):
        return RemediationError("stale_revision", "legacy_authority_changed_before_acceptance")
    game = database.execute_native("SELECT " + authority_owner._GAME_COLUMNS + " FROM imported_games WHERE id=%s FOR SHARE",
        (delivery.game_id,)).fetchone()
    if not game or dict(game) != captured.game:
        return RemediationError("stale_revision", "legacy_game_changed_before_acceptance")
    captured_cards = {row["id"]: row for row in captured.tables["cards"]}
    for card_id in affected:
        row = database.execute_native("SELECT " + authority_owner._CARD_COLUMNS + " FROM cards WHERE id=%s", (card_id,)).fetchone()
        before = captured_cards.get(card_id)
        if (row is None or before is None or any(row[key] != before[key] for key in
                authority_owner._CARD_COLUMNS.split(",") if key != "introduced_at")
                or (row["introduced_at"] is None) != (before["introduced_at"] is None)
                or not authority_owner.introduced_before_game(row["introduced_at"],
                    datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00")))
                or database.execute_native("SELECT 1 FROM deleted_cards WHERE card_id=%s", (card_id,)).fetchone()
                or database.execute_native("SELECT 1 FROM repertoire_integrity_card_blocks WHERE repertoire_id=%s AND card_id=%s",
                    (delivery.authority.repertoire_id, card_id)).fetchone()):
            return RemediationError("stale_revision", "legacy_physical_card_changed_before_acceptance")
    return None


def receive_legacy_delivery(database, prepared):
    if database.read_only:
        raise RuntimeError("Legacy evidence requires the command writer transaction")
    # Pure receipt replay cannot write an obligation and needs no new source
    # binding. New effects acquire encompassing locks before the module lock.
    error = prepared.error if prepared.replay else _lock_and_recheck(database, prepared)
    repository = PostgresRepository(BorrowedTupleConnection(database), LEGACY_WORKSPACE_ID, background=True)
    delivery = prepared.delivery
    context = ReceiveContext(LEGACY_WORKSPACE_ID, delivery.game_id, delivery.game_revision,
        delivery.evidence_id, delivery.evidence_revision, datetime.now(timezone.utc), CurrentLegacyAuthority(prepared, error))
    response = repository.receive(delivery, context)
    if response.status == "retryable_failure":
        raise BackgroundAdmissionDeferred("Legacy evidence is waiting for foreground Study")
    return response.model_dump(mode="json")


register_command("game_evidence.receive_legacy", receive_legacy_delivery, prepare=prepare_legacy_delivery)
