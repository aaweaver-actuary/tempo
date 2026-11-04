"""Pure, non-authoritative crosswalk for a legacy JSONB plan value.

The JSON text transports a JSONB value; original transport bytes are unavailable.
The detached content and position inputs do not themselves establish authority.
"""
from __future__ import annotations
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from decimal import Decimal
import hashlib, json, math, re
from typing import Any, Mapping, Literal
import chess
from tempo_evidence_math import opening_position_key
from tempo_evidence_math.position_learning import (PositionKey, PositionDefinition as _Definition, DueAtIssuance, Hold, UCI_RE)
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
@dataclass(frozen=True)
class DecisionSlot:
    """A learner decision, preserving its authored ply as well as wire offset."""

    move_offset: int
    original_ply: int
    definition: _Definition
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
    target: _Definition
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


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _definition_record(value: _Definition) -> dict[str, Any]:
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


def _record_hold(reason: str, detail: str = "") -> Hold:
    return Hold(reason, detail)


def _exact_mapping(raw: Any, keys: set[str]) -> bool:
    return isinstance(raw, Mapping) and set(raw) == keys


def _parse_definition(raw: Any) -> _Definition | Hold:
    keys = {"repertoire_id", "position", "variant", "expected_uci", "policy_version"}
    if not _exact_mapping(raw, keys):
        return _record_hold("invalid_issued_record_definition")
    if any(not isinstance(raw[k], str) for k in keys):
        return _record_hold("invalid_issued_record_definition")
    try:
        key = PositionKey(raw["repertoire_id"], raw["position"], raw["variant"])
        return _Definition(key, raw["expected_uci"], raw["policy_version"])
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
    definition_by_position: dict[PositionKey, _Definition] = {}
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



@dataclass(frozen=True)
class OriginalPlanEvidence:
    original_attempt_id: str
    queue_entry_id: int
    card_id: str
    card_revision: int
    repertoire_id: str
    variant: str
    owner_epoch: str
    source_authority: str
    source_digest: str
    issue_study_day: date
    issued_at: datetime
    stored_record_json: str
    original_plan_digest: str
    storage_encoding: Literal['postgres-jsonb-value']
    canonical_codec: Literal['legacy-issued-plan-record-v1']

@dataclass(frozen=True)
class ContentOccurrence:
    repertoire_id: str
    variant: str
    route_id: str
    original_ply: int
    fen: str
    exact_position: str
    expected_uci: str
    policy_version: str

@dataclass(frozen=True)
class VerifiedContentGeneration:
    authority: str
    source_digest: str
    snapshot_id: str
    source_revision: str
    generation_identity: str
    certificate_digest: str
    complete: bool
    current: bool
    bindings: tuple[ContentOccurrence, ...]

@dataclass(frozen=True)
class PositionDefinition:
    repertoire_id: str
    variant: str
    exact_position: str
    expected_uci: str
    policy_version: str

@dataclass(frozen=True)
class VerifiedPositionOwner:
    authority: str
    repertoire_id: str
    owner_epoch: str
    source_digest: str
    current: bool
    definitions: tuple[PositionDefinition, ...]

@dataclass(frozen=True)
class DueWitness:
    definition: PositionDefinition
    due: bool
    study_day: date
    owner_epoch: str
    position_revision: int
    issued_at: datetime

@dataclass(frozen=True)
class DecisionSlotBinding:
    attempt_id: str
    decision_index: int
    original_occurrence_id: tuple[str, int]
    repertoire_id: str
    variant: str
    exact_position: str
    expected_uci: str
    policy_version: str
    owner_epoch: str
    source_digest: str
    plan_digest: str
    content_certificate_digest: str
    mapping_version: Literal['L-PLAN.v1']
    move_offset: int
    original_fen: str
    due_witness: DueWitness | None

@dataclass(frozen=True)
class Blocked:
    status: Literal['BLOCK']
    code: str
    attempt_id: str
    decision_index: int | None


def _public_definition(value: _Definition) -> PositionDefinition:
    return PositionDefinition(value.key.repertoire_id, value.key.variant, value.key.position,
                              value.expected_uci, value.policy_version)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def _same_json_value(exact: Any, decoded: Any) -> bool:
    """Compare JSON numbers numerically, before any binary-float precision loss."""
    if isinstance(exact, Decimal):
        return type(decoded) is float and math.isfinite(decoded) and Decimal(str(decoded)) == exact
    if type(exact) is int:
        return type(decoded) is int and exact == decoded
    if isinstance(exact, list):
        return isinstance(decoded, list) and len(exact) == len(decoded) and all(
            _same_json_value(a, b) for a, b in zip(exact, decoded))
    if isinstance(exact, dict):
        return isinstance(decoded, dict) and exact.keys() == decoded.keys() and all(
            _same_json_value(exact[k], decoded[k]) for k in exact)
    return type(exact) is type(decoded) and exact == decoded


def map_original_plan_to_slots(plan: OriginalPlanEvidence, content: VerifiedContentGeneration,
                               position_owner: VerifiedPositionOwner) -> tuple[DecisionSlotBinding, ...] | Blocked:
    """Map each frozen occurrence, without granting issuance or grade authority."""
    def block(code: str, index: int | None = None) -> Blocked:
        return Blocked('BLOCK', code, plan.original_attempt_id, index)
    try:
        raw = json.loads(plan.stored_record_json, object_pairs_hook=_unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        exact = json.loads(plan.stored_record_json, object_pairs_hook=_unique_object,
                           parse_float=Decimal,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
        if not _same_json_value(exact, raw):
            return block('LP_PLAN_INVALID')
        decoded = decode_issued_record(raw)
        if isinstance(decoded, Hold):
            return block('LP_PLAN_INVALID')
        if (plan.storage_encoding != 'postgres-jsonb-value' or plan.canonical_codec != 'legacy-issued-plan-record-v1'
            or decoded.plan_id != plan.original_plan_digest or decoded.attempt_id != plan.original_attempt_id
            or type(plan.queue_entry_id) is not int or decoded.queue_entry_id != plan.queue_entry_id
            or type(plan.card_revision) is not int or decoded.original_card_revision != plan.card_revision
            or decoded.original_card_id != plan.card_id or decoded.owner_epoch != plan.owner_epoch
            or decoded.target.key.repertoire_id != plan.repertoire_id or decoded.target.key.variant != plan.variant
            or decoded.source_digest != plan.source_digest or decoded.local_day != plan.issue_study_day
            or decoded.issued_at != plan.issued_at):
            return block('LP_PLAN_INVALID')
        # Replay the entire frozen route and verify the original slot FENs.
        board = chess.Board(decoded.starting_fen)
        if not board.is_valid():
            return block('LP_PLAN_INVALID')
        boards = []
        for move_text in decoded.moves_uci:
            boards.append(board.copy())
            move = chess.Move.from_uci(move_text)
            if move not in board.legal_moves:
                return block('LP_PLAN_INVALID')
            board.push(move)
        for slot in decoded.decision_slots:
            if opening_position_key(boards[slot.move_offset].fen(en_passant='legal')) != opening_position_key(slot.fen):
                return block('LP_PLAN_INVALID')
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
        return block('LP_PLAN_INVALID')
    if content.complete is not True or content.current is not True:
        return block('LP_CONTENT_UNVERIFIED')
    if (not decoded.source_complete or not decoded.source_current_integrity or not decoded.route_legal_verified
        or not position_owner.current or not plan.source_authority or content.authority != plan.source_authority
        or not position_owner.authority or not content.generation_identity or not content.certificate_digest
        or content.source_digest != decoded.source_digest or content.snapshot_id != decoded.source_snapshot_id
        or content.source_revision != decoded.source_revision or position_owner.source_digest != decoded.source_digest
        or position_owner.owner_epoch != decoded.owner_epoch or position_owner.repertoire_id != plan.repertoire_id):
        return block('LP_SOURCE_STALE')
    result = []
    for index, slot in enumerate(decoded.decision_slots):
        matches = [item for item in content.bindings if (item.repertoire_id, item.variant, item.route_id, item.original_ply)
                   == (plan.repertoire_id, plan.variant, decoded.route_id, slot.original_ply)]
        if not matches:
            return block('LP_SLOT_MISSING', index)
        if len(matches) != 1:
            return block('LP_SLOT_AMBIGUOUS', index)
        item = matches[0]
        try:
            if chess.Move.from_uci(item.expected_uci) not in boards[slot.move_offset].legal_moves:
                return block('LP_SLOT_ILLEGAL', index)
            if (opening_position_key(item.fen) != item.exact_position
                or item.exact_position != opening_position_key(slot.fen)
                or item.expected_uci != slot.definition.expected_uci
                or item.policy_version != slot.definition.policy_version):
                return block('LP_POSITION_OWNER_MISMATCH', index)
        except (ValueError, TypeError):
            return block('LP_SLOT_ILLEGAL', index)
        expected = _public_definition(slot.definition)
        owners = [d for d in position_owner.definitions if (d.repertoire_id, d.variant, d.exact_position)
                  == (expected.repertoire_id, expected.variant, expected.exact_position)]
        if len(owners) != 1 or owners[0] != expected:
            return block('LP_POSITION_OWNER_MISMATCH', index)
        witness = next((d for d in decoded.due_positions if d.definition == slot.definition), None)
        due = None if witness is None else DueWitness(_public_definition(witness.definition), witness.due,
                    witness.study_day, witness.owner_epoch, witness.position_revision, witness.issued_at)
        result.append(DecisionSlotBinding(plan.original_attempt_id, index, (decoded.route_id, slot.original_ply),
            item.repertoire_id, item.variant, item.exact_position, item.expected_uci, item.policy_version,
            decoded.owner_epoch, decoded.source_digest, decoded.plan_id, content.certificate_digest,
            'L-PLAN.v1', slot.move_offset, slot.fen, due))
    return tuple(result)
