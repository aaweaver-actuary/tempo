"""P05: actual issued position review and transport receipt share one transaction.

Selected only through the marked Compose-owned position native bridge. This is
not a browser or installed-service acceptance test.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
import re

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from tempo_storage.postgres import PostgresConnection, tempo_row_factory


def test_p05_real_issued_review_transport_effect_and_checkpoint_rollback(monkeypatch):
    dsn = os.environ.get('TEMPO_POSITION_TEST_DSN', '')
    project = os.environ.get('TEMPO_TEST_COMPOSE_PROJECT', '')
    params = conninfo_to_dict(dsn)
    assert os.environ.get('TEMPO_TEST_INSTANCE') == 'disposable'
    assert re.fullmatch(r'tempo-pg-regressions-[0-9]+-[a-f0-9]{8}', project)
    assert params.get('host') == 'postgres' and re.fullmatch(r'tempo_position_test_[a-f0-9]{32}', params.get('dbname', ''))
    with psycopg.connect(dsn) as guard:
        row = guard.execute("SELECT current_database(), shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
        assert row == (params['dbname'], project), 'native database is not owned by this invocation'

    @contextmanager
    def reader(*_args, **_kwargs):
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as connection:
            connection.execute('SET TRANSACTION READ ONLY')
            yield PostgresConnection(connection, read_only=True)

    @contextmanager
    def writer(_background):
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as connection:
            yield PostgresConnection(connection)

    def review_journey(raw, _port, _dsn, scoped, plan, entry, card_id, original_fsrs):
        from app import command_gateway, review_commands  # registers the actual handlers
        from app.contracts.opening_evidence import OpeningEvidenceCheckpoint
        from app.services.opening_decision_evidence import decision_manifest
        from app.services import queue_refresh_wakeup
        from app.study import position_checkpoint as evidence
        from tempo_storage.commands.receipts import storage as receipt_storage

        scoped.setattr(evidence, 'read_connection', reader)
        scoped.setattr(command_gateway, '_writer_connection', writer)
        scoped.setattr(queue_refresh_wakeup, 'wake_queue_refresh', lambda: None)
        scope = raw.execute('''SELECT snapshot.*, context.effective_trained_color
            FROM opening_evidence_presentations snapshot JOIN opening_evidence_queue_contexts context
              ON context.presentation_snapshot_id=snapshot.id
            WHERE context.queue_entry_id=%s AND context.repertoire_id='n2' ''', (entry,)).fetchone()
        assert scope is not None
        manifest = evidence.issued_position_manifest(plan, decision_manifest(evidence._manifest_snapshot(dict(scope)), 'n2'))
        assert len(manifest['decisions']) >= 2
        now = datetime.now(timezone.utc).isoformat()
        events = [dict(sequence=i + 1, decision_index=i, decision_id=decision['decision_id'],
                       expected_uci=decision['expected_uci'], kind='first_response', observed_at=now,
                       response_uci=decision['expected_uci'], disposition='expected')
                  for i, decision in enumerate(manifest['decisions'])]
        checkpoint = dict(attempt_id=plan.attempt_id, manifest=manifest, origin_queue_entry_id=entry,
                          queue_entry_id=entry, started_at=now, study_timezone='UTC', source='live', events=events,
                          terminal={'state': 'complete', 'final_sequence': len(events), 'ended_at': now})
        prepared_manifest = evidence.prepare_checkpoint(OpeningEvidenceCheckpoint.model_validate(checkpoint))
        payload = {'card_id': card_id, 'review': {'outcome': 'correct', 'guided': False,
                   'queue_entry_id': entry, 'expected_revision': plan.original_card_revision,
                   'attempt_id': plan.attempt_id, 'recorded_at': now, 'opening_evidence_completion': checkpoint},
                   'prepared_manifest': prepared_manifest}
        operation = 'p05-position-review:' + plan.attempt_id
        raw.commit()

        def snapshot():
            statements = {
                'plan': ('SELECT * FROM study_position_issued_plans WHERE attempt_id=%s', plan.attempt_id),
                'origin': ('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s', entry),
                'card': ('SELECT * FROM cards WHERE id=%s', card_id),
                'queue': ('SELECT * FROM daily_queue WHERE card_id=%s ORDER BY id', card_id),
                'reviews': ('SELECT * FROM reviews WHERE card_id=%s ORDER BY id', card_id),
                'logical_receipt': ('SELECT * FROM review_attempt_receipts WHERE attempt_id=%s', plan.attempt_id),
                'checkpoint': ('SELECT * FROM opening_evidence_attempts WHERE attempt_id=%s', plan.attempt_id),
                'events': ('SELECT * FROM opening_evidence_events WHERE attempt_id=%s ORDER BY sequence', plan.attempt_id),
                'observations': ('SELECT * FROM opening_evidence_observations WHERE attempt_id=%s ORDER BY decision_index', plan.attempt_id),
                'summaries': ('SELECT * FROM opening_evidence_summaries WHERE decision_id IN (SELECT decision_id FROM opening_evidence_observations WHERE attempt_id=%s) ORDER BY decision_id', plan.attempt_id),
                'clean_days': ('SELECT * FROM opening_evidence_clean_days WHERE decision_id IN (SELECT decision_id FROM opening_evidence_observations WHERE attempt_id=%s) ORDER BY decision_id,study_day', plan.attempt_id),
                'responses': ('SELECT * FROM study_position_first_responses WHERE attempt_id=%s ORDER BY decision_index', plan.attempt_id),
                'grades': ('SELECT * FROM study_position_grades WHERE attempt_id=%s ORDER BY effect_id', plan.attempt_id),
                'states': ("SELECT * FROM study_position_states WHERE repertoire_id=%s ORDER BY variant,position", 'n2'),
                'operation': ('SELECT * FROM operation_receipts WHERE operation_id=%s', operation),
            }
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as connection:
                return {name: [dict(row) for row in connection.execute(sql, (value,)).fetchall()]
                        for name, (sql, value) in statements.items()}

        before = snapshot()
        assert len(before['plan']) == len(before['origin']) == 1
        assert before['origin'][0]['issued_as_head'] == 1 and before['card'][0]['fsrs_card_json'] == original_fsrs
        assert all(not before[name] for name in ('reviews', 'logical_receipt', 'checkpoint', 'events', 'observations', 'summaries', 'clean_days', 'responses', 'grades', 'operation'))
        complete = receipt_storage.complete_operation
        def fail_after_transport_write(receipt_writer, response_json, operation_id):
            complete(receipt_writer, response_json, operation_id)
            transaction = receipt_storage._receipt_connection(receipt_writer)
            for table, key, value in (
                ('reviews', 'card_id', card_id),
                ('review_attempt_receipts', 'attempt_id', plan.attempt_id),
                ('opening_evidence_attempts', 'attempt_id', plan.attempt_id),
                ('study_position_first_responses', 'attempt_id', plan.attempt_id),
                ('study_position_grades', 'attempt_id', plan.attempt_id),
                ('operation_receipts', 'operation_id', operation),
            ):
                assert transaction.execute(f'SELECT count(*) FROM {table} WHERE {key}=%s', (value,)).fetchone()[0] > 0, table
            raise RuntimeError('p05 injected after transport receipt')
        with scoped.context() as failure:
            failure.setattr(receipt_storage, 'complete_operation', fail_after_transport_write)
            with pytest.raises(RuntimeError, match='p05 injected after transport receipt'):
                command_gateway.execute_command(operation, 'cards.review', payload)
        assert snapshot() == before, 'effect, checkpoint, logical and transport receipts must roll back together'

        result = command_gateway.execute_command(operation, 'cards.review', payload)
        assert result and result['persisted'] and result['review_id']
        after = snapshot()
        assert len(after['reviews']) == len(after['logical_receipt']) == len(after['checkpoint']) == len(after['operation']) == 1
        assert after['logical_receipt'][0]['attempt_id'] == plan.attempt_id
        assert after['logical_receipt'][0]['review_id'] == result['review_id']
        assert after['operation'][0]['state'] == 'complete' and json.loads(after['operation'][0]['response_json']) == result
        assert len(after['responses']) == len(events) and len(after['grades']) == len(events)
        assert all(row['attempt_id'] == plan.attempt_id for row in after['responses'] + after['grades'])
        assert after['card'][0]['fsrs_card_json'] == original_fsrs
        assert command_gateway.execute_command(operation, 'cards.review', payload) == result
        assert snapshot() == after, 'same original transport replay changed accepted facts'

    # Only prerequisite source and schedule state are synthetic. The current
    # source owner, due admission, head preparation and issuer create the plan.
    from fsrs import Card
    import chess
    from tooling import apply_postgres_migrations as migrations
    from tempo_evidence_math.position_learning import Hold
    from tempo_storage.authored_repertoires.position_study_source import fence_position_source
    from app.repertoire.position_study_source import prepare_position_source
    from app.services.position_queue_admission import prepare_due, admit_due
    from app.services.queue_next_card import prepare_queue_head
    from app.queue_commands import current_study_day, issue_queue_head
    from tempo_storage import connections

    with psycopg.connect(dsn) as empty:
        assert empty.execute("SELECT count(*) FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p','v','S')").fetchone()[0] == 0
    migrations.apply_migrations(dsn)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    monkeypatch.setattr(connections, 'read_connection', reader)
    now = datetime.now(timezone.utc)
    fen = chess.STARTING_FEN
    moves = json.dumps(['e2e4', 'e7e5', 'g1f3'])
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw:
        raw.execute('INSERT INTO settings(id) VALUES(1)')
        day = current_study_day(PostgresConnection(raw), now=now)
        raw.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('n2','P05 position','fixture',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('n2',3,'ready',%s)", (now.isoformat(),))
        raw.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('route','n2','Legal route','white',%s,%s,%s)", (fen, moves, now.isoformat()))
        physical_fsrs = Card(card_id=42, due=now-timedelta(days=1)).to_json()
        raw.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at,revision,content_type,trained_color)
            VALUES('card','n2','prefix',%s,%s,'learning',%s,%s,%s,1,'opening','white')""", (fen, moves, day, physical_fsrs, (now-timedelta(days=2)).isoformat()))
        raw.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('n2','card')")
        raw.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES('n2',3,'route',0,'card',%s,%s,%s,'white')""", (fen, fen, moves))
        raw.execute("""INSERT INTO repertoire_integrity_state(repertoire_id,status,scan_status,scan_generation,checked_at,scan_completed_sources,scan_total_sources)
            VALUES('n2','clean','idle','scan:2',%s,1,1)""", (now.isoformat(),))
        for task_id, kind, generation, payload in [('graph', 'opening_graph_rebuild', 3, {}),
                ('scan', 'integrity_scan', 2, {'repertoire_id': 'n2', 'graph_generation': 3})]:
            raw.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,payload_json,next_attempt_at,created_at,updated_at)
                VALUES(%s,%s,'n2',%s,'complete',%s,%s,%s,%s)""", (task_id, kind, generation, json.dumps(payload), now.isoformat(), now.isoformat(), now.isoformat()))
        raw.commit()
        port = PostgresConnection(raw)
        prepared = prepare_position_source(port, 'n2')
        assert not isinstance(prepared, Hold), prepared
        assert fence_position_source(port, prepared) is None
        digest = prepared.authored_token.authored_digest
        raw.execute("INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES('n2','p05-epoch',%s,%s)", (digest, now))
        raw.execute("INSERT INTO study_position_owners(repertoire_id,epoch,mode,updated_at) VALUES('n2','p05-epoch','active',%s)", (now,))
        definitions = {decision.definition.key: decision.definition for route in prepared.routes for decision in route.decisions}
        assert len(definitions) >= 2
        for definition in definitions.values():
            internal_id = raw.execute("SELECT nextval('study_position_fsrs_id_seq')").fetchone()[0]
            state = Card(card_id=internal_id, due=now-timedelta(days=1)).to_json()
            raw.execute("""INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at,introduced_at)
                VALUES('n2',%s,%s,%s,%s,'p05-epoch',%s,%s,%s,%s,%s)""", (definition.key.variant, definition.key.position, definition.expected_uci,
                definition.policy_version, digest, internal_id, state, now-timedelta(days=1), now-timedelta(days=2)))
        raw.commit()
        proposal = prepare_due(port, day, {})
        assert isinstance(proposal, dict) and 'candidate' in proposal, proposal
        assert admit_due(port, day, proposal) is None
        raw.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(%s,1,'p05',%s)", (day, now.isoformat()))
        raw.commit()
        row = raw.execute("SELECT id,card_id,queue_date FROM daily_queue WHERE queue_date=%s AND status='queued' ORDER BY position,id LIMIT 1", (day,)).fetchone()
        assert row and row['card_id'] == 'card'
        entry = row['id']
        raw.commit()
        head = {'queue_date': day, 'entry_id': entry, 'card_id': 'card', 'expected_revision': 1}
        prepared_head = prepare_queue_head(head)
        assert prepared_head.mode == 'position', prepared_head
        assert row['queue_date'] == head['queue_date'] == current_study_day(port), 'queued day is no longer the authoritative Study day'
        issued = issue_queue_head(port, prepared_head)
        assert issued['issued'] and issued['position_study_hold'] is None, issued
        raw.commit()
        replay = prepare_queue_head(head)
        assert replay.mode == 'replay' and replay.proposal.to_wire() == issued['position_presentation']
        assert raw.execute('SELECT COUNT(*) FROM study_position_issued_plans').fetchone()[0] == 1
        raw.commit()
        review_journey(raw, port, dsn, monkeypatch, replay.proposal, entry, 'card', physical_fsrs)
