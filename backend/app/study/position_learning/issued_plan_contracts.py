"""Pure immutable contract for a proposed Study opening presentation.

The returned value is not an issuance receipt. A Study owner must validate the
original queue/attempt and source/epoch in its transaction before saving it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
import hashlib
import json
import math
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
from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import UCI_RE

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DecisionSlot:
    """A learner decision, preserving its authored ply as well as wire offset."""

    move_offset: int
    original_ply: int
    definition: PositionDefinition
    fen: str


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
    opponent_cue_uci: str | None
    opponent_cue_ply: int | None
    decision_slots: tuple[DecisionSlot, ...]
    due_positions: tuple[DueAtIssuance, ...]
    source_snapshot_id: str
    source_revision: str
    source_digest: str
    source_complete: bool
    source_current_integrity: bool
    route_legal_verified: bool
    local_day: date
    skipped_prefix_proof_ids: tuple[str, ...]
    issued_at: datetime
    _digest_material: str = field(default="", repr=False, compare=False)
    _authored_source_token_json: str | None = field(default=None, repr=False)

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

    def to_record(self) -> dict[str, Any]:
        """Return the complete canonical server record, including due evidence.

        This record can be decoded after process restart. Its digest detects
        accidental or untrusted edits; it is not a signature or issuance proof.
        """
        payload = _record_payload(self)
        material = _canonical_json(payload)
        if hashlib.sha256(material.encode("ascii")).hexdigest() != self.plan_id:
            raise ValueError("issued_plan_digest_or_payload_mismatch")
        return {**payload, "plan_id": self.plan_id}

    @classmethod
    def from_record(cls, raw: Mapping[str, Any]) -> IssuedPlan | Hold:
        """Strictly decode a stored server record; see :func:`decode_issued_record`."""
        return decode_issued_record(raw)


@dataclass(frozen=True)
class SelectionRequest:
    """Pure inputs supplied by Repertoire and the Study schedule owner.

    ``authored_source_token`` preserves the source owner's detached token for
    persistence. It does not replace the issuer's in-transaction source fence.
    """

    target: PositionDefinition
    routes: tuple[RouteOption, ...]
    dues: tuple[DueAtIssuance, ...]
    clean_proofs: tuple[CleanPresentationProof, ...]
    original_card_id: str
    original_card_revision: int
    queue_entry_id: int
    attempt_id: str
    authored_source_token: Mapping[str, Any] | None = None
    end_ply: int | None = None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _definition_record(value: PositionDefinition) -> dict[str, Any]:
    return {
        "repertoire_id": value.key.repertoire_id,
        "position": value.key.position,
        "variant": value.key.variant,
        "expected_uci": value.expected_uci,
        "policy_version": value.policy_version,
    }


def _json_value(value: Any) -> bool:
    """Whether a nested owner token value is a finite JSON value."""
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _authored_token_json(
    token: Mapping[str, Any] | None,
    *,
    snapshot_id: str,
    source_revision: str,
    source_digest: str,
    repertoire_id: str,
) -> str | None | Hold:
    if token is None:
        return None
    keys = {"original_snapshot_id", "original_source_revision", "original_source_digest",
            "repertoire_id", "authored_digest", "header", "integrity"}
    if not _exact_mapping(token, keys):
        return Hold("authored_source_token_fields_mismatch")
    for name in ("original_snapshot_id", "original_source_revision", "original_source_digest",
                 "repertoire_id", "authored_digest"):
        if not isinstance(token[name], str) or not token[name]:
            return Hold("invalid_authored_source_token", name)
    if (token["original_snapshot_id"] != snapshot_id
            or token["original_source_revision"] != source_revision
            or token["original_source_digest"] != source_digest
            or token["repertoire_id"] != repertoire_id):
        return Hold("authored_source_token_scope_mismatch")
    if not isinstance(token["header"], Mapping) or not isinstance(token["integrity"], Mapping):
        return Hold("invalid_authored_source_token_payload")
    if not _json_value(token["header"]) or not _json_value(token["integrity"]):
        return Hold("invalid_authored_source_token_payload")
    return _canonical_json(dict(token))


def _record_payload(plan: IssuedPlan) -> dict[str, Any]:
    proof_records: list[dict[str, str]] = []
    if plan.skipped_prefix_proof_ids:
        if len(plan.skipped_prefix_proof_ids) != 3:
            raise ValueError("invalid_skipped_prefix_proof_references")
        proof_records.append({
            "attempt_id": plan.skipped_prefix_proof_ids[0],
            "terminal_receipt_id": plan.skipped_prefix_proof_ids[1],
            "review_receipt_id": plan.skipped_prefix_proof_ids[2],
        })
    return {
        "record_version": 1,
        "original_card_id": plan.original_card_id,
        "original_card_revision": plan.original_card_revision,
        "queue_entry_id": plan.queue_entry_id,
        "attempt_id": plan.attempt_id,
        "owner_epoch": plan.owner_epoch,
        "route": {
            "route_id": plan.route_id,
            "target_ply": plan.target_ply,
            "start_ply": plan.start_ply,
            "starting_fen": plan.starting_fen,
            "moves_uci": list(plan.moves_uci),
            "opponent_cue_uci": plan.opponent_cue_uci,
            "opponent_cue_ply": plan.opponent_cue_ply,
            "decision_slots": [
                {
                    "move_offset": slot.move_offset,
                    "original_ply": slot.original_ply,
                    "definition": _definition_record(slot.definition),
                    "fen": slot.fen,
                }
                for slot in plan.decision_slots
            ],
        },
        "target": _definition_record(plan.target),
        "due_witnesses": [
            {
                "definition": _definition_record(witness.definition),
                "due": witness.due,
                "study_day": witness.study_day.isoformat(),
                "owner_epoch": witness.owner_epoch,
                "position_revision": witness.position_revision,
                "issued_at": witness.issued_at.isoformat(),
            }
            for witness in plan.due_positions
        ],
        "source": {
            "snapshot_id": plan.source_snapshot_id,
            "source_revision": plan.source_revision,
            "source_digest": plan.source_digest,
            "complete": plan.source_complete,
            "current_integrity": plan.source_current_integrity,
            "legal_verified": plan.route_legal_verified,
        },
        "local_day": plan.local_day.isoformat(),
        "prefix_proofs": proof_records,
        "issued_at": plan.issued_at.isoformat(),
        "authored_source_token": (json.loads(plan._authored_source_token_json)
                                  if plan._authored_source_token_json is not None else None),
    }


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
    route_legal_verified: bool = False,
    authored_source_token: Mapping[str, Any] | None = None,
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
    token_json = _authored_token_json(
        authored_source_token,
        snapshot_id=cert.snapshot_id,
        source_revision=cert.source_revision,
        source_digest=cert.source_digest,
        repertoire_id=target.key.repertoire_id,
    )
    if isinstance(token_json, Hold):
        return token_json

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
    occurrence_keys = {item.definition.key for item in plan.decision_occurrences}
    if set(by_key) != occurrence_keys:
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
        DecisionSlot(item.ply - plan.start_ply, item.ply, item.definition, item.fen)
        for item in plan.decision_occurrences
    )
    # Slots must point to the exact authored expected move in the issued suffix.
    for slot in slots:
        if slot.move_offset >= len(plan.moves_uci) or plan.moves_uci[slot.move_offset] != slot.definition.expected_uci:
            return Hold("decision_slot_move_mismatch")
    provisional = IssuedPlan(
        version=1,
        plan_id="",
        original_card_id=original_card_id,
        original_card_revision=original_card_revision,
        queue_entry_id=queue_entry_id,
        attempt_id=attempt_id,
        owner_epoch=epoch,
        route_id=plan.route_id,
        target=target,
        target_ply=plan.target_ply,
        starting_fen=plan.starting_fen,
        start_ply=plan.start_ply,
        moves_uci=tuple(plan.moves_uci),
        opponent_cue_uci=plan.opponent_cue_uci,
        opponent_cue_ply=plan.opponent_cue_ply,
        decision_slots=slots,
        due_positions=tuple(dict.fromkeys(by_key[s.definition.key] for s in slots)),
        source_snapshot_id=cert.snapshot_id,
        source_revision=cert.source_revision,
        source_digest=cert.source_digest,
        source_complete=cert.complete,
        source_current_integrity=cert.current_integrity,
        route_legal_verified=route_legal_verified,
        local_day=day,
        skipped_prefix_proof_ids=proof_ids,
        issued_at=min(d.issued_at for d in due_positions),
        _authored_source_token_json=token_json,
    )
    material = _canonical_json(_record_payload(provisional))
    digest = hashlib.sha256(material.encode("ascii")).hexdigest()
    return replace(provisional, plan_id=digest, _digest_material=material)


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
    # Select the stable authored candidate first. Prefix proofs can come from
    # a different authored route; the shared verifier checks the exact ordered
    # root/move prefix, source, day, epoch and current definitions.
    initial = choose_plan(request.target.key, request.routes, study_day=day, owner_epoch=epoch)
    if isinstance(initial, Hold):
        return initial
    selected = tuple(route for route in request.routes if route.route_id == initial.route_id)
    if len(selected) != 1:
        return Hold("selected_route_identity_ambiguous")
    chosen = choose_plan(request.target.key, selected, clean_proofs=request.clean_proofs,
                         study_day=day, owner_epoch=epoch)
    if isinstance(chosen, Hold):
        return chosen
    if request.end_ply is not None:
        end = request.end_ply
        if (type(end) is not int or end <= chosen.target_ply
                or end >= chosen.start_ply + len(chosen.moves_uci)
                or not any(d.ply + 1 == end for d in chosen.decision_occurrences)):
            return Hold("invalid_presentation_end")
        chosen = replace(chosen,
            moves_uci=chosen.moves_uci[:end - chosen.start_ply],
            decision_occurrences=tuple(d for d in chosen.decision_occurrences if d.ply < end))
    return freeze_issued_plan(
        original_card_id=request.original_card_id,
        original_card_revision=request.original_card_revision,
        queue_entry_id=request.queue_entry_id,
        attempt_id=request.attempt_id,
        target=request.target,
        due_positions=request.dues,
        plan=chosen,
        clean_proofs=request.clean_proofs,
        route_legal_verified=selected[0].legal_verified,
        authored_source_token=request.authored_source_token,
    )


def _record_hold(reason: str, detail: str = "") -> Hold:
    return Hold(reason, detail)


def _exact_mapping(raw: Any, keys: set[str]) -> bool:
    return isinstance(raw, Mapping) and set(raw) == keys


def _parse_definition(raw: Any) -> PositionDefinition | Hold:
    keys = {"repertoire_id", "position", "variant", "expected_uci", "policy_version"}
    if not _exact_mapping(raw, keys):
        return _record_hold("invalid_issued_record_definition")
    if any(not isinstance(raw[k], str) for k in keys):
        return _record_hold("invalid_issued_record_definition")
    try:
        key = PositionKey(raw["repertoire_id"], raw["position"], raw["variant"])
        return PositionDefinition(key, raw["expected_uci"], raw["policy_version"])
    except (TypeError, ValueError) as exc:
        return _record_hold("invalid_issued_record_definition", str(exc))


def _parse_record_date(raw: Any) -> date | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == raw else None


def _parse_record_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.utcoffset() is None or parsed.isoformat() != raw:
        return None
    return parsed


def decode_issued_record(raw: Mapping[str, Any]) -> IssuedPlan | Hold:
    """Decode and digest-check a canonical server record after restart.

    The SHA digest detects accidental/tampered record edits; it is not a
    signature, transaction receipt, or proof that a trusted issuer wrote it.
    """
    top_keys = {
        "record_version", "plan_id", "original_card_id", "original_card_revision",
        "queue_entry_id", "attempt_id", "owner_epoch", "route", "target",
        "due_witnesses", "source", "local_day", "prefix_proofs", "issued_at",
        "authored_source_token",
    }
    if not _exact_mapping(raw, top_keys):
        return _record_hold("issued_record_fields_mismatch")
    if (type(raw["record_version"]) is not int or raw["record_version"] != 1
            or not isinstance(raw["plan_id"], str) or not _DIGEST_RE.fullmatch(raw["plan_id"])):
        return _record_hold("invalid_issued_record_version_or_digest")
    for name in ("original_card_id", "attempt_id", "owner_epoch"):
        if not isinstance(raw[name], str) or not raw[name]:
            return _record_hold("invalid_issued_record_identity", name)
    for name in ("original_card_revision", "queue_entry_id"):
        if type(raw[name]) is not int or raw[name] < 1:
            return _record_hold("invalid_issued_record_identity", name)

    route_keys = {
        "route_id", "target_ply", "start_ply", "starting_fen", "moves_uci",
        "opponent_cue_uci", "opponent_cue_ply", "decision_slots",
    }
    if not _exact_mapping(raw["route"], route_keys):
        return _record_hold("issued_record_route_fields_mismatch")
    route = raw["route"]
    if (not isinstance(route["route_id"], str) or not route["route_id"]
            or not isinstance(route["starting_fen"], str) or not route["starting_fen"]
            or type(route["target_ply"]) is not int or route["target_ply"] < 0
            or type(route["start_ply"]) is not int or route["start_ply"] < 0):
        return _record_hold("invalid_issued_record_route")
    moves = route["moves_uci"]
    slots_raw = route["decision_slots"]
    if (not isinstance(moves, list) or not moves or len(moves) > 40
            or any(not isinstance(move, str) or not UCI_RE.fullmatch(move) for move in moves)
            or not isinstance(slots_raw, list) or not 1 <= len(slots_raw) <= 20):
        return _record_hold("invalid_issued_record_route_bounds")
    cue, cue_ply = route["opponent_cue_uci"], route["opponent_cue_ply"]
    if cue is None:
        if cue_ply is not None:
            return _record_hold("invalid_issued_record_opponent_cue")
    elif (not isinstance(cue, str) or not UCI_RE.fullmatch(cue)
          or type(cue_ply) is not int or cue_ply != route["start_ply"]
          or not moves or moves[0] != cue):
        return _record_hold("invalid_issued_record_opponent_cue")

    slots: list[DecisionSlot] = []
    definition_by_position: dict[PositionKey, PositionDefinition] = {}
    previous_ply = -1
    for slot_raw in slots_raw:
        slot_keys = {"move_offset", "original_ply", "definition", "fen"}
        if (not _exact_mapping(slot_raw, slot_keys)
                or type(slot_raw["move_offset"]) is not int
                or type(slot_raw["original_ply"]) is not int
                or not isinstance(slot_raw["fen"], str) or not slot_raw["fen"]):
            return _record_hold("invalid_issued_record_decision_slot")
        definition = _parse_definition(slot_raw["definition"])
        if isinstance(definition, Hold):
            return definition
        offset, ply, fen = slot_raw["move_offset"], slot_raw["original_ply"], slot_raw["fen"]
        if (offset != ply - route["start_ply"] or not 0 <= offset < len(moves)
                or ply <= previous_ply or moves[offset] != definition.expected_uci):
            return _record_hold("invalid_issued_record_decision_slot")
        prior_definition = definition_by_position.get(definition.key)
        if prior_definition is not None and prior_definition != definition:
            return _record_hold("issued_record_repeated_position_definition_conflict")
        try:
            if opening_position_key(fen) != definition.key.position:
                return _record_hold("issued_record_decision_position_mismatch")
        except (TypeError, ValueError):
            return _record_hold("invalid_issued_record_decision_fen")
        slots.append(DecisionSlot(offset, ply, definition, fen))
        definition_by_position[definition.key] = definition
        previous_ply = ply

    target = _parse_definition(raw["target"])
    if isinstance(target, Hold):
        return target
    target_matches = [s for s in slots if s.original_ply == route["target_ply"]]
    if len(target_matches) != 1 or target_matches[0].definition != target:
        return _record_hold("issued_record_target_definition_mismatch")

    source_keys = {"snapshot_id", "source_revision", "source_digest", "complete",
                   "current_integrity", "legal_verified"}
    if not _exact_mapping(raw["source"], source_keys):
        return _record_hold("issued_record_source_fields_mismatch")
    source = raw["source"]
    if (any(not isinstance(source[k], str) or not source[k]
            for k in ("snapshot_id", "source_revision", "source_digest"))
            or any(type(source[k]) is not bool for k in ("complete", "current_integrity", "legal_verified"))):
        return _record_hold("invalid_issued_record_source")
    token_json = _authored_token_json(
        raw["authored_source_token"],
        snapshot_id=source["snapshot_id"],
        source_revision=source["source_revision"],
        source_digest=source["source_digest"],
        repertoire_id=target.key.repertoire_id,
    )
    if isinstance(token_json, Hold):
        return token_json

    local_day = _parse_record_date(raw["local_day"])
    issued_at = _parse_record_datetime(raw["issued_at"])
    if local_day is None or issued_at is None:
        return _record_hold("invalid_issued_record_time")
    due_raw = raw["due_witnesses"]
    ordered_unique_definitions = tuple(dict.fromkeys(slot.definition for slot in slots))
    if not isinstance(due_raw, list) or len(due_raw) != len(ordered_unique_definitions):
        return _record_hold("invalid_issued_record_due_witnesses")
    dues: list[DueAtIssuance] = []
    due_keys = {"definition", "due", "study_day", "owner_epoch", "position_revision", "issued_at"}
    for witness_raw, expected_definition in zip(due_raw, ordered_unique_definitions):
        if (not _exact_mapping(witness_raw, due_keys)
                or type(witness_raw["due"]) is not bool
                or type(witness_raw["position_revision"]) is not int
                or witness_raw["position_revision"] < 1
                or witness_raw["owner_epoch"] != raw["owner_epoch"]):
            return _record_hold("invalid_issued_record_due_witness")
        definition = _parse_definition(witness_raw["definition"])
        if isinstance(definition, Hold):
            return definition
        witness_day = _parse_record_date(witness_raw["study_day"])
        witness_time = _parse_record_datetime(witness_raw["issued_at"])
        if (definition != expected_definition or witness_day != local_day or witness_time is None):
            return _record_hold("issued_record_due_witness_mismatch")
        try:
            dues.append(DueAtIssuance(definition, witness_raw["due"], witness_day,
                                      raw["owner_epoch"], witness_raw["position_revision"], witness_time))
        except (TypeError, ValueError) as exc:
            return _record_hold("invalid_issued_record_due_witness", str(exc))
    if min(d.issued_at for d in dues) != issued_at:
        return _record_hold("issued_record_issue_time_mismatch")

    proof_raw = raw["prefix_proofs"]
    if not isinstance(proof_raw, list) or len(proof_raw) not in (0, 1):
        return _record_hold("invalid_issued_record_prefix_proofs")
    proof_ids: tuple[str, ...] = ()
    if proof_raw:
        proof = proof_raw[0]
        proof_keys = {"attempt_id", "terminal_receipt_id", "review_receipt_id"}
        if (not _exact_mapping(proof, proof_keys)
                or any(not isinstance(proof[k], str) or not proof[k] for k in proof_keys)):
            return _record_hold("invalid_issued_record_prefix_proof")
        proof_ids = (proof["attempt_id"], proof["terminal_receipt_id"], proof["review_receipt_id"])

    plan = IssuedPlan(
        version=1,
        plan_id=raw["plan_id"],
        original_card_id=raw["original_card_id"],
        original_card_revision=raw["original_card_revision"],
        queue_entry_id=raw["queue_entry_id"],
        attempt_id=raw["attempt_id"],
        owner_epoch=raw["owner_epoch"],
        route_id=route["route_id"],
        target=target,
        target_ply=route["target_ply"],
        starting_fen=route["starting_fen"],
        start_ply=route["start_ply"],
        moves_uci=tuple(moves),
        opponent_cue_uci=cue,
        opponent_cue_ply=cue_ply,
        decision_slots=tuple(slots),
        due_positions=tuple(dues),
        source_snapshot_id=source["snapshot_id"],
        source_revision=source["source_revision"],
        source_digest=source["source_digest"],
        source_complete=source["complete"],
        source_current_integrity=source["current_integrity"],
        route_legal_verified=source["legal_verified"],
        local_day=local_day,
        skipped_prefix_proof_ids=proof_ids,
        issued_at=issued_at,
        _authored_source_token_json=token_json,
    )
    material = _canonical_json(_record_payload(plan))
    if hashlib.sha256(material.encode("ascii")).hexdigest() != plan.plan_id:
        return _record_hold("issued_record_digest_mismatch")
    decoded = replace(plan, _digest_material=material)
    if decoded.to_record() != dict(raw):
        return _record_hold("issued_record_not_canonical")
    return decoded


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
