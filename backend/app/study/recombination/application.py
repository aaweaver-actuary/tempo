"""Bounded Study presentation regrouping. No SQL, connection, commit or producer."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import json
from typing import Callable
from uuid import uuid4

import chess

from app.contracts.product_v02.presentation import PresentationChangeReceipt
from .models import CleanStreak, JoinDecision, ReviewEvent, RouteJoinPlan, RouteUse
from .policy import plan_join, reduce_clean_streak
from .ports import CurrentRoute, NewPresentation, PresentationChange, RecombinationStore, StudyBoundary


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def conservative_due(now, next_local_study_day, deadlines):
    """D10: earliest existing deadline or next local midnight, including overdue."""
    for value in (now, next_local_study_day, *deadlines):
        if value.utcoffset() is None:
            raise ValueError("aware schedule instants required")
    if not timedelta(0) < next_local_study_day - now <= timedelta(hours=26):
        raise ValueError("next local study day must be within 26 hours")
    return min((next_local_study_day, *deadlines))


class Recombination:
    """Private application checkpoint; integration supplies the retained owner.

    A successful return is provisional until the caller commits. Exceptions must
    roll back owner scheduling/queue writes and this store together. A busy/missing
    authority is a hold, never permission to accept a stale source capture.
    """
    def __init__(self, store: RecombinationStore, study: StudyBoundary, *,
                 clock: Callable[[], datetime], next_local_study_day: Callable[[datetime], datetime],
                 new_id: Callable[[], str] = lambda: uuid4().hex):
        self.store, self.study, self.clock = store, study, clock
        self.next_day, self.new_id = next_local_study_day, new_id

    def _current(self, route_id):
        current = self.study.try_current(route_id)
        if current is None or current.context.route.route_id != route_id:
            return None
        if not self.store.try_lock_evidence(tuple(member.streak for member in current.members)):
            return None
        members = []
        for member in current.members:
            # Never adopt an aggregate interval, import history or caller count.
            streak = self.store.streak(replace(member.streak, count=0))
            members.append(replace(member, streak=streak, presentation=member.presentation.model_copy(
                update={"clean_streak": streak.count})))
        return replace(current, members=tuple(members))

    def record_review(self, route_id: str, event: ReviewEvent) -> CleanStreak:
        """Only an actual saved owner fact; stale generations stay on their origin."""
        if not self.store.try_lock_route(route_id):
            raise RuntimeError("route_busy")
        identity = CleanStreak(event.presentation_id, event.presentation_revision, event.evidence_epoch)
        if not self.store.try_lock_evidence((identity,)):
            raise RuntimeError("presentation_busy")
        prior = self.store.event(event.event_id)
        if prior is not None:
            if prior != event:
                raise ValueError("event_identity_conflict")
            return self.store.streak(CleanStreak(event.presentation_id, event.presentation_revision, event.evidence_epoch))
        if not self.study.saved_review(event):
            raise ValueError("saved_review_unproved")
        state = self.store.streak(identity)
        updated = reduce_clean_streak(state, event)
        self.store.save_event(event)
        self.store.save_streak(updated)
        return updated

    def _plan(self, current: CurrentRoute, plan_id: str, result_id: str) -> JoinDecision:
        context = replace(current.context, plan_id=plan_id, result_presentation_id=result_id)
        decision = plan_join(current.members, context)
        if decision.plan is None:
            return decision
        decisions = {item.decision_id for item in decision.plan.result.components}
        obligation_ids = tuple(sorted(o.logical_id for o in current.obligations if o.decision_id in decisions))
        return plan_join(current.members, replace(context, unresolved_obligation_ids=obligation_ids))

    def propose(self, route_id: str) -> JoinDecision | None:
        if not self.store.try_lock_route(route_id):
            return None
        current = self._current(route_id)
        return self._plan(current, "join:" + self.new_id(), "presentation:" + self.new_id()) if current else None

    @staticmethod
    def _receipt(plan_id, status, result=None, reason=None):
        return PresentationChangeReceipt(owner_receipt_id="recombination:" + plan_id,
            plan_id=plan_id, status=status, resulting_presentation_id=result, reason=reason)

    def apply_join(self, plan: RouteJoinPlan) -> PresentationChangeReceipt:
        route_id = plan.result.route_id
        if not self.store.try_lock_route(route_id):
            return self._receipt(plan.plan_id, "active_attempt", reason="route_busy")
        payload_digest = digest(plan.model_dump(mode="json"))
        prior = self.store.receipt(plan.plan_id)
        if prior:
            if prior[0] != payload_digest:
                raise ValueError("command_identity_conflict")
            return prior[1]
        current = self._current(route_id)
        if current is None:
            return self._receipt(plan.plan_id, "ineligible", reason="current_authority_unavailable")
        if current.context.expected_study_revision != plan.expected_study_revision:
            return self._receipt(plan.plan_id, "stale_revision", reason="study_revision_changed")
        checked = self._plan(current, plan.plan_id, plan.result.presentation_id)
        if checked.plan != plan:
            codes = {hold.code for hold in checked.holds}
            status = "active_attempt" if "active_attempt" in codes else "ineligible"
            if any("revision" in code for code in codes):
                status = "stale_revision"
            return self._receipt(plan.plan_id, status, reason=sorted(codes)[0] if codes else "plan_changed")
        if self.store.presentation_exists(plan.result.presentation_id) or not self.study.identity_available(plan.result.presentation_id):
            return self._receipt(plan.plan_id, "ineligible", reason="result_identity_reused")
        source_ids = {plan.left.presentation_id, plan.right.presentation_id}
        decisions = {item.decision_id for item in plan.result.components}
        obligations = tuple(o for o in current.obligations if o.decision_id in decisions)
        deadlines = tuple(due for identifier, due in current.due if identifier in source_ids)
        if len({identifier for identifier, _ in current.due if identifier in source_ids}) != 2:
            return self._receipt(plan.plan_id, "ineligible", reason="member_schedule_unavailable")
        now = self.clock()
        due = conservative_due(now, self.next_day(now), deadlines + tuple(o.available_by for o in obligations))
        evidence = plan.evidence
        left, right = evidence.components
        result = NewPresentation(plan.result, "joined:" + self.new_id(), evidence.starting_fen,
            evidence.ending_fen, evidence.moves,
            RouteUse(route_id, "joined-use:" + self.new_id(), left.route_use.start_ply, right.route_use.stop_ply), due)
        change = PresentationChange(plan.plan_id, "join", route_id, evidence.source_revision,
            tuple((ref.presentation_id, ref.route_use) for ref in evidence.components), (result,), obligations)
        receipt = self._receipt(plan.plan_id, "applied", plan.result.presentation_id)
        self._apply(change, payload_digest, receipt)
        return receipt

    def _apply(self, change, payload_digest, receipt):
        self.study.apply(change)
        for result in change.results:
            self.store.save_streak(CleanStreak(result.presentation.presentation_id,
                result.presentation.revision, result.evidence_epoch))
        self.store.save_change(change, payload_digest, receipt)

    def accept_split(self, route_id: str, presentation_id: str, expected_revision: int,
                     command_id: str, *, confirmed: bool) -> PresentationChangeReceipt:
        """Manual acceptance only; final learner decision, fresh fragment identities."""
        if not self.store.try_lock_route(route_id):
            return self._receipt(command_id, "active_attempt", reason="route_busy")
        payload_digest = digest(("split", route_id, presentation_id, expected_revision, confirmed))
        prior = self.store.receipt(command_id)
        if prior:
            if prior[0] != payload_digest:
                raise ValueError("command_identity_conflict")
            return prior[1]
        current = self._current(route_id)
        matches = tuple(m for m in current.members if m.presentation.presentation_id == presentation_id) if current else ()
        if len(matches) != 1 or not confirmed:
            return self._receipt(command_id, "ineligible", reason="split_confirmation_or_current_member_missing")
        member = matches[0]
        if member.current_revision != expected_revision or member.presentation.revision != expected_revision:
            return self._receipt(command_id, "stale_revision", reason="member_revision_changed")
        if not current.context.available or not current.context.complete or not member.available:
            return self._receipt(command_id, "ineligible", reason="current_authority_unavailable")
        if member.active_attempt or member.pending_attempt:
            return self._receipt(command_id, "active_attempt", reason="active_attempt")
        recent = self.store.recent_completed(member.streak)
        if sum(event.outcome == "again" for event in recent) < 3:
            return self._receipt(command_id, "ineligible", reason="needs_three_again_among_last_five")
        uses = tuple(use for use in member.route_uses if use.route_id == route_id)
        if len(uses) != 1 or len(member.presentation.components) < 2:
            return self._receipt(command_id, "ineligible", reason="split_route_ambiguous")
        board = chess.Board(member.starting_fen)
        frames, own = [board.fen(en_passant="fen")], []
        for index, move in enumerate(member.moves):
            if board.turn == (member.presentation.learner_color == "white"):
                own.append(index)
            board.push_uci(move)
            frames.append(board.fen(en_passant="fen"))
        if len(own) != len(member.presentation.components) or own[-1] != len(member.moves) - 1:
            return self._receipt(command_id, "ineligible", reason="unsupported_split_sequence")
        cut = own[-2] + 1
        identifiers = ("presentation:" + self.new_id(), "presentation:" + self.new_id())
        if identifiers[0] == identifiers[1] or any(self.store.presentation_exists(i) or not self.study.identity_available(i) for i in identifiers):
            raise ValueError("result_identity_reused")
        obligations = tuple(o for o in current.obligations if o.decision_id in {
            c.decision_id for c in member.presentation.components})
        dues = tuple(due for identifier, due in current.due if identifier == presentation_id)
        if len(dues) != 1:
            return self._receipt(command_id, "ineligible", reason="member_schedule_unavailable")
        now = self.clock()
        due = conservative_due(now, self.next_day(now), dues + tuple(o.available_by for o in obligations))
        results = []
        for index, (start, stop, components) in enumerate(((0, cut, member.presentation.components[:-1]),
                (cut, len(member.moves), member.presentation.components[-1:]))):
            # Internal cues only; the opponent cue before the final decision is
            # retained in the continuation's playable moves and route context.
            cues = member.presentation.opponent_cues[:-1] if index == 0 else ()
            value = member.presentation.model_copy(update=dict(presentation_id=identifiers[index], revision=1,
                components=components, opponent_cues=cues, clean_streak=0,
                lineage=tuple(dict.fromkeys((*member.presentation.lineage, presentation_id))),
                recorded_parent_id=identifiers[0] if index else None))
            results.append(NewPresentation(value, "split:" + self.new_id(), frames[start], frames[stop],
                member.moves[start:stop], RouteUse(route_id, "split-use:" + self.new_id(),
                    uses[0].start_ply + start, uses[0].start_ply + stop), due))
        change = PresentationChange(command_id, "split", route_id, current.context.route.source_revision,
            ((presentation_id, uses[0]),), tuple(results), obligations)
        receipt = self._receipt(command_id, "applied", identifiers[0])
        self._apply(change, payload_digest, receipt)
        return receipt
