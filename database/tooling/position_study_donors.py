"""Choose one complete, current physical-card donor per certified position.

This is a pure detached-facts transform. It does not allocate an FSRS ID,
write a database, infer reviews, or combine fields from multiple cards.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import Mapping

from tempo_scheduling import clone_state

from tooling.position_study_inventory import (
    DonorFact,
    InventoryRequest,
    build_inventory,
)


_ELIGIBLE_INVENTORY_STATUS = "source_eligible_unvalidated_fsrs_runtime"
_DONOR_STATES = frozenset({"learning", "mature"})


@dataclass(frozen=True)
class SelectedDonor:
    repertoire_id: str
    variant: str
    position: str
    selected_response_uci: str
    donor_card_id: str
    donor_revision: int
    donor_internal_card_id: int
    due_at: datetime
    fsrs_card_json: str
    fsrs_fingerprint: str
    source_snapshot_id: str
    source_revision: str
    source_digest: str
    provenance: str
    confidence: str


@dataclass(frozen=True)
class DonorHold:
    repertoire_id: str
    variant: str | None
    position: str | None
    reason: str
    candidate_reasons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DonorDecision:
    selected: SelectedDonor | None
    hold: DonorHold | None

    def __post_init__(self) -> None:
        if (self.selected is None) == (self.hold is None):
            raise ValueError("decision_requires_exactly_one_outcome")


def select_donors(request: InventoryRequest) -> tuple[DonorDecision, ...]:
    """Select one valid donor per source-certified exact position.

    Currentness, selected response, position bindings, physical owner scope,
    archive/integrity state, and introduction are taken from E1's actual typed
    inventory projection. FSRS runtime validity is checked with Tempo's native
    FSRS 6.3.2 decoder/roundtrip via ``clone_state``; its disposable re-keyed
    result is discarded. The original complete blob is returned byte-for-byte.
    """
    inventory = build_inventory(request)
    facts_by_id: dict[str, DonorFact] = {}
    for fact in request.donors:
        prior = facts_by_id.get(fact.card_id)
        if prior is not None and prior != fact:
            # E1 currently raises for this ambiguity; keep the E2 boundary safe.
            raise ValueError("conflicting_detached_donor_facts")
        facts_by_id[fact.card_id] = fact

    decisions: list[DonorDecision] = []
    for repertoire in inventory["repertoires"]:
        repertoire_id = repertoire["repertoire_id"]
        source = _source_for(request, repertoire_id)
        if repertoire["source_status"] == "held" or source is None:
            decisions.append(DonorDecision(None, DonorHold(
                repertoire_id, None, None,
                "source_not_current_or_certified" if source is None else
                f"source_held:{repertoire['source_hold']}",
            )))
            continue
        certificate = source.source_certificate
        # E1 validated this typed certificate against the snapshot and index.
        if certificate is None:
            decisions.append(DonorDecision(None, DonorHold(
                repertoire_id, None, None, "source_certificate_missing")))
            continue
        for position_row in repertoire["positions"]:
            decisions.append(_select_position(
                repertoire_id, position_row, certificate.snapshot_id,
                certificate.source_revision, certificate.source_digest, facts_by_id))
    return tuple(decisions)


def _source_for(request: InventoryRequest, repertoire_id: str):
    matches = [
        source for source in request.sources
        if repertoire_id in set(source.snapshot.membership.scope_repertoire_ids)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _select_position(repertoire_id: str, position_row: Mapping[str, object],
                     source_snapshot_id: str, source_revision: str, source_digest: str,
                     facts_by_id: Mapping[str, DonorFact]
                     ) -> DonorDecision:
    variant = str(position_row["variant"])
    position = str(position_row["position"])
    selected_response = str(position_row["selected_response_uci"])
    eligible: list[tuple[datetime, str, DonorFact, dict]] = []
    reasons: list[tuple[str, str]] = []
    candidate_rows = position_row["donor_candidates"]
    seen_ids: set[str] = set()
    for row in candidate_rows:
        card_id = str(row["card_id"])
        if card_id in seen_ids:
            continue
        seen_ids.add(card_id)  # route aliases never count as separate donors
        fact = facts_by_id.get(card_id)
        if fact is None:
            reasons.append((card_id, "missing_card_source"))
            continue
        status = str(row["status"])
        if status != _ELIGIBLE_INVENTORY_STATUS:
            reasons.append((card_id, status))
            continue
        if fact.state not in _DONOR_STATES:
            reasons.append((card_id, "locked_or_unintroduced_state"))
            continue
        if not fact.introduced_at:
            reasons.append((card_id, "unintroduced"))
            continue
        if fact.revision is None or fact.fsrs_card_json is None:
            reasons.append((card_id, "missing_revision_or_fsrs_state"))
            continue
        try:
            raw = json.loads(fact.fsrs_card_json)
            if not isinstance(raw, dict) or type(raw.get("card_id")) is not int:
                raise ValueError("missing_native_internal_card_id")
            native_id = raw["card_id"]
            if native_id <= 0:
                raise ValueError("invalid_native_internal_card_id")
            due_at = _aware_datetime(raw.get("due"))
            # Native decoder validates all seven supported fields and full
            # serialization fidelity. This returned clone is not persisted.
            clone_state(fact.fsrs_card_json, allocated_new_card_id=native_id + 1)
        except (TypeError, ValueError, OverflowError, json.JSONDecodeError):
            reasons.append((card_id, "invalid_native_fsrs_state"))
            continue
        eligible.append((due_at, card_id, fact, raw))

    if not eligible:
        return DonorDecision(None, DonorHold(
            repertoire_id, variant, position, "no_valid_current_introduced_donor",
            tuple(sorted(reasons)),
        ))
    due_at, card_id, fact, raw = min(eligible, key=lambda item: (item[0], item[1]))
    blob = fact.fsrs_card_json
    selected = SelectedDonor(
        repertoire_id=repertoire_id,
        variant=variant,
        position=position,
        selected_response_uci=selected_response,
        donor_card_id=card_id,
        donor_revision=fact.revision,
        donor_internal_card_id=raw["card_id"],
        due_at=due_at,
        fsrs_card_json=blob,
        fsrs_fingerprint=hashlib.sha256(blob.encode("utf-8")).hexdigest(),
        source_snapshot_id=source_snapshot_id,
        source_revision=source_revision,
        source_digest=source_digest,
        provenance=(f"E1 detached source certificate {source_snapshot_id}/{source_revision}"
                    f" sha256:{source_digest}; physical donor card state, no review-history synthesis"),
        confidence="detached_source_certified_and_native_fsrs_validated",
    )
    return DonorDecision(selected, None)


def _aware_datetime(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("due_must_be_aware_timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("due_must_be_aware_timestamp")
    return parsed
