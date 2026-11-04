"""Existing command receipt/replay/rollback proof extracted from #558.

Uses only the established task-owned disposable PostgreSQL fixture.
"""
import json

import pytest

from test_product_game_lapse_postgres import owned_postgres


def test_native_unreserved_snapshot_read_profile_refuses_writes_and_keeps_background_budget(owned_postgres, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    import psycopg
    from tempo_legacy_database.adapters import postgres_store
    with owned_postgres() as fixture:
        conninfo = fixture.raw.info.dsn
        password = fixture.raw.pgconn.password.decode()
    @contextmanager
    def borrowed(**options):
        with psycopg.connect(conninfo, password=password, row_factory=postgres_store.tempo_row_factory) as raw:
            yield raw
    monkeypatch.setattr(postgres_store, '_pool', lambda read_only, **options: SimpleNamespace(connection=borrowed))
    with postgres_store.connection(read_only=True, repertoire_snapshot=True) as reader:
        assert tuple(reader.execute_native("SELECT current_setting('transaction_read_only'), "
            "current_setting('transaction_timeout'), current_setting('statement_timeout'), "
            "current_setting('lock_timeout')").fetchone()) == ('on', '0', '0', '100ms')
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        with postgres_store.connection(read_only=True, repertoire_snapshot=True) as reader:
            reader.execute_native('UPDATE settings SET id=id WHERE false')
    with postgres_store.connection(read_only=True, background=True) as reader:
        assert tuple(reader.execute_native("SELECT current_setting('transaction_timeout'), "
            "current_setting('lock_timeout')").fetchone()) == ('250ms', '25ms')


def test_native_review_commands_commit_with_receipt_replay_and_rollback(owned_postgres, monkeypatch):
    from fastapi import HTTPException
    from app import command_gateway as gateway
    from app.product_composition import game_review_commands as commands
    monkeypatch.setattr(gateway,"_handlers",{})
    monkeypatch.setattr(gateway,"_preparers",{})
    monkeypatch.setattr(gateway,"_writer_connection",lambda background:owned_postgres())
    commands.register_commands()
    configuration=dict(workspace_id="legacy",provider="lichess",account="Learner",
        window=dict(start_at="2026-10-25T00:00:00Z",end_at="2026-10-26T00:00:00Z",precision="second"),
        repertoire_ids=["white"],speeds=["rapid"],rated=True,interval_seconds=None,revision=1)
    payload=dict(configuration=configuration,expected_revision=0)
    saved=gateway.execute_command("native-config","product_games.configure",payload)
    assert saved==gateway.execute_command("native-config","product_games.configure",payload)
    run=dict(provider="lichess",operation_id="native-run")
    queued=gateway.execute_command("native-run","product_games.sync",run)
    assert queued["status"]=="pending"
    assert queued==gateway.execute_command("native-run","product_games.sync",run)
    with pytest.raises(gateway.CommandConflict):
        gateway.execute_command("native-run","product_games.sync",dict(provider="chesscom",operation_id="native-run"))
    original=gateway._handlers["product_games.sync"]
    def failed_after_enqueue(database,payload):
        original(database,payload)
        raise HTTPException(409,"synthetic_failure_after_enqueue")
    monkeypatch.setitem(gateway._handlers,"product_games.sync",failed_after_enqueue)
    assert gateway.execute_command("native-rollback","product_games.sync",
        dict(provider="lichess",operation_id="native-rollback")) is None
    with owned_postgres() as database:
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_jobs").fetchone()[0]==1
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_sync_requests").fetchone()[0]==1
        rows=database.execute_native("SELECT operation_id,state FROM operation_receipts ORDER BY operation_id").fetchall()
        assert [tuple(row) for row in rows]==[("native-config","complete"),("native-rollback","failed"),("native-run","complete")]
        # Remove only this command case's exact records, including its
        # configured account, before returning the shared owning fixture.
        database.execute_native("DELETE FROM product_feedback_sync_requests WHERE workspace_id='legacy' AND operation_id='native-run'")
        database.execute_native("DELETE FROM product_feedback_jobs WHERE job_id='native-run'")
        database.execute_native("""DELETE FROM product_feedback_configuration WHERE workspace_id='legacy'
            AND provider='lichess' AND revision=1 AND payload=%s::jsonb""",(json.dumps(configuration),))
        database.execute_native("DELETE FROM operation_receipts WHERE operation_id IN ('native-config','native-run','native-rollback')")
        assert database.execute_native("SELECT COUNT(*) FROM product_feedback_configuration").fetchone()[0]==0


def test_native_continuous_stream_selection_conditional_enqueue_and_alias_readback(owned_postgres, monkeypatch):
    """Games storage only: no Study admission, queue work or settlement."""
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    from app.games.feedback.continuous import ContinuousSync, ForwardWindow
    from app.games.feedback.ports import ConcurrentUpdate
    from tempo_legacy_database.adapters.games.product_feedback import PostgresGamesStore
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from test_product_continuous_sync import START, build, configuration, stamp
    from test_product_feedback_jobs import harness, spec

    @contextmanager
    def connections():
        with owned_postgres() as database:
            yield database.raw

    config = configuration().model_copy(update={"workspace_id": "task-d-native"})
    review, games = PostgresReviewStore(connections), PostgresGamesStore(connections)
    review.configure(config, 0)
    now = START + 5000
    schedule = ContinuousSync(workspace=config.workspace_id, store=review, policy=ForwardWindow(300),
        build_job=build, now=lambda: now, reconciliation_interval_seconds=600)
    forward = schedule.policy.next_job(config, None, now=now, build_job=build)
    review.enqueue_continuous(config, forward, now, purpose="forward", expected_previous=None)
    with connections() as database:
        next_run = database.execute("SELECT next_run_at FROM product_feedback_configuration WHERE workspace_id=%s",
                                    (config.workspace_id,)).fetchone()[0]
    barrier = Barrier(2)
    def compete(delta):
        candidate = schedule._reconciliation_job(config, None, now+delta)
        barrier.wait(timeout=5)
        try:
            return review.enqueue_continuous(config, candidate, now+delta,
                purpose="reconciliation", expected_previous=None)
        except ConcurrentUpdate:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, (0, 1)))
    winner, = [result for result in results if result is not None]
    with connections() as database:
        assert database.execute("SELECT next_run_at FROM product_feedback_configuration WHERE workspace_id=%s",
                                (config.workspace_id,)).fetchone()[0] == next_run
        assert database.execute("SELECT COUNT(*) FROM product_feedback_sync_requests WHERE workspace_id=%s",
                                (config.workspace_id,)).fetchone()[0] == 2
    retained = winner.model_copy(update={"status": "retryable", "continuation": "original-page-two",
                                        "next_attempt_at": now+90})
    games.save_job(retained)
    manual = build(config, "task-d-manual-backfill")
    review.enqueue(config, manual, now+10)
    assert review.last_job(config.workspace_id, config.provider).spec == manual
    restored = PostgresReviewStore(connections).continuous_state(config.workspace_id, now+1000)[0]
    assert restored.forward_job.spec == forward
    assert restored.reconciliation_job.spec == winner.spec
    assert restored.reconciliation_job.continuation == "original-page-two"
    assert restored.reconciliation_job.next_attempt_at == now+90
    assert not schedule.enqueue_due()

    # Old unfinished work must not be hidden even by a later complete stream job.
    held = games.load_job(forward.job_id).model_copy(update={"status": "quarantined", "continuation": "held"})
    games.save_job(held)
    later = forward.model_copy(update={"job_id": forward.job_id+"-legacy-later",
        "window": forward.window.model_copy(update={"end_at": stamp(now+1000)})})
    later_state = review.enqueue(config, later, now+20)
    games.save_job(later_state.model_copy(update={"status": "complete", "window_complete": True}))
    state = review.continuous_state(config.workspace_id, now+1000)[0]
    assert state.forward_job.spec == forward and state.forward_job.continuation == "held"

    # Reuse the configured enqueue writer; a failure rolls back job AND request.
    other = config.model_copy(update={"provider": "chesscom"})
    review.configure(other, 0)
    candidate = schedule.policy.next_job(other, None, now=now, build_job=build)
    original = PostgresGamesStore.enqueue
    def fail_after_job(store, job):
        original(store, job)
        raise RuntimeError("task-d-injected-before-request")
    with monkeypatch.context() as patch:
        patch.setattr(PostgresGamesStore, "enqueue", fail_after_job)
        with pytest.raises(RuntimeError, match="injected-before-request"):
            review.enqueue_continuous(other, candidate, now, purpose="forward", expected_previous=None)
    assert games.load_job(candidate.job_id) is None
    with connections() as database:
        assert database.execute("SELECT COUNT(*) FROM product_feedback_sync_requests WHERE job_id=%s",
                                (candidate.job_id,)).fetchone()[0] == 0

    h = harness()
    h.store.enqueue(spec())
    h.coordinator.capture_page(h.store.load_job("job"))
    source = next(iter(h.store.sources.values()))
    for alias, identity in (("chess.com", "task-d-game-a"), ("chesscom", "task-d-game-b")):
        stored = source.model_copy(update={"source_key": identity, "normalized": source.normalized.model_copy(
            update={"workspace_id": config.workspace_id, "source_id": alias, "game_id": identity})})
        with connections() as database:
            database.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,%s,%s::jsonb)",
                             (identity, stored.version, stored.model_dump_json()))
        for request_alias in ("chesscom", "chess.com"):
            assert review.source_detail(config.workspace_id, request_alias, identity) == stored
        assert review.source_detail("other-workspace", "chesscom", identity) is None
    assert {(g.provider, g.game_id) for g in review.recent(config.workspace_id, 50)} == {
        ("chesscom", "task-d-game-a"), ("chesscom", "task-d-game-b")}
    ambiguous = stored.model_copy(update={"source_key": "task-d-game-a-alias", "normalized":
        stored.normalized.model_copy(update={"game_id": "task-d-game-a", "source_id": "chesscom"})})
    with connections() as database:
        database.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,%s,%s::jsonb)",
                         (ambiguous.source_key, ambiguous.version, ambiguous.model_dump_json()))
    from app.games.feedback.review import ReviewUnavailable
    with pytest.raises(ReviewUnavailable, match="ambiguous_stored_game"):
        review.source_detail(config.workspace_id, "chesscom", "task-d-game-a")


def test_native_recent_job_projection_matches_full_reader_without_transferring_payload(owned_postgres, monkeypatch):
    """Tied aliases, isolation and scalar wire size on one disposable dataset."""
    from contextlib import contextmanager
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from pydantic import ValidationError
    from app.contracts.product_v02.games import GameRejection
    from app.games.feedback.http import create_game_review_router
    from app.games.feedback.ports import JobState
    from app.games.feedback.review import GameReview, sync_status
    from tempo_legacy_database.adapters.games.product_review import PostgresReviewStore
    from test_product_feedback_jobs import spec

    workspace = "j2-projection-native"
    captured = []
    @contextmanager
    def connections():
        with owned_postgres() as database:
            class Capture:
                def transaction(self):
                    return database.raw.transaction()
                def execute(self, sql, params=None):
                    result = database.raw.execute(sql, params)
                    if "FROM product_feedback_sync_requests request" not in sql:
                        return result
                    class CapturedResult:
                        def fetchone(self):
                            row = result.fetchone()
                            captured.append((sql, row))
                            return row
                    return CapturedResult()
            yield Capture()

    store = PostgresReviewStore(connections)
    def make(job_id, ws, provider, status="queued"):
        job_spec = spec(job_id, provider).model_copy(update={"workspace_id": ws})
        return JobState(spec=job_spec, status=status, continuation="PRIVATE-CONTINUATION-SENTINEL",
            rejections=tuple(GameRejection(source_game_id=f"game-{i}", source_revision=1,
                reason="filtered_speed") for i in range(100)))
    jobs = [make("j2-old", workspace, "lichess"), make("j2-actual", workspace, "lichess", "retryable"),
            make("j2-other-provider", workspace, "chesscom"), make("j2-other-workspace", workspace+"-other", "lichess")]
    with owned_postgres() as db:
        for job in jobs:
            db.execute_native("INSERT INTO product_feedback_jobs (job_id,version,status,next_attempt_at,lease_until,attempts,payload) VALUES(%s,0,%s,0,0,0,%s::jsonb)",
                              (job.spec.job_id, job.status, job.model_dump_json()))
        for ws, alias, provider, job_id in [(workspace,"a","lichess","j2-old"), (workspace,"z","lichess","j2-actual"),
                (workspace,"other","chesscom","j2-other-provider"), (workspace+"-other","z","lichess","j2-other-workspace")]:
            db.execute_native("INSERT INTO product_feedback_sync_requests (workspace_id,operation_id,provider,job_id,requested_at) VALUES(%s,%s,%s,%s,100)", (ws,alias,provider,job_id))
    try:
        assert store.last_job(workspace, "lichess") == jobs[1]
        assert store.last_job(workspace+"-other", "lichess") == jobs[3]
        assert store.last_job(workspace, "chesscom") == jobs[2]
        assert store.last_job(workspace+"-empty", "chesscom") is None
        for status in ("queued", "running", "complete", "retryable", "failed", "quarantined"):
            with owned_postgres() as db:
                db.execute_native("UPDATE product_feedback_jobs SET payload=jsonb_set(payload,'{status}',to_jsonb(%s::text)) WHERE job_id='j2-actual'", (status,))
            old = sync_status("lichess", store.last_job(workspace, "lichess"))
            assert sync_status("lichess", store.latest_job_status(workspace, "lichess")) == old
        with owned_postgres() as db:
            payload_bytes = db.execute_native("SELECT octet_length(payload::text) FROM product_feedback_jobs WHERE job_id='j2-actual'").fetchone()[0]
        captured.clear()
        monkeypatch.setattr(JobState, "model_validate_json", lambda *a, **k: pytest.fail("full job parsed on GET"))
        review = GameReview(workspace=workspace, store=store, learning=None, build_job=None, deliver=None, now=None)
        app = FastAPI()
        app.include_router(create_game_review_router(review))
        result = TestClient(app).get("/api/product/game-review/games")
        assert result.status_code == 200
        assert result.json()["sync"][0]["operation_id"] == "j2-actual"
        assert "PRIVATE-CONTINUATION-SENTINEL" not in result.text
        assert len(captured) == 2
        for sql, row in captured:
            assert len(json.dumps(tuple(row)).encode()) * 10 < payload_bytes
            assert "job.payload::text" not in sql
            assert "job.payload->'spec'->'job_id'" in sql and "job.payload ? 'status'" in sql
        with owned_postgres() as db:
            row = db.execute_native("SELECT payload->'spec'->'job_id',payload->'status',payload ? 'status',payload->'error' FROM product_feedback_jobs WHERE job_id='j2-actual'").fetchone()
        assert len(json.dumps(tuple(row)).encode()) * 10 < payload_bytes
        with owned_postgres() as db:
            db.execute_native("UPDATE product_feedback_jobs SET payload=payload-'status' WHERE job_id='j2-actual'")
        assert store.latest_job_status(workspace, "lichess").status == "queued"
        for expression in ("jsonb_set(payload,'{status}','null'::jsonb)", "jsonb_set(payload,'{status}','1'::jsonb)",
                           "jsonb_set(payload,'{status}', '\"unknown\"'::jsonb)", "payload #- '{spec,job_id}'"):
            with owned_postgres() as db:
                db.execute_native("UPDATE product_feedback_jobs SET payload=" + expression + " WHERE job_id='j2-actual'")
            with pytest.raises(ValidationError):
                store.latest_job_status(workspace, "lichess")
            with owned_postgres() as db:
                db.execute_native("UPDATE product_feedback_jobs SET payload=%s::jsonb WHERE job_id='j2-actual'", (jobs[1].model_dump_json(),))
    finally:
        with owned_postgres() as db:
            db.execute_native("DELETE FROM product_feedback_sync_requests WHERE workspace_id IN (%s,%s)", (workspace,workspace+"-other"))
            db.execute_native("DELETE FROM product_feedback_jobs WHERE job_id = ANY(%s)", ([job.spec.job_id for job in jobs],))
