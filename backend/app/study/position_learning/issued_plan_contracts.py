"""Pure immutable contract for a proposed Study opening presentation.

The returned value is not an issuance receipt. A Study owner must validate the
original queue/attempt and source/epoch in its transaction before saving it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Mapping

from .contracts import (
    DecisionOccurrence,
    DueAtIssuance,
    Hold,
    PositionDefinition,
    PositionKey,
    RouteOption,
)
from .prefix_eligibility import CleanPresentationProof
from .route_planner import Plan, choose_plan
from tempo_evidence_math.position_learning import UCI_RE

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DecisionSlot:
    """A learner decision, preserving its authored ply as well as wire offset."""

    move_offset: int
    original_ply: int
    definition: PositionDefinition


@dataclass(frozen=True)
class IssuedPlan:
    """Detached proposed presentation with a canonical, digest-bound wire form."""

    version: int
    plan_id: str
    original_card_id: str
    original_card_revision: int
    queue_entry_id: int
    attempt_id: str
    owner_epoch: str
    route_id: str
    target: PositionDefinition
    target_ply: int
    starting_fen: str
    start_ply: int
    moves_uci: tuple[str, ...]
    decision_slots: tuple[DecisionSlot, ...]
    due_positions: tuple[DueAtIssuance, ...]
    source_snapshot_id: str
    source_revision: str
    source_digest: str
    local_day: date
    skipped_prefix_proof_ids: tuple[str, ...]
    issued_at: datetime
    _digest_material: str = field(default="", repr=False, compare=False)

    def to_wire(self) -> dict[str, Any]:
        """Return the strict v1 browser envelope; due/FSRS state stays server-side."""
        return {
            "version": self.version,
            "plan_id": self.plan_id,
            "original_card_id": self.original_card_id,
            "original_card_revision": self.original_card_revision,
            "queue_entry_id": self.queue_entry_id,
            "attempt_id": self.attempt_id,
            "owner_epoch": self.owner_epoch,
            "route_id": self.route_id,
            "target_ply": self.target_ply,
            "starting_fen": self.starting_fen,
            "moves_uci": list(self.moves_uci),
            "decision_slots": [
                {
                    "move_offset": slot.move_offset,
                    "position_key": slot.definition.key.position,
                    "expected_uci": slot.definition.expected_uci,
                }
                for slot in self.decision_slots
            ],
            "source_digest": self.source_digest,
            "local_day": self.local_day.isoformat(),
            "skipped_prefix_proof_ids": list(self.skipped_prefix_proof_ids),
        }


@dataclass(frozen=True)
class SelectionRequest:
    """Pure inputs supplied by Repertoire and the Study schedule owner."""

    target: PositionDefinition
    routes: tuple[RouteOption, ...]
    dues: tuple[DueAtIssuance, ...]
    clean_proofs: tuple[CleanPresentationProof, ...]
    original_card_id: str
    original_card_revision: int
    queue_entry_id: int
    attempt_id: str


def _proof_identities(plan: Plan, proofs: tuple[CleanPresentationProof, ...]) -> tuple[str, ...] | Hold:
    if not plan.skipped_prefix_decisions:
        if plan.prefix_credit_attempt_id is not None:
            return Hold("prefix_proof_without_skipped_decisions")
        return ()
    matches = tuple(
        proof for proof in proofs
        if proof.attempt_id == plan.prefix_credit_attempt_id
        and proof.terminal_complete
        and proof.terminal_receipt_id
        and proof.review_receipt_id
    )
    if len(matches) != 1:
        return Hold("skipped_prefix_proof_identity_unavailable")
    proof = matches[0]
    if proof.route_id != plan.route_id:
        return Hold("skipped_prefix_route_identity_mismatch")
    return (proof.attempt_id, proof.terminal_receipt_id, proof.review_receipt_id)


def freeze_issued_plan(
    *,
    original_card_id: str,
    original_card_revision: int,
    queue_entry_id: int,
    attempt_id: str,
    target: PositionDefinition,
    due_positions: tuple[DueAtIssuance, ...],
    plan: Plan,
    clean_proofs: tuple[CleanPresentationProof, ...] = (),
) -> IssuedPlan | Hold:
    """Freeze one bounded route and every learner decision's due witness.

    This is a deterministic proposal only: booleans on a source certificate
    are claims, and cannot establish owner authority or currentness.
    """
    if (not isinstance(original_card_id, str) or not original_card_id
            or type(original_card_revision) is not int or original_card_revision < 1
            or type(queue_entry_id) is not int or queue_entry_id < 1
            or not isinstance(attempt_id, str) or not attempt_id):
        return Hold("invalid_original_attempt_identity")
    if (target.key != plan.position or not any(
        d.definition == target and d.ply == plan.target_ply for d in plan.decision_occurrences
    )):
        return Hold("target_definition_mismatch")
    if not plan.route_id or not plan.starting_fen or plan.start_ply < 0:
        return Hold("invalid_selected_route")
    if len(plan.moves_uci) > 40 or any(not UCI_RE.fullmatch(m) for m in plan.moves_uci):
        return Hold("issued_presentation_exceeds_wire_bound")
    if not due_positions:
        return Hold("missing_due_witnesses")
    if not plan.decision_occurrences or len(plan.decision_occurrences) > 20:
        return Hold("issued_presentation_exceeds_wire_bound")
    if len({d.ply for d in plan.decision_occurrences}) != len(plan.decision_occurrences):
        return Hold("repeated_position_in_one_presentation")
    if any(d.ply < plan.start_ply or d.ply - plan.start_ply >= len(plan.moves_uci)
           for d in plan.decision_occurrences):
        return Hold("decision_outside_selected_suffix")
    cert = plan.source_certificate
    if not (cert.snapshot_id and cert.source_revision and cert.source_digest):
        return Hold("source_provenance_incomplete")

    epoch_day = {(d.owner_epoch, d.study_day) for d in due_positions}
    if len(epoch_day) != 1:
        return Hold("mixed_daily_due_scope")
    epoch, day = next(iter(epoch_day))
    if not epoch:
        return Hold("missing_owner_epoch")
    by_key: dict[PositionKey, DueAtIssuance] = {}
    for witness in due_positions:
        key = witness.definition.key
        if key in by_key:
            return Hold("duplicate_position_due_witness")
        by_key[key] = witness
    occurrence_keys = tuple(item.definition.key for item in plan.decision_occurrences)
    if set(by_key) != set(occurrence_keys):
        return Hold("due_witnesses_do_not_match_selected_decisions")
    for occurrence in plan.decision_occurrences:
        witness = by_key[occurrence.definition.key]
        if witness.definition != occurrence.definition:
            return Hold("source_response_vs_due_witness_conflict")
        if witness.owner_epoch != epoch or witness.study_day != day:
            return Hold("mixed_position_due_scope")
    proof_ids = _proof_identities(plan, clean_proofs)
    if isinstance(proof_ids, Hold):
        return proof_ids
    slots = tuple(
        DecisionSlot(item.ply - plan.start_ply, item.ply, item.definition)
        for item in plan.decision_occurrences
    )
    # Slots must point to the exact authored expected move in the issued suffix.
    for slot in slots:
        if slot.move_offset >= len(plan.moves_uci) or plan.moves_uci[slot.move_offset] != slot.definition.expected_uci:
            return Hold("decision_slot_move_mismatch")
    # Target answer revision is part of the issued source fence.
    canonical = {
        "version": 1,
        "original_card_id": original_card_id,
        "original_card_revision": original_card_revision,
        "queue_entry_id": queue_entry_id,
        "attempt_id": attempt_id,
        "owner_epoch": epoch,
        "route_id": plan.route_id,
        "target": [target.key.repertoire_id, target.key.position, target.key.variant,
                   target.expected_uci, target.policy_version],
        "target_ply": plan.target_ply,
        "starting_fen": plan.starting_fen,
        "start_ply": plan.start_ply,
        "moves_uci": list(plan.moves_uci),
        "slots": [[s.move_offset, s.original_ply, s.definition.key.repertoire_id,
                   s.definition.key.position, s.definition.key.variant,
                   s.definition.expected_uci, s.definition.policy_version] for s in slots],
        "dues": [[d.definition.key.repertoire_id, d.definition.key.position,
                  d.definition.key.variant, d.definition.expected_uci,
                  d.definition.policy_version, d.due, d.position_revision,
                  d.study_day.isoformat(), d.owner_epoch, d.issued_at.isoformat()]
                 for d in sorted(due_positions, key=lambda x: x.definition.key)],
        "source": [cert.snapshot_id, cert.source_revision, cert.source_digest],
        "local_day": day.isoformat(),
        "skipped": [[d.ply, d.definition.key.repertoire_id, d.definition.key.position,
                     d.definition.expected_uci, d.definition.policy_version, d.fen]
                    for d in plan.skipped_prefix_decisions],
        "proof_ids": list(proof_ids),
        "prefix_credit_attempt_id": plan.prefix_credit_attempt_id,
    }
    material = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    return IssuedPlan(1, digest, original_card_id, original_card_revision, queue_entry_id,
                      attempt_id, epoch, plan.route_id, target, plan.target_ply,
                      plan.starting_fen, plan.start_ply, tuple(plan.moves_uci), slots,
                      tuple(by_key[s.definition.key] for s in slots), cert.snapshot_id,
                      cert.source_revision, cert.source_digest, day, proof_ids,
                      min(d.issued_at for d in due_positions), material)


def select_and_freeze(request: SelectionRequest) -> IssuedPlan | Hold:
    """Select deterministically from supplied route claims and freeze a proposal."""
    if not request.routes:
        return Hold("no_authorized_routes")
    if not request.dues:
        return Hold("missing_due_witnesses")
    if len({r.route_id for r in request.routes}) != len(request.routes):
        return Hold("ambiguous_route_identity")
    if len({(d.owner_epoch, d.study_day) for d in request.dues}) != 1:
        return Hold("mixed_daily_due_scope")
    day, epoch = request.dues[0].study_day, request.dues[0].owner_epoch
    # Select the stable authored candidate first. The prefix checker is a
    # shared claim verifier; scope proofs to that exact route because a proof's
    # route_id must match the selected authored route, even when two routes
    # happen to share a move prefix and source digest.
    initial = choose_plan(request.target.key, request.routes, study_day=day, owner_epoch=epoch)
    if isinstance(initial, Hold):
        return initial
    selected = tuple(route for route in request.routes if route.route_id == initial.route_id)
    if len(selected) != 1:
        return Hold("selected_route_identity_ambiguous")
    scoped_proofs = tuple(proof for proof in request.clean_proofs
                          if proof.route_id == initial.route_id)
    chosen = choose_plan(request.target.key, selected, clean_proofs=scoped_proofs,
                         study_day=day, owner_epoch=epoch)
    if isinstance(chosen, Hold):
        return chosen
    return freeze_issued_plan(
        original_card_id=request.original_card_id,
        original_card_revision=request.original_card_revision,
        queue_entry_id=request.queue_entry_id,
        attempt_id=request.attempt_id,
        target=request.target,
        due_positions=request.dues,
        plan=chosen,
        clean_proofs=request.clean_proofs,
    )


def validate_wire_v1(value: Mapping[str, Any], *, expected_plan: IssuedPlan | None = None) -> Hold | dict[str, Any]:
    """Strictly validate browser-facing v1 shape; this cannot verify authority."""
    fields = {"version", "plan_id", "original_card_id", "original_card_revision",
              "queue_entry_id", "attempt_id", "owner_epoch", "route_id", "target_ply",
              "starting_fen", "moves_uci", "decision_slots", "source_digest", "local_day",
              "skipped_prefix_proof_ids"}
    if not isinstance(value, Mapping) or set(value) != fields:
        return Hold("issued_plan_wire_fields_mismatch")
    if type(value["version"]) is not int or value["version"] != 1 or not isinstance(value["plan_id"], str) or not _DIGEST_RE.fullmatch(value["plan_id"]):
        return Hold("invalid_issued_plan_wire_identity")
    for name in ("original_card_id", "attempt_id", "owner_epoch", "route_id", "starting_fen", "source_digest"):
        if not isinstance(value[name], str) or not value[name]:
            return Hold("invalid_issued_plan_wire_identity", name)
    for name in ("original_card_revision", "queue_entry_id"):
        if type(value[name]) is not int or value[name] < 1:
            return Hold("invalid_issued_plan_wire_identity", name)
    if type(value["target_ply"]) is not int or value["target_ply"] < 0:
        return Hold("invalid_issued_plan_wire_identity", "target_ply")
    moves = value["moves_uci"]
    slots = value["decision_slots"]
    proof_ids = value["skipped_prefix_proof_ids"]
    if not isinstance(moves, list) or len(moves) > 40 or any(not isinstance(m, str) or not UCI_RE.fullmatch(m) for m in moves):
        return Hold("invalid_issued_plan_moves")
    if not isinstance(slots, list) or not 1 <= len(slots) <= 20:
        return Hold("invalid_issued_plan_slots")
    if not isinstance(proof_ids, list) or any(not isinstance(i, str) or not i for i in proof_ids):
        return Hold("invalid_skipped_prefix_proofs")
    seen_offsets: set[int] = set()
    for slot in slots:
        if (not isinstance(slot, Mapping) or set(slot) != {"move_offset", "position_key", "expected_uci"}
                or type(slot["move_offset"]) is not int or not 0 <= slot["move_offset"] < len(moves)
                or slot["move_offset"] in seen_offsets
                or not isinstance(slot["position_key"], str) or not slot["position_key"]
                or not isinstance(slot["expected_uci"], str) or not UCI_RE.fullmatch(slot["expected_uci"])
                or moves[slot["move_offset"]] != slot["expected_uci"]):
            return Hold("invalid_issued_plan_slot")
        seen_offsets.add(slot["move_offset"])
    try:
        parsed_day = date.fromisoformat(value["local_day"])
    except (TypeError, ValueError):
        return Hold("invalid_issued_plan_local_day")
    if parsed_day.isoformat() != value["local_day"]:
        return Hold("invalid_issued_plan_local_day")
    if expected_plan is not None:
        actual_digest = hashlib.sha256(expected_plan._digest_material.encode("ascii")).hexdigest()
        if (actual_digest != expected_plan.plan_id or dict(value) != expected_plan.to_wire()):
            return Hold("issued_plan_digest_or_payload_mismatch")
    return dict(value)
