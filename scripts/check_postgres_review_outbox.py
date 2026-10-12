"""Issue #221: invocation-owned issued heads and authoritative history observations.

Uses the existing progression fixture; does not alter review or receipt contracts.
"""
import json
import re
import sys
from uuid import uuid4

import psycopg
from check_postgres_daily_study_dispatch import configure, DSN
from check_postgres_training_transition import main as progression_fixture


def main():
    action, identifier = sys.argv[1:3]
    if not re.fullmatch(r'training-transition-[a-f0-9-]+', identifier):
        raise ValueError('An invocation-owned synthetic identifier is required')
    configure()
    if action == 'seed':
        today = sys.argv[3]
        sys.argv = [sys.argv[0], 'seed-progression', identifier, today]
        progression_fixture()
        # Both presentations are fixed before issuance; each is explicitly issued
        # as a legitimate head through the real gateway, sequentially.
        from app.command_gateway import execute_command
        with psycopg.connect(DSN) as connection:
            connection.execute("UPDATE cards SET kind='checkpoint',content_type='tactic',moves_json='[\"e2e4\"]' WHERE id=%s", (identifier+'-prepared-001',))
            # Reinsert after changing presentation so the immutable origin
            # trigger binds the new tactic rather than the prior opening.
            connection.execute('DELETE FROM daily_queue WHERE card_id=%s', (identifier+'-prepared-001',))
            connection.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,1,'due',%s)", (today,identifier+'-prepared-001',identifier))
            rows = connection.execute('SELECT q.id,c.id,c.revision,q.cycle FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE c.id IN (%s,%s) ORDER BY c.id',
                (identifier+'-prepared-000', identifier+'-prepared-001')).fetchall()
        for entry, card, revision, _ in rows:
            with psycopg.connect(DSN) as connection:
                connection.execute('UPDATE daily_queue SET position=-1 WHERE id=%s', (entry,))
            result = execute_command(str(uuid4()), 'queue.issue_head', {'queue_date': today, 'entry_id': entry,
                'card_id': card, 'expected_revision': revision})
            assert result['issued'], {'card': card, 'entry': entry, 'result': result}
            with psycopg.connect(DSN) as connection:
                connection.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (0 if card.endswith('000') else 1, entry))
        print(json.dumps({'issued': [{'queue_entry_id': entry, 'card_id': card, 'revision': revision, 'cycle': cycle, 'local_date': today}
            for entry, card, revision, cycle in rows]}))
        return
    if action == 'retire-second':
        with psycopg.connect(DSN) as connection:
            connection.execute('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(%s,%s)', (identifier+'-prepared-001', '2026-10-12'))
        print(json.dumps({'retired': identifier+'-prepared-001'}))
        return
    if action == 'observe':
        with psycopg.connect(DSN) as connection:
            cards = connection.execute('SELECT id,revision,due_date,repetitions,lapses,interval_days,fsrs_card_json FROM cards WHERE id IN (%s,%s) ORDER BY id',
                (identifier+'-prepared-000', identifier+'-prepared-001')).fetchall()
            reviews = connection.execute('SELECT card_id,id,rating,reviewed_at,previous_interval,next_interval FROM reviews WHERE card_id LIKE %s ORDER BY id', (identifier+'-prepared-%',)).fetchall()
            receipts = connection.execute('SELECT card_id,attempt_id,queue_entry_id,review_id,scheduling_status,result_json,outcome,guided,completed_at,request_json FROM review_attempt_receipts WHERE card_id LIKE %s ORDER BY attempt_id', (identifier+'-prepared-%',)).fetchall()
            operations = connection.execute('SELECT operation_id,state,request_hash,response_json,error_json,payload_json FROM operation_receipts WHERE payload_json LIKE %s ORDER BY operation_id', ('%'+identifier+'%',)).fetchall()
            origins = connection.execute('SELECT queue_entry_id,card_id,issued_as_head FROM queue_attempt_origins WHERE card_id LIKE %s ORDER BY queue_entry_id', (identifier+'-prepared-%',)).fetchall()
            print(json.dumps({'cards': cards, 'reviews': reviews, 'receipts': receipts, 'operations': operations, 'origins': origins}, default=str))
        return
    if action == 'cleanup':
        with psycopg.connect(DSN) as connection:
            connection.execute('DELETE FROM deleted_cards WHERE card_id LIKE %s', (identifier+'-prepared-%',))
        progression_fixture()
        return
    raise ValueError('Unknown fixture action')


if __name__ == '__main__':
    main()
