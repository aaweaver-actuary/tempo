"""Authoritative packaged catalog, activation, and daily introduction reservations."""
from datetime import date
from functools import lru_cache
import json
import os
from pathlib import Path

from .cards import card_id
from .puzzles import validate_puzzle_record
from tempo_legacy_database.adapters.tactic_admission import count_daily_tactic_introductions, lock_daily_tactic_admission
from tempo_legacy_database.adapters.tactics import catalog as storage

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
    active = {row[0] for row in storage.active_pack_rows(db)}
    progress = {row['puzzle_id']: row for row in storage.progress_rows(db)}
    due = {row[0] for row in storage.due_tactic_rows(db, day)}
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
    storage.set_activation(db, [(pack_id,int(active)) for pack_id in set(pack_ids)])


def seed_tactical_introductions(db, day):
    lock_daily_tactic_admission(db, day)
    limit = storage.daily_introduction_limit(db)
    reserved = count_daily_tactic_introductions(db, day)
    if reserved >= limit:
        return
    active = sorted(row[0] for row in storage.active_pack_rows(db))
    if not active:
        return
    cursor = storage.rotation_cursor(db)
    seen = {row[0] for row in storage.introduced_puzzle_rows(db)}
    maximum = storage.last_queue_position(db, day)
    storage.ensure_tactic_repertoire(db, day)
    while count_daily_tactic_introductions(db, day) < limit:
        ordered = [pack for pack in active if pack > cursor] + [pack for pack in active if pack <= cursor]
        selection = next(((pack,record) for pack in ordered for record in pack_records(pack) if record['PuzzleId'] not in seen),None)
        if not selection:
            break
        pack,record = selection
        fen,solution = validate_puzzle_record(record)
        cid = card_id(fen,solution)
        from ..card_deletion import is_card_deleted
        if is_card_deleted(db, cid):
            seen.add(record['PuzzleId'])
            continue
        storage.create_introduction_card(db, cid, fen, json.dumps(solution), day, record['PuzzleId'], record['FEN'])
        storage.save_introduction_progress(db, record['PuzzleId'], pack, cid, day, 'light')
        storage.save_introduction(db, record['PuzzleId'], pack, day)
        if not storage.existing_queue_entry(db, day, cid):
            maximum += 1
            storage.append_queue_entry(db, day, cid, maximum)
        seen.add(record['PuzzleId'])
        cursor = pack
        storage.save_rotation_cursor(db, cursor)
