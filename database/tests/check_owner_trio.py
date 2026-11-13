"""Fixed owned deletion, monotonic admission and broker wake proof selection."""
from contextlib import redirect_stdout
import io
import json
import os
import re
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'database')]


def validate_environment(environment):
    project = environment.get('TEMPO_TEST_COMPOSE_PROJECT', '')
    revision = environment.get('TEMPO_NATIVE_CANDIDATE_REVISION', '')
    if (environment.get('TEMPO_TEST_INSTANCE') != 'disposable'
            or not re.fullmatch(r'tempo-pg-regressions-[0-9]+-[a-f0-9]{8}', project)
            or not re.fullmatch(r'[a-f0-9]{40}', revision)
            or environment.get('TEMPO_REDIS_URL') != 'redis://redis:6379/0'
            or environment.get('PGPASSFILE') != '/run/secrets/admin_pgpass'
            or any(key.startswith('PG') and key not in {'PG_VERSION', 'PG_MAJOR', 'PGPASSFILE'} for key in environment)
            or any(environment.get(key) for key in ('TEMPO_DATABASE_READ_URL', 'TEMPO_DATABASE_WRITE_URL'))):
        raise RuntimeError('Refusing non-owned owner-trio environment')
    return project, revision


def main():
    project, revision = validate_environment(os.environ)
    import psycopg
    from app.services import redis_admission_gate
    from database.tests.check_postgres_deletion import main_check
    from database.tests.check_postgres_study_priority import assert_monotonic_activity_renewal
    from database.tests.redis.check_redis_background_wakes import proof_background_wakes

    with psycopg.connect('postgresql://postgres@postgres:5432/tempo', connect_timeout=5) as db:
        row = db.execute("SELECT current_database(), shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
        if row != ('tempo', 'tempo-disposable-postgres-test'):
            raise RuntimeError('Refusing unmarked disposable PostgreSQL')
    completed = []
    captured = io.StringIO()
    with redirect_stdout(captured):
        main_check()
    expected_deletion = {
        'test_postgres_tactical_deletion_batches_and_publication_race',
        'test_postgres_repertoire_delete_reassigns_both_shared_admission_scopes_without_rewriting_history',
        'test_deleted_repeated_route_prerequisite_reparents_each_occurrence_without_self_dependency',
        'test_repertoire_delete_ignores_unrelated_null_job_payload',
        'test_repertoire_keep_delete_preserves_shared_schedules_history_and_non_main_selection',
        'test_retained_introduced_cards_survive_queue_refresh_and_next_day_without_a_review',
        'test_permanent_card_deletion_foreground_contention_yields_and_replays_idempotently',
        'test_permanent_card_delete_global_history_rebuild_restart_descendant_usability_and_delayed_review',
        'test_import_does_not_report_deleted_content_as_created_or_shared',
    }
    reported = re.findall(r'^PASS (test_[a-z0-9_]+)', captured.getvalue(), re.MULTILINE)
    if len(reported) != len(expected_deletion) or set(reported) != expected_deletion:
        raise RuntimeError('Deletion proof did not report its nine original cases')
    completed.append('deletion')
    server = redis_admission_gate.client()
    result = assert_monotonic_activity_renewal(server, f'tempo:test:owner-trio:{uuid4().hex}')
    if result.get('name') != 'test_postgres_priority_monotonic_activity_renewal' or not result.get('shorter_pulses_preserve_held_and_pending_leases'):
        raise RuntimeError('Monotonic renewal proof did not complete')
    completed.append('monotonic_renewal')
    captured = io.StringIO()
    with redirect_stdout(captured):
        proof_background_wakes()
    wake = json.loads(captured.getvalue().strip())
    if wake.get('test') != 'test_real_broker_wakes_coalesce_restart_and_fence_delivery':
        raise RuntimeError('Broker wake proof did not complete')
    completed.append('broker_wakes')
    print(json.dumps({'project': project, 'revision': revision, 'completed': completed, 'cleanup': 'completed'}))


if __name__ == '__main__':
    main()
