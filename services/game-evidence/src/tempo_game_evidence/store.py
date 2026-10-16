"""Game Evidence's transaction owner. No cross-domain database access."""
import hashlib
from pathlib import Path
import uuid

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .comparison import compare_game
from .snapshot import ComparisonSnapshot, parse_snapshot
from .source import INDEX_VERSION, MAX_SOURCE_BYTES, identity, normalize_pgn, replay_positions, utc_now


from .payloads import make_delivery
from .reconciliation import finalize, request_reconciliation, resume_reconciliation


class EvidenceStore:
    def __init__(self, database_url):
        if not database_url:
            raise ValueError("explicit_game_evidence_database_required")
        self.database_url = database_url

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=3,
                               options="-c statement_timeout=5000 -c lock_timeout=1000")

    def migrate(self):
        with self.connect() as database:
            database.execute("SELECT pg_advisory_xact_lock(410001)")
            directory = Path(__file__).with_name("migrations")
            database.execute(directory.joinpath("001.sql").read_text())
            for migration in sorted(directory.glob("*.sql")):
                version = int(migration.stem)
                if not database.execute("SELECT 1 FROM evidence_schema_migrations WHERE version=%s", (version,)).fetchone():
                    database.execute(migration.read_text())

    def import_pgn(self, workspace, provider, source_id, player, raw, *, played_at=None, database=None):
        if len(raw)>MAX_SOURCE_BYTES:
            raise ValueError("source_byte_bound_exceeded")
        if not all(isinstance(value, str) and value.strip() for value in (workspace, provider, source_id, player)):
            raise ValueError("explicit_source_identity_required")
        game_id = identity(workspace, provider, source_id, player.casefold())
        raw_digest = hashlib.sha256(raw).hexdigest()
        input_digest = identity(raw_digest, str(played_at) if played_at else None)
        if database is None:
            with self.connect() as reading_database:
                previous = reading_database.execute("SELECT input_digest FROM games WHERE game_id=%s", (game_id,)).fetchone()
            if previous and previous["input_digest"] == input_digest:
                return game_id
        try:
            document = normalize_pgn(raw, player, played_at=played_at)
        except (ValueError, UnicodeError) as error:
            document = {"source_error": str(error) if isinstance(error, ValueError) else "invalid_utf8",
                        "player": player.casefold(), "played_at": None}
        document.update(workspace_id=workspace, game_id=game_id, provider=provider,
                        provider_game_id=source_id, source_digest=raw_digest)
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
        snapshot = parse_snapshot(snapshot)
        document = snapshot.model_dump(mode="json")
        digest = identity(document)
        workspace = snapshot.publication.workspace_id
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
            if previous and snapshot.schema_version == 2:
                old = parse_snapshot(previous['document'])
                if old.snapshot_id == snapshot.snapshot_id and old.revision >= snapshot.revision:
                    raise ValueError('stale_snapshot_revision')
                if old.publication.publication_id == snapshot.publication.publication_id and old.publication.publication_generation > snapshot.publication.publication_generation:
                    raise ValueError('stale_snapshot_generation')
            affected = set(current["changed_positions"])
            for item in [snapshot, parse_snapshot(previous["document"]) if previous else None]:
                if item:
                    affected.update(key for _, keys, _ in item.compiled_routes() for key in keys)
            database.execute("INSERT INTO snapshots VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                             (digest,workspace,snapshot.snapshot_id,snapshot.revision,Jsonb(document)))
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
        job = self.claim_job()
        if not job:
            return False
        try:
            if job["kind"] == "rescope":
                self._rescope(job)
            elif job["kind"] == "reconcile":
                resume_reconciliation(self, job)
            else:
                self._compare(job, before_publish)
        except Exception:
            with self.connect() as database:
                database.execute("""UPDATE jobs SET lease_token=NULL,leased_until=NULL,attempts=attempts+1,
                  error='comparison_failed',next_attempt_at=now()+interval '5 seconds'
                  WHERE job_key=%s AND lease_token=%s""", (job["job_key"],job["lease_token"]))
            raise
        return True

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
        snapshot = parse_snapshot(snapshot_row["document"]) if snapshot_row else None
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
                report = {"schema_version": 1, "game": game, "comparison_revision": revision,
                          "readiness": "unknown" if not snapshot or not snapshot.serving_current() or "source_error" in game else "ready",
                          "reason": game.get("source_error") or ("snapshot_missing" if not snapshot else None),
                          "snapshot": {"digest": inputs["snapshot_digest"], "snapshot_id": snapshot.snapshot_id,
                              "revision": snapshot.revision, "publication_id": snapshot.publication.publication_id,
                              "publication_generation": snapshot.publication.publication_generation,
                              "source_revision": snapshot.publication.source_revision} if snapshot else None,
                          "findings": findings}
                database.execute("INSERT INTO comparisons VALUES(%s,%s,%s,%s)", (game["game_id"],revision,token,Jsonb(report)))
                database.execute("UPDATE games SET report_revision=%s,positions=%s,index_receipt=%s WHERE game_id=%s",
                                 (revision,Jsonb(positions),Jsonb({"input_digest": row["input_digest"], "version": INDEX_VERSION,
                                  "position_count": len(positions), "complete": "source_error" not in game}),game["game_id"]))
                request_reconciliation(database, job["workspace_id"], game["game_id"])
                finalize(database, game["game_id"])
            database.execute("UPDATE jobs SET published=requested,lease_token=NULL,leased_until=NULL,error=NULL WHERE job_key=%s", (job["job_key"],))

    def report(self, workspace, game_id):
        with self.connect() as database:
            row = database.execute("""SELECT game.document,game.report_revision,game.reconciliation_status,game.reconciliation_reason,comparison.report,inputs.snapshot_digest AS current_snapshot_digest
              FROM games game LEFT JOIN comparisons comparison ON comparison.game_id=game.game_id AND comparison.revision=game.report_revision
              JOIN workspace_inputs inputs ON inputs.workspace_id=game.workspace_id
              WHERE game.game_id=%s AND game.workspace_id=%s""", (game_id,workspace)).fetchone()
            if not row:
                raise KeyError(game_id)
            batches = database.execute("SELECT delivery_id,status,reason,attempts,acknowledgement FROM outbox WHERE game_id=%s AND (comparison_revision=%s OR (attempts>0 AND status IN ('pending','accepted_pending','retryable_failure'))) ORDER BY evidence_revision,bucket",
                                       (game_id,row["report_revision"])).fetchall()
        report = row["report"] or {"schema_version":1,"game":row["document"],"readiness":"pending","findings":[]}
        priority = ["blocked","terminal_failure","conflict","stale_revision","retryable_failure","waiting_predecessor","accepted_pending","pending","completed"]
        status = next((status for status in priority if any(batch["status"]==status for batch in batches)),"not_required" if row["report"] else "not_ready")
        if row["reconciliation_status"] in {"pending","waiting_predecessor","blocked"}:
            status = row["reconciliation_status"]
        report["delivery"] = {"status":status,"reason":row["reconciliation_reason"] or next((batch["reason"] for batch in batches if batch["reason"]),None),
                              "attempts":sum(batch["attempts"] for batch in batches),"batches":batches}
        if report["game"]["revision"] != row["document"]["revision"]:
            report["readiness"] = "pending"
            report["reason"] = "source_revision_changed"
        elif (report.get("snapshot") or {}).get("digest") != row["current_snapshot_digest"]:
            report["readiness"] = "pending"
            report["reason"] = "snapshot_changed"
        return report

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
        if not result:
            raise KeyError(import_id)
        result["next_attempt_at"]=result["next_attempt_at"].isoformat()
        return result
