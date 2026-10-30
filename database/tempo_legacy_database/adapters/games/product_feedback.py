"""Games-owned PostgreSQL store. Never opens a provider/Study connection.

Composition supplies a dedicated, bounded connection context factory. Tables must
already exist; SCHEMA_SQL is a source/fixture contract for T10's reserved additive
migration, never an automatic runtime migration or live-schema initializer.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime,timezone

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
    next_attempt_at DOUBLE PRECISION NOT NULL, payload JSONB NOT NULL,
    handoff_priority TEXT
);
CREATE INDEX product_feedback_ready_jobs ON product_feedback_jobs(status, next_attempt_at, attempts);
CREATE INDEX product_feedback_ready_deliveries ON product_feedback_deliveries(state, next_attempt_at);
CREATE INDEX product_feedback_effect_controls ON product_feedback_deliveries(effect_key)
    WHERE command_kind='control' AND state IN ('pending','acknowledged');
CREATE INDEX product_feedback_determined_pending ON product_feedback_deliveries
    ((COALESCE(payload->'encounter'->'key'->>'workspace_id',payload->'lapse'->'key'->>'workspace_id',
        payload->'control'->'key'->>'workspace_id')),handoff_priority COLLATE "C",next_attempt_at)
    WHERE state='pending' AND handoff_priority IS NOT NULL;
"""


def fence_qualified_feedback(database, command, qualification, *, expected_configuration,
                             expected_source, require_active_mode=False, mode_runtime_id=None):
    """Borrow Study's writer to fence Games-owned facts, without replay or I/O.

    Preparation supplied actual owner reads, not producer labels. Study separately
    fences every selected publication and the exact current physical target.
    """
    from tempo_game_evidence_contracts.legacy import fingerprint
    from app.games.feedback.review import SyncConfiguration, ReviewUnavailable
    from .authority import acceptance

    def require(condition, reason):
        if not condition:
            raise ReviewUnavailable(reason)

    key = command.key
    if require_active_mode:
        try:
            from .managed_mode import fence_feedback_handoff
        except ImportError as error:
            raise ReviewUnavailable("verified_feedback_mode_unavailable") from error
        fence_feedback_handoff(database, key.workspace_id, runtime_id=mode_runtime_id)
    require(qualification.matches(command), "qualification_original_command_mismatch")
    require((expected_configuration.workspace_id, expected_configuration.provider,
             expected_configuration.revision) ==
            (key.workspace_id, key.provider, qualification.configuration_revision),
            "qualified_configuration_scope_mismatch")
    lock = database.execute_native(
        "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
        ("tempo:product-sync-config:" + key.workspace_id + ":" + key.provider,)).fetchone()
    require(lock is not None and lock[0], "qualified_configuration_busy")
    row = database.execute_native("""SELECT payload::text FROM product_feedback_configuration
        WHERE workspace_id=%s AND provider=%s FOR SHARE SKIP LOCKED""",
        (key.workspace_id, key.provider)).fetchone()
    actual_configuration = SyncConfiguration.model_validate_json(row[0]) if row else None
    require(actual_configuration == expected_configuration, "qualified_configuration_changed")
    require(set(item.repertoire_id for item in qualification.selection.selected_scope)
            <= set(actual_configuration.repertoire_ids), "qualified_selection_scope_changed")

    row = database.execute_native("""SELECT payload::text FROM product_feedback_sources
        WHERE source_key=%s FOR SHARE SKIP LOCKED""", (expected_source.source_key,)).fetchone()
    actual_source = SourceState.model_validate_json(row[0]) if row else None
    require(actual_source is not None and not actual_source.quarantined
            and actual_source.normalized is not None, "qualified_source_unavailable")
    require(actual_source.normalized == expected_source.normalized
            and actual_source.record_fingerprint == expected_source.record_fingerprint,
            "qualified_source_changed")
    game = actual_source.normalized
    provider = "chesscom" if game.source_id == "chess.com" else game.source_id
    require((game.workspace_id, provider, game.game_id, game.source_revision, game.source_digest) ==
            (key.workspace_id, key.provider, key.game_id,
             qualification.selection.source.source_revision, qualification.selection.source.source_digest),
            "qualified_source_identity_changed")
    require(any(effect == command for effect in actual_source.effects),
            "qualified_original_effect_unproved")
    controls = database.execute_native("""SELECT 1 FROM product_feedback_deliveries
        WHERE effect_key=%s AND command_kind='control'
          AND state IN ('pending','acknowledged') LIMIT 1""", (key.logical_id(),)).fetchone()
    require(controls is None, "qualified_effect_controlled")
    raw = acceptance.imported_game(database, key.game_id)
    account = database.execute_native("SELECT username FROM imported_games WHERE id=%s FOR SHARE SKIP LOCKED",
        (key.game_id,)).fetchone()
    require(raw is not None and not raw["adaptive_excluded"]
            and fingerprint(dict(raw)) == game.source_digest
            and raw["provider_game_id"] == actual_source.original.source_game_id
            and account is not None and account[0].casefold() == actual_configuration.account.casefold(),
            "qualified_canonical_source_changed")


def _borrowed_writer(database):
    raw = getattr(database, "raw", database)
    if (getattr(database, "read_only", False)
            or raw.info.transaction_status.name != "INTRANS"):
        raise ValueError("borrowed_owner_writer_transaction_required")
    return raw


def lock_delivery_in_transaction(database, operation_id: str, *, expected=None, service_cursor=False):
    """Games-owned short row fence in the caller's existing transaction."""
    raw = _borrowed_writer(database)
    if service_cursor:
        locked = raw.execute("SELECT pg_try_advisory_xact_lock(hashtextextended('tempo:game-feedback-delivery',0))").fetchone()[0]
        if not locked:
            raise ConcurrentUpdate("delivery service cursor busy")
    row = raw.execute("SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s FOR UPDATE NOWAIT",
        (operation_id,)).fetchone()
    if row is None:
        raise ValueError("delivery was not durably captured")
    actual = Delivery.model_validate_json(row[0])
    if expected is not None and actual != expected:
        raise ConcurrentUpdate("prepared delivery changed")
    return actual


def save_delivery_in_transaction(database, delivery: Delivery, *, expected=None, record_service=False):
    """Persist one outbox result without starting or committing a transaction."""
    raw = _borrowed_writer(database)
    previous = lock_delivery_in_transaction(raw, delivery.operation_id, expected=expected,
        service_cursor=record_service)
    if previous.command != delivery.command or previous.source_key != delivery.source_key:
        raise ValueError("captured delivery command cannot be replaced")
    if delivery.attempts < previous.attempts:
        raise ConcurrentUpdate("delivery attempt advanced")
    if previous.determined_at is not None and delivery.determined_at != previous.determined_at:
        raise ValueError("actionable determination cannot be restarted")
    if previous.state == "superseded":
        delivery = delivery.model_copy(update={"state": "superseded"})
    elif previous.state != "pending":
        return previous
    delivery = delivery.model_copy(update={"service_sequence":max(delivery.service_sequence,previous.service_sequence)})
    if record_service and delivery.control is None:
        sequence = raw.execute("""SELECT COALESCE(MAX(
            COALESCE((payload->>'service_sequence')::bigint,0)),0)+1
            FROM product_feedback_deliveries""").fetchone()[0]
        delivery = delivery.model_copy(update={"service_sequence": sequence})
    raw.execute("""UPDATE product_feedback_deliveries SET state=%s,next_attempt_at=%s,payload=%s::jsonb,
        handoff_priority=%s WHERE operation_id=%s""", (delivery.state, delivery.next_attempt_at,
        delivery.model_dump_json(), handoff_priority_for(delivery),delivery.operation_id))
    return delivery


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
                delivery = delivery.model_copy(update={
                    "origin_job_id": delivery.origin_job_id or commit.job.spec.job_id,
                    "stream": delivery.stream or commit.job.spec.stream,
                    "configuration_revision": (delivery.configuration_revision
                        or commit.job.spec.configuration_revision),
                })
                command = delivery.command
                if delivery.control is not None:
                    database.execute("""UPDATE product_feedback_deliveries SET state='superseded',
                        payload=jsonb_set(payload,'{state}','"superseded"'::jsonb)
                        WHERE effect_key=%s AND command_kind IN ('lapse','encounter') AND state='pending'""", (command.key.logical_id(),))
                database.execute("""INSERT INTO product_feedback_deliveries
                    (operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload,handoff_priority)
                    VALUES(%s,%s,%s,%s,'pending',0,%s::jsonb,%s) ON CONFLICT(operation_id) DO NOTHING""",
                    (delivery.operation_id, delivery.source_key, command.key.logical_id(),
                     delivery.command_kind, delivery.model_dump_json(),handoff_priority_for(delivery)))

    def delivery(self, operation_id: str) -> Delivery | None:
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_deliveries WHERE operation_id=%s", (operation_id,)).fetchone()
            return Delivery.model_validate_json(row[0]) if row else None

    def pending_delivery(self, now: float, *, ready_only: bool = False,
                         workspace: str | None = None, priority_order: bool = False) -> Delivery | None:
        """Controls first, then alternate live and historical ready service.

        The last actual attempt is retained in an existing delivery's JSON. A
        restart therefore cannot put a whole historical page ahead of live work
        or reset history's turn. Missing old provenance belongs to history.
        """
        with self._transaction() as database:
            row = database.execute("""WITH last_service AS (
                SELECT payload->>'stream'='forward' AS was_live
                FROM product_feedback_deliveries
                WHERE command_kind<>'control'
                  AND COALESCE((payload->>'service_sequence')::bigint,0)>0
                  AND (%s::text IS NULL OR COALESCE(payload->'encounter'->'key'->>'workspace_id',
                       payload->'lapse'->'key'->>'workspace_id')=%s)
                ORDER BY (payload->>'service_sequence')::bigint DESC LIMIT 1
            ) SELECT delivery.payload::text FROM product_feedback_deliveries delivery
                WHERE delivery.state='pending' AND delivery.next_attempt_at<=%s
                  AND (delivery.payload->>'qualification_status' IS DISTINCT FROM 'held'
                       OR COALESCE((delivery.payload->>'qualification_retryable')::boolean,true))
                  AND (%s::text IS NULL OR COALESCE(delivery.payload->'encounter'->'key'->>'workspace_id',
                       delivery.payload->'lapse'->'key'->>'workspace_id',
                       delivery.payload->'control'->'key'->>'workspace_id')=%s)
                  AND (NOT %s OR delivery.command_kind='control' OR (
                       delivery.payload->>'qualification_status' IN ('ready','held')
                       AND delivery.payload->'qualification'<>'null'::jsonb))
                ORDER BY CASE WHEN command_kind='control' THEN 0
                    WHEN %s THEN CASE WHEN payload->>'stream'='forward' THEN 1 ELSE 2 END
                    WHEN COALESCE(payload->>'stream','')='forward'
                         AND NOT COALESCE((SELECT was_live FROM last_service),false) THEN 1
                    WHEN COALESCE(payload->>'stream','')<>'forward'
                         AND COALESCE((SELECT was_live FROM last_service),false) THEN 1
                    ELSE 2 END,
                    COALESCE(payload->>'determined_at',payload->'encounter'->>'detected_at',
                        payload->'lapse'->>'detected_at')::timestamptz NULLS FIRST,
                    CASE WHEN %s THEN COALESCE(payload->>'owner_effect_id',operation_id) ELSE operation_id END
                    LIMIT 1""",
                (workspace, workspace, now, workspace, workspace, ready_only,
                 priority_order, priority_order)).fetchone()
            return Delivery.model_validate_json(row[0]) if row else None

    def has_determined_delivery(self, workspace: str, *, before_stream: str | None = None,
                               before_determined_at: str | None = None,
                               before_effect_id: str | None = None) -> bool:
        """Bounded existence observation, with no dispatch or source replay."""
        with self._transaction() as database:
            return has_determined_delivery_in_transaction(database, workspace,
                before_stream=before_stream,before_determined_at=before_determined_at,
                before_effect_id=before_effect_id)

    def save_delivery(self, delivery: Delivery, *, serviced=False) -> None:
        with self._transaction() as database:
            save_delivery_in_transaction(database, delivery, record_service=serviced)


def has_determined_delivery_in_transaction(database, workspace: str, *, before_stream=None,
                                           before_determined_at=None,before_effect_id=None) -> bool:
    """Observe pending Games facts in the caller's transaction, without mutation.

    No connection, transaction configuration, source replay or row lock is
    acquired. Optional-schema errors are contained by the composition's savepoint.
    """
    execute = getattr(database,"execute_native",None) or database.execute
    cutoff = (handoff_priority_key(before_stream,before_determined_at,before_effect_id)
              if before_stream is not None else None)
    bound = ' AND handoff_priority COLLATE "C" < %s COLLATE "C"' if cutoff is not None else ''
    return execute("""SELECT EXISTS(SELECT 1 FROM product_feedback_deliveries
        WHERE state='pending' AND handoff_priority IS NOT NULL
          AND next_attempt_at<=extract(epoch FROM statement_timestamp())
          AND COALESCE(payload->'encounter'->'key'->>'workspace_id',
              payload->'lapse'->'key'->>'workspace_id',payload->'control'->'key'->>'workspace_id')=%s"""
        + bound + ")",(workspace,cutoff) if cutoff is not None else (workspace,)).fetchone()[0]


def handoff_priority_key(stream,determined_at,effect_id):
    """Canonical derived ordering only; the original clock/key remain unchanged."""
    parsed = datetime.fromisoformat(determined_at.replace("Z","+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("handoff priority requires original UTC determination")
    return ("0:" if stream == "forward" else "1:") + parsed.astimezone(timezone.utc).isoformat(
        timespec="microseconds") + ":" + (effect_id or "")


def handoff_priority_for(delivery):
    """Small indexed sidecar derived from typed original owner facts at writes.

    Unqualified collection history has no sidecar. A legacy label/clock alone
    cannot enter the head hint; the actual witness must match the original.
    """
    if delivery.state != "pending":
        return None
    if delivery.control is not None:
        return ""
    witness = delivery.qualification
    if (witness is None or not witness.matches(delivery.command)
            or delivery.qualification_status not in {"ready","held"}
            or (delivery.qualification_status == "held" and not delivery.qualification_retryable)):
        return None
    return handoff_priority_key(witness.stream,witness.determined_at,delivery.owner_effect_id)
