"""Games-owned PostgreSQL store. Never opens a provider/Study connection.

Composition supplies a dedicated, bounded connection context factory. Tables must
already exist; SCHEMA_SQL is a source/fixture contract for T10's reserved additive
migration, never an automatic runtime migration or live-schema initializer.
"""
from __future__ import annotations

from contextlib import contextmanager

from app.games.feedback.ports import ConcurrentUpdate, Delivery, JobSpec, JobState, PageCommit, SourceState

SCHEMA_SQL = """
CREATE TABLE product_feedback_jobs (
    job_id TEXT PRIMARY KEY, version BIGINT NOT NULL, status TEXT NOT NULL,
    next_attempt_at DOUBLE PRECISION NOT NULL, lease_until DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE product_feedback_pages (
    job_id TEXT NOT NULL REFERENCES product_feedback_jobs(job_id),
    page_key TEXT NOT NULL, input_continuation TEXT, page_json JSONB NOT NULL,
    PRIMARY KEY (job_id, page_key)
);
CREATE TABLE product_feedback_sources (
    source_key TEXT PRIMARY KEY, version BIGINT NOT NULL, payload JSONB NOT NULL
);
CREATE TABLE product_feedback_captures (
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    source_revision BIGINT NOT NULL, source_digest TEXT NOT NULL,
    comparison_revision TEXT NOT NULL, payload JSONB NOT NULL,
    PRIMARY KEY (source_key, source_revision, source_digest, comparison_revision)
);
CREATE TABLE product_feedback_deliveries (
    operation_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    effect_key TEXT NOT NULL, command_kind TEXT NOT NULL, state TEXT NOT NULL,
    next_attempt_at DOUBLE PRECISION NOT NULL, payload JSONB NOT NULL
);
CREATE INDEX product_feedback_ready_jobs ON product_feedback_jobs(status, next_attempt_at, attempts);
CREATE INDEX product_feedback_ready_deliveries ON product_feedback_deliveries(state, next_attempt_at);
"""


class PostgresGamesStore:
    def __init__(self, connections):
        self._connections = connections

    @contextmanager
    def _transaction(self):
        with self._connections() as database:
            with database.transaction():
                database.execute("SET LOCAL lock_timeout = '100ms'")
                database.execute("SET LOCAL statement_timeout = '250ms'")
                yield database

    def enqueue(self, spec: JobSpec) -> JobState:
        job = JobState(spec=spec)
        with self._transaction() as database:
            database.execute("""INSERT INTO product_feedback_jobs
                (job_id,version,status,next_attempt_at,lease_until,attempts,payload)
                VALUES(%s,0,'queued',0,0,0,%s::jsonb) ON CONFLICT(job_id) DO NOTHING""",
                (spec.job_id, job.model_dump_json()))
            row = database.execute("SELECT payload::text FROM product_feedback_jobs WHERE job_id=%s", (spec.job_id,)).fetchone()
            existing = JobState.model_validate_json(row[0])
            if existing.spec != spec:
                raise ValueError("job identity cannot be reused for a different request")
            return existing

    def load_job(self, job_id: str) -> JobState | None:
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_jobs WHERE job_id=%s", (job_id,)).fetchone()
            return JobState.model_validate_json(row[0]) if row else None

    def claim_job(self, now: float, lease_until: float) -> JobState | None:
        with self._transaction() as database:
            row = database.execute("""SELECT payload::text FROM product_feedback_jobs
                WHERE (status IN ('queued','retryable') AND next_attempt_at<=%s)
                   OR (status='running' AND lease_until<=%s)
                ORDER BY attempts,next_attempt_at,job_id LIMIT 1 FOR UPDATE SKIP LOCKED""", (now, now)).fetchone()
            if row is None:
                return None
            old = JobState.model_validate_json(row[0])
            claimed = old.model_copy(update={"status": "running", "lease_until": lease_until, "attempts": old.attempts + 1})
            return self._save_job(database, claimed)

    @staticmethod
    def _save_job(database, job: JobState) -> JobState:
        saved = job.model_copy(update={"version": job.version + 1})
        count = database.execute("""UPDATE product_feedback_jobs SET version=%s,status=%s,
            next_attempt_at=%s,lease_until=%s,attempts=%s,payload=%s::jsonb
            WHERE job_id=%s AND version=%s""", (saved.version, saved.status, saved.next_attempt_at,
            saved.lease_until, saved.attempts, saved.model_dump_json(), saved.spec.job_id, job.version)).rowcount
        if count != 1:
            raise ConcurrentUpdate("job checkpoint changed")
        return saved

    def save_job(self, job: JobState) -> None:
        with self._transaction() as database:
            self._save_job(database, job)

    def source(self, source_key: str) -> SourceState | None:
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_sources WHERE source_key=%s", (source_key,)).fetchone()
            return SourceState.model_validate_json(row[0]) if row else None

    def commit_page(self, commit: PageCommit) -> None:
        with self._transaction() as database:
            self._save_job(database, commit.job)
            if commit.provider_page_json is not None:
                import hashlib
                identity = (commit.input_continuation or "") + "\0" + commit.provider_page_json
                page_key = hashlib.sha256(identity.encode()).hexdigest()
                database.execute("""INSERT INTO product_feedback_pages(job_id,page_key,input_continuation,page_json)
                    VALUES(%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (commit.job.spec.job_id, page_key, commit.input_continuation, commit.provider_page_json))
            # Stable ordering keeps overlapping import writers from reversing lock order.
            for source in sorted(commit.sources, key=lambda item: item.source_key):
                saved = source.model_copy(update={"version": source.version + 1})
                if source.version == 0:
                    count = database.execute("""INSERT INTO product_feedback_sources(source_key,version,payload)
                        VALUES(%s,%s,%s::jsonb) ON CONFLICT(source_key) DO NOTHING""",
                        (source.source_key, saved.version, saved.model_dump_json())).rowcount
                else:
                    count = database.execute("""UPDATE product_feedback_sources SET version=%s,payload=%s::jsonb
                        WHERE source_key=%s AND version=%s""",
                        (saved.version, saved.model_dump_json(), source.source_key, source.version)).rowcount
                if count != 1:
                    raise ConcurrentUpdate("source changed before page checkpoint")
            for capture in commit.captures:
                database.execute("""INSERT INTO product_feedback_captures
                    (source_key,source_revision,source_digest,comparison_revision,payload)
                    VALUES(%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (capture.source_key, capture.record.source_revision, capture.record.source_digest,
                     capture.comparison_revision, capture.model_dump_json()))
            for delivery in commit.deliveries:
                command = delivery.command
                if delivery.control is not None:
                    database.execute("""UPDATE product_feedback_deliveries SET state='superseded',
                        payload=jsonb_set(payload,'{state}','"superseded"'::jsonb)
                        WHERE effect_key=%s AND command_kind IN ('lapse','encounter') AND state='pending'""", (command.key.logical_id(),))
                database.execute("""INSERT INTO product_feedback_deliveries
                    (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload)
                    VALUES(%s,%s,%s,%s,'pending',0,%s::jsonb) ON CONFLICT(operation_id) DO NOTHING""",
                    (delivery.operation_id, delivery.source_key, command.key.logical_id(),
                     delivery.command_kind, delivery.model_dump_json()))

    def delivery(self, operation_id: str) -> Delivery | None:
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s", (operation_id,)).fetchone()
            return Delivery.model_validate_json(row[0]) if row else None

    def pending_delivery(self, now: float) -> Delivery | None:
        with self._transaction() as database:
            row = database.execute("""SELECT payload::text FROM product_feedback_deliveries
                WHERE state='pending' AND next_attempt_at<=%s
                ORDER BY CASE command_kind WHEN 'control' THEN 0 ELSE 1 END,next_attempt_at,operation_id LIMIT 1""", (now,)).fetchone()
            return Delivery.model_validate_json(row[0]) if row else None

    def save_delivery(self, delivery: Delivery) -> None:
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s FOR UPDATE", (delivery.operation_id,)).fetchone()
            if row is None:
                raise ValueError("delivery was not durably captured")
            previous = Delivery.model_validate_json(row[0])
            # A delayed sender acknowledgment cannot reactivate a superseded command.
            if previous.state == "superseded":
                delivery = delivery.model_copy(update={"state": "superseded"})
            elif previous.state != "pending":
                return
            database.execute("""UPDATE product_feedback_deliveries SET state=%s,next_attempt_at=%s,payload=%s::jsonb
                WHERE operation_id=%s""", (delivery.state, delivery.next_attempt_at, delivery.model_dump_json(), delivery.operation_id))
