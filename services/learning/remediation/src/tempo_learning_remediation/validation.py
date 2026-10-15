"""Semantic linkage against trusted accepted Learning definitions."""
from datetime import datetime, timezone
import hashlib
import json

from tempo_game_evidence_contracts import v1, v2

from .models import Contribution, ReceiveContext, RemediationError


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authoritative times must include a timezone")
    return value.astimezone(timezone.utc)


def validate_delivery(delivery: v1.EvidenceDelivery | v2.EvidenceDelivery,
                      context: ReceiveContext) -> tuple[Contribution, ...]:
    if (delivery.workspace_id, delivery.game_id, delivery.evidence_id) != (
            context.workspace_id, context.game_id, context.evidence_id):
        raise RemediationError("terminal_failure", "source_authority_mismatch")
    if (delivery.game_revision, delivery.evidence_revision) != (context.game_revision, context.evidence_revision):
        raise RemediationError("stale_revision", "source_revision_mismatch")
    for items in (delivery.encounters, delivery.comparisons, delivery.misses, delivery.obligations,
                  getattr(delivery, "withdrawals", ())):
        if len(items) > v2.MAX_BATCH_ITEMS:
            raise RemediationError("terminal_failure", "batch_limit_exceeded")
    received_at = instant(context.received_at)
    occurred_at = (datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00"))
                   if isinstance(delivery, v2.EvidenceDelivery) and delivery.occurred_at else None)
    if occurred_at is not None:
        occurred_at = instant(occurred_at)
        if occurred_at > received_at:
            raise RemediationError("terminal_failure", "occurrence_in_future")
    encounters = {item.encounter_id: item for item in delivery.encounters}
    if any(len(item.expected_moves) > 20 for item in delivery.comparisons):
        raise RemediationError("terminal_failure", "batch_limit_exceeded")
    contributions = []
    for obligation in delivery.obligations:
        comparisons = [item for item in delivery.comparisons if
                       (item.card_id, item.decision_id, item.publication_generation) ==
                       (obligation.card_id, obligation.decision_id, obligation.publication_generation)
                       and (not isinstance(delivery, v2.EvidenceDelivery) or
                            (item.encounter_id, item.publication_id) ==
                            (obligation.encounter_id, obligation.publication_id))]
        if len(comparisons) != 1:
            raise RemediationError("terminal_failure", "ambiguous_obligation_linkage")
        comparison = comparisons[0]
        encounter = encounters[comparison.encounter_id]
        accepted = context.content.decision(obligation.card_id, obligation.decision_id,
                                            comparison.publication_id, comparison.publication_generation)
        if accepted is None or not accepted.active:
            raise RemediationError("stale_revision", "publication_not_active")
        if ((accepted.decision_id, accepted.publication_id, accepted.publication_generation)
                != (obligation.decision_id, comparison.publication_id, comparison.publication_generation)
                or (isinstance(delivery, v2.EvidenceDelivery) and accepted.card_revision != obligation.card_revision)):
            raise RemediationError("stale_revision", "accepted_definition_mismatch")
        if (encounter.decision_id != obligation.decision_id or encounter.position_key != accepted.position_key
                or not accepted.accepted_moves or set(comparison.expected_moves) != set(accepted.accepted_moves)):
            raise RemediationError("terminal_failure", "unsupported_decision_linkage")
        actual_miss = encounter.observed_move not in accepted.accepted_moves
        if (comparison.status == "missed" and not actual_miss) or (comparison.status == "matched" and actual_miss):
            raise RemediationError("terminal_failure", "incoherent_comparison")
        supporting_misses = []
        if obligation.kind == "real_game_miss":
            misses = [item for item in delivery.misses if
                      (item.encounter_id, item.decision_id) == (encounter.encounter_id, obligation.decision_id)
                      and (not isinstance(delivery, v2.EvidenceDelivery) or item.miss_id == obligation.miss_id)]
            if len(misses) != 1 or comparison.status != "missed" or not actual_miss:
                raise RemediationError("terminal_failure", "unsupported_miss_provenance")
            supporting_misses = misses
        elif comparison.status == "unknown":
            raise RemediationError("terminal_failure", "unknown_is_not_permission")
        # Stable across transport, source reanalysis, encounter aliases and route aliases.
        incident_id = digest([context.workspace_id, context.game_id, encounter.ply, accepted.card_id])
        contributions.append(Contribution(obligation.obligation_id,
            getattr(obligation, "obligation_revision", 1), incident_id, accepted.card_id,
            accepted.card_revision, accepted.publication_id, accepted.publication_generation,
            occurred_at, obligation.kind, canonical({
                "obligation": obligation.model_dump(mode="json"),
                "encounter": encounter.model_dump(mode="json"),
                "comparison": comparison.model_dump(mode="json"),
                "misses": [item.model_dump(mode="json") for item in supporting_misses],
                "game_id": delivery.game_id, "game_revision": delivery.game_revision,
                "occurred_at": occurred_at.isoformat() if occurred_at else None,
            })))
    return tuple(contributions)


def logical_hash(contribution: Contribution) -> str:
    payload = json.loads(contribution.payload_json)
    return digest([contribution.incident_id, contribution.card_revision, contribution.publication_id,
                   contribution.publication_generation, contribution.kind,
                   contribution.occurred_at.isoformat() if contribution.occurred_at else None,
                   payload["encounter"]["position_key"], payload["encounter"]["observed_move"],
                   sorted(set(payload["comparison"]["expected_moves"]))])
