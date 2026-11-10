"""Original evidence reader against an invocation-owned, empty N2 database."""
import os
import json
from pathlib import Path
import secrets
import sys

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT = Path(__file__).resolve().parents[3]
for directory in [ROOT / 'backend', ROOT / 'database', ROOT, *ROOT.glob('packages/*/src'),
                  *ROOT.glob('services/*/src'), *ROOT.glob('services/learning/*/src'), *ROOT.glob('contracts/*/src')]:
    sys.path.insert(0, str(directory))

from database.tests.position_study import acceptance
from tempo_storage.postgres import PostgresConnection, tempo_row_factory
from tempo_storage.study.original_position_evidence import (
    EvidenceReadBlocked, read_original_plan, read_original_first_responses, read_original_completion,
)


def test_original_evidence_real_saved_later_mistake_and_repeatable_snapshot(monkeypatch):
    admin = os.environ.get('TEMPO_LEARNING_TEST_ADMIN')
    assert admin and os.environ.get('TEMPO_LEARNING_TEST_FIXTURE', '').startswith('tempo-learning-')
    params = conninfo_to_dict(admin)
    assert params.get('host') in ('127.0.0.1', 'localhost') and params.get('dbname') == 'postgres'
    name = 'tempo_position_test_n2_' + secrets.token_hex(12)
    assert name.startswith('tempo_position_test_n2_')
    with psycopg.connect(admin, autocommit=True) as connection:
        assert not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
        for role in ('tempo_reader', 'tempo_writer'):
            assert not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone()
            connection.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(params['password'])))
        connection.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn = make_conninfo(admin, dbname=name)
    monkeypatch.setenv('TEMPO_POSITION_N2_DSN', dsn)
    migrate = acceptance._migrate
    def migrate_with_large_id(raw):
        migrate(raw)
        raw.execute("SELECT setval(pg_get_serial_sequence('reviews','id'), 9007199254740993, false)")
    monkeypatch.setattr(acceptance, '_migrate', migrate_with_large_id)
    from app.study.position_learning.issued_plan_contracts import IssuedPlan
    from app.services.postgres_opening_evidence import decode_position_first_response, _position_digest
    from tempo_evidence_math.position_learning import Hold
    def decoder(payload, digest, record):
        plan = IssuedPlan.from_record(record)
        if isinstance(plan, Hold): raise ValueError('original_plan_invalid')
        return decode_position_first_response(payload, digest, plan)
    journey = acceptance._real_review_journey
    def check_journey(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs):
        journey(raw, port, dsn, monkeypatch, plan, entry, card_id, fsrs)
        # Second real issued attempt: ordinary checkpoint owns assisted capture.
        from app.services.position_queue_admission import prepare_due, admit_due
        from app.services.queue_next_card import prepare_queue_head
        from app.queue_commands import issue_queue_head
        from app.services import postgres_opening_evidence as source
        from app.services.opening_decision_evidence import decision_manifest
        from tempo_storage.authored_repertoires.position_study_source import fence_position_source
        from tempo_storage.study.position_owner_epoch import owner_mode
        import chess
        from datetime import datetime, timezone
        day = plan.local_day.isoformat()
        proposal = prepare_due(port, day, {})
        assert admit_due(port, day, proposal) is None
        raw.commit()
        next_row = raw.execute("SELECT id,card_id FROM daily_queue WHERE status='queued' ORDER BY position,id").fetchone()
        assert next_row is not None
        next_entry, next_card = next_row['id'], next_row['card_id']
        raw.commit()
        request_head = {'queue_date': day, 'entry_id': next_entry, 'card_id': next_card, 'expected_revision': 1}
        issued = issue_queue_head(port, prepare_queue_head(request_head))
        assert issued['issued']
        raw.commit()
        second = prepare_queue_head(request_head).proposal
        scope_row = raw.execute("""SELECT snapshot.*,context.effective_trained_color FROM opening_evidence_presentations snapshot
            JOIN opening_evidence_queue_contexts context ON context.presentation_snapshot_id=snapshot.id
            WHERE context.queue_entry_id=%s AND context.repertoire_id='n2'""", (next_entry,)).fetchone()
        manifest = source.issued_position_manifest(second, decision_manifest(source._manifest_snapshot(dict(scope_row)), 'n2'))
        assert len(manifest['decisions']) >= 2
        decision = manifest['decisions'][0]
        now = datetime.now(timezone.utc).isoformat()
        common = dict(decision_index=0, decision_id=decision['decision_id'], expected_uci=decision['expected_uci'], observed_at=now)
        checkpoint = dict(attempt_id=second.attempt_id, manifest=manifest, origin_queue_entry_id=next_entry,
            queue_entry_id=next_entry, started_at=now, study_timezone='UTC', source='live', events=[
                dict(common, sequence=1, kind='assistance', assistance='hint'),
                dict(common, sequence=2, kind='first_response', response_uci=decision['expected_uci'], disposition='expected')])
        raw.commit()
        prepared = source.prepare_standalone_checkpoint({'checkpoint': checkpoint})
        raw.execute('BEGIN')
        assert source.commit_standalone_checkpoint(port, prepared)['persisted']
        raw.commit()
        # Low-level source API fixture with a synthetic adjudicator: no claim that
        # the current high-level producer emits alternate or unverified history.
        raw.execute('BEGIN')
        fence = source.begin_position_checkpoint(port, prepared.position_preparation)
        source._validate_checkpoint_scope(port, prepared.request, completing_review=False)
        assert fence.storage.lock_original_queue(port, second)
        assert fence.storage.lock_attempt(port, second.attempt_id)
        assert raw.execute('SELECT 1 FROM opening_evidence_attempts WHERE attempt_id=%s FOR UPDATE', (second.attempt_id,)).fetchone()
        assert fence_position_source(port, prepared.position_preparation.source) is None
        owner = owner_mode(port, second.target.key.repertoire_id, fence.authored_digest)
        assert owner.disposition == 'position' and owner.epoch == second.owner_epoch
        slot = second.decision_slots[1]
        alternative = next(m.uci() for m in chess.Board(slot.fen).legal_moves if m.uci() != slot.definition.expected_uci)
        envelope = source.position_first_response_envelope(second, event_id='fixture-alternate', decision_index=1,
            actual_uci=alternative, manual_failure=False, assistance_before=False, observed_at=now,
            classify_alternative=lambda *_: 'alternate')
        def no_negative_effect(*_):
            pytest.fail('alternate response emitted a negative effect')
        source.persist_position_first_response(port, second, envelope, datetime.now(timezone.utc), apply_grade=no_negative_effect)
        raw.commit()
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as second_reader:
            second_reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            second_rows = read_original_first_responses(PostgresConnection(second_reader, read_only=True), attempt_id=second.attempt_id, decode_response=decoder)
            assert len(second_rows) == 2
            assert json.loads(second_rows[0].stored_payload_json)['assistance_before'] is True
            assert json.loads(second_rows[1].stored_payload_json)['disposition'] == 'alternate'
            assert second_rows[1].effect_id is None
            assert read_original_completion(PostgresConnection(second_reader, read_only=True), attempt_id=second.attempt_id).status == 'pending'
            from app.study.position_learning.issuance import read_clean_presentation_proof
            assert isinstance(read_clean_presentation_proof(PostgresConnection(second_reader, read_only=True), second.attempt_id), Hold)
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as reader:
            reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            scope = PostgresConnection(reader, read_only=True)
            original = read_original_plan(scope, attempt_id=plan.attempt_id)
            responses = read_original_first_responses(scope, attempt_id=plan.attempt_id, decode_response=decoder)
            complete = read_original_completion(scope, attempt_id=plan.attempt_id)
            assert not isinstance(original, EvidenceReadBlocked), original
            assert not isinstance(responses, EvidenceReadBlocked), responses
            assert not isinstance(complete, EvidenceReadBlocked), complete
            assert original.queue_entry_id == entry and len(responses) >= 2
            assert complete.status == 'committed' and complete.outcome == 'again' and complete.guided
            assert complete.review_id > 2**53
            assert original.provenance == complete.provenance == responses[0].provenance
            assert complete.study_timezone == 'UTC' and complete.completed_at.tzinfo is not None
            assert responses[0].observed_at.microsecond != 0 and responses[0].accepted_at.microsecond != 0
            assert isinstance(read_original_plan(scope, attempt_id='missing'), EvidenceReadBlocked)
            with psycopg.connect(dsn) as writer:
                writer.execute('UPDATE reviews SET invalidated_at=%s WHERE id=%s', ('2026-11-04T12:00:00Z', complete.review_id))
            assert read_original_completion(scope, attempt_id=plan.attempt_id) == complete
        # Test-local read-result corruption: durable original rows remain untouched.
        saved = json.loads(responses[0].stored_payload_json)
        original_digest = responses[0].payload_digest
        class CorruptRows:
            def __init__(self, cursor, payload, digest):
                self.cursor, self.payload, self.digest = cursor, payload, digest
            def fetchall(self):
                rows = [dict(row) for row in self.cursor.fetchall()]
                rows[0]['stored_json'] = json.dumps(self.payload)
                rows[0]['payload_digest'] = self.digest
                return rows
        class CorruptScope:
            def __init__(self, delegate, payload, digest):
                self.delegate, self.payload, self.digest = delegate, payload, digest
            @property
            def in_transaction(self): return self.delegate.in_transaction
            @property
            def read_only(self): return self.delegate.read_only
            def execute_native(self, statement, parameters=()):
                cursor = self.delegate.execute_native(statement, parameters)
                if 'FROM public.study_position_first_responses r' in statement:
                    return CorruptRows(cursor, self.payload, self.digest)
                return cursor
        def fresh_responses(payload=None, digest=None):
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as connection:
                connection.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                reader = PostgresConnection(connection, read_only=True)
                if payload is not None: reader = CorruptScope(reader, payload, digest)
                return read_original_first_responses(reader, attempt_id=plan.attempt_id, decode_response=decoder)
        changed = dict(saved, actual_uci='a2a3')
        assert fresh_responses(changed, original_digest).code == 'LP_RESPONSE_INVALID'
        changed = dict(saved, original_ply=saved['original_ply'] + 2)
        assert fresh_responses(changed, _position_digest(changed)).code == 'LP_RESPONSE_INVALID'
        changed = dict(saved, event_id='different-event')
        assert fresh_responses(changed, _position_digest(changed)).code == 'LP_RESPONSE_ROW_MISMATCH'
        assert [r.stored_payload_json for r in fresh_responses()] == [r.stored_payload_json for r in responses]
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as fresh:
            fresh.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            invalidated = read_original_completion(PostgresConnection(fresh, read_only=True), attempt_id=plan.attempt_id)
            assert invalidated.status == 'invalidated' and invalidated.review_id == complete.review_id
        # A complete attempt is committed only with the canonical receipt and
        # exact source reference for either allowed review source kind.
        with psycopg.connect(dsn) as writer:
            writer.execute('UPDATE reviews SET invalidated_at=NULL WHERE id=%s', (complete.review_id,))
            writer.execute("UPDATE review_attempt_receipts SET scheduling_status='deferred' WHERE attempt_id=%s", (plan.attempt_id,))
        def fresh_completion():
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as fresh:
                fresh.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                return read_original_completion(PostgresConnection(fresh, read_only=True), attempt_id=plan.attempt_id)
        assert fresh_completion().status == 'conflict'
        with psycopg.connect(dsn) as writer:
            writer.execute("UPDATE review_attempt_receipts SET scheduling_status='canonical' WHERE attempt_id=%s", (plan.attempt_id,))
            writer.execute("UPDATE reviews SET source_kind='study', source_ref='unrelated' WHERE id=%s", (complete.review_id,))
        assert fresh_completion().status == 'conflict'
        with psycopg.connect(dsn) as writer:
            writer.execute('UPDATE reviews SET source_ref=%s WHERE id=%s', ('attempt:' + plan.attempt_id, complete.review_id))
        assert fresh_completion().status == 'committed'
        # A malformed receipt is a conflict; retirement preserves its exact identity.
        with psycopg.connect(dsn) as writer:
            writer.execute('UPDATE reviews SET invalidated_at=NULL WHERE id=%s', (complete.review_id,))
            writer.execute('UPDATE review_attempt_receipts SET queue_entry_id=queue_entry_id+1 WHERE attempt_id=%s', (plan.attempt_id,))
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as fresh:
            fresh.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            conflict = read_original_completion(PostgresConnection(fresh, read_only=True), attempt_id=plan.attempt_id)
            assert isinstance(conflict, EvidenceReadBlocked) and conflict.status == 'conflict'
        with psycopg.connect(dsn) as writer:
            writer.execute('UPDATE review_attempt_receipts SET queue_entry_id=queue_entry_id-1 WHERE attempt_id=%s', (plan.attempt_id,))
            writer.execute('INSERT INTO opening_evidence_events(attempt_id,sequence,event_json) VALUES(%s,%s,%s)', (plan.attempt_id, complete.contiguous_sequence + 1, '{}'))
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as fresh:
            fresh.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            assert read_original_completion(PostgresConnection(fresh, read_only=True), attempt_id=plan.attempt_id).status == 'conflict'
        with psycopg.connect(dsn) as writer:
            writer.execute('DELETE FROM opening_evidence_events WHERE attempt_id=%s AND sequence=%s', (plan.attempt_id, complete.contiguous_sequence + 1))
            writer.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) VALUES('fixture-retire','fixture','fixture','complete')")
            writer.execute("UPDATE opening_evidence_attempts SET retired_operation_id='fixture-retire' WHERE attempt_id=%s", (plan.attempt_id,))
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as fresh:
            fresh.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            retired = read_original_completion(PostgresConnection(fresh, read_only=True), attempt_id=plan.attempt_id)
            assert retired.status == 'invalidated' and retired.retired_operation_id == 'fixture-retire'
        with psycopg.connect(dsn) as locker:
            locker.execute('LOCK TABLE study_position_issued_plans IN ACCESS EXCLUSIVE MODE')
            with psycopg.connect(dsn, row_factory=tempo_row_factory) as blocked:
                blocked.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                blocked.execute("SET LOCAL lock_timeout='100ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    read_original_plan(PostgresConnection(blocked, read_only=True), attempt_id=plan.attempt_id)
        with psycopg.connect(dsn, row_factory=tempo_row_factory) as wrong:
            wrong.execute('SELECT 1')
            assert isinstance(read_original_plan(PostgresConnection(wrong, read_only=True), attempt_id=plan.attempt_id), EvidenceReadBlocked)
    monkeypatch.setattr(acceptance, '_real_review_journey', check_journey)
    monkeypatch.setattr(acceptance, '_real_daily_credit_followup', lambda *args, **kwargs: None)
    try:
        acceptance._run_schema_journey(monkeypatch, branches=True)
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            assert not connection.execute('SELECT 1 FROM pg_database WHERE datname=%s', (name,)).fetchone()
