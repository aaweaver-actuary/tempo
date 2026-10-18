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


def verified_owner(configuration):
    container=configuration['container']
    inspect=json.loads(subprocess.check_output(['docker','inspect',container],text=True))[0]
    labels=inspect['Config']['Labels']
    if labels.get('tempo.owner') != container or labels.get('tempo.disposable') != 'true' or not container.startswith('tempo-real-game-feedback-c3-'):
        raise ValueError('verified_disposable_container_required')
    dsn=configuration['databases']['learning']['owner'];connection=conninfo_to_dict(dsn)
    port=inspect['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
    if connection.get('host')!='127.0.0.1' or connection.get('port')!=port or connection.get('dbname')!='learning':
        raise ValueError('rehearsal_database_endpoint_mismatch')
    return dsn


def selected_cards(capture, manifest):
    tables=capture['tables'];selected=set(manifest['legacy_aliases'].values());cards={card['id']:card for card in tables['cards']}
    while True:
        dependencies={cards[key]['unlock_after_card_id'] for key in selected if cards[key]['unlock_after_card_id']}
        if dependencies<=selected:break
        selected |= dependencies
    return selected,cards


def advance_review_identity(database):
    """Preserve copied IDs and never move an existing sequence backwards.

    Sequence changes are not transactional; advancing can leave harmless gaps.
    The table lock prevents an ordinary review INSERT from allocating an ID
    between inspection and advancement. This is rehearsal-owner work only.
    """
    database.execute("SET LOCAL lock_timeout='250ms'")
    database.execute("SET LOCAL statement_timeout='5s'")
    database.execute('LOCK TABLE learning.reviews IN SHARE ROW EXCLUSIVE MODE')
    maximum=database.execute('SELECT max(id) FROM learning.reviews').fetchone()[0]
    previous,was_called=database.execute('SELECT last_value,is_called FROM learning.reviews_id_seq').fetchone()
    target=max(previous,maximum) if maximum is not None else previous
    called=was_called or maximum is not None and maximum>=previous
    changed=(target,called)!=(previous,was_called)
    if changed:
        database.execute("SELECT setval(pg_get_serial_sequence('learning.reviews','id'),%s,%s)",(target,called))
    return {'previous_last_value':previous,'previous_is_called':was_called,
        'maximum_existing_review_id':maximum,'last_value':target,'is_called':called,
        'next_generated_id':target+1 if called else target,'changed':changed}


def repair_review_identity(configuration,capture,manifest):
    """Finish an earlier copied rehearsal only while every review is unchanged."""
    dsn=verified_owner(configuration)
    selected,_=selected_cards(capture,manifest)
    expected=[row for row in capture['tables']['reviews'] if row['card_id'] in selected]
    with psycopg.connect(dsn,options='-c search_path=learning,pg_catalog') as database:
        if database.execute('SELECT workspace_id FROM learning.instance').fetchone()[0]!=configuration['workspace']:
            raise ValueError('rehearsal_workspace_mismatch')
        database.execute("SET LOCAL lock_timeout='250ms'")
        database.execute("SET LOCAL statement_timeout='5s'")
        database.execute('LOCK TABLE learning.reviews IN SHARE ROW EXCLUSIVE MODE')
        if database.execute('SELECT count(*) FROM learning.reviews').fetchone()[0]!=len(expected):
            raise ValueError('copied_reviews_changed_identity_repair_refused')
        if expected:
            columns=list(expected[0])
            rows=database.execute(sql.SQL('SELECT {} FROM learning.reviews').format(sql.SQL(',').join(map(sql.Identifier,columns)))).fetchall()
            actual=[dict(zip(columns,row)) for row in rows]
            canonical=lambda row:json.dumps(row,sort_keys=True,separators=(',',':'),default=str)
            if sorted(map(canonical,actual))!=sorted(map(canonical,expected)):
                raise ValueError('copied_reviews_changed_identity_repair_refused')
        receipt=advance_review_identity(database)
    return {'scope':'Verified disposable copied-review identity completion only',
        'preserved_reviews':len(expected),'sequence':receipt}


def restore(configuration, capture, manifest):
    dsn=verified_owner(configuration)
    tables=capture['tables'];selected,cards=selected_cards(capture,manifest)
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
    with psycopg.connect(dsn,options='-c search_path=learning,pg_catalog') as database:
        sequence=advance_review_identity(database)
    baseline={'cards':{key:{field:cards[key][field] for field in ['id','fsrs_card_json','due_date','interval_days','repetitions','lapses','state']} for key in sorted(selected)},
        'review_count':len(reviews),'review_digest':hashlib.sha256(json.dumps(reviews,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'scope':'Disposable restoration only; no live migration, queue restoration or review creation from games.', 'issued_attempts_restored':False,
        'review_identity_sequence':sequence}
    return baseline


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--configuration',type=Path,required=True);parser.add_argument('--capture',type=Path,required=True);parser.add_argument('--manifest',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--repair-review-identity',action='store_true');args=parser.parse_args()
    operation=repair_review_identity if args.repair_review_identity else restore
    result=operation(json.loads(args.configuration.read_text()),json.loads(args.capture.read_text()),json.loads(args.manifest.read_text()))
    args.output.write_text(json.dumps(result,sort_keys=True));print(json.dumps({'preserved_reviews':result['preserved_reviews'],'next_generated_id':result['sequence']['next_generated_id']}) if args.repair_review_identity else json.dumps({'restored_cards':len(result['cards']),'restored_reviews':result['review_count']}))

if __name__=='__main__':main()
