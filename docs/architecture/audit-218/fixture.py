"""Synthetic audit fixture; requires explicitly disposable resources."""
import os,sys,json
sys.path.insert(0,'/source/backend')
import psycopg
from datetime import datetime
from zoneinfo import ZoneInfo
assert os.environ.get('TEMPO_TEST_INSTANCE')=='disposable'
DSN='postgresql://postgres@postgres:5432/tempo'
FEN='rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
if sys.argv[1]=='seed':
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('audit-218','Audit synthetic','synthetic',%s)",(today,))
        db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('audit-line','audit-218','Synthetic','white',%s,'[\"e2e4\"]',%s)",(FEN,today))
        db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) SELECT 'audit-card-'||lpad(number::text,5,'0'),'audit-218','prefix',%s,'[\"e2e4\"]','learning',%s,'tactic','white' FROM generate_series(1,5000) number",(FEN,today))
        db.execute("INSERT INTO daily_queue(queue_date,card_id,position) SELECT %s,id,row_number() OVER(ORDER BY id)-1 FROM cards WHERE repertoire_id='audit-218'",(today,))
        db.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0",(today,))
    print(json.dumps({'seeded_cards':5000,'workspace_date':today}))
elif sys.argv[1]=='verify':
    with psycopg.connect(DSN) as db:
        counts=db.execute("SELECT count(*),count(DISTINCT card_id) FROM reviews WHERE card_id LIKE 'audit-card-%%'").fetchone()
        receipts=db.execute("SELECT count(*) FROM review_attempt_receipts WHERE attempt_id LIKE 'audit-%%'").fetchone()[0]
        print(json.dumps({'reviews':counts[0],'distinct_review_cards':counts[1],'logical_receipts':receipts}))
        assert counts[0]==counts[1]==receipts
