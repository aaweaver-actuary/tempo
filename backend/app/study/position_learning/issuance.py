"""Immutable position plan issuance in the caller's PostgreSQL transaction.

No connection, commit, selection, graph projection, or clock is owned here.
Replay deliberately does not consult current source or schedule.
"""

from __future__ import annotations

import json

from tempo_evidence_math.position_learning import Hold
from tempo_storage.study import position_issued_plans as storage
from tempo_storage.study.position_due_candidates import PositionDueCandidateReader
from tempo_storage.study.position_owner_epoch import (
    fence_position_owner_in_writer,
    owner_mode,
)

from app.study.position_learning.issued_plan_contracts import IssuedPlan
from app.study.position_learning.issued_plan_service import (
    is_prepared_position_issuance,
)


def _writer(db):
    try:
        return not db.read_only and db.raw.info.transaction_status.name == "INTRANS"
    except AttributeError:
        return False


def _decode(row):
    if row is None:
        return None
    raw = row["plan_json"]
    try:
        if isinstance(raw, str):
            raw = json.loads(raw)
        plan = IssuedPlan.from_record(raw)
        if isinstance(plan, Hold):
            return plan
        token = raw["authored_source_token"]
        if (
            not isinstance(token, dict)
            or row["attempt_id"] != plan.attempt_id
            or row["queue_entry_id"] != plan.queue_entry_id
            or row["card_id"] != plan.original_card_id
            or row["card_revision"] != plan.original_card_revision
            or row["repertoire_id"] != plan.target.key.repertoire_id
            or row["variant"] != plan.target.key.variant
            or row["owner_epoch"] != plan.owner_epoch
            or row["source_digest"] != token["authored_digest"]
            or row["issue_study_day"] != plan.local_day
            or row["plan_digest"] != plan.plan_id
            or row["issued_at"] != plan.issued_at
        ):
            return Hold("position_issued_row_mismatch")
        return plan
    except (KeyError, TypeError, ValueError):
        return Hold("position_issued_row_invalid")


def replay_exact_attempt(database, attempt_id):
    """Return the immutable record for an existing attempt, or None if absent."""
    if not isinstance(attempt_id, str) or not attempt_id:
        return Hold("position_attempt_identity_required")
    return _decode(storage.issued_plan(database, (attempt_id,)).fetchone())


def _prefix_matches(route, plan, proof):
    """Check the bounded frozen prefix; preparation already did chess planning."""
    cert = route.certificate
    if (
        proof.root_fen != route.start_fen
        or proof.study_day != plan.local_day
        or proof.owner_epoch != plan.owner_epoch
        or (proof.source_snapshot_id, proof.source_revision, proof.source_digest)
        != (cert.snapshot_id, cert.source_revision, cert.source_digest)
    ):
        return False
    shared = 0
    for actual, expected in zip(proof.moves_uci, route.moves_uci):
        if actual != expected:
            break
        shared += 1
    if shared == 0 or plan.target_ply < shared:
        return False
    prior = {d.ply: d for d in proof.attempt_decisions}
    route_prefix = {d.ply: d for d in route.decisions if d.ply < shared}
    if any(prior.get(ply) != decision for ply, decision in route_prefix.items()):
        return False
    if any(
        ply < shared and route_prefix.get(ply) != decision
        for ply, decision in prior.items()
    ):
        return False
    if not any(d.ply < shared and d.ply < plan.target_ply for d in route.decisions):
        return False
    uncovered = [d for d in route.decisions if d.ply >= shared]
    return bool(uncovered) and plan.decision_slots[0].original_ply == uncovered[0].ply


def issue_in_borrowed_transaction(database, prepared):
    """Persist one prepared plan or return the original, with no commit.

    The queue owner must invoke this before returning the head. A Hold requires
    the caller to abandon issuance; no effects are made before the final insert.
    """
    if not _writer(database):
        return Hold("borrowed_owner_writer_transaction_required")
    if not is_prepared_position_issuance(prepared):
        return Hold("position_issuance_preparation_required")
    plan, source = prepared.plan, prepared.source
    try:
        record = plan.to_record()
    except (AttributeError, TypeError, ValueError):
        return Hold("position_proposal_invalid")
    decoded = IssuedPlan.from_record(record)
    if (
        isinstance(decoded, Hold)
        or record.get("authored_source_token") != source.authored_token.as_dict()
    ):
        return Hold("position_proposal_source_mismatch")
    # An explicit retry is resolved before current source validation.
    existing = replay_exact_attempt(database, plan.attempt_id)
    if existing is not None:
        if isinstance(existing, Hold):
            return existing
        return (
            existing
            if existing.to_record() == record
            else Hold("position_attempt_payload_conflict")
        )
    token = source.authored_token
    request = prepared.request
    if (
        request.routes != source.routes
        or request.target != plan.target
        or request.dues != plan.due_positions
        or (
            request.original_card_id,
            request.original_card_revision,
            request.queue_entry_id,
            request.attempt_id,
        )
        != (
            plan.original_card_id,
            plan.original_card_revision,
            plan.queue_entry_id,
            plan.attempt_id,
        )
    ):
        return Hold("position_proposal_mismatch")
    candidates = [
        (r, d)
        for r in source.routes
        for d in r.decisions
        if d.definition.key == plan.target.key
    ]
    if (
        not candidates
        or len({d.definition for _, d in candidates}) != 1
        or len(
            {
                (
                    r.certificate.snapshot_id,
                    r.certificate.source_revision,
                    r.certificate.source_digest,
                )
                for r, _ in candidates
            }
        )
        != 1
    ):
        return Hold("position_route_unproven")
    candidates.sort(
        key=lambda item: (
            item[0].route_id,
            item[1].ply,
            item[0].moves_uci,
            item[0].certificate.snapshot_id,
            item[0].certificate.source_revision,
            item[0].certificate.source_digest,
        )
    )
    eligible = [
        (r, d)
        for r, d in candidates
        if r.legal_verified
        and r.certificate.complete
        and r.certificate.current_integrity
    ]
    if not eligible or (eligible[0][0].route_id, eligible[0][1].ply) != (
        plan.route_id,
        plan.target_ply,
    ):
        return Hold("position_route_unproven")
    # Check the frozen route against the source owner's capability. Prefix
    # eligibility was prepared outside the writer and its evidence is re-read
    # below before any insert.
    routes = [r for r in source.routes if r.route_id == plan.route_id]
    if len(routes) != 1:
        return Hold("position_route_unproven")
    route = routes[0]
    cert = route.certificate
    end_ply = plan.start_ply + len(plan.moves_uci)
    expected_decisions = tuple(
        d for d in route.decisions if plan.start_ply <= d.ply < end_ply
    )
    omitted_decisions = tuple(d for d in route.decisions if d.ply >= end_ply)
    shortened_end = end_ply < len(route.moves_uci)
    if (
        not route.legal_verified
        or not cert.complete
        or not cert.current_integrity
        or plan.moves_uci != route.moves_uci[plan.start_ply : end_ply]
        or end_ply > len(route.moves_uci)
        or (
            shortened_end
            and (
                request.end_ply != end_ply
                or not omitted_decisions
                or not expected_decisions
                or expected_decisions[-1].ply + 1 != end_ply
                or end_ply <= plan.target_ply
            )
        )
        or (not shortened_end and request.end_ply is not None)
        or (plan.start_ply == 0 and plan.starting_fen != route.start_fen)
        or (
            not plan.skipped_prefix_proof_ids
            and (
                plan.start_ply != 0
                or plan.opponent_cue_uci is not None
                or plan.opponent_cue_ply is not None
            )
        )
        or (
            plan.skipped_prefix_proof_ids
            and (
                len(plan.skipped_prefix_proof_ids) != 3
                or not expected_decisions
                or plan.start_ply != expected_decisions[0].ply - 1
                or plan.opponent_cue_ply != plan.start_ply
                or plan.opponent_cue_uci != route.moves_uci[plan.start_ply]
            )
        )
        or (plan.source_snapshot_id, plan.source_revision, plan.source_digest)
        != (cert.snapshot_id, cert.source_revision, cert.source_digest)
        or not plan.source_complete
        or not plan.source_current_integrity
        or not plan.route_legal_verified
        or tuple((s.original_ply, s.definition, s.fen) for s in plan.decision_slots)
        != tuple((d.ply, d.definition, d.fen) for d in expected_decisions)
        or not any(
            d.ply == plan.target_ply and d.definition == plan.target
            for d in route.decisions
        )
    ):
        return Hold("position_route_unproven")
    if plan.skipped_prefix_proof_ids:
        # The start is the opponent move immediately after a preceding authored
        # learner decision. Derive its FEN with at most one move, not a graph walk.
        import chess

        previous = [d for d in route.decisions if d.ply == plan.start_ply - 1]
        if len(previous) != 1:
            return Hold("position_route_unproven")
        try:
            board = chess.Board(previous[0].fen)
            board.push_uci(route.moves_uci[previous[0].ply])
            if board.fen() != plan.starting_fen:
                return Hold("position_route_unproven")
        except (ValueError, IndexError):
            return Hold("position_route_unproven")
    owner = owner_mode(database, token.repertoire_id, token.authored_digest)
    if owner.disposition != "position" or owner.epoch != plan.owner_epoch:
        return Hold(
            owner.reason
            if owner.disposition != "position"
            else "position_owner_epoch_changed"
        )
    # Match the existing card-edit lock before card/day/queue and origin locks.
    lock = storage.lock_card(
        database, ("tempo:card-edit:" + plan.original_card_id,)
    ).fetchone()
    if not lock or not lock[0]:
        return Hold("position_card_busy")
    card = storage.locked_card(database, (plan.original_card_id,)).fetchone()
    day = storage.locked_day(database, (plan.local_day.isoformat(),)).fetchone()
    queue = storage.locked_queue(database, (plan.queue_entry_id,)).fetchone()
    if (
        not card
        or not day
        or not queue
        or card["revision"] != plan.original_card_revision
        or card["archived"]
        or card["content_type"] != "opening"
        or queue["card_id"] != plan.original_card_id
        or queue["status"] != "queued"
        or queue["queue_date"] != plan.local_day.isoformat()
    ):
        return Hold("position_original_admission_unavailable")
    # Require the schema owner to install an exact uniqueness/index fence.
    # Schema 57 by itself has only the attempt-id primary key; a LIMIT on an
    # unindexed origin lookup would still scan unbounded history.
    index = storage.origin_index_exists(database, ()).fetchone()
    if not index or not index[0]:
        return Hold("position_origin_unique_index_required")
    # Two tabs cannot mint different attempts for the same retained origin.
    rows = storage.issued_for_entry(
        database,
        (plan.queue_entry_id, plan.original_card_id, plan.original_card_revision),
    ).fetchall()
    if rows:
        if len(rows) != 1:
            return Hold("position_original_admission_ambiguous")
        prior = _decode(rows[0])
        if isinstance(prior, Hold):
            return prior
        return (
            prior
            if prior.attempt_id != plan.attempt_id or prior.to_record() == record
            else Hold("position_attempt_payload_conflict")
        )
    decision = fence_position_owner_in_writer(
        database,
        source,
        definition=plan.target,
        epoch=plan.owner_epoch,
        queue_entry_id=plan.queue_entry_id,
        card_id=plan.original_card_id,
        revision=plan.original_card_revision,
    )
    if decision.disposition != "position":
        return Hold(decision.reason)
    count = storage.origin_count(database, (plan.queue_entry_id,)).fetchone()
    if not count or count[0] != 1:
        return Hold("position_original_admission_ambiguous")
    origin = storage.locked_origins(database, (plan.queue_entry_id,)).fetchall()
    if (
        len(origin) != 1
        or origin[0]["card_id"] != plan.original_card_id
        or origin[0]["revision"] != plan.original_card_revision
        or origin[0]["queue_date"] != queue["queue_date"]
        or origin[0]["cycle"] != queue["cycle"]
        or origin[0]["admission_repertoire_id"] != token.repertoire_id
        or any(
            origin[0][k] != card[k]
            for k in ("start_fen", "moves_json", "trained_color", "content_type")
        )
    ):
        return Hold("position_original_admission_ambiguous")
    # Serialize even when the attempt row does not exist yet.
    attempt_lock = storage.lock_attempt(
        database, ("tempo:position-attempt:" + plan.attempt_id,)
    ).fetchone()
    if not attempt_lock or not attempt_lock[0]:
        return Hold("position_attempt_busy")
    # Existing evidence or review may already bind this caller-supplied attempt.
    evidence_count = storage.attempt_count(database, (plan.attempt_id,)).fetchone()
    evidence = storage.locked_attempt(database, (plan.attempt_id,)).fetchone()
    if evidence_count[0] and evidence is None:
        return Hold("position_attempt_busy")
    if evidence is not None and (
        evidence["card_id"],
        evidence["card_revision"],
        evidence["queue_entry_id"],
        evidence["repertoire_id"],
    ) != (
        plan.original_card_id,
        plan.original_card_revision,
        plan.queue_entry_id,
        token.repertoire_id,
    ):
        return Hold("position_attempt_identity_conflict")
    if storage.review_receipt(database, (plan.attempt_id,)).fetchone():
        return Hold("position_attempt_already_reviewed")
    reader = PositionDueCandidateReader(
        database,
        repertoire_id=token.repertoire_id,
        owner_epoch=plan.owner_epoch,
        source_digest=token.authored_digest,
        issued_at=plan.issued_at,
        study_day=plan.local_day,
    )
    if shortened_end:
        # The proposed cutoff is authorized only by the first omitted state's
        # actual absence, not merely by an omitted or forged witness.
        omitted = reader.read_context((omitted_decisions[0].definition.key,))
        if (
            isinstance(omitted, Hold)
            or len(omitted) != 1
            or not isinstance(omitted[0], Hold)
            or omitted[0].reason != "position_state_missing"
        ):
            return Hold("position_presentation_end_unproven")
    keys = tuple(w.definition.key for w in plan.due_positions)
    locked = reader.states.read_many(keys, lock=True)
    if isinstance(locked, Hold):
        return locked
    if any(isinstance(s, Hold) for s in locked):
        return Hold("position_due_state_unavailable")
    current = reader.read_context(keys)
    if isinstance(current, Hold) or current != plan.due_positions:
        return Hold("position_due_witness_changed")
    if plan.skipped_prefix_proof_ids:
        matches = [
            p
            for p in request.clean_proofs
            if (p.attempt_id, p.terminal_receipt_id, p.review_receipt_id)
            == plan.skipped_prefix_proof_ids
        ]
        if len(matches) != 1:
            return Hold("position_prefix_authority_unavailable")
        actual = read_applicable_clean_proofs(
            database,
            (matches[0].attempt_id,),
            target=plan.target,
            owner_epoch=plan.owner_epoch,
            study_day=plan.local_day,
            lock=True,
        )
        if (
            isinstance(actual, Hold)
            or len(actual) != 1
            or actual[0] != matches[0]
            or not _prefix_matches(route, plan, actual[0])
        ):
            return Hold("position_prefix_authority_unavailable")
    target_witness = next(
        (w for w in plan.due_positions if w.definition == plan.target), None
    )
    if target_witness is None:
        return Hold("position_due_state_unavailable")
    already_good = storage.good_today(
        database,
        (
            token.repertoire_id,
            plan.target.key.variant,
            plan.target.key.position,
            plan.local_day,
            plan.owner_epoch,
            target_witness.position_revision,
        ),
    ).fetchone()
    if already_good:
        return Hold("position_already_cleanly_tested_today")
    saved = storage.insert_plan(
        database,
        (
            plan.attempt_id,
            plan.queue_entry_id,
            plan.original_card_id,
            plan.original_card_revision,
            token.repertoire_id,
            plan.target.key.variant,
            plan.owner_epoch,
            token.authored_digest,
            plan.local_day,
            plan.plan_id,
            json.dumps(record, sort_keys=True, separators=(",", ":")),
            plan.issued_at,
        ),
    ).fetchone()
    if saved:
        return plan
    prior = replay_exact_attempt(database, plan.attempt_id)
    if isinstance(prior, Hold):
        return prior
    return (
        prior
        if prior is not None and prior.to_record() == record
        else Hold("position_attempt_payload_conflict")
    )


def read_clean_presentation_proof(database, attempt_id, *, lock=False):
    """Read one complete, saved, clean full-root presentation by exact ID.

    Bounded by the plan's 20 learner decisions and receipt uniqueness. A prior
    shortened plan cannot establish its skipped moves and is held explicitly.
    """
    from tempo_evidence_math.position_learning import DecisionOccurrence

    from app.study.position_learning.prefix_eligibility import (
        CleanFirstResponse,
        CleanPresentationProof,
    )

    try:
        from app.study.position_learning.first_response_codec import (
            decode_position_first_response,
        )
    except ImportError:
        return Hold("position_response_decoder_unavailable")
    plan = replay_exact_attempt(database, attempt_id)
    if plan is None:
        return Hold("position_prior_attempt_missing")
    if isinstance(plan, Hold):
        return plan
    if plan.start_ply != 0 or plan.skipped_prefix_proof_ids:
        return Hold("position_prior_shortened_proof_unavailable")
    row = storage.saved_completion(database, (attempt_id,), lock=lock).fetchone()
    if not row:
        return Hold("position_prior_completion_unavailable")
    try:
        terminal = (
            json.loads(row["terminal_json"])
            if isinstance(row["terminal_json"], str)
            else row["terminal_json"]
        )
        if (
            row["state"] != "complete"
            or row["retired_operation_id"] is not None
            or not isinstance(terminal, dict)
            or terminal.get("state") != "complete"
            or type(terminal.get("final_sequence")) is not int
            or terminal["final_sequence"] != row["contiguous_sequence"]
            or row["review_id"] is None
            or row["review_id"] != row["receipt_review"]
            or row["outcome"] != "correct"
            or row["guided"]
            or row["source_kind"] not in ("study", "position_study")
            or (
                row["source_kind"] == "position_study"
                and row["source_ref"] != f"attempt:{attempt_id}"
            )
            or row["invalidated_at"] is not None
            or (
                row["card_id"],
                row["card_revision"],
                row["queue_entry_id"],
                row["repertoire_id"],
            )
            != (
                plan.original_card_id,
                plan.original_card_revision,
                plan.queue_entry_id,
                plan.target.key.repertoire_id,
            )
            or (row["receipt_card"], row["receipt_queue"], row["review_card"])
            != (plan.original_card_id, plan.queue_entry_id, plan.original_card_id)
        ):
            return Hold("position_prior_completion_unavailable")
        rows = storage.first_responses(database, (attempt_id,), lock=lock).fetchall()
        if len(rows) != len(plan.decision_slots):
            return Hold("position_prior_responses_incomplete")
        decisions, responses = [], []
        for index, (receipt, slot) in enumerate(zip(rows, plan.decision_slots)):
            payload = receipt["payload_json"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            payload = decode_position_first_response(
                payload, receipt["payload_digest"], plan
            )
            if (
                receipt["decision_index"] != index
                or receipt["event_id"] != payload["event_id"]
                or receipt["attempt_id"] != attempt_id
                or payload["decision_index"] != index
                or receipt["repertoire_id"] != slot.definition.key.repertoire_id
                or receipt["variant"] != slot.definition.key.variant
                or receipt["position"] != slot.definition.key.position
                or receipt["owner_epoch"] != plan.owner_epoch
                or payload["disposition"] != "expected"
                or payload["manual_failure"]
                or payload["assistance_before"]
                or payload["actual_uci"] != slot.definition.expected_uci
            ):
                return Hold("position_prior_responses_not_clean")
            decisions.append(
                DecisionOccurrence(slot.original_ply, slot.definition, slot.fen)
            )
            responses.append(
                CleanFirstResponse(
                    slot.original_ply,
                    slot.definition,
                    payload["actual_uci"],
                    False,
                    payload["event_id"],
                )
            )
        return CleanPresentationProof(
            attempt_id,
            plan.route_id,
            plan.starting_fen,
            plan.moves_uci,
            tuple(decisions),
            tuple(responses),
            plan.source_snapshot_id,
            plan.source_revision,
            plan.source_digest,
            plan.local_day,
            plan.owner_epoch,
            True,
            attempt_id,
            str(row["review_id"]),
        )
    except (KeyError, TypeError, ValueError):
        return Hold("position_prior_evidence_invalid")


def read_applicable_clean_proofs(
    database, attempt_ids, *, target, owner_epoch, study_day, lock=False
):
    """Read bounded prior proofs and remove only superseded target coverage.

    A later accepted Again may reopen the target, while the prior clean moves
    before its first occurrence remain usable as context. The returned proof is
    a conservative projection of the saved full-root receipt; callers must not
    supply their own proof claims. The writer repeats this read after locking
    the current state before it accepts a shortened plan.
    """
    from dataclasses import replace

    from tempo_evidence_math.position_learning import PositionDefinition

    if (
        not isinstance(target, PositionDefinition)
        or not isinstance(attempt_ids, (tuple, list))
        or len(attempt_ids) > 20
        or any(not isinstance(a, str) or not a for a in attempt_ids)
        or len(set(attempt_ids)) != len(attempt_ids)
    ):
        return Hold("position_prefix_proof_request_invalid")
    results = []
    for attempt_id in attempt_ids:
        proof = read_clean_presentation_proof(database, attempt_id, lock=lock)
        if isinstance(proof, Hold):
            return proof
        if proof.owner_epoch != owner_epoch or proof.study_day != study_day:
            return Hold("position_prefix_proof_scope_changed")
        target_plies = [
            d.ply for d in proof.attempt_decisions if d.definition == target
        ]
        if not target_plies:
            results.append(proof)
            continue
        prior = replay_exact_attempt(database, attempt_id)
        if prior is None or isinstance(prior, Hold):
            return Hold("position_prior_attempt_missing")
        witness = next((w for w in prior.due_positions if w.definition == target), None)
        if witness is None:
            return Hold("position_prior_target_witness_missing")
        # The lookup must be backed by the schema owner's partial Again index;
        # LIMIT alone cannot bound a scan over historical grades.
        index = storage.again_index_exists(database, ()).fetchone()
        if not index or not index[0]:
            return Hold("position_again_lookup_index_required")
        superseded = storage.again_after_review(
            database,
            (
                attempt_id,
                target.key.repertoire_id,
                target.key.variant,
                target.key.position,
                owner_epoch,
                study_day,
                target.expected_uci,
                target.policy_version,
                witness.position_revision,
                witness.position_revision,
            ),
        ).fetchone()
        if not superseded:
            results.append(proof)
            continue
        cutoff = min(target_plies)
        results.append(
            replace(
                proof,
                moves_uci=proof.moves_uci[:cutoff],
                attempt_decisions=tuple(
                    d for d in proof.attempt_decisions if d.ply < cutoff
                ),
                first_responses=tuple(
                    r for r in proof.first_responses if r.ply < cutoff
                ),
            )
        )
    return tuple(results)
