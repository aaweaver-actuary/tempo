"""Indexed position-only due pages and all-position issuance witnesses.

These are stored facts, subject to the separate source-owner fence and final
Study issuance/grade transaction. A cursor fixes the owner's epoch, instant and
explicit Study day, not a snapshot across concurrent schedule changes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from tempo_evidence_math.position_learning import DueAtIssuance, Hold, PositionKey

from .position_schedule_state import (
    MAX_POSITIONS, NativePositionTransaction, PositionScheduleState,
    PositionScheduleStateReader, _STATE_COLUMNS, _STATE_OWNER_JOIN, _aware,
)


@dataclass(frozen=True)
class PositionDueCursor:
    repertoire_id: str
    owner_epoch: str
    source_digest: str
    issued_at: datetime
    study_day: date
    include_unintroduced: bool
    due_at: datetime
    variant: str
    position: str


@dataclass(frozen=True)
class PositionDueCandidate:
    state: PositionScheduleState
    witness: DueAtIssuance
    reason: Literal["due", "unintroduced"]


@dataclass(frozen=True)
class PositionDuePage:
    candidates: tuple[PositionDueCandidate, ...]
    next_cursor: PositionDueCursor | None


class PositionDueCandidateReader:
    def __init__(self, database: NativePositionTransaction, *, repertoire_id: str,
                 owner_epoch: str, source_digest: str, issued_at: datetime, study_day: date):
        _aware(issued_at, "issued_at")
        if not isinstance(study_day, date) or isinstance(study_day, datetime):
            raise ValueError("authoritative_study_day_required")
        self.states = PositionScheduleStateReader(database, repertoire_id=repertoire_id,
            owner_epoch=owner_epoch, source_digest=source_digest)
        self.issued_at, self.study_day = issued_at, study_day

    def _binding(self, include_unintroduced: bool) -> tuple:
        return (self.states.repertoire_id, self.states.owner_epoch, self.states.source_digest,
                self.issued_at, self.study_day, include_unintroduced)

    def _witness(self, state: PositionScheduleState) -> DueAtIssuance:
        return DueAtIssuance(state.definition,
            state.introduced_at is not None and state.due_at <= self.issued_at,
            self.study_day, state.owner_epoch, state.version, self.issued_at)

    def read_due_page(self, *, after: PositionDueCursor | None = None, limit: int = 100,
                      include_unintroduced: bool = False
                      ) -> PositionDuePage | Hold:
        """Unintroduced opt-in is only a hint for H2's normal intro admission.

        It grants no automatic due obligation or exception to introduction budget.
        A Good fulfills this target only while it is the current schedule effect.
        A newer Again may make the position due again; daily Good credit is a
        separate acceptance rule enforced by the grade owner.
        """
        if type(limit) is not int or not 1 <= limit <= MAX_POSITIONS:
            raise ValueError("position_page_limit_between_1_and_100_required")
        if type(include_unintroduced) is not bool:
            raise ValueError("include_unintroduced_boolean_required")
        if after is not None:
            if not isinstance(after, PositionDueCursor) or (
                    after.repertoire_id, after.owner_epoch, after.source_digest,
                    after.issued_at, after.study_day, after.include_unintroduced
                    ) != self._binding(include_unintroduced):
                return Hold("position_due_cursor_binding_changed")
            _aware(after.due_at, "cursor_due_at")
        held = self.states.fence_owner()
        if held is not None:
            return held
        params = [self.states.repertoire_id, self.states.owner_epoch, self.issued_at,
                  self.study_day]
        cursor_sql = "" if include_unintroduced else " AND s.introduced_at IS NOT NULL"
        if after is not None:
            cursor_sql += " AND (s.due_at,s.variant,s.position) > (%s,%s,%s)"
            params.extend((after.due_at, after.variant, after.position))
        params.append(limit + 1)
        rows = self.states.database.execute_native("SELECT " + _STATE_COLUMNS + """
 FROM study_position_states s """ + _STATE_OWNER_JOIN + """
 WHERE s.repertoire_id=%s AND s.owner_epoch=%s AND s.due_at<=%s
 AND NOT EXISTS (SELECT 1 FROM study_position_grades good
   WHERE good.repertoire_id=s.repertoire_id AND good.variant=s.variant
     AND good.position=s.position AND good.accepted_study_day=%s AND good.grade='good'
     AND good.owner_epoch=s.owner_epoch AND good.version_after=s.version)
 """ + cursor_sql + " ORDER BY s.due_at,s.variant,s.position LIMIT %s", tuple(params)).fetchall()
        candidates = []
        for row in rows:
            state = self.states.decode_state(row)
            if isinstance(state, Hold):
                return state
            candidates.append(PositionDueCandidate(state, self._witness(state),
                "unintroduced" if state.introduced_at is None else "due"))
        next_cursor = None
        if len(candidates) > limit:
            last = candidates[limit - 1].state
            next_cursor = PositionDueCursor(*self._binding(include_unintroduced), last.due_at,
                last.definition.key.variant, last.definition.key.position)
        return PositionDuePage(tuple(candidates[:limit]), next_cursor)

    def read_context(self, positions: tuple[PositionKey, ...]
                     ) -> tuple[DueAtIssuance | Hold, ...] | Hold:
        """Report current due facts for all shown decisions, including nondue.

        A due witness does not grant another Good on an already credited day.
        The grade owner separately rechecks the cross-epoch daily positive cap.
        """
        states = self.states.read_many(positions)
        if isinstance(states, Hold) or not positions:
            return states
        return tuple(state if isinstance(state, Hold) else self._witness(state)
            for state in states)
