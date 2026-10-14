"""Seed a frozen-order browser proof in the owning disposable PostgreSQL stack."""
import json
import os
from pathlib import Path
import sys
import chess
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.database import background_connection
from app.services import frozen_opening_order as order
from app.services.cards import card_id
from app.services.frozen_maia_math import content_fingerprint
from app.services.queue_application import randomize_daily_queue
from datetime import date

if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable': raise RuntimeError('Disposable fixture required')
action=sys.argv[1]
with background_connection() as database:
    day=date.today().isoformat()
    if action=='seed':
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('frozen-browser','Frozen browser','fixture',?)",(day,))
        for position,move in enumerate(('e2e4','d2d4')):
            identifier=card_id(chess.STARTING_FEN,[move])
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color,introduced_at) VALUES(?,'frozen-browser','prefix',?,?,'learning',?,'opening','white',?)",(identifier,chess.STARTING_FEN,json.dumps([move]),day,day))
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,?,'new','frozen-browser')",(day,identifier,position))
    identifiers=[card_id(chess.STARTING_FEN,[move]) for move in ('e2e4','d2d4')]
    ranks=[]
    for ordinal,identifier in enumerate(reversed(identifiers) if action=='seed' else identifiers,1):
        card=dict(database.execute('SELECT * FROM cards WHERE id=?',(identifier,)).fetchone())
        ranks.append({'id':identifier,'rank':ordinal,'fingerprint':content_fingerprint(card,['frozen-browser']),'memberships':['frozen-browser']})
    plan={'run_id':'frozen-browser-'+action,'checksum':'frozen-browser-'+action,'ranks':ranks}
    order.stage_run(database,plan,day);order.stage_ranks(database,plan,ranks);order.activate_run(database,plan)
    randomize_daily_queue(database,day)
    print(json.dumps({'ranked_ids':[row['id'] for row in ranks]}))
