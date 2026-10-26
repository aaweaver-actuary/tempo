"""T22 private PostgreSQL storage; borrows a writer, never connects or commits.

SCHEMA is a task-fixture/source DDL proposal. Production migration numbering and
installation belong to integration; importing/constructing this adapter runs no
DDL. No cards, reviews, attempts, source repertoire or #487 effect row is changed.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json

from app.contracts.product_v02.presentation import PresentationChangeReceipt
from app.study.recombination.models import CleanStreak, ReviewEvent


SCHEMA = """
CREATE TABLE study_recombination_streaks (
    presentation_id text NOT NULL, revision bigint NOT NULL CHECK (revision > 0),
    evidence_epoch text NOT NULL, state_json jsonb NOT NULL,
    PRIMARY KEY (presentation_id, revision, evidence_epoch)
);
CREATE TABLE study_recombination_events (
    event_id text PRIMARY KEY, event_json jsonb NOT NULL
);
CREATE TABLE study_recombination_completions (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id text NOT NULL UNIQUE, review_id text NOT NULL UNIQUE,
    presentation_id text NOT NULL, revision bigint NOT NULL, evidence_epoch text NOT NULL,
    completed_at timestamptz NOT NULL, event_json jsonb NOT NULL
);
CREATE INDEX study_recombination_recent ON study_recombination_completions
    (presentation_id, revision, evidence_epoch, completed_at DESC, sequence DESC);
CREATE TABLE study_recombination_changes (
    command_id text PRIMARY KEY, digest text NOT NULL,
    change_json jsonb NOT NULL, receipt_json jsonb NOT NULL
);
CREATE TABLE study_recombination_presentations (
    presentation_id text PRIMARY KEY,
    command_id text NOT NULL REFERENCES study_recombination_changes(command_id),
    result_json jsonb NOT NULL
);
CREATE TABLE study_recombination_current (
    route_id text NOT NULL, decision_id text NOT NULL,
    presentation_id text NOT NULL REFERENCES study_recombination_presentations(presentation_id),
    PRIMARY KEY (route_id, decision_id)
);
"""


def _json(value):
    def encode(item):
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, (set, frozenset)):
            return sorted(item)
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError(type(item).__name__)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=encode)


def _event(value):
    data = dict(value)
    data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
    return ReviewEvent(**data)


class PostgresRecombinationStore:
    def __init__(self, database):
        self.database = database

    def _writer(self):
        if self.database.read_only or self.database.raw.info.transaction_status.name != "INTRANS":
            raise RuntimeError("recombination requires a borrowed writer transaction")

    def try_lock_route(self, route_id):
        self._writer()
        # Nonblocking: saved review/card/day locks must never wait on a route.
        return bool(self.database.execute_native(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
            ("tempo:recombination-route:" + route_id,)).fetchone()[0])

    def streak(self, identity):
        row = self.database.execute_native("""SELECT state_json FROM study_recombination_streaks
            WHERE presentation_id=%s AND revision=%s AND evidence_epoch=%s""",
            (identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)).fetchone()
        if not row:
            # No inference from the caller's aggregate count or imported history.
            return CleanStreak(identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)
        value = dict(row[0])
        if value["last_event_at"] is not None:
            value["last_event_at"] = datetime.fromisoformat(value["last_event_at"])
        return CleanStreak(**value)

    def try_lock_evidence(self, identities):
        self._writer()
        keys = sorted({(s.presentation_id, s.presentation_revision, s.evidence_epoch) for s in identities})
        for key in keys:
            if not self.database.execute_native(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))",
                ("tempo:recombination-evidence:" + _json(key),)).fetchone()[0]:
                return False
        return True

    def save_streak(self, state):
        self._writer()
        self.database.execute_native("""INSERT INTO study_recombination_streaks
            (presentation_id,revision,evidence_epoch,state_json) VALUES(%s,%s,%s,%s::jsonb)
            ON CONFLICT(presentation_id,revision,evidence_epoch)
            DO UPDATE SET state_json=EXCLUDED.state_json""",
            (state.presentation_id, state.presentation_revision, state.evidence_epoch, _json(asdict(state))))

    def event(self, event_id):
        row = self.database.execute_native("SELECT event_json FROM study_recombination_events WHERE event_id=%s",
                                            (event_id,)).fetchone()
        return _event(row[0]) if row else None

    def save_event(self, event):
        self._writer()
        payload = _json(asdict(event))
        self.database.execute_native("INSERT INTO study_recombination_events(event_id,event_json) VALUES(%s,%s::jsonb)",
                                     (event.event_id, payload))
        if event.complete and event.source == "study":
            self.database.execute_native("""INSERT INTO study_recombination_completions
                (attempt_id,review_id,presentation_id,revision,evidence_epoch,completed_at,event_json)
                VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (event.attempt_id, event.review_id, event.presentation_id, event.presentation_revision,
                 event.evidence_epoch, event.occurred_at, payload))

    def recent_completed(self, identity):
        rows = self.database.execute_native("""SELECT event_json FROM study_recombination_completions
            WHERE presentation_id=%s AND revision=%s AND evidence_epoch=%s
            ORDER BY completed_at DESC,sequence DESC LIMIT 5""",
            (identity.presentation_id, identity.presentation_revision, identity.evidence_epoch)).fetchall()
        return tuple(_event(row[0]) for row in rows)

    def receipt(self, command_id):
        row = self.database.execute_native("SELECT digest,receipt_json FROM study_recombination_changes WHERE command_id=%s",
                                            (command_id,)).fetchone()
        return (row[0], PresentationChangeReceipt.model_validate(row[1])) if row else None

    def presentation_exists(self, presentation_id):
        return bool(self.database.execute_native("SELECT 1 FROM study_recombination_presentations WHERE presentation_id=%s",
                                                 (presentation_id,)).fetchone())

    def save_change(self, change, digest, receipt):
        self._writer()
        self.database.execute_native("""INSERT INTO study_recombination_changes
            (command_id,digest,change_json,receipt_json) VALUES(%s,%s,%s::jsonb,%s::jsonb)""",
            (change.command_id, digest, _json(asdict(change)), receipt.model_dump_json()))
        for result in change.results:
            self.database.execute_native("""INSERT INTO study_recombination_presentations
                (presentation_id,command_id,result_json) VALUES(%s,%s,%s::jsonb)""",
                (result.presentation.presentation_id, change.command_id, _json(asdict(result))))
            for component in result.presentation.components:
                self.database.execute_native("""INSERT INTO study_recombination_current
                    (route_id,decision_id,presentation_id) VALUES(%s,%s,%s)
                    ON CONFLICT(route_id,decision_id) DO UPDATE SET presentation_id=EXCLUDED.presentation_id""",
                    (change.route_id, component.decision_id, result.presentation.presentation_id))

    def current_presentation(self, route_id, decision_id):
        """Read projection only; original #487 identity/deadline remains the owner row."""
        row = self.database.execute_native("""SELECT presentation_id FROM study_recombination_current
            WHERE route_id=%s AND decision_id=%s""", (route_id, decision_id)).fetchone()
        return row[0] if row else None
