"""Bounded Games-owned projections/configuration; no Study table access."""
from contextlib import contextmanager
import hashlib

from app.contracts.product_v02.games import Finding, GameSummary
from app.games.feedback.ports import ConcurrentUpdate, Delivery, JobState, SourceState
from app.games.feedback.continuous import ContinuousProviderState, stream_prefix
from app.games.feedback.review import ReviewUnavailable, SyncConfiguration
from .product_feedback import PostgresGamesStore

REVIEW_SCHEMA_SQL = """
CREATE TABLE product_feedback_configuration (
    workspace_id TEXT NOT NULL, provider TEXT NOT NULL,
    revision BIGINT NOT NULL, next_run_at DOUBLE PRECISION NOT NULL,
    payload JSONB NOT NULL, PRIMARY KEY(workspace_id,provider)
);
CREATE TABLE product_feedback_sync_requests (
    workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    provider TEXT NOT NULL, job_id TEXT NOT NULL REFERENCES product_feedback_jobs(job_id),
    requested_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY(workspace_id,operation_id)
);
CREATE INDEX product_feedback_latest_request
    ON product_feedback_sync_requests(workspace_id,provider,requested_at DESC);
CREATE TABLE product_feedback_retry_requests (
    workspace_id TEXT NOT NULL, operation_id TEXT NOT NULL,
    source_key TEXT NOT NULL REFERENCES product_feedback_sources(source_key),
    delivery_id TEXT NOT NULL REFERENCES product_feedback_deliveries(operation_id),
    finding_digest TEXT NOT NULL, PRIMARY KEY(workspace_id,operation_id)
);
CREATE INDEX product_feedback_source_game ON product_feedback_sources
    ((payload->'normalized'->>'workspace_id'),(payload->'normalized'->>'source_id'),
     (payload->'normalized'->>'game_id'));
"""


class DedicatedGamesConnections:
    """One optional connection at a time, distinct from foreground Study pools."""
    def __init__(self, dsn, *, read_only=False):
        from threading import BoundedSemaphore
        self.dsn, self._slot = dsn, BoundedSemaphore(1)
        self.read_only=read_only

    @contextmanager
    def __call__(self, *, repertoire_snapshot=False):
        import psycopg
        from ..postgres_store import tempo_row_factory
        if not self._slot.acquire(timeout=0.25):
            raise ReviewUnavailable("games_connection_busy", 503)
        try:
            with psycopg.connect(self.dsn, autocommit=True, connect_timeout=2,
                                 row_factory=tempo_row_factory, application_name="tempo-product-games") as database:
                with database.transaction():
                    if self.read_only or repertoire_snapshot:
                        database.execute("SET TRANSACTION READ ONLY")
                    if repertoire_snapshot:
                        # Whole-repertoire reads are read-only and finish before
                        # chess projection. Keep ordinary Games writes bounded.
                        database.execute("""SELECT set_config('transaction_timeout','0',true),
                            set_config('lock_timeout','100ms',true),set_config('statement_timeout','0',true)""")
                    else:
                        database.execute("""SELECT set_config('transaction_timeout','250ms',true),
                            set_config('lock_timeout','100ms',true),set_config('statement_timeout','250ms',true)""")
                    yield database
        finally:
            self._slot.release()

    @contextmanager
    def writer(self):
        if self.read_only:
            raise ReviewUnavailable("games_writer_profile_required",503)
        from ..postgres_store import PostgresConnection
        with self() as database:
            yield PostgresConnection(database)


class PostgresReviewStore:
    def __init__(self, connections):
        self._connections = connections

    @contextmanager
    def _transaction(self):
        with self._connections() as database:
            with database.transaction():
                database.execute("""SELECT set_config('transaction_timeout','250ms',true),
                    set_config('lock_timeout','100ms',true),set_config('statement_timeout','250ms',true)""")
                yield database

    def configuration(self, workspace, provider):
        with self._transaction() as database:
            row = database.execute("SELECT payload::text FROM product_feedback_configuration WHERE workspace_id=%s AND provider=%s",
                                   (workspace, provider)).fetchone()
            return SyncConfiguration.model_validate_json(row[0]) if row else None

    def configure(self, configuration, expected_revision):
        if configuration.revision != expected_revision + 1:
            raise ReviewUnavailable("configuration_revision_mismatch")
        with self._transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"tempo:product-sync-config:{configuration.workspace_id}:{configuration.provider}",))
            active = database.execute("""SELECT 1 FROM product_feedback_jobs
                WHERE payload->'spec'->>'workspace_id'=%s AND payload->'spec'->>'provider'=%s
                AND status IN ('queued','running','retryable') LIMIT 1""",
                (configuration.workspace_id, configuration.provider)).fetchone()
            if active:
                raise ReviewUnavailable("finish_existing_sync_before_changing_configuration")
            if expected_revision == 0:
                changed = database.execute("""INSERT INTO product_feedback_configuration
                    (workspace_id,provider,revision,next_run_at,payload) VALUES(%s,%s,%s,0,%s::jsonb)
                    ON CONFLICT DO NOTHING""", (configuration.workspace_id, configuration.provider,
                    configuration.revision, configuration.model_dump_json())).rowcount
            else:
                changed = database.execute("""UPDATE product_feedback_configuration SET
                    revision=%s,next_run_at=0,payload=%s::jsonb
                    WHERE workspace_id=%s AND provider=%s AND revision=%s""",
                    (configuration.revision, configuration.model_dump_json(), configuration.workspace_id,
                     configuration.provider, expected_revision)).rowcount
            if changed != 1:
                raise ReviewUnavailable("configuration_changed_reload_before_saving")
        return configuration

    def recent(self, workspace, limit):
        if not 1 <= limit <= 100:
            raise ValueError("recent game limit must be 1..100")
        with self._transaction() as database:
            rows = database.execute("""SELECT payload->'normalized'->>'source_id',
                payload->'normalized'->>'game_id',payload->'original'->>'source_revision',
                payload->'original'->>'played_at',payload->'original'->>'time_precision',
                payload->'normalized'->>'learner_color' FROM product_feedback_sources
                WHERE payload->'normalized'->>'workspace_id'=%s
                ORDER BY (payload->'original'->>'played_at')::timestamptz DESC NULLS LAST,source_key
                LIMIT %s""", (workspace, limit)).fetchall()
        return tuple(GameSummary(provider="chesscom" if row[0] == "chess.com" else row[0], game_id=row[1], source_revision=int(row[2]),
            played_at=row[3], time_precision=row[4], player_color=row[5]) for row in rows)

    def source_detail(self, workspace, provider, game_id):
        with self._transaction() as database:
            rows = database.execute("""SELECT payload::text FROM product_feedback_sources
                WHERE payload->'normalized'->>'workspace_id'=%s
                  AND payload->'normalized'->>'source_id'=ANY(%s)
                  AND payload->'normalized'->>'game_id'=%s LIMIT 2""",
                (workspace, ["chesscom", "chess.com"] if provider in {"chesscom", "chess.com"} else [provider], game_id)).fetchall()
        if len(rows) > 1:
            raise ReviewUnavailable("ambiguous_stored_game")
        return SourceState.model_validate_json(rows[0][0]) if rows else None

    def canonical_game_id(self, provider, provider_game_id):
        aliases = ["chesscom", "chess.com"] if provider in {"chesscom", "chess.com"} else [provider]
        with self._transaction() as database:
            rows = database.execute("""SELECT id FROM imported_games
                WHERE provider=ANY(%s) AND provider_game_id=%s LIMIT 2""",
                (aliases, provider_game_id)).fetchall()
        if len(rows) != 1:
            raise ValueError("one_existing_canonical_game_required")
        return rows[0][0]

    def deliveries(self, source_key):
        with self._transaction() as database:
            rows = database.execute("""SELECT payload::text FROM product_feedback_deliveries
                WHERE source_key=%s ORDER BY operation_id LIMIT 1001""", (source_key,)).fetchall()
        if len(rows) > 1000:
            raise ReviewUnavailable("game_delivery_projection_exceeds_bound")
        return tuple(Delivery.model_validate_json(row[0]) for row in rows)

    def delivery(self, operation_id):
        return PostgresGamesStore(self._connections).delivery(operation_id)

    def last_job(self, workspace, provider):
        with self._transaction() as database:
            row = database.execute("""SELECT job.payload::text FROM product_feedback_sync_requests request
                JOIN product_feedback_jobs job ON job.job_id=request.job_id
                WHERE request.workspace_id=%s AND request.provider=%s
                ORDER BY request.requested_at DESC,request.operation_id DESC LIMIT 1""",
                (workspace, provider)).fetchone()
        return JobState.model_validate_json(row[0]) if row else None

    def refresh_job(self, workspace, operation_id):
        with self._transaction() as database:
            row = database.execute("""SELECT job.payload::text FROM product_feedback_sync_requests request
                JOIN product_feedback_jobs job ON job.job_id=request.job_id
                WHERE request.workspace_id=%s AND request.operation_id=%s
                  AND job.payload->'spec'->>'purpose'='refresh'""", (workspace, operation_id)).fetchone()
        if row is None:
            return None
        return JobState.model_validate_json(row[0])

    def enqueue_refresh(self, workspace, provider, operation_id, build_job):
        """Serialize the pure job plan with configuration, aliases and checkpoints.

        The caller's command transaction also stages the durable task/receipt.
        build_job is pure policy supplied by the Games owner; it performs no I/O.
        """
        with self._transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"tempo:product-sync-config:{workspace}:{provider}",))
            prior = database.execute("""SELECT job.payload::text FROM product_feedback_sync_requests request
                JOIN product_feedback_jobs job ON job.job_id=request.job_id
                WHERE request.workspace_id=%s AND request.operation_id=%s""",
                (workspace, operation_id)).fetchone()
            if prior:
                job = JobState.model_validate_json(prior[0])
                if job.spec.provider != provider or job.spec.purpose != "refresh":
                    raise ReviewUnavailable("operation_provider_mismatch")
                return job, False
            row = database.execute("""SELECT payload::text FROM product_feedback_configuration
                WHERE workspace_id=%s AND provider=%s""", (workspace, provider)).fetchone()
            if row is None:
                raise ReviewUnavailable("Save this provider's account, filters and repertoires in Games Settings before refreshing.", 422)
            configuration = SyncConfiguration.model_validate_json(row[0])
            active = database.execute("""SELECT payload::text FROM product_feedback_jobs
                WHERE payload->'spec'->>'workspace_id'=%s AND payload->'spec'->>'provider'=%s
                  AND payload->'spec'->>'purpose'='refresh'
                  AND (payload->'spec'->>'configuration_revision')::bigint=%s
                  AND status IN ('queued','running','retryable') ORDER BY job_id LIMIT 1""",
                (workspace, provider, configuration.revision)).fetchone()
            if active:
                job = JobState.model_validate_json(active[0])
                created = False
            else:
                checkpoint = database.execute("""SELECT payload::text FROM product_feedback_jobs
                    WHERE payload->'spec'->>'workspace_id'=%s AND payload->'spec'->>'provider'=%s
                      AND status='complete' AND payload->>'window_complete'='true'
                      AND (starts_with(job_id,%s) OR (payload->'spec'->>'purpose'='refresh'
                        AND (payload->'spec'->>'configuration_revision')::bigint=%s))
                    ORDER BY (payload->'spec'->'window'->>'end_at')::timestamptz DESC LIMIT 1""",
                    (workspace, provider, f"following:{provider}:{configuration.revision}:",
                     configuration.revision)).fetchone()
                spec = build_job(configuration, JobState.model_validate_json(checkpoint[0]) if checkpoint else None)
                @contextmanager
                def borrowed():
                    yield database
                job = PostgresGamesStore(borrowed).enqueue(spec)
                created = True
            database.execute("""INSERT INTO product_feedback_sync_requests
                (workspace_id,operation_id,provider,job_id,requested_at)
                VALUES(%s,%s,%s,%s,extract(epoch FROM clock_timestamp()))""",
                (workspace, operation_id, provider, job.spec.job_id))
            return job, created

    @staticmethod
    def _stream_job(database, configuration, purpose):
        prefix = stream_prefix(configuration, purpose)
        row = database.execute("""SELECT job.payload::text FROM product_feedback_sync_requests request
            JOIN product_feedback_jobs job ON job.job_id=request.job_id
            WHERE request.workspace_id=%s AND request.provider=%s
              AND starts_with(job.job_id,%s)
            ORDER BY (job.status='complete' AND job.payload->>'window_complete'='true'),
              CASE WHEN NOT (job.status='complete' AND job.payload->>'window_complete'='true')
                THEN request.requested_at END ASC,
              (job.payload->'spec'->'window'->>'end_at')::timestamptz DESC,job.job_id
            LIMIT 1""", (configuration.workspace_id, configuration.provider, prefix)).fetchone()
        return JobState.model_validate_json(row[0]) if row else None

    def continuous_state(self, workspace, now):
        with self._transaction() as database:
            rows = database.execute("""SELECT payload::text,next_run_at FROM product_feedback_configuration
                WHERE workspace_id=%s AND payload->>'interval_seconds' IS NOT NULL
                  AND provider IN ('lichess','chesscom') ORDER BY provider LIMIT 2""", (workspace,)).fetchall()
            result = []
            for payload, next_run in rows:
                configuration = SyncConfiguration.model_validate_json(payload)
                result.append(ContinuousProviderState(configuration, next_run <= now,
                    self._stream_job(database, configuration, "forward"),
                    self._stream_job(database, configuration, "reconciliation")))
            return tuple(result)

    def enqueue_continuous(self, configuration, job, now, *, purpose, expected_previous):
        if not job.job_id.startswith(stream_prefix(configuration, purpose)):
            raise ReviewUnavailable("continuous_job_purpose_mismatch")
        with self._transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"tempo:product-sync-config:{configuration.workspace_id}:{configuration.provider}",))
            current = self._stream_job(database, configuration, purpose)
            if current != expected_previous:
                raise ConcurrentUpdate("continuous_stream_changed")
            if current is not None and (current.status != "complete" or not current.window_complete):
                raise ConcurrentUpdate("continuous_stream_unfinished")
            @contextmanager
            def borrowed():
                yield database
            # Reuse the one configured enqueue authority and its exact transaction.
            return PostgresReviewStore(borrowed)._enqueue(configuration, job, now,
                                                          advance_schedule=purpose == "forward")

    def enqueue(self, configuration, job, now):
        return self._enqueue(configuration, job, now)

    def _enqueue(self, configuration, job, now, *, advance_schedule=True):
        with self._transaction() as database:
            database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"tempo:product-sync-config:{configuration.workspace_id}:{configuration.provider}",))
            prior = database.execute("""SELECT job.payload::text FROM product_feedback_sync_requests request
                JOIN product_feedback_jobs job ON job.job_id=request.job_id
                WHERE request.workspace_id=%s AND request.operation_id=%s""",
                (configuration.workspace_id, job.job_id)).fetchone()
            if prior:
                old = JobState.model_validate_json(prior[0])
                if old.spec.provider != job.provider:
                    raise ReviewUnavailable("operation_provider_mismatch")
                return old
            current = database.execute("""SELECT revision FROM product_feedback_configuration
                WHERE workspace_id=%s AND provider=%s FOR UPDATE""",
                (configuration.workspace_id, configuration.provider)).fetchone()
            if current is None or current[0] != configuration.revision:
                raise ReviewUnavailable("configuration_changed_reload_before_running")
            @contextmanager
            def borrowed():
                yield database
            # Borrow this exact transaction; the existing Games owner writes its job.
            state = PostgresGamesStore(borrowed).enqueue(job)
            database.execute("""INSERT INTO product_feedback_sync_requests
                (workspace_id,operation_id,provider,job_id,requested_at) VALUES(%s,%s,%s,%s,%s)""",
                (configuration.workspace_id, job.job_id, job.provider, job.job_id, now))
            if advance_schedule and configuration.interval_seconds is not None:
                database.execute("""UPDATE product_feedback_configuration SET next_run_at=%s
                    WHERE workspace_id=%s AND provider=%s""",
                    (now + configuration.interval_seconds, configuration.workspace_id, configuration.provider))
            return state

    def due_configuration(self, workspace, now):
        with self._transaction() as database:
            row = database.execute("""SELECT payload::text FROM product_feedback_configuration
                WHERE workspace_id=%s AND next_run_at<=%s AND payload->>'interval_seconds' IS NOT NULL
                ORDER BY next_run_at,provider LIMIT 1""", (workspace, now)).fetchone()
        return SyncConfiguration.model_validate_json(row[0]) if row else None

    def due_configurations(self, workspace, now):
        """Both explicitly configured providers; a held one cannot hide the other."""
        with self._transaction() as database:
            rows=database.execute("""SELECT payload::text FROM product_feedback_configuration
                WHERE workspace_id=%s AND next_run_at<=%s AND payload->>'interval_seconds' IS NOT NULL
                  AND provider IN ('lichess','chesscom')
                ORDER BY next_run_at,provider LIMIT 2""",(workspace,now)).fetchall()
        return tuple(SyncConfiguration.model_validate_json(row[0]) for row in rows)

    def request_retry(self, workspace, source, finding: Finding, operation_id, *, delivery_id=None):
        digest = hashlib.sha256(finding.model_dump_json().encode()).hexdigest()
        with self._transaction() as database:
            prior = database.execute("""SELECT delivery_id,finding_digest FROM product_feedback_retry_requests
                WHERE workspace_id=%s AND operation_id=%s""", (workspace, operation_id)).fetchone()
            if prior:
                if prior[1] != digest:
                    raise ReviewUnavailable("retry_operation_finding_mismatch")
                if delivery_id is not None and prior[0] != delivery_id:
                    raise ReviewUnavailable("retry_operation_delivery_mismatch")
                row = database.execute("SELECT payload::text FROM product_feedback_sources WHERE source_key=%s FOR UPDATE",
                                       (source.source_key,)).fetchone()
                current = SourceState.model_validate_json(row[0]) if row else None
                if current is None or current.quarantined or finding not in current.findings:
                    raise ReviewUnavailable("current_confirmed_finding_required")
                @contextmanager
                def borrowed():
                    yield database
                return PostgresGamesStore(borrowed).delivery(prior[0])
            row = database.execute("SELECT payload::text FROM product_feedback_sources WHERE source_key=%s FOR UPDATE",
                                   (source.source_key,)).fetchone()
            current = SourceState.model_validate_json(row[0]) if row else None
            if (current is None or current.version != source.version or current.quarantined
                    or finding not in current.findings or not finding.lapse_eligible
                    or current.normalized.workspace_id != workspace):
                raise ReviewUnavailable("current_confirmed_finding_required")
            if delivery_id is not None:
                # Gateway preparation already selected the exact retained row.
                # Its short writer must use the primary key, not rescan history.
                rows = database.execute("""SELECT payload::text FROM product_feedback_deliveries
                    WHERE operation_id=%s FOR UPDATE""",(delivery_id,)).fetchall()
            else:
                rows = database.execute("""SELECT payload::text FROM product_feedback_deliveries
                    WHERE source_key=%s AND command_kind='lapse' ORDER BY operation_id LIMIT 1001 FOR UPDATE""",
                    (source.source_key,)).fetchall()
            candidates = [Delivery.model_validate_json(row[0]) for row in rows]
            selected = [item for item in candidates if item.source_key == source.source_key and item.lapse is not None
                        and item.lapse.key.decision_id == finding.decision_id
                        and item.lapse.key.occurrence_ply == finding.ply]
            if len(rows) > 1000 or len(selected) != 1 or selected[0].state in {"superseded", "failed"}:
                raise ReviewUnavailable("retryable_stored_command_required")
            delivery = selected[0]
            database.execute("""INSERT INTO product_feedback_retry_requests
                (workspace_id,operation_id,source_key,delivery_id,finding_digest) VALUES(%s,%s,%s,%s,%s)""",
                (workspace, operation_id, source.source_key, delivery.operation_id, digest))
            return delivery


def fence_imported_game(database, lapse, retained):
    """Games authenticates its canonical source before calling Study's factory.

    Borrow the receiver transaction. Hold only Games source/import identity locks
    while Study applies its semantic command; no HTTP or chess replay occurs here.
    This T10 fence requires T10's committed source and is not a T07 prerequisite.
    """
    import json
    from .authority import acceptance
    game = retained["game"]
    provider = "chesscom" if game["provider"] == "chess.com" else game["provider"]
    if (game["id"], provider, retained["authority"]["workspace_id"]) != (
            lapse.key.game_id, lapse.key.provider, lapse.key.workspace_id):
        raise ReviewUnavailable("canonical_imported_game_scope_mismatch")
    acceptance.lock_imported_source(database, game["provider"], game["provider_game_id"])
    actual = acceptance.imported_game(database, game["id"])
    if actual is None or dict(actual) != game or game["adaptive_excluded"]:
        raise ReviewUnavailable("canonical_imported_game_changed_or_excluded")
    rows = database.execute_native("""SELECT payload::text FROM product_feedback_sources
        WHERE payload->'normalized'->>'workspace_id'=%s
          AND payload->'normalized'->>'source_id'=%s
          AND payload->'normalized'->>'game_id'=%s LIMIT 2 FOR SHARE""",
        (lapse.key.workspace_id, lapse.key.provider, lapse.key.game_id)).fetchall()
    source = SourceState.model_validate_json(rows[0][0]) if len(rows) == 1 else None
    if (source is None or source.quarantined or source.normalized is None
            or source.original.source_game_id != game["provider_game_id"]
            or source.original.source_digest != lapse.finding.source_digest
            or source.original.source_revision != lapse.finding.source_revision
            or source.original.played_at != lapse.occurred_at
            or source.normalized.starting_fen != game["start_fen"]
            or source.normalized.moves != json.loads(game["moves_json"])
            or source.normalized.learner_color != game["color"]
            or not any(f.kind == "confirmed_miss" and f.decision_id == lapse.key.decision_id
                and f.ply == lapse.key.occurrence_ply and f.source_digest == lapse.finding.source_digest
                and f.position_key == lapse.finding.position_key
                and f.played_uci == lapse.finding.played_uci and f.expected_uci == lapse.finding.expected_uci
                for f in source.findings)):
        raise ReviewUnavailable("committed_game_source_changed_or_unproved")
