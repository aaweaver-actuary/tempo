"""Learning-owned restore of captured history into a verified empty rehearsal DB.

Not a live migration command. Docker labels, published port, exact database name,
workspace binding and emptiness are checked before writes. Credentials stay in a
private local configuration and are never emitted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def restore(configuration, capture, manifest):
    container=configuration['container']
    inspect=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]
    labels=inspect['Config']['Labels']
    if labels.get('tempo.owner') != container or labels.get('tempo.disposable') != 'true' or not container.startswith('tempo-real-game-feedback-c3-'):
        raise ValueError('verified_disposable_container_required')
    dsn=configuration['databases']['learning']['owner'];connection=conninfo_to_dict(dsn)
    port=inspect['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
    if connection.get('host')!='127.0.0.1' or connection.get('port')!=port or connection.get('dbname')!='learning':
        raise ValueError('rehearsal_database_endpoint_mismatch')
    tables=capture['tables'];selected=set(manifest['legacy_aliases'].values());cards={card['id']:card for card in tables['cards']}
    while True:
        dependencies={cards[key]['unlock_after_card_id'] for key in selected if cards[key]['unlock_after_card_id']}
        if dependencies<=selected:break
        selected |= dependencies
    counts={}
    with psycopg.connect(dsn, options="-c search_path=learning,pg_catalog") as db:
        if db.execute('SELECT workspace_id FROM learning.instance').fetchone()[0]!=configuration['workspace']:
            raise ValueError('rehearsal_workspace_mismatch')
        if db.execute('SELECT count(*) FROM learning.cards').fetchone()[0]:
            raise ValueError('empty_rehearsal_required')
        columns=[row[0] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='learning' AND table_name='cards' ORDER BY ordinal_position")]
        # Only captured source columns are restored; serving ownership retains
        # the independent runtime defaults until genuine Content activation.
        columns=[name for name in columns if name in next(iter(cards.values()))]
        repertoire_ids={cards[key]['repertoire_id'] for key in selected}
        for row in tables['repertoires']:
            if row['id'] in repertoire_ids:
                db.execute('INSERT INTO learning.repertoires(id,name,source_name,created_at,is_main) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',tuple(row[name] for name in ['id','name','source_name','created_at','is_main']))
    pending=set(selected)
    while pending:
        batch=[key for key in sorted(pending) if cards[key]['unlock_after_card_id'] is None or cards[key]['unlock_after_card_id'] not in pending][:64]
        if not batch:raise ValueError('historical_parent_cycle')
        with psycopg.connect(dsn, options="-c search_path=learning,pg_catalog") as db:
            for key in batch:
                row=cards[key]
                statement=sql.SQL('INSERT INTO learning.cards({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                db.execute(statement,tuple(row[name] for name in columns))
        pending.difference_update(batch)
    reviews=[row for row in tables['reviews'] if row['card_id'] in selected]
    for offset in range(0,len(reviews),64):
        with psycopg.connect(dsn, options="-c search_path=learning,pg_catalog") as db:
            for row in reviews[offset:offset+64]:
                columns=list(row)
                db.execute(sql.SQL('INSERT INTO learning.reviews({}) VALUES({})').format(sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns)),tuple(row.values()))
    baseline={'cards':{key:{field:cards[key][field] for field in ['id','fsrs_card_json','due_date','interval_days','repetitions','lapses','state']} for key in sorted(selected)},
        'review_count':len(reviews),'review_digest':hashlib.sha256(json.dumps(reviews,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'scope':'Disposable restoration only; no live migration, queue restoration or review creation from games.', 'issued_attempts_restored':False}
    return baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--configuration',type=Path,required=True);parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=restore(json.loads(args.configuration.read_text()),json.loads(args.capture.read_text()),json.loads(args.manifest.read_text()))
    args.output.write_text(json.dumps(result,sort_keys=True));print(json.dumps({'restored_cards':len(result['cards']),'restored_reviews':result['review_count']}))

if __name__=='__main__':main()
