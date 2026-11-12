"""Pure canonical first-response adjudication and receipt decoding."""

import json
from typing import Any


def _position_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _position_digest(value: Any) -> str:
    import hashlib

    return hashlib.sha256(_position_canonical(value).encode("ascii")).hexdigest()


def position_first_response_envelope(
    plan,
    *,
    event_id,
    decision_index,
    actual_uci,
    manual_failure,
    assistance_before,
    observed_at,
    classify_alternative,
):
    """Adjudicate one actual action against the decoded frozen server plan.

    ``classify_alternative`` is the authored-source owner's fenced capability;
    it returns wrong, alternate or unverified for a legal non-expected move.
    The caller must use the contiguous checkpoint prefix and retain the first
    action only. Neither client FEN nor client disposition enters this function.
    """
    import chess
    import re
    from datetime import datetime

    if not isinstance(event_id, str) or not event_id or type(decision_index) is not int:
        raise ValueError("invalid_first_response_identity")
    if decision_index < 0 or decision_index >= len(plan.decision_slots):
        raise ValueError("decision_index_outside_issued_plan")
    if type(manual_failure) is not bool or type(assistance_before) is not bool:
        raise ValueError("invalid_first_response_flags")
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("invalid_observed_at") from exc
    if observed.utcoffset() is None:
        raise ValueError("invalid_observed_at")
    slot = plan.decision_slots[decision_index]
    definition = slot.definition
    if manual_failure:
        if actual_uci is not None:
            raise ValueError("manual_failure_has_move")
        disposition = "manual_failure"
    else:
        if not isinstance(actual_uci, str) or not re.fullmatch(
            '[a-h][1-8][a-h][1-8][qrbn]?', actual_uci
        ):
            raise ValueError("invalid_actual_uci")
        try:
            move = chess.Move.from_uci(actual_uci)
            board = chess.Board(slot.fen)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid_actual_uci") from exc
        if actual_uci == definition.expected_uci:
            if move not in board.legal_moves:
                raise ValueError("invalid_issued_expected_move")
            disposition = "expected"
        elif move not in board.legal_moves:
            disposition = "illegal"
        else:
            disposition = classify_alternative(definition, actual_uci)
            if disposition not in {"wrong", "alternate", "unverified"}:
                raise ValueError("invalid_source_disposition")
    return {
        "version": 1,
        "event_id": event_id,
        "attempt_id": plan.attempt_id,
        "decision_index": decision_index,
        "queue_entry_id": plan.queue_entry_id,
        "card_id": plan.original_card_id,
        "card_revision": plan.original_card_revision,
        "repertoire_id": definition.key.repertoire_id,
        "variant": definition.key.variant,
        "position": definition.key.position,
        "owner_epoch": plan.owner_epoch,
        "original_ply": slot.original_ply,
        "expected_uci": definition.expected_uci,
        "policy_version": definition.policy_version,
        "actual_uci": actual_uci,
        "manual_failure": manual_failure,
        "assistance_before": assistance_before,
        "observed_at": observed_at,
        "disposition": disposition,
    }


def decode_position_first_response(payload, digest, plan):
    """Verify stored canonical bytes and all immutable issued decision identities.

    A digest establishes integrity, not source authority. The disposition is
    authoritative only because this decoder is used for an owner-saved row.
    """
    if not isinstance(payload, dict) or _position_digest(payload) != digest:
        raise ValueError("first_response_digest_mismatch")
    keys = {
        "version",
        "event_id",
        "attempt_id",
        "decision_index",
        "queue_entry_id",
        "card_id",
        "card_revision",
        "repertoire_id",
        "variant",
        "position",
        "owner_epoch",
        "original_ply",
        "expected_uci",
        "policy_version",
        "actual_uci",
        "manual_failure",
        "assistance_before",
        "observed_at",
        "disposition",
    }
    if set(payload) != keys or payload["version"] != 1:
        raise ValueError("invalid_first_response_envelope")
    # Revalidate all facts, preserving the saved source adjudication only for
    # legal alternatives. It must never convert alternate/unverified to wrong.
    expected = position_first_response_envelope(
        plan,
        event_id=payload["event_id"],
        decision_index=payload["decision_index"],
        actual_uci=payload["actual_uci"],
        manual_failure=payload["manual_failure"],
        assistance_before=payload["assistance_before"],
        observed_at=payload["observed_at"],
        classify_alternative=lambda _definition, _actual: payload["disposition"],
    )
    if expected != payload:
        raise ValueError("first_response_issued_plan_mismatch")
    return expected
