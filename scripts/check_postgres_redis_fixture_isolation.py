"""Bounded native PostgreSQL fixture regressions, run with workload consumers stopped.

Uses the migrated disposable database (real triggers/FKs), rolls each scenario
back, and restores the one committed ownership scenario in a finally block.
"""
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

import psycopg

import check_postgres_redis_admission as fixture


class RedisFixtureIsolation(unittest.TestCase):
    def setUp(self):
        self.database = psycopg.connect(fixture.DSN, connect_timeout=5)
        self.addCleanup(self.database.close)
        self.addCleanup(self.database.rollback)
        self.identity = f'redis-admission-{uuid4()}'
        self.control = f'redis-fixture-control-{uuid4()}'
        self.day = '2099-12-30'
        self.database.execute('UPDATE settings SET study_only_mode=0 WHERE id=1')
        self.database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,'Isolation control','synthetic',%s,0)", (self.control, self.day))
        self.database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,content_type) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','learning',%s,'white','tactic')",
                              (self.control, self.control, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', self.day))
        self.entry = self.database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,-7,'due',%s) RETURNING id", (self.day, self.control, self.control)).fetchone()[0]
        self.database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error,updated_at,blocked_count) VALUES(%s,'failed',23,1,'control error','original timestamp',3)", (self.day,))
        self.before = self.state()

    def state(self):
        return {
            'settings': fixture.snapshot_row(self.database, 'settings', 'id', 1),
            'queue': self.database.execute('SELECT * FROM daily_queue WHERE queue_date=%s ORDER BY id', (self.day,)).fetchall(),
            'projection': fixture.snapshot_row(self.database, 'queue_projections', 'queue_date', self.day),
            'control_card': fixture.snapshot_row(self.database, 'cards', 'id', self.control),
            'control_repertoire': fixture.snapshot_row(self.database, 'repertoires', 'id', self.control),
        }

    def seed(self):
        fixture.seed_fixture(self.database, self.identity, self.day)

    def cleanup(self):
        return fixture.cleanup_fixture(self.database, self.identity)

    def assert_no_owned_rows(self):
        for table, column in [('cards', 'id'), ('daily_queue', 'card_id'), ('reviews', 'card_id'),
                              ('review_attempt_receipts', 'card_id'), ('queue_attempt_origins', 'card_id'),
                              ('card_revisions', 'card_id'), ('repertoire_cards', 'card_id')]:
            count = self.database.execute(f'SELECT count(*) FROM {table} WHERE {column}=ANY(%s)', (fixture.card_ids(self.identity),)).fetchone()[0]
            self.assertEqual(count, 0, table)
        self.assertIsNone(fixture.snapshot_row(self.database, 'repertoires', 'id', self.identity))

    def test_redis_fixture_setup_failure_rolls_back_every_mutation(self):
        original_execute = self.database.execute

        def fail_card_insert(statement, parameters=()):
            if str(statement).startswith('INSERT INTO cards'):
                raise RuntimeError('controlled seed interruption after snapshot and queue shift')
            return original_execute(statement, parameters)

        with patch.object(self.database, 'execute', side_effect=fail_card_insert):
            with self.assertRaisesRegex(RuntimeError, 'controlled seed'):
                self.seed()
        self.assertEqual(self.state(), self.before)
        self.assert_no_owned_rows()

    def test_redis_fixture_cleanup_failure_rolls_back_and_can_retry(self):
        self.seed()
        seeded = self.state()
        original_delete = fixture.delete_owned_rows

        def interrupted_delete(database, identity):
            original_delete(database, identity)
            raise RuntimeError('controlled cleanup interruption')

        with patch.object(fixture, 'delete_owned_rows', side_effect=interrupted_delete):
            with self.assertRaisesRegex(RuntimeError, 'controlled cleanup'):
                self.cleanup()
        self.assertEqual(self.state(), seeded)
        self.assertIsNotNone(fixture.snapshot_row(self.database, 'repertoires', 'id', self.identity))
        self.assertTrue(self.cleanup()['cleaned'])
        self.assertEqual(self.state(), self.before)
        self.assert_no_owned_rows()
        self.assertFalse(self.cleanup()['cleaned'])

    def test_redis_fixture_restores_absent_settings_and_projection(self):
        self.database.execute('DELETE FROM settings WHERE id=1')
        self.database.execute('DELETE FROM queue_projections WHERE queue_date=%s', (self.day,))
        before = self.state()
        self.seed()
        self.cleanup()
        self.assertEqual(self.state(), before)
        self.assert_no_owned_rows()

    def test_redis_fixture_cleanup_detects_settings_conflict_without_partial_deletion(self):
        self.seed()
        self.database.execute('UPDATE settings SET study_only_mode=0 WHERE id=1')
        conflicting = self.state()
        with self.assertRaisesRegex(RuntimeError, 'settings.study_only_mode'):
            self.cleanup()
        self.assertEqual(self.state(), conflicting)
        self.assertIsNotNone(fixture.snapshot_row(self.database, 'repertoires', 'id', self.identity))

    def test_redis_fixture_cleanup_detects_changed_created_settings(self):
        self.database.execute('DELETE FROM settings WHERE id=1')
        self.seed()
        self.database.execute('UPDATE settings SET new_cards_per_day=99 WHERE id=1')
        conflicting = self.state()
        with self.assertRaisesRegex(RuntimeError, 'fixture-created settings'):
            self.cleanup()
        self.assertEqual(self.state(), conflicting)

    def test_redis_fixture_cleanup_detects_missing_and_reordered_queue_entries(self):
        self.seed()
        for statement in ['UPDATE daily_queue SET position=99 WHERE id=%s', 'DELETE FROM daily_queue WHERE id=%s']:
            with self.database.transaction(force_rollback=True):
                self.database.execute(statement, (self.entry,))
                conflicting = self.state()
                with self.assertRaisesRegex(RuntimeError, 'missing or reordered'):
                    self.cleanup()
                self.assertEqual(self.state(), conflicting)
        self.cleanup()
        self.assertEqual(self.state(), self.before)

    def test_redis_fixture_cleanup_rejects_changed_ownership_before_cascades(self):
        self.seed()
        owned_card = fixture.card_ids(self.identity)[0]
        for statement in [
            'UPDATE cards SET repertoire_id=%s WHERE id=%s',
            'INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)',
        ]:
            with self.database.transaction(force_rollback=True):
                self.database.execute(statement, (self.control, owned_card))
                conflicting = self.state()
                with self.assertRaisesRegex(RuntimeError, 'synthetic card'):
                    self.cleanup()
                self.assertEqual(self.state(), conflicting)
        self.cleanup()
        self.assertEqual(self.state(), self.before)

    def test_redis_fixture_cleanup_detects_projection_conflict(self):
        self.seed()
        self.database.execute('UPDATE queue_projections SET generation=24 WHERE queue_date=%s', (self.day,))
        conflicting = self.state()
        with self.assertRaisesRegex(RuntimeError, 'queue_projections'):
            self.cleanup()
        self.assertEqual(self.state(), conflicting)

    def test_redis_fixture_cleanup_removes_owned_receipts_and_preserves_unrelated_records(self):
        self.seed()
        owned_card = fixture.card_ids(self.identity)[0]
        review_ids = []
        for card in [owned_card, self.control]:
            review_id = self.database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'good',%s,1,2) RETURNING id", (card, self.day)).fetchone()[0]
            review_ids.append(review_id)
            self.database.execute("INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES(%s,'{}')", (review_id,))
            self.database.execute("INSERT INTO review_attempt_receipts(attempt_id,card_id,outcome,guided,review_id,scheduling_status,result_json) VALUES(%s,%s,'correct',0,%s,'scheduled','{}')", (card, card, review_id))
            self.database.execute("INSERT INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) VALUES(%s,1,'control','[]','full',%s)", (card, self.day))
        # Contains the owned identity as irrelevant text: substring cleanup would corrupt it.
        unrelated_payload = json.dumps({'card_id': self.control, 'note': owned_card})
        for operation_id, payload in [(owned_card, json.dumps({'card_id': owned_card})), (self.control, unrelated_payload)]:
            self.database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json,payload_json) VALUES(%s,'cards.review','hash','complete','{}',%s)", (operation_id, payload))
        self.database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json,payload_json) VALUES(%s,'queue.issue_head','hash','complete','{}',%s)",
                              (owned_card + '-issuance', json.dumps({'card_id': owned_card})))
        self.cleanup()
        self.assertIsNone(self.database.execute('SELECT operation_id FROM operation_receipts WHERE operation_id=%s', (owned_card + '-issuance',)).fetchone())
        self.assertEqual(self.state(), self.before)
        self.assert_no_owned_rows()
        self.assertEqual(self.database.execute('SELECT review_id FROM review_schedule_snapshots WHERE review_id=ANY(%s)', (review_ids,)).fetchall(), [(review_ids[1],)])
        self.assertEqual(self.database.execute('SELECT card_id FROM review_attempt_receipts WHERE card_id=ANY(%s)', ([owned_card, self.control],)).fetchall(), [(self.control,)])
        self.assertEqual(self.database.execute('SELECT payload_json FROM operation_receipts WHERE operation_id=ANY(%s)', ([owned_card, self.control],)).fetchall(), [(unrelated_payload,)])
        self.assertIsNotNone(self.database.execute('SELECT id FROM reviews WHERE id=%s', (review_ids[1],)).fetchone())
        self.assertIsNotNone(self.database.execute('SELECT card_id FROM queue_attempt_origins WHERE queue_entry_id=%s', (self.entry,)).fetchone())

    def test_redis_fixture_committed_seed_output_loss_is_recoverable_and_concurrent_seed_is_rejected(self):
        # Discard scenario overlays before the one cross-connection committed proof.
        self.database.rollback()
        identity = f'redis-admission-{uuid4()}'
        competing = f'redis-admission-{uuid4()}'
        with psycopg.connect(fixture.DSN, connect_timeout=5) as owner, psycopg.connect(fixture.DSN, connect_timeout=5) as contender:
            before_settings = fixture.snapshot_row(owner, 'settings', 'id', 1)
            before_projection = fixture.snapshot_row(owner, 'queue_projections', 'queue_date', self.day)
            owner.commit()
            try:
                fixture.seed_fixture(owner, identity, self.day)
                owner.commit()  # Recovery receives only identity, never seed output.
                with self.assertRaisesRegex(RuntimeError, 'already owns'):
                    fixture.seed_fixture(contender, competing, self.day)
                contender.rollback()
                with owner.transaction():
                    fixture.lock_fixture(owner)
                    with self.assertRaises(psycopg.errors.LockNotAvailable):
                        fixture.cleanup_fixture(contender, identity)
                contender.rollback()
                self.assertTrue(fixture.cleanup_fixture(contender, identity)['cleaned'])
                contender.commit()
                self.assertFalse(fixture.cleanup_fixture(owner, identity)['cleaned'])
                self.assertEqual(fixture.snapshot_row(owner, 'settings', 'id', 1), before_settings)
                self.assertEqual(fixture.snapshot_row(owner, 'queue_projections', 'queue_date', self.day), before_projection)
            finally:
                owner.rollback()
                contender.rollback()
                fixture.cleanup_fixture(owner, identity)
                owner.commit()


if __name__ == '__main__':
    fixture.configure()
    unittest.main(verbosity=2)
