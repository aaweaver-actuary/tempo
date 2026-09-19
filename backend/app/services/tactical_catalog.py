"""Authoritative packaged catalog, activation, and daily introduction reservations."""
from datetime import date
from functools import lru_cache
import json
import os
from pathlib import Path

from .cards import card_id
from .puzzles import validate_puzzle_record

DATA_ROOT = Path(os.getenv('TEMPO_CATALOG_ROOT', str(Path(__file__).resolve().parents[3] / 'public')))


@lru_cache(maxsize=1)
def catalog():
    value = json.loads((DATA_ROOT / 'data/tactics-catalog.json').read_text())
    if value['version'] != 1:
        raise ValueError('Unsupported tactics catalog version')
    return value


@lru_cache(maxsize=692)
def pack_records(pack_id):
    pack = next((pack for pack in catalog()['packs'] if pack['id'] == pack_id), None)
    if not pack:
        raise ValueError('Unknown tactical pack')
    return json.loads((DATA_ROOT / pack['asset']).read_text())


@lru_cache(maxsize=1)
def puzzle_membership():
    return {record['PuzzleId']: (pack['id'], record) for pack in catalog()['packs'] for record in pack_records(pack['id'])}


def progress_pack_id(puzzle_id, legacy_deck):
    return puzzle_membership().get(puzzle_id, (legacy_deck, None))[0]


def catalog_status(db, day=None):
    day = day or date.today().isoformat()
    active = {row[0] for row in db.execute('SELECT pack_id FROM tactic_pack_activation WHERE active=1')}
    progress = {row['puzzle_id']: row for row in db.execute('SELECT puzzle_id,clean_pass_at,admitted_at,card_id FROM tactic_progress')}
    due = {row[0] for row in db.execute("SELECT source_ref FROM cards WHERE content_type='tactic' AND archived=0 AND state IN ('learning','mature') AND due_date<=?",(day,))}
    packs = []
    for pack in catalog()['packs']:
        identities = [record['PuzzleId'] for record in pack_records(pack['id'])]
        packs.append(pack | {
            'active':pack['id'] in active,
            'clean':sum(bool(progress.get(identity) and progress[identity]['clean_pass_at']) for identity in identities),
            'introduced':sum(bool(progress.get(identity) and progress[identity]['admitted_at']) for identity in identities),
            'due':sum(identity in due for identity in identities),
        })
    return catalog() | {'packs':packs}


def activate(db, pack_ids, active):
    known = {pack['id'] for pack in catalog()['packs']}
    if not pack_ids or not set(pack_ids) <= known:
        raise ValueError('Select known tactical packs')
    db.executemany('INSERT INTO tactic_pack_activation(pack_id,active) VALUES(?,?) ON CONFLICT(pack_id) DO UPDATE SET active=excluded.active',[(pack_id,int(active)) for pack_id in set(pack_ids)])


def seed_tactical_introductions(db, day):
    limit = db.execute('SELECT tactics_new_per_day FROM settings WHERE id=1').fetchone()[0]
    reserved = db.execute('SELECT COUNT(*) FROM tactic_introductions WHERE introduction_date=?',(day,)).fetchone()[0]
    if reserved >= limit:
        return
    active = sorted(row[0] for row in db.execute('SELECT pack_id FROM tactic_pack_activation WHERE active=1'))
    if not active:
        return
    cursor = db.execute('SELECT last_pack_id FROM tactic_rotation WHERE id=1').fetchone()[0]
    seen = {row[0] for row in db.execute('SELECT puzzle_id FROM tactic_progress WHERE admitted_at IS NOT NULL')}
    maximum = db.execute('SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?',(day,)).fetchone()[0]
    db.execute("INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','Lichess puzzle database',?)",(day,))
    for _ in range(limit-reserved):
        ordered = [pack for pack in active if pack > cursor] + [pack for pack in active if pack <= cursor]
        selection = next(((pack,record) for pack in ordered for record in pack_records(pack) if record['PuzzleId'] not in seen),None)
        if not selection:
            break
        pack,record = selection
        fen,solution = validate_puzzle_record(record)
        cid = card_id(fen,solution)
        db.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,scheduling_mode,source_ref,source_fen,introduced_at) VALUES(?,'__tactics__','checkpoint',?,?,'learning',?,'tactic','light',?,?,?)",(cid,fen,json.dumps(solution),day,record['PuzzleId'],record['FEN'],day))
        db.execute('INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at,admission_mode) VALUES(?,?,?,?,?) ON CONFLICT(puzzle_id) DO UPDATE SET card_id=excluded.card_id,admitted_at=COALESCE(tactic_progress.admitted_at,excluded.admitted_at)',(record['PuzzleId'],pack,cid,day,'light'))
        db.execute('INSERT INTO tactic_introductions(puzzle_id,pack_id,introduction_date) VALUES(?,?,?)',(record['PuzzleId'],pack,day))
        if not db.execute('SELECT 1 FROM daily_queue WHERE queue_date=? AND card_id=?',(day,cid)).fetchone():
            maximum += 1
            db.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)',(day,cid,maximum))
        seen.add(record['PuzzleId'])
        cursor = pack
        db.execute('UPDATE tactic_rotation SET last_pack_id=? WHERE id=1',(cursor,))
