"""Prepare and fence an ordinary position-owned queue head before its writer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.study.position_learning.issued_plan_contracts import IssuedPlan
    from app.study.position_learning.issued_plan_service import PreparedPositionIssuance
from collections import OrderedDict
from threading import Lock
from time import monotonic

_source_cache = OrderedDict()
_source_cache_lock = Lock()


def _cached_position_source(database, repertoire_id, epoch, digest):
    """Keep a tiny short-lived revision-aware preparation cache.

    This is only a performance hint: every new issue still acquires the source
    owner's complete in-writer fence, including authored rows and integrity.
    """
    from tempo_evidence_math.position_learning import Hold
    from app.repertoire.position_study_source import prepare_position_source
    from tempo_storage.http_composition.queue_projection import (
        position_source_revision,
    )

    revision = position_source_revision(database, repertoire_id)
    if revision is None:
        return Hold("position_source_revision_unavailable")
    key = (repertoire_id, epoch, digest, tuple(revision))
    now = monotonic()
    with _source_cache_lock:
        cached = _source_cache.get(key)
        if cached and now - cached[0] < 10:
            _source_cache.move_to_end(key)
            return cached[1]
    source = prepare_position_source(database, repertoire_id)
    if not isinstance(source, Hold):
        with _source_cache_lock:
            _source_cache[key] = (now, source)
            _source_cache.move_to_end(key)
            while len(_source_cache) > 2:
                _source_cache.popitem(last=False)
    return source


@dataclass(frozen=True)
class PreparedQueueHead:
    payload: dict[str, object]
    mode: Literal["legacy", "replay", "position", "hold"]
    proposal: IssuedPlan | PreparedPositionIssuance | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in ("legacy", "replay", "position", "hold"):
            raise ValueError("Unknown prepared queue head mode")
        if (self.mode in ("replay", "position")) != (self.proposal is not None):
            raise ValueError("Prepared queue head proposal does not match its mode")
        if self.mode == "hold":
            if not self.reason:
                raise ValueError("Held queue head needs its original reason")
        elif self.reason is not None:
            raise ValueError("Only a held queue head has a reason")


def prepare_queue_head(payload):
    """Read an exact replay or prepare one position head before the writer opens."""
    from tempo_storage.study import next_exercise_reads as reads
    from datetime import date, datetime, timezone
    from uuid import NAMESPACE_URL, uuid5
    from tempo_evidence_math.position_learning import Hold
    from tempo_storage.connections import read_connection
    from tempo_storage.study.position_due_candidates import (
        PositionDueCandidateReader,
    )
    from app.study.position_learning.issuance import replay_exact_attempt, read_clean_presentation_proof, read_applicable_clean_proofs
    from app.study.position_learning.issued_plan_contracts import SelectionRequest
    from app.study.position_learning.issued_plan_service import (
        prepare_position_issuance,
    )
    from app.study.position_learning.route_planner import choose_plan
    from .position_queue_admission import select_carrier
    from .background_runtime import training_timing_stage

    def held(reason):
        return PreparedQueueHead(payload, "hold", reason=reason)

    with read_connection() as db:
        rows = reads.original_attempt_ids(db, entry_id=payload["entry_id"], card_id=payload["card_id"], revision=payload["expected_revision"])
        if len(rows) > 1:
            return held("position_original_admission_ambiguous")
        if rows:
            plan = replay_exact_attempt(db, rows[0])
            if isinstance(plan, Hold) or plan is None:
                return held(
                    plan.reason if isinstance(plan, Hold) else "position_plan_missing"
                )
            return PreparedQueueHead(payload, "replay", plan)
        origin = reads.admission_origin(db, entry_id=payload["entry_id"], card_id=payload["card_id"], revision=payload["expected_revision"])
        if not origin:
            return held("position_original_admission_unavailable")
        if origin.content_type != "opening":
            return PreparedQueueHead(payload, "legacy")
        rep = origin.repertoire_id
        if not rep:
            owners = reads.applicable_owner_exists(db, card_id=payload["card_id"])
            return (
                held("position_admission_scope_ambiguous")
                if owners
                else PreparedQueueHead(payload, "legacy")
            )
        owner = reads.position_owner(db, repertoire_id=rep)
        if not owner or owner.mode in ("inactive", "shadow"):
            return PreparedQueueHead(payload, "legacy")
        if owner.mode != "active":
            return held("position_owner_" + owner.mode)
        if origin.issued_as_head:
            return held("position_issued_head_plan_missing")
        with training_timing_stage("position_source_prepare"):
            source = _cached_position_source(
                db, rep, owner.epoch, owner.source_digest
            )
        if isinstance(source, Hold):
            return held(source.reason)
        if source.authored_token.authored_digest != owner.source_digest:
            return held("position_owner_source_changed")
        instant, day = (
            datetime.now(timezone.utc),
            date.fromisoformat(payload["queue_date"]),
        )
        reader = PositionDueCandidateReader(
            db,
            repertoire_id=rep,
            owner_epoch=owner.epoch,
            source_digest=owner.source_digest,
            issued_at=instant,
            study_day=day,
        )
        with training_timing_stage("position_due_carrier_scan"):
            target, cursor = None, None
            # Bound work independently of catalogue size; a larger backlog stays
            # held rather than falling back to physical-card scheduling.
            for _ in range(10):
                page = reader.read_due_page(after=cursor, limit=100)
                if isinstance(page, Hold):
                    return held(page.reason)
                for candidate in page.candidates:
                    carrier = select_carrier(
                        source,
                        candidate.state.definition,
                        card_id=payload["card_id"],
                    )
                    if (
                        not isinstance(carrier, Hold)
                        and carrier.presentation_revision
                        == payload["expected_revision"]
                    ):
                        target = candidate.state.definition
                        break
                if target or page.next_cursor is None:
                    break
                cursor = page.next_cursor
        if target is None:
            return held("position_due_target_unavailable")
        with training_timing_stage("position_prior_clean_proofs"):
            proofs = []
            prior = reads.prior_attempt_ids(db, repertoire_id=rep, owner_epoch=owner.epoch, study_day=day)
            for attempt_id in prior:
                proof = read_clean_presentation_proof(db, attempt_id)
                if not isinstance(proof, Hold):
                    proofs.append(proof)
            proofs = read_applicable_clean_proofs(
                db,
                [p.attempt_id for p in proofs],
                target=target,
                owner_epoch=owner.epoch,
                study_day=day,
            )
        if isinstance(proofs, Hold):
            return held(proofs.reason)
        with training_timing_stage("position_route_choice"):
            route = choose_plan(
                target.key,
                source.routes,
                clean_proofs=proofs,
                study_day=day,
                owner_epoch=owner.epoch,
            )
        if isinstance(route, Hold):
            return held(route.reason)
        keys = tuple(
            dict.fromkeys(d.definition.key for d in route.decision_occurrences)
        )
        with training_timing_stage("position_context_witnesses"):
            witnesses = reader.read_context(keys)
        if isinstance(witnesses, Hold):
            return held(witnesses.reason)
        # Only an absent future state may end a presentation. Every decision
        # through the target still needs its actual owner witness.
        by_key = dict(zip(keys, witnesses))
        end_ply = None
        for index, decision in enumerate(route.decision_occurrences):
            witness = by_key[decision.definition.key]
            if isinstance(witness, Hold):
                if (
                    witness.reason != "position_state_missing"
                    or decision.ply <= route.target_ply
                    or index == 0
                ):
                    return held(witness.reason)
                end_ply = route.decision_occurrences[index - 1].ply + 1
                break
        if end_ply is not None:
            shown_keys = tuple(
                dict.fromkeys(
                    d.definition.key
                    for d in route.decision_occurrences
                    if d.ply < end_ply
                )
            )
            witnesses = tuple(by_key[key] for key in shown_keys)
        attempt = str(
            uuid5(
                NAMESPACE_URL,
                "tempo:position-study:%s:%s:%s"
                % (
                    payload["entry_id"],
                    payload["card_id"],
                    payload["expected_revision"],
                ),
            )
        )
        request = SelectionRequest(
            target,
            source.routes,
            tuple(witnesses),
            tuple(proofs),
            payload["card_id"],
            payload["expected_revision"],
            payload["entry_id"],
            attempt,
            end_ply=end_ply,
        )
        with training_timing_stage("position_plan_proposal"):
            proposal = prepare_position_issuance(source, request)
        return (
            held(proposal.reason)
            if isinstance(proposal, Hold)
            else PreparedQueueHead(payload, "position", proposal)
        )
