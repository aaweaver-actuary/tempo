"""Study storage over the existing caller-owned PostgreSQL transaction.

Only this adapter writes study_game_feedback. No connection, commit, worker,
provider access or scheduling policy. Card/day locks precede effect locks.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import psycopg

from app.contracts.product_v02.learning import GameEffectControl, GameEffectKey, GameLapse
from app.study.game_feedback import CardFacts, Completion, Effect, GameFeedback, effect_id, instant
from tempo_legacy_database.adapters.scheduling_review_store import OwnedConnectionSchedulingReviewStore
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position

PRIORITY_REASON = "product_game_lapse"


class PostgresGameFeedbackStore:
    def __init__(self, database, *, promote=None):
        self.database = database
        self.scheduling = OwnedConnectionSchedulingReviewStore(database)
        self._promote = promote
        self.targeting = self

    def _writer(self):
        if self.database.read_only or self.database.raw.info.transaction_status.name != "INTRANS":
            raise RuntimeError("game feedback requires a borrowed writer transaction")

    def lock_target(self, card_id, day):
        self._writer()
        # Existing Study card-edit/row order before the queue date reservation.
        self.database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                     ("tempo:card-edit:" + card_id,))
        self.database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,))
        lock_queue_date_for_position(self.database, day)

    def lock_effect(self, key):
        self._writer()
        self.database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                     (effect_id(key),))

    def try_lock_target(self, card_id, day):
        # Queue seeding may already own the day. Never wait for a card from
        # there: foreground review owns card-before-day and always takes priority.
        self._writer()
        if not self.database.execute_native(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:card-edit:" + card_id,)).fetchone()[0]:
            return False
        if not self.database.execute_native(
            "SELECT id FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED", (card_id,)).fetchone():
            return False
        return self.database.execute_native(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:daily-queue-position:" + day,)).fetchone()[0]

    @staticmethod
    def _effect(row):
        return Effect(
            key=GameEffectKey.model_validate_json(row["identity_json"]),
            source_revision=int(row["source_revision"]), received_at=row["received_at"],
            status=row["status"],
            lapse=GameLapse.model_validate_json(row["payload_json"]) if row["payload_json"] else None,
            payload_hash=row["payload_hash"], available_by=row["available_by"],
            card_id=row["card_id"], card_revision=row["card_revision"], reason=row["reason"],
            applied_at=row["applied_at"], review_id=row["review_id"],
            settled_at=row["settled_at"], settlement_review_id=row["settlement_review_id"],
            control=GameEffectControl.model_validate_json(row["hold_json"]) if row["hold_json"] else None,
            conflict_json=row["conflict_json"],
        )

    def get(self, key):
        row = self.database.execute_native("SELECT * FROM study_game_feedback WHERE effect_key=%s",
                                            (effect_id(key),)).fetchone()
        return self._effect(row) if row else None

    def save(self, effect, now):
        self._writer()
        previous = self.get(effect.key)
        lapse = effect.lapse
        self.database.execute_native("""INSERT INTO study_game_feedback(
            effect_key,identity_json,source_revision,payload_json,payload_hash,card_id,card_revision,
            decision_id,occurred_at,detected_at,received_at,available_by,status,reason,applied_at,
            review_id,settled_at,settlement_review_id,hold_revision,hold_json,conflict_json,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(effect_key) DO UPDATE SET
            status=EXCLUDED.status,reason=EXCLUDED.reason,applied_at=EXCLUDED.applied_at,
            review_id=EXCLUDED.review_id,settled_at=EXCLUDED.settled_at,
            settlement_review_id=EXCLUDED.settlement_review_id,hold_revision=EXCLUDED.hold_revision,
            hold_json=EXCLUDED.hold_json,conflict_json=EXCLUDED.conflict_json,updated_at=EXCLUDED.updated_at""",
            (effect_id(effect.key), effect.key.model_dump_json(), str(effect.source_revision),
             lapse.model_dump_json() if lapse else None, effect.payload_hash, effect.card_id,
             effect.card_revision, effect.key.decision_id,
             instant(lapse.occurred_at) if lapse and lapse.occurred_at else None,
             instant(lapse.detected_at) if lapse else None, effect.received_at, effect.available_by,
             effect.status, effect.reason, effect.applied_at, effect.review_id, effect.settled_at,
             effect.settlement_review_id, str(effect.control.source_revision) if effect.control else None,
             effect.control.model_dump_json() if effect.control else None, effect.conflict_json, now))
        if effect.applied_at is not None and (previous is None or previous.applied_at is None):
            from .recombination import record_admitted_failure
            record_admitted_failure(self.database, effect)

    def card_facts(self, card_id, day):
        row = self.database.execute_native("""SELECT c.revision,c.introduced_at,
            (c.archived=0 AND c.pending_validation=0 AND c.state IN ('learning','mature')
             AND NOT EXISTS(SELECT 1 FROM deleted_cards d WHERE d.card_id=c.id)
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks b WHERE b.card_id=c.id)) available,
            EXISTS(SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o
              ON o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.revision=c.revision
              WHERE q.card_id=c.id AND q.status='queued' AND o.issued_as_head=1) active_attempt,
            EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id
              AND q.queue_date=%s AND q.status='buried') buried,
            (SELECT MAX(reviewed_at::timestamptz) FROM reviews r
              WHERE r.card_id=c.id AND r.invalidated_at IS NULL) latest_review_at
            FROM cards c WHERE c.id=%s""", (day, card_id)).fetchone()
        introduced = row["introduced_at"] if row else None
        if introduced and len(introduced) == 10:
            # A legacy day is not an exact timestamp. Use the end of that day
            # as a conservative upper bound, never infer same-day introduction.
            introduction_time = datetime.fromisoformat(introduced).replace(tzinfo=timezone.utc) + timedelta(days=1)
        else:
            introduction_time = instant(introduced) if introduced else None
        return CardFacts(int(row["revision"]), introduction_time,
                         bool(row["available"]), bool(row["active_attempt"]), bool(row["buried"]),
                         row["latest_review_at"]) if row else None

    def latest_clean_review(self, card_id, revision, after):
        # Revision proof comes from the real owning receipt/origin, never position
        # equality or a producer-supplied historical success.
        row = self.database.execute_native("""SELECT r.id,r.reviewed_at,r.source_kind
            FROM reviews r JOIN review_attempt_receipts receipt ON receipt.review_id=r.id
            JOIN queue_attempt_origins origin ON origin.queue_entry_id=receipt.queue_entry_id
              AND origin.card_id=r.card_id AND origin.revision=%s
            WHERE r.card_id=%s AND r.rating='correct' AND r.guided=0 AND r.invalidated_at IS NULL
              AND r.source_kind='study' AND origin.attempt_failed=0
              AND r.reviewed_at::timestamptz>=%s ORDER BY r.reviewed_at::timestamptz DESC,r.id DESC LIMIT 1""",
            (revision, card_id, after)).fetchone()
        return Completion(row["id"], card_id, revision, instant(row["reviewed_at"]), True,
                          row["source_kind"]) if row else None

    def last_game_review_id(self, key):
        return self.database.execute_native("SELECT id FROM reviews WHERE source_kind='game_lapse' AND source_ref=%s",
                                            (effect_id(key),)).fetchone()[0]

    def promote(self, card_id, day):
        if self._promote:
            return self._promote(card_id, day)
        from app.services.review_service import ensure_card_queued_after
        ensure_card_queued_after(self.database, card_id, 0, priority_reason=PRIORITY_REASON,
                                 queue_date=day, protect_presented=True, preserve_order=False)

    def owed_page(self, after, limit):
        rows = self.database.execute_native("""SELECT * FROM study_game_feedback
            WHERE status IN ('pending','applied') AND effect_key>%s ORDER BY effect_key LIMIT %s""",
            (after, limit)).fetchall()
        return tuple(self._effect(row) for row in rows)

    def card_effects(self, card_id):
        from .recombination import installed as recombination_installed
        if recombination_installed(self.database):
            rows = self.database.execute_native("""SELECT effect.* FROM study_game_feedback effect
                WHERE effect.status IN ('pending','applied') AND (effect.card_id=%s OR EXISTS(
                    SELECT 1 FROM study_recombination_current current
                    JOIN study_recombination_presentations p ON p.presentation_id=current.presentation_id
                    WHERE current.presentation_id=%s AND current.decision_id=effect.decision_id
                      AND p.result_json->'presentation'->>'repertoire_id'=effect.payload_json::jsonb->'finding'->>'repertoire_id'))
                ORDER BY effect.effect_key""", (card_id,card_id)).fetchall()
            return tuple(self._effect(row) for row in rows)
        rows = self.database.execute_native("""SELECT * FROM study_game_feedback
            WHERE card_id=%s AND status IN ('pending','applied') ORDER BY effect_key""", (card_id,)).fetchall()
        return tuple(self._effect(row) for row in rows)

    def promote_effect(self, effect, day):
        from .recombination import effect_target, current_presentation_available
        target = effect_target(self.database,effect)
        if target!=effect.card_id:
            if not self.try_lock_target(target,day) or not current_presentation_available(self.database,target):
                return False
            facts = self.card_facts(target,day)
            if not facts or not facts.available or facts.active_attempt or facts.buried:
                return False
        self.promote(target,day)
        return True

    def completion_matches(self, effect, completion):
        from .recombination import effect_target, current_presentation_available
        target = effect_target(self.database,effect)
        if target!=completion.card_id:
            return False
        if target==effect.card_id:
            return effect.card_revision==completion.revision
        facts = self.card_facts(target,completion.reviewed_at.date().isoformat())
        return facts is not None and facts.revision==completion.revision and current_presentation_available(self.database,target)


def installed(database):
    """Old SQLite/recovery and pre-migration owners retain their current path."""
    return isinstance(getattr(database, "raw", None), psycopg.Connection) and database.execute_native(
        "SELECT to_regclass('study_game_feedback') IS NOT NULL").fetchone()[0]


def has_owed_feedback(database, card_id):
    return bool(PostgresGameFeedbackStore(database).card_effects(card_id))


def settle_saved_review(database, card_id, review_id, revision):
    if not installed(database):
        return
    row = database.execute_native("""SELECT rating,guided,reviewed_at,source_kind,invalidated_at
        FROM reviews WHERE id=%s AND card_id=%s""", (review_id, card_id)).fetchone()
    if not row:
        return
    completion = Completion(review_id, card_id, revision, instant(row["reviewed_at"]),
        row["rating"] == "correct" and not row["guided"] and row["invalidated_at"] is None,
        row["source_kind"])
    GameFeedback(PostgresGameFeedbackStore(database), None,
                 clock=lambda: datetime.now(timezone.utc)).settle(completion)


def invalidate_saved_review(database, card_id, review_id):
    """Existing invalidation owner has already written the actual review row."""
    if not installed(database):
        return
    row = database.execute_native("SELECT invalidated_at FROM reviews WHERE id=%s AND card_id=%s",
                                  (review_id, card_id)).fetchone()
    if not row or row[0] is None:
        raise ValueError("the review owner must invalidate the actual settling review first")
    database.execute_native("""UPDATE study_game_feedback SET
        status=CASE WHEN applied_at IS NULL THEN 'pending' ELSE 'applied' END,
        settled_at=NULL,settlement_review_id=NULL,reason='settling_review_invalidated',updated_at=%s
        WHERE settlement_review_id=%s AND status='settled'""",
        (datetime.now(timezone.utc), review_id))


def resume_available_feedback(database, day, *, limit=16):
    """Ordinary Study requeues applied owed effects without any producer/index.

    Pending scheduling needs GameFeedback.maintain with current decision facts;
    composition owns that injection. Queue projection never sets learning dates.
    """
    if not installed(database):
        return
    store = PostgresGameFeedbackStore(database)
    rows = database.execute_native("""SELECT * FROM study_game_feedback
        WHERE status='applied' AND card_id NOT IN
          (SELECT card_id FROM daily_queue WHERE queue_date=%s AND status='queued'
           AND gameplay_priority_reason='product_game_lapse')
        ORDER BY available_by,effect_key LIMIT %s""", (day, limit)).fetchall()
    for row in rows:
        observed = store._effect(row)
        if not store.try_lock_target(observed.card_id, day):
            continue
        store.lock_effect(observed.key)
        effect = store.get(observed.key)
        facts = store.card_facts(observed.card_id, day)
        if effect.status != "applied" or not facts:
            continue
        if not facts.available or facts.revision != effect.card_revision:
            store.save(replace(effect, status="held", reason="current_card_unavailable"),
                       datetime.now(timezone.utc))
        elif not facts.active_attempt and not facts.buried:
            store.promote_effect(effect, day)
