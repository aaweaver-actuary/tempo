"""Legacy evidence receiver under the existing worker command receipt owner.

HTTP dispatch owns no database write; immutable operation and module receipts
commit together in the caller's existing command transaction.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException
from tempo_game_evidence_contracts import v3
from tempo_learning_remediation import PostgresRepository, ReceiveContext, RemediationError
from tempo_learning_remediation.validation import digest
from tempo_legacy_database.adapters.games.authority import acceptance as storage
from tempo_legacy_database.adapters.games.authority import driver, headers
from tempo_legacy_database.adapters.games.authority.driver import BorrowedTupleConnection

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
    prior = headers.delivery_receipt(authority_owner.read_rows, LEGACY_WORKSPACE_ID, delivery.delivery_id)
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
    storage.lock_imported_source(database, delivery.game_source.provider, delivery.game_source.provider_game_id)
    storage.lock_prefix_reservations(database)
    storage.lock_opening_graph(database, delivery.authority.repertoire_id)
    obligation_cards = {item.card_id for item in delivery.obligations}
    authority_cards = ({row["card_id"] for row in captured.graphs}
        if isinstance(captured, authority_owner.Capture) else set())
    affected = obligation_cards | authority_cards
    for withdrawal in delivery.withdrawals:
        row = storage.retained_contribution(database, LEGACY_WORKSPACE_ID, withdrawal.obligation_id)
        try:
            authority_owner.validate_withdrawal_lineage(delivery, withdrawal, row)
        except RemediationError as error:
            return error
        affected.add(row["card_id"])
    for card_id in sorted(affected):
        storage.lock_card_edit(database, card_id)
        storage.lock_card_row(database, card_id)
    if captured is None:
        return RemediationError("terminal_failure", "legacy_capture_required")
    identity = storage.backend_identity(database)
    if (not identity or tuple(identity) != (captured.target_id, LEGACY_WORKSPACE_ID)
            or identity["target_id"] != delivery.authority.target_id):
        return RemediationError("stale_revision", "legacy_backend_identity_changed")
    if isinstance(captured, authority_owner.WithdrawalCapture):
        game = storage.imported_game_header(database, delivery.game_id)
        current = {key: game[key] for key in authority_owner._GAME_HEADER_COLUMNS.split(",")} if game else None
        if current != captured.game:
            return RemediationError("stale_revision", "legacy_withdrawal_game_changed_before_acceptance")
        return None
    repertoire = storage.repertoire_revisions(database, delivery.authority.repertoire_id)
    scope = storage.scope_generation(database)
    graph = storage.graph_generation(database, delivery.authority.repertoire_id)
    integrity = storage.integrity_status(database, delivery.authority.repertoire_id)
    expected = captured.metadata
    if (not scope or not repertoire or not graph or not integrity or
            (scope[0], repertoire[0], repertoire[1], graph[0], graph[1], *tuple(integrity)) != (
                expected["catalogue_generation"], expected["scope_source_revision"], expected["canonical_prefix_revision"],
                expected["graph_generation"], expected["graph_state"], expected["integrity_status"],
                expected["scan_status"], expected["scan_generation"])):
        return RemediationError("stale_revision", "legacy_authority_changed_before_acceptance")
    from .services.postgres_integrity import current_integrity_certificate
    certificate = current_integrity_certificate(database, delivery.authority.repertoire_id, expected["graph_generation"])
    if certificate is None or certificate != expected.get("integrity_certificate"):
        return RemediationError("stale_revision", "legacy_integrity_certificate_changed_before_acceptance")
    game = storage.imported_game(database, delivery.game_id)
    if not game or dict(game) != captured.game:
        return RemediationError("stale_revision", "legacy_game_changed_before_acceptance")
    captured_cards = {row["id"]: row for row in captured.tables["cards"]}
    for card_id in obligation_cards | authority_cards:
        row = storage.physical_card(database, card_id)
        before = captured_cards.get(card_id)
        memberships = storage.card_memberships(database, card_id)
        expected_memberships = sorted((item["repertoire_id"], item.get("canonical_route_source"))
            for item in captured.tables["repertoire_cards"] if item["card_id"] == card_id)
        deleted = bool(storage.deleted_card(database, card_id))
        blocked = bool(storage.blocked_card(database, card_id))
        if before is None and card_id not in obligation_cards:
            if row is None or row["content_type"] != "opening" or deleted:
                continue  # Still unavailable; a newly eligible owner must be recaptured.
            return RemediationError("stale_revision", "legacy_physical_candidate_changed_before_acceptance")
        if (row is None or before is None or any(row[key] != before[key] for key in
                authority_owner._CARD_COLUMNS.split(",") if key != "introduced_at")
                or sorted(tuple(item) for item in memberships) != expected_memberships
                or deleted != bool(before.get("deleted")) or blocked != bool(before.get("integrity_blocked"))
                or (card_id in obligation_cards and ((row["introduced_at"] is None) != (before["introduced_at"] is None)
                    or not authority_owner.introduced_before_game(row["introduced_at"],
                        datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00"))) or deleted or blocked))):
            return RemediationError("stale_revision", "legacy_physical_card_changed_before_acceptance")
    return None


def receive_legacy_delivery(database, prepared):
    driver.require_writer(database)
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


def admit_legacy_card(database, payload):
    if set(payload) != {"card_id"} or not isinstance(payload["card_id"], str) or not 1 <= len(payload["card_id"]) <= 128:
        raise HTTPException(422, "One accepted physical card identity is required")
    from .services.legacy_study_remediation import admit_card
    return admit_card(database, payload)


register_command("game_evidence.admit_legacy", admit_legacy_card)
