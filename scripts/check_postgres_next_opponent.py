"""Issue #107: populated upgrade, real task restart, source races and foreground proof."""
from contextlib import contextmanager
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
from app import postgres_store
from app.services import durable_tasks
from app.services import postgres_next_opponent as service
from app.services.redis_admission_gate import foreground_lease

ADMIN_DSN = os.getenv("TEMPO_PROFILE_REHEARSAL_ADMIN_DSN", "postgresql://postgres@postgres:5432/postgres")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


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


def test_pr116_migration041_malformed_historical_timestamps_upgrade_idempotently(dsn):
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
            "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, 42))
    apply_migrations(dsn)
    with psycopg.connect(dsn) as database:
        assert [row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, 42))
        assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == source_before
    print("PASS test_pr116_migration041_malformed_historical_timestamps_upgrade_idempotently")


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
    admission_started = threading.Event()
    compute_started = threading.Event()
    resume = threading.Event()
    result = []
    errors = []
    actual_lease = service.background_lease
    actual_build = service.build_profile
    @contextmanager
    def observed_admission():
        admission_started.set()
        with actual_lease():
            yield
    def paused_compute(*args, **kwargs):
        compute_started.set()
        assert resume.wait(10)
        return actual_build(*args, **kwargs)
    def run():
        try:
            result.append(service.execute_profile_slice(claimed))
        except BaseException as error:
            errors.append(error)
    with patch.object(service, "background_lease", observed_admission), patch.object(service, "build_profile", paused_compute):
        with foreground_lease():
            worker = threading.Thread(target=run)
            worker.start()
            assert admission_started.wait(5)
            assert not compute_started.is_set()
            assert read().profile == shifted
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
        for identifier, played_at in [('future-first', first_eligible), ('future-second', second_eligible)]:
            database.execute(
                "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,"
                "moves_json,player_rating,opponent_rating,rating_change) "
                "VALUES(%s,'lichess','future',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500,0)",
                (identifier, played_at.isoformat(), FEN))
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
            assert published.profile.cohorts[1].latest_game_at == eligible_at.isoformat()
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


def main():
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
        with psycopg.connect(dsn) as database:
            assert [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, 40))
            source_before = database.execute("SELECT * FROM imported_games ORDER BY id").fetchall()
            settings_before = database.execute("SELECT * FROM settings ORDER BY id").fetchall()
        test_pr116_migration041_malformed_historical_timestamps_upgrade_idempotently(dsn)
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
        with patch.dict(os.environ, {"TEMPO_DATABASE_WRITE_URL": dsn, "TEMPO_DATABASE_READ_URL": dsn}):
            original = test_issue107_postgres_profile_upgrade_restart_concurrency_and_replay(dsn)
            shifted = test_issue107_postgres_source_race_retains_last_complete_snapshot(dsn, original)
            test_issue107_postgres_foreground_contends_without_compute_transaction(dsn, shifted)
            test_issue107_postgres_profile_reads_preserve_queue_and_exclusions(dsn)
            test_pr116_future_evidence_becomes_eligible_at_successful_sync_without_source_mutation(dsn)
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database_name)))


if __name__ == "__main__":
    main()
