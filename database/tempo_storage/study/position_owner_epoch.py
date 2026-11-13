"""Disabled-safe position owner reads and new-issuance fence.

All calls borrow a PostgreSQL transaction. F4's future owner writer must take
an exclusive transaction advisory lock on ``owner_lock_key(repertoire_id)``
*before* changing an owner or epoch, and preserve immutable epoch rows. This
module never changes ownership, seeds, or commits. An active row is not proof
of seed completion; F4 must establish that separately before activation.

Order: A1 source/graph fence, owner advisory lock, owner row, origin row, then
existing card/day/queue locks. All acquired locks here are nonblocking. Existing
issued attempts use their immutable receipt through I1, not this issuance fence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from tempo_evidence_math.position_learning import PositionDefinition, PreparedPositionSourceView
from tempo_storage.authored_repertoires.position_study_source import fence_position_source


@dataclass(frozen=True)
class OwnerDecision:
    disposition: Literal["legacy", "hold", "position"]
    mode: str | None
    epoch: str | None
    source_digest: str | None
    reason: str


@dataclass(frozen=True)
class PositionOwnerFacts:
    repertoire_id: str
    mode: str
    epoch: str
    source_digest: str


def next_active_owner(database, after_repertoire: str) -> PositionOwnerFacts | None:
    """Read the next active owner from the caller's borrowed connection."""
    row = database.execute_native(
        """SELECT o.repertoire_id,o.epoch,e.source_digest
            FROM study_position_owners o JOIN study_position_epochs e
              ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.mode='active' AND o.repertoire_id>%s ORDER BY o.repertoire_id LIMIT 1""",
        (after_repertoire,),
    ).fetchone()
    return None if row is None else PositionOwnerFacts(row["repertoire_id"], "active", row["epoch"], row["source_digest"])


def active_owner(database, repertoire_id: str) -> PositionOwnerFacts | None:
    """Read the exact active owner used by a continued due scan."""
    row = database.execute_native(
        """SELECT o.repertoire_id,o.epoch,e.source_digest
            FROM study_position_owners o JOIN study_position_epochs e
              ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
            WHERE o.mode='active' AND o.repertoire_id=%s""",
        (repertoire_id,),
    ).fetchone()
    return None if row is None else PositionOwnerFacts(row["repertoire_id"], "active", row["epoch"], row["source_digest"])


def current_owner(database, repertoire_id: str) -> PositionOwnerFacts | None:
    """Read routing facts without acquiring the later writer fence."""
    row = database.execute_native(
        """SELECT o.mode,o.epoch,e.source_digest
        FROM study_position_owners o JOIN study_position_epochs e
          ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s""",
        (repertoire_id,),
    ).fetchone()
    return None if row is None else PositionOwnerFacts(repertoire_id, row["mode"], row["epoch"], row["source_digest"])


def owner_lock_key(repertoire_id: str) -> str:
    """Shared with the future owner writer; no connection is opened here."""
    return "tempo:position-owner:" + repertoire_id


def _writer(database) -> bool:
    try:
        return not database.read_only and database.raw.info.transaction_status.name == "INTRANS"
    except AttributeError:
        return False


def _hold(reason: str, mode=None, epoch=None, digest=None) -> OwnerDecision:
    return OwnerDecision("hold", mode, epoch, digest, reason)


def owner_mode(database, repertoire_id: str, expected_authored_digest: str | None) -> OwnerDecision:
    """Read a stable owner in the caller's transaction, holding its lock to end.

    ``expected_authored_digest`` is the authored authority digest recorded in
    study_position_epochs.source_digest. The full snapshot digest is separate
    immutable provenance and can change with physical card bindings.
    Missing owner and inactive/shadow cohorts retain legacy scheduling. Staged
    and paused cohorts hold: seeded position history may already exist.
    """
    if not _writer(database):
        return _hold("borrowed_owner_writer_transaction_required")
    if not isinstance(repertoire_id, str) or not repertoire_id:
        return _hold("repertoire_required")
    locked = database.execute_native(
        "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
        (owner_lock_key(repertoire_id),),
    ).fetchone()
    if not locked or not locked[0]:
        return _hold("position_owner_busy")
    row = database.execute_native(
        """SELECT o.mode,o.epoch,e.source_digest FROM study_position_owners o
        JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s""", (repertoire_id,),
    ).fetchone()
    if row is None:
        return OwnerDecision("legacy", "inactive", None, None, "position_owner_absent")
    mode, epoch, digest = row["mode"], row["epoch"], row["source_digest"]
    if mode in ("inactive", "shadow"):
        return OwnerDecision("legacy", mode, epoch, digest, "position_owner_" + mode)
    if mode in ("staged", "paused"):
        return _hold("position_owner_" + mode, mode, epoch, digest)
    if mode != "active":
        return _hold("position_owner_invalid_mode", mode, epoch, digest)
    if not epoch or not digest or not expected_authored_digest:
        return _hold("position_owner_identity_missing", mode, epoch, digest)
    if expected_authored_digest != digest:
        return _hold("position_owner_source_changed", mode, epoch, digest)
    return OwnerDecision("position", mode, epoch, digest, "position_owner_active")


def fence_position_owner_in_writer(database, prepared_source: PreparedPositionSourceView, *,
                                   definition: PositionDefinition, epoch: str, queue_entry_id: int, card_id: str,
                                   revision: int) -> OwnerDecision:
    """Authorize *new* issuance only, using A1 and retained queue admission.

    Call before the existing card/day/queue locks. A successful result does not
    authorize settling a previous receipt or assert a completed seed.
    """
    if not _writer(database):
        return _hold("borrowed_owner_writer_transaction_required")
    source_hold = fence_position_source(database, prepared_source)
    if source_hold is not None:
        return _hold(source_hold.reason)
    token = prepared_source.authored_token
    if (not isinstance(definition, PositionDefinition)
            or definition.key.repertoire_id != token.repertoire_id
            or definition.key.variant != "standard"
            or not any(occurrence.definition == definition for route in prepared_source.routes
                       for occurrence in route.decisions)):
        return _hold("position_definition_unproven")
    decision = owner_mode(database, token.repertoire_id, token.authored_digest)
    if decision.disposition != "position":
        # This is an authorization fence, not the routing decision. Legacy
        # routing is available from owner_mode, never as a successful fence.
        return _hold(decision.reason, decision.mode, decision.epoch, decision.source_digest)
    if not epoch or epoch != decision.epoch:
        return _hold("position_owner_epoch_changed", decision.mode, decision.epoch, decision.source_digest)
    if (type(queue_entry_id) is not int or queue_entry_id <= 0 or not card_id
            or type(revision) is not int or revision < 0):
        return _hold("position_admission_identity_missing", decision.mode, decision.epoch, decision.source_digest)
    origin = database.execute_native(
        """SELECT admission_repertoire_id FROM queue_attempt_origins
        WHERE queue_entry_id=%s AND card_id=%s AND revision=%s
        FOR SHARE SKIP LOCKED""", (queue_entry_id, card_id, revision),
    ).fetchone()
    if origin is None or origin["admission_repertoire_id"] != token.repertoire_id:
        return _hold("position_admission_unproven", decision.mode, decision.epoch, decision.source_digest)
    return decision
