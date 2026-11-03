"""Bounded schema-57 state reads in an existing native PostgreSQL transaction.

The source_digest field is the B1 authored_token.authored_digest stored on the
epoch/state, not the full physical snapshot digest. Matching that binding never
proves live authored source is current. Study separately fences the Repertoire capability.
No connection, clock, grade, commit or physical-card fallback lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from tempo_evidence_math.position_learning import Hold, PositionDefinition, PositionKey

MAX_POSITIONS = 100


class NativePositionTransaction(Protocol):
    """Caller retains transaction, connection and commit ownership."""
    in_transaction: bool
    read_only: bool

    def execute_native(self, statement: str, parameters: tuple = ()): ...


@dataclass(frozen=True)
class PositionScheduleState:
    definition: PositionDefinition
    owner_epoch: str
    source_digest: str
    version: int
    fsrs_internal_card_id: int
    fsrs_json: str
    due_at: datetime
    introduced_at: datetime | None
    donor_card_id: str | None
    donor_card_revision: int | None
    donor_fingerprint: str | None
    donor_selection_policy: str | None

    @property
    def revision(self) -> int:
        return self.version


_STATE_COLUMNS = """s.repertoire_id,s.variant,s.position,s.expected_uci,s.policy_version,
 s.owner_epoch,s.source_digest,s.version,s.fsrs_internal_card_id,s.fsrs_json,
 s.due_at,s.introduced_at,s.donor_card_id,s.donor_card_revision,
 s.donor_fingerprint,s.donor_selection_policy,current_owner.epoch current_owner_epoch,
 current_owner.mode current_owner_mode,current_epoch.source_digest current_source_digest"""
_STATE_OWNER_JOIN = """ JOIN study_position_owners current_owner
 ON current_owner.repertoire_id=s.repertoire_id
 JOIN study_position_epochs current_epoch ON current_epoch.repertoire_id=current_owner.repertoire_id
 AND current_epoch.epoch=current_owner.epoch """
_OWNER_SQL = """SELECT owner.epoch,owner.mode,epoch.source_digest
 FROM study_position_owners owner JOIN study_position_epochs epoch
 ON epoch.repertoire_id=owner.repertoire_id AND epoch.epoch=owner.epoch
 WHERE owner.repertoire_id=%s"""


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(name + "_aware_required")


class PositionScheduleStateReader:
    def __init__(self, database: NativePositionTransaction, *, repertoire_id: str,
                 owner_epoch: str, source_digest: str):
        if not all(isinstance(v, str) and v for v in (repertoire_id, owner_epoch, source_digest)):
            raise ValueError("position_owner_binding_required")
        self.database = database
        self.repertoire_id = repertoire_id
        self.owner_epoch = owner_epoch
        self.source_digest = source_digest

    def fence_owner(self, *, lock: bool = False) -> Hold | None:
        """Check stored activation/epoch only; lock before states for writers."""
        if not self.database.in_transaction:
            return Hold("borrowed_native_transaction_required")
        if lock and self.database.read_only:
            return Hold("borrowed_owner_writer_transaction_required")
        row = self.database.execute_native(
            _OWNER_SQL + (" FOR SHARE OF owner,epoch SKIP LOCKED" if lock else ""),
            (self.repertoire_id,)).fetchone()
        if row is None:
            return Hold("position_owner_busy_or_missing" if lock else "position_owner_missing")
        if row["mode"] != "active":
            return Hold("position_owner_inactive", row["mode"])
        if row["epoch"] != self.owner_epoch:
            return Hold("position_owner_epoch_changed")
        if row["source_digest"] != self.source_digest:
            return Hold("position_owner_source_changed")
        return None

    def decode_state(self, row) -> PositionScheduleState | Hold:
        """Decode exact stored bytes; scheduling validation belongs to D1/D3."""
        # Recheck at the state query's own snapshot under READ COMMITTED.
        if row["current_owner_mode"] != "active":
            return Hold("position_owner_inactive", row["current_owner_mode"])
        if row["current_owner_epoch"] != self.owner_epoch:
            return Hold("position_owner_epoch_changed")
        if row["current_source_digest"] != self.source_digest:
            return Hold("position_owner_source_changed")
        if row["owner_epoch"] != self.owner_epoch:
            return Hold("position_state_epoch_changed")
        if row["source_digest"] != self.source_digest:
            return Hold("position_state_source_changed")
        try:
            key = PositionKey(row["repertoire_id"], row["position"], row["variant"])
            if key.repertoire_id != self.repertoire_id:
                return Hold("position_repertoire_mismatch")
            definition = PositionDefinition(key, row["expected_uci"], row["policy_version"])
            _aware(row["due_at"], "position_due_at")
            if row["introduced_at"] is not None:
                _aware(row["introduced_at"], "position_introduced_at")
            if (type(row["version"]) is not int or row["version"] < 1
                    or type(row["fsrs_internal_card_id"]) is not int
                    or row["fsrs_internal_card_id"] < 1
                    or not isinstance(row["fsrs_json"], str)):
                raise ValueError("invalid_position_state_fields")
            return PositionScheduleState(definition, row["owner_epoch"], row["source_digest"],
                row["version"], row["fsrs_internal_card_id"], row["fsrs_json"], row["due_at"],
                row["introduced_at"], row["donor_card_id"], row["donor_card_revision"],
                row["donor_fingerprint"], row["donor_selection_policy"])
        except (ValueError, TypeError, KeyError) as exc:
            return Hold("position_state_invalid", str(exc))

    def read_current(self, position: PositionKey) -> PositionScheduleState | Hold:
        result = self.read_many((position,))
        return result if isinstance(result, Hold) else result[0]

    def lock_current(self, position: PositionKey) -> PositionScheduleState | Hold:
        result = self.read_many((position,), lock=True)
        return result if isinstance(result, Hold) else result[0]

    def read_many(self, positions: tuple[PositionKey, ...], *, lock: bool = False
                  ) -> tuple[PositionScheduleState | Hold, ...] | Hold:
        """Two bounded queries, original order; aliases share one locked row.

        SKIP LOCKED returns an explicit hold rather than silently omitting a
        contended state. Writers abort effects for any held required key.
        """
        if len(positions) > MAX_POSITIONS:
            raise ValueError("position_batch_exceeds_100")
        if any(p.repertoire_id != self.repertoire_id for p in positions):
            return Hold("position_repertoire_mismatch")
        if not positions:
            return ()
        held = self.fence_owner(lock=lock)
        if held is not None:
            return held
        keys = sorted({(p.variant, p.position) for p in positions})
        rows = self.database.execute_native("SELECT " + _STATE_COLUMNS + """
 FROM study_position_states s """ + _STATE_OWNER_JOIN + """
 JOIN unnest(%s::text[],%s::text[]) requested(variant,position)
 ON requested.variant=s.variant AND requested.position=s.position
 WHERE s.repertoire_id=%s
 ORDER BY s.variant,s.position LIMIT 100""" + (" FOR UPDATE OF s SKIP LOCKED" if lock else ""),
            ([k[0] for k in keys], [k[1] for k in keys], self.repertoire_id)).fetchall()
        found = {(row["variant"], row["position"]): self.decode_state(row) for row in rows}
        missing = Hold("position_state_busy_or_missing" if lock else "position_state_missing")
        return tuple(found.get((p.variant, p.position), missing) for p in positions)
