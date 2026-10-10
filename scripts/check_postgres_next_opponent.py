"""Issue #107: populated upgrade, real task restart, source races and foreground proof."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid
from unittest.mock import patch

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from scripts.apply_postgres_migrations import MIGRATIONS, apply_migrations
from app import postgres_store, tasks
from app.schema_version import POSTGRES_SCHEMA_VERSION
from app.services import durable_tasks
from app.services import postgres_next_opponent as service
from app.services import redis_admission_gate
from app.services.redis_admission_gate import foreground_lease, BackgroundAdmissionDeferred

ADMIN_DSN = os.getenv("TEMPO_PROFILE_REHEARSAL_ADMIN_DSN", "postgresql://postgres@postgres:5432/postgres")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


@contextmanager
def _owned_profile_admission():
    """Share only this helper's admission keys with its restart subprocesses."""
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Profile admission isolation requires a disposable instance")
    inherited_namespace = os.getenv("TEMPO_PROFILE_PROOF_ADMISSION_ID")
    namespace = inherited_namespace or "tempo:test:profile:" + uuid.uuid4().hex
    owned_keys = namespace + ":foreground", namespace + ":background"
    try:
        with patch.dict(os.environ, {"TEMPO_PROFILE_PROOF_ADMISSION_ID": namespace}), \
                patch.object(redis_admission_gate, "_FOREGROUND_KEY", owned_keys[0]), \
                patch.object(redis_admission_gate, "_BACKGROUND_KEY", owned_keys[1]):
            yield
    finally:
        if inherited_namespace is None:
            redis_admission_gate.client().delete(*owned_keys)


def child(mode):
    claimed = durable_tasks.claim_task(kind=service.TASK_KIND)
    assert claimed is not None
    if mode == "--interrupt":
        def interrupted(*args, **kwargs):
            os._exit(73)  # Actual process loss after the input connection closes.
        service.build_profile = interrupted
    assert service.execute_profile_slice(claimed)
    assert not service.execute_profile_slice(claimed)


def request():
    with postgres_store.connection() as database:
        return service.request_profile_refresh(database)


def read():
    with postgres_store.connection(read_only=True, repeatable_read=True) as database:
        return service.read_profile(database)


def seed_historical_timestamps(database):
    for identifier, timestamp in (
        ('offset', '2026-01-01T00:00:00-05:00'),
        ('naive', '2026-01-01T00:00:00'),
        ('malformed', 'not-a-timestamp'),
        ('calendar', '2026-02-30T00:00:00Z'),
        ('displacement', '2026-01-01T00:00:00+99:00'),
    ):
        database.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
            "start_fen,moves_json,player_rating,opponent_rating) "
            "VALUES(%s,'lichess','historical',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500)",
            (f'historical-{identifier}', timestamp, FEN))


def test_pr116_migration044_malformed_historical_timestamps_upgrade_idempotently(dsn):
    with psycopg.connect(dsn) as database:
        source_before = database.execute("SELECT * FROM imported_games ORDER BY id").fetchall()
    apply_migrations(dsn)
    with psycopg.connect(dsn) as database:
        database.execute("SET TIME ZONE 'America/New_York'")
        parsed = dict(database.execute(
            "SELECT id,next_opponent_game_time(played_at) FROM imported_games WHERE username='historical'"))
        assert parsed == {
            'historical-offset': datetime(2026, 1, 1, 5, tzinfo=timezone.utc),
            'historical-naive': datetime(2026, 1, 1, tzinfo=timezone.utc),
            'historical-malformed': None,
            'historical-calendar': None,
            'historical-displacement': None,
        }
        assert database.execute("SELECT next_opponent_game_time(NULL)").fetchone()[0] is None
        indexes = database.execute(
            "SELECT c.relname,i.indisvalid,i.indisready FROM pg_index i "
            "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname IN "
            "('idx_next_opponent_account_time','idx_next_opponent_speed_rating_time')").fetchall()
        assert len(indexes) == 2 and all(valid and ready for _, valid, ready in indexes)
        assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == source_before
        assert [row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
    apply_migrations(dsn)
    with psycopg.connect(dsn) as database:
        assert [row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
        assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == source_before
    print("PASS test_pr116_migration044_malformed_historical_timestamps_upgrade_idempotently")


def test_issue107_postgres_profile_upgrade_restart_concurrency_and_replay(dsn):
    request()
    interrupted = subprocess.run([sys.executable, __file__, "--interrupt"], timeout=30)
    assert interrupted.returncode == 73
    assert read().profile is None
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE kind=%s", (service.TASK_KIND,))
    subprocess.run([sys.executable, __file__, "--resume"], timeout=30, check=True)
    original = read()
    assert original.availability == "available" and original.profile.cohorts[1].player_rating == 1450
    assert not request()
    # Explicit equivalent retry changes operational source generation, not model identity.
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE next_opponent_accounts SET input_generation=input_generation+1 WHERE account='alice'")
    assert request()
    claimed = durable_tasks.claim_task(kind=service.TASK_KIND)
    assert service.execute_profile_slice(claimed)
    assert read().profile == original.profile and read().published_at == original.published_at
    with psycopg.connect(dsn) as database:
        assert database.execute("SELECT COUNT(*) FROM next_opponent_snapshots").fetchone()[0] == 1
        try:
            with database.transaction():
                database.execute("UPDATE next_opponent_snapshots SET published_at='altered'")
        except psycopg.errors.RaiseException:
            pass
        else:
            raise AssertionError("Immutable snapshots accepted an update")
    print("PASS test_issue107_postgres_profile_upgrade_restart_concurrency_and_replay")
    return original


def test_issue107_postgres_source_race_retains_last_complete_snapshot(dsn, original):
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE imported_games SET rating_change=20 WHERE id='profile-game'")
    request()
    claimed = durable_tasks.claim_task(kind=service.TASK_KIND)
    actual_build = service.build_profile
    def source_changes(*args, **kwargs):
        computed = actual_build(*args, **kwargs)
        with psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory) as database:
            database.execute("UPDATE imported_games SET rating_change=100 WHERE id='profile-game'")
            service.request_profile_refresh(postgres_store.PostgresConnection(database))
        return computed
    with patch.object(service, "build_profile", source_changes):
        assert not service.execute_profile_slice(claimed)
    assert read().profile == original.profile
    assert read().refresh_status == "pending"
    replacement = durable_tasks.claim_task(kind=service.TASK_KIND)
    assert service.execute_profile_slice(replacement)
    shifted = read().profile
    assert shifted.cohorts[1].player_rating == 1550 and shifted.version != original.profile.version
    print("PASS test_issue107_postgres_source_race_retains_last_complete_snapshot")
    return shifted


def test_issue107_postgres_foreground_contends_without_compute_transaction(dsn, shifted):
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE imported_games SET rating_change=110 WHERE id='profile-game'")
    request()
    claimed = durable_tasks.claim_task(kind=service.TASK_KIND)
    # A raced foreground arrival releases the lease promptly without computing
    # or consuming a failure attempt. Resume through the normal durable reader.
    with foreground_lease():
        assert not tasks._execute_claimed_background_slice(claimed, None)
        assert read().profile == shifted
        with psycopg.connect(dsn) as database:
            deferred = database.execute(
                "SELECT state,attempt_count,lease_token,payload_json FROM background_tasks WHERE id=%s",
                (claimed['id'],)).fetchone()
            assert deferred[:3] == ('retrying', 0, None)
            assert json.loads(deferred[3]) == claimed['payload']
    with psycopg.connect(dsn) as database:
        # Drive the existing one-second retry boundary with explicit persisted
        # fixture time rather than introducing wall-clock sleeps.
        database.execute("UPDATE background_tasks SET next_attempt_at='2000-01-01T00:00:00+00:00' WHERE id=%s",
                         (claimed['id'],))
    resumed_claim = durable_tasks.claim_task(kind=service.TASK_KIND)
    assert resumed_claim['id'] == claimed['id'] and resumed_claim['generation'] == claimed['generation']
    assert resumed_claim['lease_token'] != claimed['lease_token']
    compute_started = threading.Event()
    resume = threading.Event()
    result = []
    errors = []
    actual_build = service.build_profile
    def paused_compute(*args, **kwargs):
        compute_started.set()
        assert resume.wait(10)
        return actual_build(*args, **kwargs)
    def run():
        try:
            result.append(service.execute_profile_slice(resumed_claim))
        except BaseException as error:
            errors.append(error)
    with patch.object(service, "build_profile", paused_compute):
        worker = threading.Thread(target=run)
        worker.start()
        try:
            assert compute_started.wait(5)
            with foreground_lease(), psycopg.connect(dsn) as database:
                # Real foreground write succeeds while computation is suspended.
                database.execute("SET LOCAL lock_timeout='100ms'")
                database.execute("UPDATE settings SET lichess_username='bob' WHERE id=1")
                database.execute("UPDATE imported_games SET rating_change=120 WHERE id='profile-game'")
            resume.set()
            worker.join(10)
            assert not worker.is_alive() and errors == [] and result == [True]
            with psycopg.connect(dsn) as database:
                preserved = database.execute("SELECT profile_version FROM next_opponent_accounts WHERE account='alice'").fetchone()[0]
                assert preserved == shifted.version
                database.execute("UPDATE settings SET lichess_username='alice' WHERE id=1")
            request()
            subprocess.run([sys.executable, __file__, "--resume"], timeout=30, check=True)
            assert read().profile.cohorts[1].player_rating == 1570
        finally:
            resume.set()
            worker.join(10)
    print("PASS test_issue107_postgres_foreground_contends_without_compute_transaction")


def test_issue107_postgres_profile_reads_preserve_queue_and_exclusions(dsn):
    with psycopg.connect(dsn) as database:
        before = database.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0]
        database.execute("UPDATE imported_games SET adaptive_excluded=1 WHERE id='profile-game'")
    request()
    subprocess.run([sys.executable, __file__, "--resume"], timeout=30, check=True)
    postgres_store.close_pools()  # Recreate the service's database clients.
    assert read().availability == "unknown"
    with postgres_store.connection(read_only=True, repeatable_read=True) as database:
        response = service.read_profile(database, "blitz")
        assert response.effective_speed_mixture[0].speed == "blitz"
    with psycopg.connect(dsn) as database:
        assert database.execute("SELECT COUNT(*) FROM daily_queue").fetchone()[0] == before
        assert database.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    print("PASS test_issue107_postgres_profile_reads_preserve_queue_and_exclusions")


def test_pr116_future_evidence_becomes_eligible_at_successful_sync_without_source_mutation(dsn):
    from app.services.postgres_game_sync_completion import finish_game_sync_if_complete
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first_eligible = cutoff + timedelta(days=1)
    second_eligible = cutoff + timedelta(days=2)

    def account_state():
        with psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory) as database:
            return dict(database.execute("SELECT * FROM next_opponent_accounts WHERE account='future'").fetchone())

    def task_state():
        with psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory) as database:
            return dict(database.execute(
                "SELECT state,generation,payload_json FROM background_tasks WHERE kind=%s AND deduplication_key='future'",
                (service.TASK_KIND,)).fetchone())

    def successful_sync():
        job_id = str(uuid.uuid4())
        with postgres_store.connection() as database:
            database.execute(
                "INSERT INTO game_sync_jobs(id,request_json,status,created_at,updated_at) VALUES(?,?,'running',?,?)",
                (job_id, json.dumps({'lichess_username': 'future'}), cutoff.isoformat(), cutoff.isoformat()))
            database.execute(
                "INSERT INTO game_sync_windows(id,job_id,provider,window_kind,window_start_ms,window_end_ms,"
                "status,created_at,updated_at) VALUES(?,?,'lichess','games',0,1,'complete',?,?)",
                (str(uuid.uuid4()), job_id, cutoff.isoformat(), cutoff.isoformat()))
            assert finish_game_sync_if_complete(database, job_id)
            assert not finish_game_sync_if_complete(database, job_id)

    with psycopg.connect(dsn) as database:
        database.execute("UPDATE settings SET lichess_username='future' WHERE id=1")
        for identifier, played_at, speed in [('future-second', second_eligible, 'rapid'),
                                              ('future-first', first_eligible, 'bullet')]:
            database.execute(
                "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,"
                "moves_json,player_rating,opponent_rating,rating_change) "
                "VALUES(%s,'lichess','future',%s,%s,1,'white','1-0',%s,'[]',1450,1500,0)",
                (identifier, played_at.isoformat(), speed, FEN))
        earlier = (cutoff + timedelta(hours=1)).isoformat()
        for identifier, provider, account, rated, excluded, timestamp in [
            ('future-casual', 'lichess', 'future', 0, 0, earlier),
            ('future-excluded', 'lichess', 'future', 1, 1, earlier),
            ('future-other-account', 'lichess', 'other', 1, 0, earlier),
            ('future-other-provider', 'chess.com', 'future', 1, 0, earlier),
            ('future-malformed', 'lichess', 'future', 1, 0, '2026-02-30T00:00:00Z'),
        ]:
            database.execute(
                "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,adaptive_excluded,"
                "color,result,start_fen,moves_json,player_rating,opponent_rating) "
                "VALUES(%s,%s,%s,%s,'rapid',%s,%s,'white','1-0',%s,'[]',1450,1500)",
                (identifier, provider, account, timestamp, rated, excluded, FEN))
    source_generation = account_state()['input_generation']
    # Freeze only the profile cutoff; durable delivery keeps its normal lease clock.
    clock = {'now': cutoff}
    with patch.object(service, '_current_utc_time', lambda: clock['now']):
        assert request()
        assert service.execute_profile_slice(durable_tasks.claim_task(kind=service.TASK_KIND))
        original = read()
        assert original.profile.game_count == 0
        assert account_state()['next_evidence_at'] == first_eligible
        original_task = task_state()
        for _ in range(3):
            assert not request()
            successful_sync()
        assert task_state() == original_task
        assert read().profile == original.profile and read().published_at == original.published_at

        for eligible_at, expected_count in [(first_eligible, 1), (second_eligible, 2)]:
            clock['now'] = eligible_at
            assert read().refresh_status == 'pending'
            successful_sync()
            queued = task_state()
            assert queued['state'] == 'queued', 'Successful sync must refresh newly eligible evidence'
            assert json.loads(queued['payload_json'])['input_generation'] == source_generation
            assert not request()
            successful_sync()
            assert task_state() == queued
            claimed = durable_tasks.claim_task(kind=service.TASK_KIND)
            leased = task_state()
            assert leased['state'] == 'leased'
            assert not request()
            successful_sync()
            assert task_state() == leased
            assert service.execute_profile_slice(claimed)
            assert not service.execute_profile_slice(claimed)
            published = read()
            assert published.profile.game_count == expected_count
            assert published.profile.evidence_watermark == eligible_at.isoformat()
            assert published.profile.unsupported_speed_mass > 0
            assert published.availability == ('unsupported' if expected_count == 1 else 'available')
            assert account_state()['input_generation'] == source_generation
            assert account_state()['next_evidence_at'] == (second_eligible if expected_count == 1 else None)
            completed = task_state()
            successful_sync()
            assert not request() and task_state() == completed
            assert read().profile == published.profile and read().published_at == published.published_at

        # Equivalent forced publication reuses immutable semantic identity/time.
        with psycopg.connect(dsn) as database:
            database.execute("UPDATE next_opponent_accounts SET published_generation=NULL WHERE account='future'")
        clock['now'] += timedelta(days=1)
        assert request()
        assert service.execute_profile_slice(durable_tasks.claim_task(kind=service.TASK_KIND))
        assert read().profile == published.profile and read().published_at == published.published_at
        assert not request()
        with psycopg.connect(dsn) as database:
            assert database.execute("SELECT COUNT(*) FROM next_opponent_snapshots WHERE account='future'").fetchone()[0] == 3
    print("PASS test_pr116_future_evidence_becomes_eligible_at_successful_sync_without_source_mutation")


def test_pr116_far_future_timestamp_does_not_break_profile_deadline_publication(dsn):
    # PostgreSQL accepts this offset date as year 10000 UTC, beyond Python's
    # datetime range. Deadline handling must not deserialize it into datetime.
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE settings SET lichess_username='distant' WHERE id=1")
        database.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,"
            "moves_json,player_rating,opponent_rating,rating_change) "
            "VALUES('distant-game','lichess','distant','9999-12-31T23:59:59-01:00','rapid',1,"
            "'white','1-0',%s,'[]',1450,1500,0)", (FEN,))
    with patch.object(service, '_current_utc_time', lambda: datetime(2026, 1, 1, tzinfo=timezone.utc)):
        assert request()
        assert service.execute_profile_slice(durable_tasks.claim_task(kind=service.TASK_KIND))
        published = read()
        assert published.profile.game_count == 0 and published.refresh_status == 'idle'
        assert not request()
        assert read().profile == published.profile and read().published_at == published.published_at
    with psycopg.connect(dsn) as database:
        assert database.execute(
            "SELECT next_evidence_at::text FROM next_opponent_accounts WHERE account='distant'").fetchone()[0].startswith('10000-01-01')
    print("PASS test_pr116_far_future_timestamp_does_not_break_profile_deadline_publication")


def test_pr116_current_schema43_upgrade_preserves_published_migrations_and_sources():
    database_name = "tempo_profile_upgrade_" + uuid.uuid4().hex[:12]
    dsn = psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname=database_name)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    try:
        with psycopg.connect(dsn) as database:
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if int(migration.name[:3]) == POSTGRES_SCHEMA_VERSION:
                    break
                database.execute(migration.read_text(), prepare=False)
                database.commit()
            database.execute("INSERT INTO settings(id,lichess_username) VALUES(1,'historical')")
            seed_historical_timestamps(database)
            assert [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, POSTGRES_SCHEMA_VERSION))
            assert database.execute("SELECT to_regclass('next_opponent_accounts')").fetchone()[0] is None
            settings_before = database.execute("SELECT * FROM settings").fetchall()
            turns_before = database.execute("SELECT * FROM background_scheduling_turns").fetchall()
        test_pr116_migration044_malformed_historical_timestamps_upgrade_idempotently(dsn)
        with psycopg.connect(dsn) as database:
            assert database.execute("SELECT * FROM settings").fetchall() == settings_before
            assert database.execute("SELECT * FROM background_scheduling_turns").fetchall() == turns_before
        print("PASS test_pr116_current_schema43_upgrade_preserves_published_migrations_and_sources")
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


def test_pr116_concurrent_profile_refresh_requests_share_task_identity_and_generation(dsn):
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE settings SET lichess_username='concurrent' WHERE id=1")
        database.execute("INSERT INTO next_opponent_accounts(account) VALUES('concurrent')")
    ready = threading.Barrier(2)
    def concurrent_request():
        with psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory) as database:
            database.execute("SET LOCAL lock_timeout='2s'")
            ready.wait(timeout=5)
            return service.request_profile_refresh(postgres_store.PostgresConnection(database))
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(concurrent_request) for _ in range(2)]
        assert sorted(future.result(timeout=10) for future in futures) == [False, True]
    with psycopg.connect(dsn) as database:
        singleton = database.execute("SELECT id,generation,state FROM background_tasks "
            "WHERE kind=%s AND deduplication_key='concurrent'", (service.TASK_KIND,)).fetchall()
        assert len(singleton) == 1 and singleton[0][1:] == (1, 'queued')
        assert database.execute("SELECT COUNT(*) FROM background_task_events WHERE task_id=%s AND event='enqueued'",
                                (singleton[0][0],)).fetchone()[0] == 1
    claimed = durable_tasks.claim_task(allowed_kinds=(service.TASK_KIND,))
    assert claimed['id'] == singleton[0][0]
    assert service.execute_profile_slice(claimed)
    assert read().profile.source_account == 'concurrent'
    assert not request()
    print("PASS test_pr116_concurrent_profile_refresh_requests_share_task_identity_and_generation")


def test_pr116_older_cutoff_cannot_replace_newer_snapshot_or_future_deadline(dsn):
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE settings SET lichess_username='cutoff-replay' WHERE id=1")
        for index in (1, 2):
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
                "start_fen,moves_json,player_rating,opponent_rating) "
                "VALUES(%s,'lichess','cutoff-replay',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500)",
                (f'cutoff-game-{index}', (cutoff + timedelta(days=index)).isoformat(), FEN))
    with patch.object(service, '_current_utc_time', lambda: cutoff):
        assert request()
        older_task = durable_tasks.claim_task(kind=service.TASK_KIND)
        generation, records, ratings, deadline = service._load_inputs('cutoff-replay', cutoff)
        older_profile = service.build_profile('cutoff-replay', records, as_of=cutoff, rating_records=ratings)
        assert deadline is not None and older_profile.game_count == 0
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=%s",
                         (older_task['id'],))
    with patch.object(service, '_current_utc_time', lambda: cutoff + timedelta(days=3)):
        newer_task = durable_tasks.claim_task(kind=service.TASK_KIND)
        assert newer_task['generation'] == older_task['generation']
        assert newer_task['lease_token'] != older_task['lease_token']
        assert service.execute_profile_slice(newer_task)
        authoritative = read()
        assert authoritative.profile.game_count == 2
        with service.background_lease(), postgres_store.connection(background=True) as database:
            assert not service._publish(database, older_task, generation, older_profile, deadline)
        assert read() == authoritative
        with psycopg.connect(dsn) as database:
            assert database.execute("SELECT next_evidence_at FROM next_opponent_accounts "
                                    "WHERE account='cutoff-replay'").fetchone()[0] is None
            assert database.execute("SELECT COUNT(*) FROM next_opponent_snapshots "
                                    "WHERE account='cutoff-replay'").fetchone()[0] == 1
    print("PASS test_pr116_older_cutoff_cannot_replace_newer_snapshot_or_future_deadline")


def test_pr116_postgres_ancient_and_out_of_range_utc_dates_publish_without_crashing(dsn):
    with psycopg.connect(dsn) as database:
        database.execute("UPDATE settings SET lichess_username='ancient' WHERE id=1")
        for index, timestamp in enumerate(('0001-01-01T00:00:00+01:00', '0001-01-01T00:00:00Z')):
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
                "start_fen,moves_json,player_rating,opponent_rating) "
                "VALUES(%s,'lichess','ancient',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500)",
                (f'ancient-game-{index}', timestamp, FEN))
    assert request()
    assert service.execute_profile_slice(durable_tasks.claim_task(kind=service.TASK_KIND))
    response = read()
    assert response.profile.game_count == 1
    assert response.profile.evidence_watermark == '0001-01-01T00:00:00+00:00'
    assert response.stale and not request()
    print("PASS test_pr116_postgres_ancient_and_out_of_range_utc_dates_publish_without_crashing")


def _run_profile_proof():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Next-opponent proof requires a disposable PostgreSQL instance")
    # The maintenance container intentionally lacks application broker wiring.
    # This proof opts into its disposable sibling Redis for actual admission.
    os.environ.setdefault("TEMPO_REDIS_URL", "redis://redis:6379/0")
    if len(sys.argv) > 1:
        child(sys.argv[1])
        return
    database_name = f"tempo_profile_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    dsn = psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname=database_name)
    try:
        with psycopg.connect(dsn) as database:
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if int(migration.name[:3]) > 39:
                    break
                database.execute(migration.read_text(), prepare=False)
                database.commit()
            now = datetime.now(timezone.utc).isoformat()
            database.execute("INSERT INTO settings(id,lichess_username) VALUES(1,'Alice')")
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,player_rating,opponent_rating,rating_change) VALUES('profile-game','lichess','Alice',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500,0)", (now, FEN))
            database.execute("INSERT INTO game_sync_state(provider,username,status,last_success_at) VALUES('lichess','Alice','idle',%s)", (now,))
            seed_historical_timestamps(database)
        with psycopg.connect(dsn) as database:
            assert [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, 40))
            source_before = database.execute("SELECT * FROM imported_games ORDER BY id").fetchall()
            settings_before = database.execute("SELECT * FROM settings ORDER BY id").fetchall()
        test_pr116_migration044_malformed_historical_timestamps_upgrade_idempotently(dsn)
        with psycopg.connect(dsn) as database:
            assert [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
            assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == source_before
            assert database.execute("SELECT * FROM settings ORDER BY id").fetchall() == settings_before
            for schema_object in ('idx_opening_graph_current_roots', 'idx_opening_graph_mature_parent_candidates',
                                  'idx_cards_locked_openings', 'idx_cards_mature_parent',
                                  'next_opponent_accounts', 'next_opponent_snapshots'):
                assert database.execute('SELECT to_regclass(%s)', (schema_object,)).fetchone()[0] == schema_object
            assert {row[0] for row in database.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='background_tasks' "
                "AND column_name IN ('transaction_timeout_count','transaction_timeout_checkpoint')",
            )} == {'transaction_timeout_count', 'transaction_timeout_checkpoint'}
        print('PASS test_pr116_schema39_upgrade_preserves_sources_and_contiguous_queue_profile_history')
        test_pr116_current_schema43_upgrade_preserves_published_migrations_and_sources()
        with patch.dict(os.environ, {"TEMPO_DATABASE_WRITE_URL": dsn, "TEMPO_DATABASE_READ_URL": dsn}):
            original = test_issue107_postgres_profile_upgrade_restart_concurrency_and_replay(dsn)
            shifted = test_issue107_postgres_source_race_retains_last_complete_snapshot(dsn, original)
            test_issue107_postgres_foreground_contends_without_compute_transaction(dsn, shifted)
            test_issue107_postgres_profile_reads_preserve_queue_and_exclusions(dsn)
            test_pr116_future_evidence_becomes_eligible_at_successful_sync_without_source_mutation(dsn)
            test_pr116_far_future_timestamp_does_not_break_profile_deadline_publication(dsn)
            test_pr116_concurrent_profile_refresh_requests_share_task_identity_and_generation(dsn)
            test_pr116_older_cutoff_cannot_replace_newer_snapshot_or_future_deadline(dsn)
            test_pr116_postgres_ancient_and_out_of_range_utc_dates_publish_without_crashing(dsn)
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


def main():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Next-opponent proof requires a disposable PostgreSQL instance")
    os.environ.setdefault("TEMPO_REDIS_URL", "redis://redis:6379/0")
    with _owned_profile_admission():
        _run_profile_proof()


if __name__ == "__main__":
    main()
