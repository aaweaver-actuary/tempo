"""Bounded preparation facts read through the caller's borrowed connection."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class AdmissionOrigin:
    repertoire_id: str | None
    issued_as_head: bool
    content_type: str


@dataclass(frozen=True)
class PositionOwner:
    mode: str
    epoch: str
    source_digest: str


def original_attempt_ids(db, *, entry_id, card_id, revision) -> tuple[str, ...]:
    rows = db.execute_native(
        """SELECT p.attempt_id FROM study_position_issued_plans p
        WHERE p.queue_entry_id=%s AND p.card_id=%s AND p.card_revision=%s LIMIT 2""",
        (entry_id, card_id, revision),
    ).fetchall()
    return tuple(row["attempt_id"] for row in rows)


def admission_origin(db, *, entry_id, card_id, revision) -> AdmissionOrigin | None:
    row = db.execute_native(
        """SELECT o.admission_repertoire_id,o.issued_as_head,c.content_type
        FROM queue_attempt_origins o JOIN cards c ON c.id=o.card_id
        WHERE o.queue_entry_id=%s AND o.card_id=%s AND o.revision=%s""",
        (entry_id, card_id, revision),
    ).fetchone()
    if not row:
        return None
    return AdmissionOrigin(
        row["admission_repertoire_id"], row["issued_as_head"], row["content_type"]
    )


def applicable_owner_exists(db, *, card_id) -> bool:
    rows = db.execute_native(
        """SELECT DISTINCT o.repertoire_id,o.mode FROM study_position_owners o
        JOIN cards c ON c.id=%s WHERE (c.repertoire_id=o.repertoire_id OR EXISTS
          (SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=c.id AND rc.repertoire_id=o.repertoire_id))
        AND o.mode IN ('active','staged','paused') LIMIT 2""",
        (card_id,),
    ).fetchall()
    return bool(rows)


def position_owner(db, *, repertoire_id) -> PositionOwner | None:
    row = db.execute_native(
        """SELECT o.mode,o.epoch,e.source_digest FROM study_position_owners o
        JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s""",
        (repertoire_id,),
    ).fetchone()
    if not row:
        return None
    return PositionOwner(row["mode"], row["epoch"], row["source_digest"])


def prior_attempt_ids(db, *, repertoire_id, owner_epoch, study_day: date) -> tuple[str, ...]:
    rows = db.execute_native(
        """SELECT attempt_id FROM study_position_issued_plans
        WHERE repertoire_id=%s AND owner_epoch=%s AND issue_study_day=%s
        ORDER BY issued_at DESC,attempt_id LIMIT 20""",
        (repertoire_id, owner_epoch, study_day),
    ).fetchall()
    return tuple(row["attempt_id"] for row in rows)
