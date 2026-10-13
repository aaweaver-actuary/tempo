"""Small, owned PostgreSQL fixture for the issue #225 native HTTP/browser proof."""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))


def main():
    if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Redis admission proof requires a disposable instance')
    action, identity = sys.argv[1:3]
    if not re.fullmatch(r'redis-admission-[a-f0-9-]+', identity):
        raise ValueError('Invocation-owned identity required')
    dsn = os.environ.get('TEMPO_REDIS_ADMISSION_TEST_DSN', 'postgresql://postgres@postgres:5432/tempo')
    with psycopg.connect(dsn) as database:
        if action == 'seed':
            today = date.today().isoformat()
            database.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            previous_mode = database.execute('SELECT study_only_mode FROM settings WHERE id=1').fetchone()[0]
            database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1')
            displaced = database.execute('SELECT id,position FROM daily_queue WHERE queue_date=%s ORDER BY position,id', (today,)).fetchall()
            shift = 2 - min((row[1] for row in displaced), default=0)
            for entry_id, position in displaced:
                database.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (position+shift,entry_id))
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,'Redis admission proof','synthetic',%s,0)", (identity,today))
            for index in range(2):
                card_id = f'{identity}-{index}'
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,content_type,introduced_at,first_correct_at,interval_days) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','learning',%s,'white','tactic','2000-01-01','2000-01-01T00:00:00+00:00',1)", (card_id,identity,'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',today))
                database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (identity,card_id))
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,%s,'due',%s)", (today,card_id,index,identity))
            database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (today,))
            print(json.dumps({'previous_mode':previous_mode,'displaced':[[entry,position,position+shift] for entry,position in displaced]}))
        elif action == 'inspect':
            print(json.dumps({
                'reviews':database.execute('SELECT card_id,rating FROM reviews WHERE card_id LIKE %s ORDER BY card_id', (identity+'-%',)).fetchall(),
                'receipts':database.execute('SELECT attempt_id,queue_entry_id,review_id,result_json FROM review_attempt_receipts WHERE card_id LIKE %s ORDER BY attempt_id', (identity+'-%',)).fetchall(),
                'operations':database.execute("SELECT operation_id,state,response_json FROM operation_receipts WHERE command_name='cards.review' AND payload_json LIKE %s ORDER BY operation_id", ('%'+identity+'%',)).fetchall(),
                'queue':database.execute('SELECT id,status,card_id FROM daily_queue WHERE card_id LIKE %s ORDER BY position', (identity+'-%',)).fetchall(),
            },default=str))
        elif action == 'cleanup':
            database.execute('DELETE FROM reviews WHERE card_id LIKE %s',(identity+'-%',))
            database.execute('DELETE FROM cards WHERE repertoire_id=%s',(identity,))
            database.execute('DELETE FROM repertoires WHERE id=%s',(identity,))
            saved = json.loads(sys.argv[3])
            database.execute('UPDATE settings SET study_only_mode=%s WHERE id=1', (saved['previous_mode'],))
            for entry_id, original_position, displaced_position in saved['displaced']:
                database.execute('UPDATE daily_queue SET position=%s WHERE id=%s AND position=%s', (original_position,entry_id,displaced_position))
        else:
            raise ValueError('Unknown fixture action')


if __name__ == '__main__':
    main()
