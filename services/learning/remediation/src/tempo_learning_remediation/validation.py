"""Semantic linkage against trusted accepted Learning definitions."""
from datetime import datetime, timezone
import hashlib
import json

from tempo_game_evidence_contracts import v1, v2, v3

from .models import Contribution, LegacyAcceptedDecision, ReceiveContext, RemediationError, accepted_storage_identity


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def instant(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("authoritative times must include a timezone")
    return value.astimezone(timezone.utc)


def validate_delivery(delivery: v1.EvidenceDelivery | v2.EvidenceDelivery | v3.EvidenceDelivery,
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
    legacy = isinstance(delivery, v3.EvidenceDelivery)
    exact = isinstance(delivery, (v2.EvidenceDelivery, v3.EvidenceDelivery))
    if legacy:
        context.content.validate_legacy_source(delivery)
        try:
            captured_at = instant(datetime.fromisoformat(delivery.captured_at.replace("Z", "+00:00")))
        except OverflowError as error:
            raise RemediationError("terminal_failure", "legacy_capture_out_of_range") from error
        if captured_at > received_at:
            raise RemediationError("terminal_failure", "legacy_capture_in_future")
    occurred_at = (datetime.fromisoformat(delivery.occurred_at.replace("Z", "+00:00"))
                   if exact and delivery.occurred_at else None)
    if occurred_at is not None:
        try:
            occurred_at = instant(occurred_at)
        except OverflowError as error:
            raise RemediationError("terminal_failure", "occurrence_out_of_range") from error
        if occurred_at > received_at:
            raise RemediationError("terminal_failure", "occurrence_in_future")
    encounters = {item.encounter_id: item for item in delivery.encounters}
    if any(len(item.expected_moves) > 20 for item in delivery.comparisons):
        raise RemediationError("terminal_failure", "batch_limit_exceeded")
    contributions = []
    for obligation in delivery.obligations:
        comparisons = [item for item in delivery.comparisons if
                       (item.card_id, item.decision_id) == (obligation.card_id, obligation.decision_id)
                       and (legacy or item.publication_generation == obligation.publication_generation)
                       and (not exact or (item.encounter_id == obligation.encounter_id and
                            (legacy or item.publication_id == obligation.publication_id)))]
        if len(comparisons) != 1:
            raise RemediationError("terminal_failure", "ambiguous_obligation_linkage")
        comparison = comparisons[0]
        encounter = encounters[comparison.encounter_id]
        accepted = (context.content.legacy_decision(obligation.card_id, obligation.decision_id,
                        delivery.authority, comparison) if legacy else context.content.decision(
                        obligation.card_id, obligation.decision_id,
                        comparison.publication_id, comparison.publication_generation))
        if legacy and accepted is not None:
            if not isinstance(accepted, LegacyAcceptedDecision):
                raise RemediationError("terminal_failure", "legacy_authority_variant_required")
            if not accepted.introduced:
                raise RemediationError("terminal_failure", "legacy_card_not_introduced")
            if not accepted.introduced_before_game:
                raise RemediationError("terminal_failure", "legacy_introduction_chronology_unknown")
            if accepted.aliases or accepted.card_id != obligation.card_id:
                raise RemediationError("terminal_failure", "legacy_physical_identity_required")
            if (accepted.authority != delivery.authority or
                    (accepted.route_id, accepted.route_ply, accepted.exercise_ply, accepted.executable_digest)
                    != (comparison.route_id, comparison.route_ply, comparison.exercise_ply, comparison.executable_digest)):
                raise RemediationError("stale_revision", "legacy_binding_authority_changed")
            if (not accepted.physical_binding_complete or
                    set(accepted.authored_moves) != set(accepted.accepted_moves)):
                raise RemediationError("terminal_failure", "unknown_is_not_permission")
            if accepted.scope_proof is not None:
                proof = accepted.scope_proof
                if (proof.authority_id != accepted.authority.storage_identity()[0]
                        or proof.scope.game_id != delivery.game_id
                        or proof.scope.content_hash != delivery.game_source.content_hash
                        or encounter.ply not in proof.scope.decision_plies):
                    raise RemediationError("stale_revision", "legacy_decision_scope_changed")
                anchor = proof.authority_anchor(accepted.authority.repertoire_id)
                if (anchor is None or anchor > encounter.ply
                        or proof.prefix_policies[encounter.ply] != [accepted.authority.repertoire_id]):
                    raise RemediationError("terminal_failure", "legacy_route_policy_ambiguous")
        if accepted is None or not accepted.active:
            raise RemediationError("stale_revision", "legacy_authority_not_current" if legacy else "publication_not_active")
        if accepted.workspace_id != context.workspace_id:
            raise RemediationError("terminal_failure", "accepted_workspace_mismatch")
        if obligation.card_id not in (accepted.card_id, *accepted.aliases):
            raise RemediationError("terminal_failure", "unsupported_card_linkage")
        authority_id, authority_generation = accepted_storage_identity(accepted)
        if (accepted.decision_id != obligation.decision_id
                or (not legacy and (authority_id, authority_generation) != (
                    comparison.publication_id, comparison.publication_generation))
                or (exact and accepted.card_revision != obligation.card_revision)):
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
                      and (not exact or item.miss_id == obligation.miss_id)]
            if len(misses) != 1 or comparison.status != "missed" or not actual_miss:
                raise RemediationError("terminal_failure", "unsupported_miss_provenance")
            supporting_misses = misses
        elif comparison.status == "unknown":
            raise RemediationError("terminal_failure", "unknown_is_not_permission")
        # Stable across transport, source reanalysis, encounter aliases and route aliases.
        incident_id = digest([context.workspace_id, context.game_id, encounter.ply, accepted.card_id])
        contributions.append(Contribution(obligation.obligation_id,
            getattr(obligation, "obligation_revision", 1), incident_id, accepted.card_id,
            accepted.card_revision, authority_id, authority_generation,
            occurred_at, obligation.kind, canonical({
                "obligation": obligation.model_dump(mode="json"),
                "encounter": encounter.model_dump(mode="json"),
                "comparison": comparison.model_dump(mode="json"),
                "misses": [item.model_dump(mode="json") for item in supporting_misses],
                "game_id": delivery.game_id, "game_revision": delivery.game_revision,
                "occurred_at": occurred_at.isoformat() if occurred_at else None,
                **({"authority": delivery.authority.model_dump(mode="json"),
                    "capture_digest": delivery.capture_digest,
                    "captured_at": delivery.captured_at,
                    "game_source": delivery.game_source.model_dump(mode="json"),
                    **({"decision_scope_proof": accepted.scope_proof.model_dump(mode="json")}
                       if accepted.scope_proof is not None else {})} if legacy else {}),
            })))
    return tuple(contributions)


def logical_hash(contribution: Contribution) -> str:
    payload = json.loads(contribution.payload_json)
    # A new publication reference is not new work when the immutable exercise
    # revision and supported decision semantics are unchanged.
    return digest([contribution.incident_id, contribution.card_revision, contribution.kind,
                   contribution.occurred_at.isoformat() if contribution.occurred_at else None,
                   payload["encounter"]["position_key"], payload["encounter"]["observed_move"],
                   sorted(set(payload["comparison"]["expected_moves"]))])
