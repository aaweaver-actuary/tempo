"""Game Evidence's transaction owner. No cross-domain database access."""
from pathlib import Path
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from tempo_game_evidence.comparison import compare_game
from tempo_game_evidence.snapshot import ComparisonSnapshot, prepare_snapshot, snapshot_changed_positions, snapshot_storage, validated_snapshot_cache
from tempo_game_evidence.source import INDEX_VERSION, identity, prepare_import_identity, prepare_import_document, replay_positions, utc_now
from tempo_game_evidence.reporting import build_comparison_report, comparison_report_response, import_status_response


from tempo_game_evidence.payloads import make_delivery
from tempo_game_evidence.reconciliation import finalize, request_reconciliation
from tempo_game_evidence.worker import process_comparison_job


class EvidenceStore:
    def __init__(self, database_url):
        if not database_url:
            raise ValueError("explicit_game_evidence_database_required")
        self.database_url = database_url
        self._validated_snapshot = None

    def comparison_snapshot(self, digest, document):
        # One immutable snapshot per worker, with changed bytes invalidating the
        # cache even under a reused identity. Epoch fencing still governs publish.
        self._validated_snapshot = validated_snapshot_cache(self._validated_snapshot, digest, document)
        return self._validated_snapshot[1]

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3,
                               options="-c statement_timeout=5000 -c lock_timeout=1000")

    def check_schema_access(self):
        with self.connect() as database:
            database.execute("SELECT 1 FROM evidence_schema_migrations WHERE version=1")

    def migrate(self):
        with self.connect() as database:
            database.execute("SELECT pg_advisory_xact_lock(410001)")
            directory = Path(__file__).parent.parent / "ddl" / "migrations"
            database.execute(directory.joinpath("001.sql").read_text())
            for migration in sorted(directory.glob("*.sql")):
                version = int(migration.stem)
                if not database.execute("SELECT 1 FROM evidence_schema_migrations WHERE version=%s", (version,)).fetchone():
                    database.execute(migration.read_text())

    def import_pgn(self, workspace, provider, source_id, player, raw, *, played_at=None, database=None):
        game_id, raw_digest, input_digest = prepare_import_identity(
            workspace, provider, source_id, player, raw, played_at=played_at)
        if database is None:
            with self.connect() as reading_database:
                previous = reading_database.execute("SELECT input_digest FROM games WHERE game_id=%s", (game_id,)).fetchone()
            if previous and previous["input_digest"] == input_digest:
                return game_id
        document = prepare_import_document(workspace, provider, source_id, player, raw,
                                           raw_digest, game_id, played_at=played_at)
        if database is None:
            with self.connect() as owned_database:
                return self._import(owned_database, workspace, provider, source_id, player, raw,
                                    raw_digest, input_digest, game_id, document)
        return self._import(database, workspace, provider, source_id, player, raw,
                            raw_digest, input_digest, game_id, document)

    def _import(self, database, workspace, provider, source_id, player, raw, raw_digest, input_digest, game_id, document):
        database.execute("INSERT INTO workspace_inputs(workspace_id) VALUES(%s) ON CONFLICT DO NOTHING", (workspace,))
        database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (game_id,))
        previous = database.execute("SELECT * FROM games WHERE game_id=%s FOR UPDATE", (game_id,)).fetchone()
        if previous and previous["input_digest"] == input_digest:
            return game_id
        revision = previous["revision"] + 1 if previous else 1
        document["revision"] = revision
        document["imported_at"] = utc_now().isoformat()
        database.execute("""INSERT INTO games(game_id,workspace_id,provider,provider_game_id,player,revision,
          input_digest,source_digest,raw_source,document) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT(game_id) DO UPDATE SET revision=excluded.revision,input_digest=excluded.input_digest,
          source_digest=excluded.source_digest,raw_source=excluded.raw_source,document=excluded.document,
          index_receipt=NULL,positions=NULL""",
          (game_id,workspace,provider,source_id,player.casefold(),revision,input_digest,raw_digest,raw,Jsonb(document)))
        database.execute("INSERT INTO game_sources VALUES(%s,%s,%s,%s,%s,%s)",
                         (game_id,revision,input_digest,raw_digest,raw,Jsonb(document)))
        self._request(database, workspace, game_id)
        return game_id

    def _request(self, database, workspace, game_id):
        database.execute("""INSERT INTO jobs(job_key,workspace_id,game_id,kind) VALUES(%s,%s,%s,'compare')
            ON CONFLICT(job_key) DO UPDATE SET requested=jobs.requested+1,next_attempt_at=now(),error=NULL""",
            ("compare:"+game_id, workspace, game_id))

    def load_snapshot(self, snapshot):
        snapshot, document, digest, workspace = prepare_snapshot(snapshot)
        with self.connect() as database:
            database.execute("INSERT INTO workspace_inputs(workspace_id) VALUES(%s) ON CONFLICT DO NOTHING", (workspace,))
            current = database.execute("SELECT * FROM workspace_inputs WHERE workspace_id=%s FOR UPDATE", (workspace,)).fetchone()
            if current["snapshot_digest"] == digest:
                return digest
            existing = database.execute("SELECT digest FROM snapshots WHERE workspace_id=%s AND snapshot_id=%s AND revision=%s",
                                        (workspace,snapshot.snapshot_id,snapshot.revision)).fetchone()
            if existing and existing["digest"] != digest:
                raise ValueError("immutable_snapshot_revision_conflict")
            previous = database.execute("SELECT document FROM snapshots WHERE digest=%s", (current["snapshot_digest"],)).fetchone()
            affected = snapshot_changed_positions(snapshot, previous, current)
            database.execute("INSERT INTO snapshots VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             (digest,workspace,snapshot.snapshot_id,snapshot.revision,Jsonb(snapshot_storage(document))))
            database.execute("UPDATE workspace_inputs SET epoch=epoch+1,snapshot_digest=%s,changed_positions=%s WHERE workspace_id=%s",
                             (digest,Jsonb(sorted(affected)),workspace))
            database.execute("""INSERT INTO jobs(job_key,workspace_id,kind) VALUES(%s,%s,'rescope')
              ON CONFLICT(job_key) DO UPDATE SET requested=jobs.requested+1,cursor='',next_attempt_at=now()""",
              ("rescope:"+workspace,workspace))
        return digest

    def request_comparison(self, game_id):
        with self.connect() as database:
            game = database.execute("SELECT workspace_id FROM games WHERE game_id=%s", (game_id,)).fetchone()
            if not game:
                raise KeyError(game_id)
            self._request(database, game["workspace_id"], game_id)

    def claim_job(self):
        with self.connect() as database:
            job = database.execute("""SELECT * FROM jobs WHERE published<requested AND next_attempt_at<=now()
              AND (leased_until IS NULL OR leased_until<now()) ORDER BY kind DESC,job_key
              LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
            if job:
                job["lease_token"] = uuid.uuid4().hex
                database.execute("UPDATE jobs SET lease_token=%s,leased_until=now()+interval '30 seconds' WHERE job_key=%s",
                                 (job["lease_token"],job["job_key"]))
            return job

    def process_one(self, *, before_publish=None):
        return process_comparison_job(self, before_publish=before_publish)

    def record_job_failure(self, job):
        with self.connect() as database:
            database.execute("""UPDATE jobs SET lease_token=NULL,leased_until=NULL,attempts=attempts+1,
                  error='comparison_failed',next_attempt_at=now()+interval '5 seconds'
                  WHERE job_key=%s AND lease_token=%s""", (job["job_key"],job["lease_token"]))

    def _rescope(self, job):
        with self.connect() as database:
            current = database.execute("SELECT * FROM jobs WHERE job_key=%s FOR UPDATE", (job["job_key"],)).fetchone()
            if not self._current(current, job):
                self._release(database, job)
                return
            inputs = database.execute("SELECT * FROM workspace_inputs WHERE workspace_id=%s", (job["workspace_id"],)).fetchone()
            games = database.execute("""SELECT game_id FROM games WHERE workspace_id=%s AND game_id>%s
              ORDER BY game_id LIMIT 64""",
                (job["workspace_id"],job["cursor"])).fetchall()
            for game in games:
                self._request(database, job["workspace_id"], game["game_id"])
            database.execute("UPDATE jobs SET cursor=%s,published=%s,lease_token=NULL,leased_until=NULL WHERE job_key=%s",
                             (games[-1]["game_id"] if games else job["cursor"],
                              job["requested"] if len(games)<64 else job["published"],job["job_key"]))

    @staticmethod
    def _current(current, claimed):
        return (current and current["requested"] == claimed["requested"]
                and current["lease_token"] == claimed["lease_token"]
                and current["leased_until"] is not None and current["leased_until"] > utc_now())

    @staticmethod
    def _release(database, job):
        database.execute("UPDATE jobs SET lease_token=NULL,leased_until=NULL WHERE job_key=%s AND lease_token=%s",
                         (job["job_key"],job["lease_token"]))

    def _compare(self, job, before_publish):
        with self.connect() as database:
            row = database.execute("SELECT * FROM games WHERE game_id=%s", (job["game_id"],)).fetchone()
            inputs = database.execute("SELECT * FROM workspace_inputs WHERE workspace_id=%s", (job["workspace_id"],)).fetchone()
            snapshot_row = database.execute("SELECT document FROM snapshots WHERE digest=%s", (inputs["snapshot_digest"],)).fetchone()
        game = row["document"]
        receipt = row["index_receipt"]
        reusable = (receipt and receipt == {"input_digest": row["input_digest"], "version": INDEX_VERSION,
                    "position_count": len(game.get("moves", []))+1, "complete": True}
                    and row["positions"] is not None and len(row["positions"]) == receipt["position_count"])
        positions = row["positions"] if reusable else replay_positions(game) if "source_error" not in game else []
        snapshot = self.comparison_snapshot(inputs["snapshot_digest"], snapshot_row["document"]) if snapshot_row else None
        findings = compare_game(game, positions, snapshot) if snapshot and positions else []
        token = identity(row["revision"],row["input_digest"],inputs["epoch"],inputs["snapshot_digest"],INDEX_VERSION)
        if before_publish:
            before_publish()
        # Lock ordering: workspace -> game -> job -> outbox. Imports take game -> job.
        with self.connect() as database:
            current_inputs = database.execute("SELECT epoch FROM workspace_inputs WHERE workspace_id=%s FOR SHARE", (job["workspace_id"],)).fetchone()
            current_game = database.execute("SELECT * FROM games WHERE game_id=%s FOR UPDATE", (job["game_id"],)).fetchone()
            current_job = database.execute("SELECT * FROM jobs WHERE job_key=%s FOR UPDATE", (job["job_key"],)).fetchone()
            if (not self._current(current_job, job) or current_game["revision"] != row["revision"]
                    or current_inputs["epoch"] != inputs["epoch"]):
                self._release(database, job)
                return
            existing = database.execute("SELECT 1 FROM comparisons WHERE game_id=%s AND input_token=%s", (job["game_id"],token)).fetchone()
            if not existing:
                revision = current_game["report_revision"] + 1
                report = build_comparison_report(game, revision, snapshot, inputs["snapshot_digest"], findings)
                database.execute("INSERT INTO comparisons VALUES(%s,%s,%s,%s)", (game["game_id"],revision,token,Jsonb(report)))
                database.execute("UPDATE games SET report_revision=%s,positions=%s,index_receipt=%s WHERE game_id=%s",
                                 (revision,Jsonb(positions),Jsonb({"input_digest": row["input_digest"], "version": INDEX_VERSION,
                                  "position_count": len(positions), "complete": "source_error" not in game}),game["game_id"]))
                request_reconciliation(database, job["workspace_id"], game["game_id"])
                finalize(database, game["game_id"], snapshot_loader=self.comparison_snapshot)
            database.execute("UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error=NULL WHERE job_key=%s", (job["job_key"],))

    def report(self, workspace, game_id):
        with self.connect() as database:
            row = database.execute("""SELECT game.document,game.report_revision,game.reconciliation_status,game.reconciliation_reason,comparison.report,inputs.snapshot_digest AS current_snapshot_digest
              FROM games game LEFT JOIN comparisons comparison ON comparison.game_id=game.game_id AND comparison.revision=game.report_revision
              JOIN workspace_inputs inputs ON inputs.workspace_id=game.workspace_id
              WHERE game.game_id=%s AND game.workspace_id=%s""", (game_id,workspace)).fetchone()
            if not row:
                raise KeyError(game_id)
            from tempo_game_evidence.delivery import inspect_delivery
            delivery = inspect_delivery(database,game_id,row['report_revision'],row['reconciliation_status'],row['reconciliation_reason'],has_report=row['report'] is not None)
        return comparison_report_response(row, delivery)

    def list_games(self, workspace, after="", limit=64):
        if not 1 <= limit <= 100:
            raise ValueError("page_limit_out_of_range")
        with self.connect() as database:
            return database.execute("SELECT game_id,revision,report_revision,document->>'source_error' AS source_error FROM games WHERE workspace_id=%s AND game_id>%s ORDER BY game_id LIMIT %s",
                                    (workspace,after,limit)).fetchall()

    def import_status(self, workspace, import_id):
        with self.connect() as database:
            result=database.execute("SELECT import_id,status,reason,cursor,next_attempt_at,attempts FROM import_cursors WHERE workspace_id=%s AND import_id=%s",
                                    (workspace,import_id)).fetchone()
        return import_status_response(result, import_id)
