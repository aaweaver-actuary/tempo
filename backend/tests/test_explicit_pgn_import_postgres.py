"""One native explicit-import round trip using the existing capped owner fixture.

Eventual command (requires separate fixture authority):
  make python-file FILE=backend/tests/test_explicit_pgn_import_postgres.py

The reused owned_postgres fixture creates its own PostgreSQL18/schema51 database,
credential and container when executed. Collection does not run that fixture.
Only the borrowed database transport, broker wakes, Redis lease transport and
policy day are substituted. Receipt, source, claim, graph, integrity and normal
Study queue SQL/handlers remain real. This is not Redis/worker-stack qualification
or acceptance of Andy's genuine Taimanov file.
"""
from contextlib import contextmanager, nullcontext
from datetime import date
import json
from uuid import uuid4

from test_product_game_lapse import NOW
from test_product_game_lapse_postgres import case, owned_postgres  # noqa: F401 - existing pytest fixtures


BLACK_PGN = ('[Event "Synthetic Black branching import"]\n\n'
             '1. e4 c5 2. Nf3 (2. Nc3 e6) e6 3. d4 cxd4 4. Nxd4 Nc6 *')


def _protected_state(database, card_id, issue_operation):
    """Exact retained learning/history/issued-state readback, not a fake store."""
    return {
        'card': dict(database.execute_native('SELECT * FROM cards WHERE id=%s', (card_id,)).fetchone()),
        'reviews': [dict(row) for row in database.execute_native(
            'SELECT * FROM reviews WHERE card_id=%s ORDER BY id', (card_id,)).fetchall()],
        'feedback': [dict(row) for row in database.execute_native(
            'SELECT * FROM study_game_feedback WHERE card_id=%s ORDER BY effect_key', (card_id,)).fetchall()],
        'queue': [dict(row) for row in database.execute_native(
            'SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id', (card_id,)).fetchall()],
        'origins': [dict(row) for row in database.execute_native(
            'SELECT * FROM queue_attempt_origins WHERE card_id=%s ORDER BY queue_entry_id,revision',
            (card_id,)).fetchall()],
        'receipt': dict(database.execute_native(
            'SELECT * FROM operation_receipts WHERE operation_id=%s', (issue_operation,)).fetchone()),
    }


def test_native_explicit_black_import_round_trip_preserves_study(case, monkeypatch):
    from app import main, pgn_import_commands, queue_commands, tasks
    from app.chess.pgn import parse_pgn
    from app.command_gateway import execute_command, read_operation, request_digest
    from app.services import durable_tasks, postgres_integrity, postgres_opening_graph, redis_admission_gate, study_mode
    from app.services.background_activity import set_control_in_transaction
    from tempo_legacy_database.adapters import postgres_store

    @contextmanager
    def transport(*, read_only=False, **_options):
        with case.transaction() as database:
            if read_only:
                database.execute_native('SET TRANSACTION READ ONLY')
            yield database

    class PolicyDate(date):
        @classmethod
        def today(cls):
            return NOW.date()

    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(postgres_store, 'connection', transport)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    # The native fixture has no broker/Redis. Keep domain execution real while
    # manually driving the already-existing durable slice entry point below.
    monkeypatch.setattr(postgres_opening_graph, 'background_lease', nullcontext)
    monkeypatch.setattr(postgres_integrity, 'background_lease', nullcontext)
    monkeypatch.setattr(tasks.celery_app, 'send_task', lambda *a, **k: None)
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(pgn_import_commands, 'date', PolicyDate)
    monkeypatch.setattr(queue_commands, 'date', PolicyDate)
    monkeypatch.setattr(main, 'date', PolicyDate)
    day = NOW.date().isoformat()
    issue_operation = 'protected-issue-' + uuid4().hex
    with case.transaction() as database:
        database.execute_native('UPDATE settings SET study_only_mode=1,new_cards_per_day=0,timezone=%s WHERE id=1', ('UTC',))
        database.execute_native('INSERT INTO tactic_rotation(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        database.execute_native("UPDATE repertoires SET is_main=1 WHERE id='white'")
        database.execute_native("UPDATE cards SET trained_color='white' WHERE id=%s", (case.card_id,))
        # Existing T06 owner creates a real accepted effect/review/priority row.
        assert case.service(database).accept_lapse(case.lapse).status == 'applied'
        entry = database.execute_native(
            "SELECT id FROM daily_queue WHERE card_id=%s AND queue_date=%s AND status='queued'",
            (case.card_id, day)).fetchone()[0]
    assert execute_command(issue_operation, 'queue.issue_head', dict(
        entry_id=entry, card_id=case.card_id, expected_revision=1, queue_date=day)) == {'issued': True}
    with case.transaction() as database:
        before = _protected_state(database, case.card_id, issue_operation)
        assert before['origins'] and before['origins'][0]['issued_as_head'] == 1
        assert len(before['reviews']) == 2 and before['feedback'][0]['status'] == 'applied'
        main_before = tuple(database.execute_native(
            "SELECT id,is_main,name,source_name FROM repertoires WHERE id='white'").fetchone())
    assert study_mode.study_only_enabled() is True
    optional = durable_tasks.enqueue_task('opening_graph_rebuild', 'unrelated-' + uuid4().hex, {'cursor': 7})

    operation = 'explicit-import-' + uuid4().hex
    source_name = operation + '.pgn'
    games, lines = parse_pgn(BLACK_PGN)
    assert games == 1 and len(lines) == 2
    payload = pgn_import_commands.prepare_import_payload(source_name, 'black', 2, games, lines)
    payload['operation_id'] = operation
    saved = tasks.execute_foreground_command.run(operation, 'imports.pgn.explicit', payload)
    assert saved is not None, read_operation(operation)
    assert saved['preparation_state'] == 'pending' and saved['graph_state'] == 'refreshing'
    assert saved['cards_admitted_today'] == 0
    assert saved['cards_created'] == len(payload['segment_ids']) > 0
    repertoire_id = saved['repertoire_id']
    receipt = read_operation(operation, command_name='imports.pgn.explicit',
                             request_hash=request_digest('imports.pgn.explicit', payload))
    assert receipt['state'] == 'complete' and receipt['response'] == saved
    graph_id = saved['preparation']['graph_task_id']
    graph_generation = saved['preparation']['graph_generation']
    pending = next(row for row in main.list_repertoires()['repertoires'] if row['id'] == repertoire_id)
    assert pending['graph_state'] == 'refreshing' and pending['card_count'] == 0
    with case.transaction() as database:
        graph = database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (graph_id,)).fetchone()
        assert graph['generation'] == graph_generation and graph['state'] == 'queued'
        assert json.loads(graph['payload_json'])['origin_import_operation_id'] == operation
        stored = database.execute_native(
            'SELECT moves_json,trained_color FROM repertoire_lines WHERE repertoire_id=%s', (repertoire_id,)).fetchall()
        assert {tuple(json.loads(row['moves_json'])) for row in stored} == {tuple(line.moves) for line in lines}
        assert {row['trained_color'] for row in stored} == {'black'}
        assert set_control_in_transaction(database, 'durable', graph_id, 'pause')
    assert durable_tasks.claim_task(explicit_imports_only=True) is None
    with case.transaction() as database:
        assert set_control_in_transaction(database, 'durable', graph_id, 'resume')

    # Public foreground retry resolves its original receipt and cannot enqueue
    # another graph generation or create another source/review.
    assert tasks.execute_foreground_command.run(operation, 'imports.pgn.explicit', payload) is None
    assert execute_command(operation, 'imports.pgn.explicit', payload) == saved
    seen_kinds = set()
    for _ in range(256):
        task = durable_tasks.claim_task(explicit_imports_only=True)
        if task is None:
            break
        assert task['kind'] in {'opening_graph_rebuild', 'integrity_scan'}
        assert task['payload']['origin_import_operation_id'] == operation
        seen_kinds.add(task['kind'])
        tasks.execute_background_slice.run(task)
    else:
        raise AssertionError('Small explicit import did not finish within 256 existing owner slices')
    assert seen_kinds == {'opening_graph_rebuild', 'integrity_scan'}
    for _ in range(256):
        task = durable_tasks.claim_task(allowed_kinds=study_mode.STUDY_TASK_KINDS)
        if task is None:
            break
        assert task['kind'] == 'daily_queue'
        tasks.execute_background_slice.run(task)
    else:
        raise AssertionError('Normal Study publication did not finish within 256 existing owner slices')

    ready = next(row for row in main.list_repertoires()['repertoires'] if row['id'] == repertoire_id)
    assert ready['graph_state'] == 'ready' and ready['graph_updated_at']
    assert ready['integrity_status'] == 'clean' and ready['integrity_scan_status'] == 'idle'
    assert ready['graph_generation'] == graph_generation and ready['due_count'] == 0
    assert ready['card_count'] == len(payload['segment_ids'])
    # The immutable save receipt stays truthful about its original pending
    # checkpoint; current owner projections establish readiness separately.
    assert execute_command(operation, 'imports.pgn.explicit', payload) == saved
    assert read_operation(operation)['response'] == saved
    with case.transaction() as database:
        assert tuple(database.execute_native(
            'SELECT study_only_mode,new_cards_per_day FROM settings WHERE id=1').fetchone()) == (1, 0)
        assert tuple(database.execute_native(
            "SELECT id,is_main,name,source_name FROM repertoires WHERE id='white'").fetchone()) == main_before
        assert database.execute_native('SELECT is_main FROM repertoires WHERE id=%s', (repertoire_id,)).fetchone()[0] == 0
        assert _protected_state(database, case.card_id, issue_operation) == before
        assert tuple(database.execute_native('SELECT state,generation FROM background_tasks WHERE id=%s',
            (graph_id,)).fetchone()) == ('complete', graph_generation)
        child = database.execute_native("SELECT state,payload_json FROM background_tasks WHERE kind='integrity_scan' AND deduplication_key=%s",
            (repertoire_id,)).fetchone()
        assert child['state'] == 'complete'
        assert json.loads(child['payload_json'])['origin_graph_task_id'] == graph_id
        assert json.loads(child['payload_json'])['graph_generation'] == graph_generation
        assert database.execute_native('SELECT COUNT(*) FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()[0] == 1
        assert database.execute_native('SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id=%s', (repertoire_id,)).fetchone()[0] == 2
        assert database.execute_native('SELECT COUNT(*) FROM cards WHERE repertoire_id=%s AND introduced_at IS NOT NULL',
            (repertoire_id,)).fetchone()[0] == 0
        published_ids = {row[0] for row in database.execute_native(
            'SELECT card_id FROM repertoire_cards WHERE repertoire_id=%s', (repertoire_id,)).fetchall()}
        assert published_ids == set(payload['segment_ids'])
        assert tuple(database.execute_native('SELECT state,refresh_pending FROM queue_projections WHERE queue_date=%s',
            (day,)).fetchone()) == ('ready', 0)
        unchanged_optional = database.execute_native('SELECT state,payload_json FROM background_tasks WHERE id=%s',
            (optional['id'],)).fetchone()
        assert unchanged_optional['state'] == 'queued' and json.loads(unchanged_optional['payload_json']) == {'cursor': 7}

    # A failure after the real source/task writes must roll those effects back
    # together, retaining only its failed command receipt.
    failed_operation = 'rollback-import-' + uuid4().hex
    failed_payload = {**payload, 'source_name': failed_operation + '.pgn', 'operation_id': failed_operation}
    original_request = pgn_import_commands.request_graph_rebuild_in_transaction
    with monkeypatch.context() as failure:
        def after_real_graph(*args, **kwargs):
            original_request(*args, **kwargs)
            raise RuntimeError('Synthetic failure after graph enqueue')
        failure.setattr(pgn_import_commands, 'request_graph_rebuild_in_transaction', after_real_graph)
        assert tasks.execute_foreground_command.run(failed_operation, 'imports.pgn.explicit', failed_payload) is None
    assert read_operation(failed_operation)['state'] == 'failed'
    with case.transaction() as database:
        assert database.execute_native('SELECT COUNT(*) FROM repertoires WHERE source_name=%s',
            (failed_payload['source_name'],)).fetchone()[0] == 0
        assert database.execute_native('SELECT COUNT(*) FROM repertoire_lines WHERE name=%s',
            (failed_payload['source_name'],)).fetchone()[0] == 0
        assert database.execute_native("SELECT COUNT(*) FROM background_tasks WHERE payload_json::jsonb->>'origin_import_operation_id'=%s",
            (failed_operation,)).fetchone()[0] == 0
        assert _protected_state(database, case.card_id, issue_operation) == before
    assert study_mode.study_only_enabled() is True
