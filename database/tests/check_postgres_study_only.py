"""Real migration, admission, foreground and receipt proof in an owned database."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))
from datetime import date
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app import main, tasks
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from app.command_gateway import execute_command, claim_recoverable_operation, read_operation
from app.services import durable_tasks, study_mode
from database.tooling.apply_postgres_migrations import MIGRATIONS, apply_migrations


def proof():
    if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Study-only proof requires an explicitly disposable PostgreSQL runner')
    parent_url = 'postgresql://postgres@postgres:5432/tempo'
    identity = 'tempo_study_mode_' + uuid4().hex
    owned_url = make_conninfo(parent_url, dbname=identity)
    created = False
    try:
        with psycopg.connect(parent_url, autocommit=True) as parent:
            parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(identity)))
            created = True
        with psycopg.connect(owned_url) as previous:
            for migration in sorted(MIGRATIONS.glob('*.sql')):
                if int(migration.name[:3]) <= 46:
                    previous.execute(migration.read_text(), prepare=False)
            previous.execute('INSERT INTO settings(id) VALUES(1)')
            settings_before = previous.execute('SELECT * FROM settings').fetchone()
        apply_migrations(owned_url)
        apply_migrations(owned_url)
        with psycopg.connect(owned_url) as upgraded:
            assert upgraded.execute('SELECT * FROM settings').fetchone() == (*settings_before, 0)
            assert upgraded.execute('SELECT COUNT(*),MAX(version) FROM tempo_schema_migrations').fetchone() == (POSTGRES_SCHEMA_VERSION, POSTGRES_SCHEMA_VERSION)
        print('PASS additive_migration_47_preserves_schema46_settings_and_replays')
        postgres_store.close_pools()
        # This exclusive database executes synchronously; its wake must not be
        # delivered to the parent runner's workers connected to another database.
        with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': owned_url, 'TEMPO_DATABASE_READ_URL': owned_url}), \
                patch('app.services.queue_refresh_wakeup.wake_queue_refresh'):
            def save_mode(enabled):
                settings = main.get_settings().model_dump(mode='json')
                settings['study_only_mode'] = enabled
                return execute_command(uuid4().hex, 'settings.update',
                    {'settings': settings, 'supplied_fields': sorted(settings)})

            assert save_mode(True)['study_only_mode'] is True
            postgres_store.close_pools()
            assert main.get_settings().study_only_mode
            omitted = main.get_settings().model_dump(mode='json')
            omitted.pop('study_only_mode')
            assert execute_command(uuid4().hex, 'settings.update',
                {'settings': omitted, 'supplied_fields': sorted(omitted)})['study_only_mode'] is True
            print('PASS study_only_toggle_persists_and_omitted_field_preserves_value')

            optional = durable_tasks.enqueue_task('opening_graph_rebuild', 'preserved-mode-work', {'cursor': 7})
            assert durable_tasks.claim_task(kind='opening_graph_rebuild') is None
            import_operation = uuid4().hex
            worker = SimpleNamespace(request=SimpleNamespace(headers={}))
            with patch.object(tasks, 'execute_command', side_effect=AssertionError('Paused import executed')):
                assert tasks._execute_foreground_command(worker, import_operation, 'imports.pgn.admit', {'saved_source': 'preserve'}) is None
            assert read_operation(import_operation)['state'] == 'retrying'
            accepted_operation = uuid4().hex
            with patch.object(tasks, 'execute_command', side_effect=AssertionError('Paused aggregation executed')):
                assert tasks.execute_background_command.run(accepted_operation, 'coverage.maia.submit',
                    {'accepted_report': {'preserve': True}}) is None
            postgres_store.close_pools()
            with postgres_store.connection() as database:
                assert database.execute('SELECT payload_json FROM operation_receipts WHERE operation_id=?',
                    (accepted_operation,)).fetchone()[0] == '{"accepted_report":{"preserve":true}}'
            assert claim_recoverable_operation(allowed_background_commands=study_mode.STUDY_BACKGROUND_COMMANDS,
                paused_commands=study_mode.PAUSED_IMPORT_COMMANDS) is None

            today = date.today().isoformat()
            with postgres_store.connection() as database:
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('mode-proof','Mode proof','synthetic',?)", (today,))
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
                    "VALUES('mode-line','mode-proof','Saved source','white',?,'[\"d2d4\"]',?)",
                    ('rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', today))
                entries = []
                for index, card_id in enumerate(('superseded', 'deleted', 'eligible-a', 'eligible-b')):
                    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,'mode-proof','prefix',?,'[\"e2e4\"]','learning',?,'tactic')",
                        (card_id, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', today))
                    entries.append(database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?) RETURNING id', (today, card_id, index)).fetchone()[0])
                database.execute("UPDATE cards SET superseded_by='eligible-a' WHERE id='superseded'")
                database.execute("INSERT INTO deleted_cards(card_id,deleted_at) VALUES('deleted',?)", (today,))
                database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(?,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (today,))
            from app.services.repertoire_move_verification import verify_repertoire_move
            source_fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
            assert verify_repertoire_move('mode-proof', source_fen, 'd2d4')['verified'] is True
            assert verify_repertoire_move('mode-proof', source_fen, 'c2c4')['verified'] is False
            postgres_store.close_pools()
            assert verify_repertoire_move('mode-proof', source_fen, 'd2d4')['verified'] is True
            print('PASS study_only_mode_verifies_non_expected_move_from_saved_sources_without_bulk_preparation')
            issued = execute_command(uuid4().hex, 'queue.issue_head',
                {'entry_id': entries[2], 'queue_date': today, 'card_id': 'eligible-a', 'expected_revision': 1})
            assert issued == {'issued': True}
            queue = main.queue_window(2)
            assert [card['id'] for card in queue['cards']] == ['eligible-a', 'eligible-b']
            assert queue['count'] == 2
            assert main.queue_entry_state(entries[2])['state'] == 'head'
            review_payload = {'card_id': 'eligible-a', 'review': {'outcome': 'correct',
                'queue_entry_id': entries[2], 'attempt_id': 'mode-proof-review', 'expected_revision': 1}}
            review_operation = uuid4().hex
            reviewed = execute_command(review_operation, 'cards.review', review_payload)
            assert reviewed['persisted'] is True
            postgres_store.close_pools()
            assert execute_command(review_operation, 'cards.review', review_payload) == reviewed
            assert execute_command(uuid4().hex, 'cards.review', review_payload) == reviewed
            assert execute_command(uuid4().hex, 'queue.issue_head',
                {'entry_id': entries[3], 'queue_date': today, 'card_id': 'eligible-b', 'expected_revision': 1})['issued']
            assert main.queue_window(1)['cards'][0]['id'] == 'eligible-b'
            with postgres_store.connection() as database:
                assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id='eligible-a'").fetchone()[0] == 1
                assert database.execute("SELECT COUNT(*) FROM review_attempt_receipts WHERE attempt_id='mode-proof-review'").fetchone()[0] == 1
                assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE id IN (?,?,?,?)', entries).fetchone()[0] == 4
                assert database.execute('SELECT COUNT(*) FROM queue_attempt_origins WHERE queue_entry_id IN (?,?,?,?)', entries).fetchone()[0] == 4
            print('PASS superseded_queue_head_does_not_block_eligible_successor')
            print('PASS deleted_queue_head_is_excluded_from_payload_and_count')
            print('PASS study_only_mode_preserves_foreground_review_and_queue_progression')
            print('PASS study_only_mode_preserves_receipts_and_idempotent_replay')
            save_mode(False)
            resumed = durable_tasks.claim_task(kind='opening_graph_rebuild')
            assert (resumed['id'], resumed['generation'], resumed['payload']) == (optional['id'], optional['generation'], {'cursor': 7})
            print('PASS study_only_mode_pauses_optional_work_and_resumes_after_restart')
    finally:
        postgres_store.close_pools()
        if created:
            with psycopg.connect(parent_url, autocommit=True) as parent:
                parent.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(identity)))


if __name__ == '__main__':
    proof()
