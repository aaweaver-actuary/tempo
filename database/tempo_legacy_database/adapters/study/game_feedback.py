"""Study storage over the existing caller-owned PostgreSQL transaction.

Only this adapter writes study_game_feedback. No connection, commit, worker,
provider access or scheduling policy. Card/day locks precede effect locks.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import psycopg

from app.contracts.product_v02.learning import (EncounterProgress, FirstDepartureWitness,
    GameEffectControl, GameEffectKey, GameLapse, RemediationFence)
from app.study.game_feedback import CardFacts, Completion, Effect, GameFeedback, effect_id, instant
from app.services.review_service import ensure_card_queued_after
from tempo_legacy_database.adapters.scheduling_review_store import OwnedConnectionSchedulingReviewStore
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position

PRIORITY_REASON = "product_game_lapse"
ENCOUNTER_PRIORITY_REASON = "product_game_encounter"


class PostgresGameFeedbackStore:
    def __init__(self, database, *, promote=None, prepared_priority=None):
        self.database = database
        self.scheduling = OwnedConnectionSchedulingReviewStore(database)
        self._promote = promote
        self.prepared_priority = prepared_priority
        self.targeting = self

    def _writer(self):
        if self.database.read_only or self.database.raw.info.transaction_status.name != "INTRANS":
            raise RuntimeError("game feedback requires a borrowed writer transaction")

    def lock_intake_target(self, card_id):
        self._writer()
        # Intake takes only the exact card lock, never a queue-date reservation.
        self.database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                                     ("tempo:card-edit:" + card_id,))
        self.database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,))

    def lock_target(self, card_id, day):
        self.lock_intake_target(card_id)
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
        payload = json.loads(row["payload_json"]) if row["payload_json"] else None
        encounter, owner = None, {}
        envelope = payload and payload.get("kind") in {"encounter", "lapse"}
        if envelope:
            owner = payload.get("owner", {})
        if envelope and payload["kind"] == "encounter":
            from app.contracts.product_v02.learning import GameEncounter
            encounter = GameEncounter.model_validate_json(json.dumps(payload["command"]))
        lapse = (GameLapse.model_validate_json(json.dumps(payload["command"]))
                 if envelope and payload["kind"] == "lapse" else
                 GameLapse.model_validate_json(row["payload_json"]) if payload and not envelope else None)
        return Effect(
            key=GameEffectKey.model_validate_json(row["identity_json"]),
            source_revision=int(row["source_revision"]), received_at=row["received_at"],
            status=row["status"],
            lapse=lapse,
            payload_hash=row["payload_hash"], available_by=row["available_by"],
            card_id=row["card_id"], card_revision=row["card_revision"], reason=row["reason"],
            applied_at=row["applied_at"], review_id=row["review_id"],
            settled_at=row["settled_at"], settlement_review_id=row["settlement_review_id"],
            control=GameEffectControl.model_validate_json(row["hold_json"]) if row["hold_json"] else None,
            conflict_json=row["conflict_json"],
            encounter=encounter, disposition=owner.get("disposition"),
            admission_status=owner.get("admission_status"), deferred_reason=owner.get("deferred_reason"),
            observation_recorded=owner.get("observation_recorded", False),
            qualification=FirstDepartureWitness.model_validate_json(json.dumps(owner["qualification"]))
                if owner.get("qualification") else None,
            progress=EncounterProgress.model_validate_json(json.dumps(owner["progress"]))
                if owner.get("progress") else None,
            remediation_fence=RemediationFence.model_validate_json(json.dumps(owner["remediation_fence"]))
                if owner.get("remediation_fence") else None,
        )

    def get(self, key):
        row = self.database.execute_native("SELECT * FROM study_game_feedback WHERE effect_key=%s",
                                            (effect_id(key),)).fetchone()
        return self._effect(row) if row else None

    def save(self, effect, now):
        self._writer()
        previous = self.get(effect.key)
        lapse, command = effect.lapse, effect.encounter or effect.lapse
        payload = None
        if command is not None:
            payload = json.dumps(dict(kind="encounter" if effect.encounter else "lapse",
                command=command.model_dump(mode="json"), original_command_json=command.model_dump_json(),
                owner=dict(disposition=effect.disposition, admission_status=effect.admission_status,
                    deferred_reason=effect.deferred_reason, observation_recorded=effect.observation_recorded,
                    qualification=effect.qualification.model_dump(mode="json") if effect.qualification else None,
                    progress=effect.progress.model_dump(mode="json") if effect.progress else None,
                    remediation_fence=effect.remediation_fence.model_dump(mode="json")
                        if effect.remediation_fence else None)))
        self.database.execute_native("""INSERT INTO study_game_feedback(
            effect_key,identity_json,source_revision,payload_json,payload_hash,card_id,card_revision,
            decision_id,occurred_at,detected_at,received_at,available_by,status,reason,applied_at,
            review_id,settled_at,settlement_review_id,hold_revision,hold_json,conflict_json,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(effect_key) DO UPDATE SET
            payload_json=CASE WHEN study_game_feedback.payload_json::jsonb->>'kind' IN ('encounter','lapse')
                THEN jsonb_set(study_game_feedback.payload_json::jsonb,'{owner}',
                     EXCLUDED.payload_json::jsonb->'owner')::text
                WHEN study_game_feedback.payload_json IS NOT NULL
                    AND EXCLUDED.payload_json::jsonb->>'kind'='lapse'
                THEN jsonb_build_object('kind','lapse','command',study_game_feedback.payload_json::jsonb,
                    'original_command_json',study_game_feedback.payload_json,
                    'owner',EXCLUDED.payload_json::jsonb->'owner')::text
                ELSE study_game_feedback.payload_json END,
            status=EXCLUDED.status,reason=EXCLUDED.reason,applied_at=EXCLUDED.applied_at,
            review_id=EXCLUDED.review_id,settled_at=EXCLUDED.settled_at,
            settlement_review_id=EXCLUDED.settlement_review_id,hold_revision=EXCLUDED.hold_revision,
            hold_json=EXCLUDED.hold_json,conflict_json=EXCLUDED.conflict_json,updated_at=EXCLUDED.updated_at""",
            (effect_id(effect.key), effect.key.model_dump_json(), str(effect.source_revision),
             payload, effect.payload_hash, effect.card_id,
             effect.card_revision, effect.key.decision_id,
             instant(command.occurred_at) if command and command.occurred_at else None,
             instant(command.detected_at) if command else None, effect.received_at, effect.available_by,
             effect.status, effect.reason, effect.applied_at, effect.review_id, effect.settled_at,
             effect.settlement_review_id, str(effect.control.source_revision) if effect.control else None,
             effect.control.model_dump_json() if effect.control else None, effect.conflict_json, now))

        if effect.review_id is not None and (previous is None or previous.review_id is None):
            from .recombination import record_admitted_failure
            record_admitted_failure(self.database, effect)

    def study_day(self, now):
        from tempo_learning_review import review_day_at
        row = self.database.execute_native("SELECT timezone FROM settings WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("current_study_policy_unavailable")
        return review_day_at(now, row[0])

    def issuance_frontier(self):
        from .issuance import issuance_frontier
        return issuance_frontier(self.database, lock=True)

    def observed_progress(self, effect):
        progress = effect.progress
        if progress is None or progress.remediation_fence is None or progress.first_issued is not None:
            return progress
        from .issuance import first_target_issuance
        issued = first_target_issuance(self.database, progress.target.card_id,
            progress.target.card_revision, progress.determined_at,
            after_issuance_sequence=progress.remediation_fence.after_issuance_sequence)
        return progress.model_copy(update=dict(first_issued=issued[0], first_issued_ordinal=issued[1])) if issued else progress

    def card_facts(self, card_id, day):
        return self._card_facts(card_id, day, states=['learning','mature'])

    def encounter_card_facts(self, card_id, day):
        return self._card_facts(card_id, day, states=['new','locked','learning','mature'])

    def _card_facts(self, card_id, day, *, states):
        row = self.database.execute_native("""SELECT c.revision,c.introduced_at,
            (c.archived=0 AND c.pending_validation=0 AND c.state=ANY(%s)
             AND NOT EXISTS(SELECT 1 FROM deleted_cards d WHERE d.card_id=c.id)
             AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks b WHERE b.card_id=c.id)) available,
            EXISTS(SELECT 1 FROM daily_queue q JOIN queue_attempt_origins o
              ON o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.revision=c.revision
              WHERE q.card_id=c.id AND q.status='queued' AND o.issued_as_head=1) active_attempt,
            EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id
              AND q.queue_date=%s AND q.status='buried') buried,
            (SELECT MAX(reviewed_at::timestamptz) FROM reviews r
              WHERE r.card_id=c.id AND r.invalidated_at IS NULL) latest_review_at
            FROM cards c WHERE c.id=%s""", (states, day, card_id)).fetchone()
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
        ensure_card_queued_after(self.database, card_id, 0, priority_reason=PRIORITY_REASON,
                                 queue_date=day, protect_presented=True, preserve_order=False)

    def promote_encounter(self, card_id, day, *, priority_order=None):
        ensure_card_queued_after(self.database, card_id, 0, attempt_state="clean", priority_reason=ENCOUNTER_PRIORITY_REASON,
                                 queue_date=day, protect_presented=True, preserve_order=False,
                                 prepared_priority=self.prepared_priority, priority_order=priority_order)

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
                      AND p.result_json->'presentation'->>'repertoire_id'=COALESCE(effect.payload_json::jsonb->'finding'->>'repertoire_id',
                          effect.payload_json::jsonb->'command'->'finding'->>'repertoire_id',
                          effect.payload_json::jsonb->'command'->'decision'->>'repertoire_id')))
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
        if effect.encounter is None:
            if effect.progress is None:
                self.promote(target,day)
            else:
                self.promote_encounter(target,day,priority_order=dict(
                    urgent_determined_at=effect.progress.determined_at,
                    urgent_stream=effect.progress.stream, urgent_effect_id=effect_id(effect.key)))
        else:
            self.promote_encounter(target,day,priority_order=(dict(
                urgent_determined_at=effect.progress.determined_at,
                urgent_stream=effect.progress.stream, urgent_effect_id=effect_id(effect.key))
                if effect.progress else None))
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


def owed_feedback_card_ids(database, card_ids, *, read=None, include_regrouped=False):
    """One borrowed or bounded read for the requested outstanding obligations."""
    ids = tuple(dict.fromkeys(card_ids))
    if not ids:
        return set()
    if include_regrouped:
        placeholder = '?' if read is not None else '%s'
        requested = ','.join('(' + placeholder + ')' for _ in ids)
        statement = """WITH requested(card_id) AS (VALUES """ + requested + """)
            SELECT DISTINCT requested.card_id FROM requested
            WHERE EXISTS(SELECT 1 FROM study_game_feedback effect
                WHERE effect.status IN ('pending','applied') AND (effect.card_id=requested.card_id OR EXISTS(
                    SELECT 1 FROM study_recombination_current current
                    JOIN study_recombination_presentations p ON p.presentation_id=current.presentation_id
                    WHERE current.presentation_id=requested.card_id AND current.decision_id=effect.decision_id
                      AND json_extract_path_text(json_extract_path(CAST(p.result_json AS json),'presentation'),'repertoire_id')=COALESCE(
                        json_extract_path_text(json_extract_path(CAST(effect.payload_json AS json),'finding'),'repertoire_id'),
                        json_extract_path_text(json_extract_path(json_extract_path(CAST(effect.payload_json AS json),'command'),'finding'),'repertoire_id'),
                        json_extract_path_text(json_extract_path(json_extract_path(CAST(effect.payload_json AS json),'command'),'decision'),'repertoire_id')))))"""
        rows = read(statement, ids) if read is not None else database.execute_native(statement, ids).fetchall()
        return {row[0] for row in rows}
    if read is not None:
        placeholders = ','.join('?' for _ in ids)
        rows = read("SELECT DISTINCT card_id FROM study_game_feedback "
            f"WHERE card_id IN ({placeholders}) AND status IN ('pending','applied')", ids)
    else:
        if database is None:
            raise RuntimeError('owed feedback requires a borrowed connection or bounded reader')
        rows = database.execute_native("SELECT DISTINCT card_id FROM study_game_feedback "
            "WHERE card_id=ANY(%s::text[]) AND status IN ('pending','applied')", (list(ids),)).fetchall()
    return {row[0] for row in rows}


def has_owed_feedback(database, card_id):
    return card_id in owed_feedback_card_ids(database, (card_id,))


def feedback_queue_priorities(database, card_ids):
    """Study-owned ready urgency; acceptance or old priority is insufficient."""
    ids = tuple(dict.fromkeys(card_ids))
    if not ids or not installed(database):
        return {}
    rows = database.execute_native("""SELECT * FROM study_game_feedback
        WHERE status='applied' AND payload_json::jsonb->'owner'->'progress'->'target'->>'card_id'=ANY(%s)
          AND payload_json::jsonb->'owner'->'progress'->'remediation_fence' IS NOT NULL
          AND payload_json::jsonb->'owner'->>'admission_status'='available'""", (list(ids),)).fetchall()
    result = {}
    for row in rows:
        effect = PostgresGameFeedbackStore._effect(row)
        progress = effect.progress
        if progress is None or progress.remediation_fence is None or effect.control:
            continue
        candidate = dict(urgent_determined_at=progress.determined_at,
            urgent_stream=progress.stream, urgent_effect_id=effect_id(effect.key))
        card_id = progress.target.card_id
        def order(item):
            return (item['urgent_stream'] != 'forward', instant(item['urgent_determined_at']), item['urgent_effect_id'])
        if card_id not in result or order(candidate) < order(result[card_id]):
            result[card_id] = candidate
    return result


def record_feedback_issuance(database, entry_id, card_id, revision):
    """Denormalize an actual issuance, never authorize or invent one.

    Nonblocking effect locks avoid reversing the application/global-issuance
    lock order. A busy owner receipt can recover the same endpoint from the
    durable origins during status/settlement without delaying ordinary Study.
    """
    if not installed(database):
        return
    from .issuance import attempt_issuance
    issued = attempt_issuance(database, entry_id, card_id, revision)
    if issued is None:
        return
    rows = database.execute_native("""SELECT identity_json FROM study_game_feedback
        WHERE status='applied'
          AND payload_json::jsonb->'owner'->'progress'->'target'->>'card_id'=%s
        ORDER BY effect_key""", (card_id,)).fetchall()
    store = PostgresGameFeedbackStore(database)
    for row in rows:
        key = GameEffectKey.model_validate_json(row['identity_json'])
        if not database.execute_native("SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                                       (effect_id(key),)).fetchone()[0]:
            continue
        effect = store.get(key)
        if effect is None or effect.status != 'applied' or effect.control or effect.progress is None:
            continue
        progress = store.observed_progress(effect)
        if progress != effect.progress:
            store.save(replace(effect, progress=progress), instant(issued.issued_at))


def next_feedback_preparation(database, day):
    """One qualified local obligation that needs preparation or queue recovery."""
    if not installed(database):
        return None
    row = database.execute_native("""SELECT effect.* FROM study_game_feedback effect
        JOIN cards card ON card.id=COALESCE(
            effect.payload_json::jsonb->'owner'->'progress'->'target'->>'card_id',effect.card_id)
        WHERE effect.status IN ('pending','applied') AND effect.hold_json IS NULL
          AND effect.payload_json::jsonb->'owner'->'qualification' IS NOT NULL
          AND effect.payload_json::jsonb->'owner'->'qualification'<>'null'::jsonb
          AND effect.reason IS DISTINCT FROM 'source_hold'
          AND COALESCE(effect.payload_json::jsonb->'owner'->>'deferred_reason','') NOT IN
              ('paused','disabled','authority_unavailable')
          AND card.archived=0 AND card.pending_validation=0 AND card.superseded_by IS NULL
          AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
          AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id)
          AND NOT EXISTS(SELECT 1 FROM daily_queue queue JOIN queue_attempt_origins origin
              ON origin.queue_entry_id=queue.id AND origin.card_id=queue.card_id
              WHERE queue.card_id=card.id AND queue.status='queued' AND origin.issued_as_head=1)
          AND NOT EXISTS(SELECT 1 FROM daily_queue buried WHERE buried.card_id=card.id
              AND buried.queue_date=%s AND buried.status='buried')
          AND (effect.status='pending' OR NOT EXISTS(SELECT 1 FROM daily_queue queued
              WHERE queued.card_id=card.id AND queued.queue_date=%s AND queued.status='queued'))
        ORDER BY CASE effect.payload_json::jsonb->'owner'->'progress'->>'stream' WHEN 'forward' THEN 0 ELSE 1 END,
          effect.payload_json::jsonb->'owner'->'progress'->>'determined_at',effect.effect_key LIMIT 1""", (day, day)).fetchone()
    return PostgresGameFeedbackStore._effect(row) if row else None


def pending_feedback_preparation(database, day):
    return next_feedback_preparation(database, day) is not None


def retained_encounter_introduction_query():
    """Complete Study-owned read projection for the legacy admission adapter.

    An actual same-definition introduction must survive ordinary budget rebuilds.
    A later evidence hold does not refund or reverse that historical admission.
    Pending/unapplied observations and changed physical revisions grant nothing.
    Callers check installed() before using this PostgreSQL-only projection.
    """
    return """SELECT DISTINCT effect.card_id FROM study_game_feedback effect
        JOIN cards current_card ON current_card.id=effect.card_id
          AND current_card.revision=effect.card_revision
        WHERE effect.applied_at IS NOT NULL AND current_card.introduced_at IS NOT NULL
          AND effect.payload_json::jsonb->>'kind'='encounter'"""


def retains_encounter_introduction(database, card_id):
    """Read an existing material admission fact; never introduce or restore it."""
    if not installed(database):
        return False
    return bool(database.execute_native('SELECT 1 FROM (' + retained_encounter_introduction_query() +
        ') retained WHERE retained.card_id=%s LIMIT 1', (card_id,)).fetchone())


def settle_saved_review(database, card_id, review_id, revision):
    if not installed(database):
        return
    row = database.execute_native("""SELECT rating,guided,reviewed_at,source_kind,invalidated_at
        FROM reviews WHERE id=%s AND card_id=%s""", (review_id, card_id)).fetchone()
    if not row:
        return
    from .issuance import completion_issuance
    completion = Completion(review_id, card_id, revision, instant(row["reviewed_at"]),
        row["rating"] == "correct" and not row["guided"] and row["invalidated_at"] is None,
        row["source_kind"], completion_issuance(database, review_id, card_id, revision))
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
           AND gameplay_priority_reason IN ('product_game_lapse','product_game_encounter'))
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
