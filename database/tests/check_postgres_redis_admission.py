"""Owned Redis proof fixture for the regular, serial disposable PostgreSQL runner.

The runner verifies fresh project-scoped volumes/credentials and tears them down
on failure. This fixture additionally restores state shared by sequential tests.
Its synthetic repertoire holds the committed recovery snapshot: stdout is never
needed to clean up, and no application schema or review semantics are changed.
"""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))
import json
import os
import re
import sys
from datetime import date

import psycopg
from psycopg import sql

DSN = 'postgresql://postgres@postgres:5432/tempo'
MARKER = 'tempo-redis-admission-fixture'
VERSION = 2
QUEUE_OWNERSHIP_COLUMNS = ('id', 'card_id', 'queue_date', 'cycle', 'admission_repertoire_id')


def configure():
    if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Redis admission proof requires a disposable instance')
    if not re.fullmatch(r'tempo-pg-regressions-\d+-[a-f0-9]+', os.environ.get('TEMPO_TEST_COMPOSE_PROJECT', '')):
        raise RuntimeError('Redis admission proof requires the regular invocation-owned project')
    if os.environ.get('TEMPO_REDIS_ADMISSION_TEST_DSN'):
        raise RuntimeError('Custom Redis admission database URLs are not supported')


def card_ids(identity):
    if not re.fullmatch(r'redis-admission-[a-f0-9-]+', identity):
        raise ValueError('Invocation-owned identity required')
    # The third card is an unreviewed successor: browser refresh/issuance must
    # never reach a displaced, unrelated queue entry after the two reviews.
    return [f'{identity}-{index}' for index in range(3)]


def lock_fixture(database):
    # No session lock or transaction is held across browser/network work.
    database.execute("SET LOCAL lock_timeout='2s'")
    database.execute("SET LOCAL statement_timeout='5s'")
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (MARKER,))


def snapshot_row(database, table, key, value):
    return_row = database.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t WHERE {}=%s FOR UPDATE').format(
        sql.Identifier(table), sql.Identifier(key)), (value,)).fetchone()
    return return_row[0] if return_row else None


def restore_row(database, table, key, saved_row):
    columns = [column for column in saved_row if column != key]
    assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(column)) for column in columns)
    database.execute(sql.SQL('UPDATE {} SET {} WHERE {}=%s').format(
        sql.Identifier(table), assignments, sql.Identifier(key)),
        [saved_row[column] for column in columns] + [saved_row[key]])


def seed_fixture(database, identity, queue_date=None):
    owned_cards = card_ids(identity)
    queue_date = queue_date or date.today().isoformat()
    with database.transaction():
        lock_fixture(database)
        active = database.execute("SELECT id FROM repertoires WHERE source_name LIKE %s LIMIT 1", (MARKER + ':%',)).fetchone()
        if active:
            raise RuntimeError(f'Redis admission fixture already owns this database: {active[0]}')
        settings_before = snapshot_row(database, 'settings', 'id', 1)
        database.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1')
        settings_installed = snapshot_row(database, 'settings', 'id', 1)
        displaced = database.execute('SELECT id,position FROM daily_queue WHERE queue_date=%s ORDER BY position,id FOR UPDATE', (queue_date,)).fetchall()
        shift = 3 - min((entry[1] for entry in displaced), default=0)
        for entry_id, position in displaced:
            database.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (position + shift, entry_id))
        projection_before = snapshot_row(database, 'queue_projections', 'queue_date', queue_date)
        database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0) ON CONFLICT(queue_date) DO UPDATE SET state='ready',refresh_pending=0", (queue_date,))
        saved = {'marker': MARKER, 'version': VERSION, 'identity': identity, 'queue_date': queue_date,
                 'settings_before': settings_before, 'settings_installed': settings_installed,
                 'displaced': [[entry, position, position + shift] for entry, position in displaced],
                 'projection_before': projection_before,
                 'projection_installed': snapshot_row(database, 'queue_projections', 'queue_date', queue_date),
                 'owned_queue_entries': []}
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,'Redis admission proof',%s,%s,0)",
                         (identity, MARKER + ':' + json.dumps(saved), queue_date))
        for index, card_id in enumerate(owned_cards):
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,content_type,introduced_at,first_correct_at,interval_days) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','learning',%s,'white','tactic','2000-01-01','2000-01-01T00:00:00+00:00',1)",
                             (card_id, identity, 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', queue_date))
            database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (identity, card_id))
            queue_entry = database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(%s,%s,%s,'due',%s) RETURNING id,card_id,queue_date,cycle,admission_repertoire_id", (queue_date, card_id, index, identity)).fetchone()
            saved['owned_queue_entries'].append(dict(zip(QUEUE_OWNERSHIP_COLUMNS, queue_entry)))
        # The final recovery manifest commits atomically with all queue identities.
        database.execute('UPDATE repertoires SET source_name=%s WHERE id=%s', (MARKER + ':' + json.dumps(saved), identity))
    return {'identity': identity}


def validate_restoration(saved, settings, projection, positions):
    if settings is None or settings['study_only_mode'] != saved['settings_installed']['study_only_mode']:
        raise RuntimeError('Redis fixture restoration conflict: settings.study_only_mode')
    if saved['settings_before'] is None and settings != saved['settings_installed']:
        raise RuntimeError('Redis fixture restoration conflict: fixture-created settings row')
    if projection != saved['projection_installed']:
        raise RuntimeError('Redis fixture restoration conflict: queue_projections')
    for entry_id, _, installed_position in saved['displaced']:
        if positions.get(entry_id) != installed_position:
            raise RuntimeError(f'Redis fixture restoration conflict: daily_queue entry {entry_id} missing or reordered')


def validate_synthetic_queue_ownership(saved, queue_rows):
    expected = {entry['id']: entry for entry in saved['owned_queue_entries']}
    actual = {row[0]: dict(zip(QUEUE_OWNERSHIP_COLUMNS, row)) for row in queue_rows}
    if actual != expected:
        raise RuntimeError('Redis fixture restoration conflict: synthetic queue ownership missing, reassigned, or additional')


def delete_owned_rows(database, identity):
    owned_cards = card_ids(identity)
    database.execute('DELETE FROM review_schedule_snapshots WHERE review_id IN (SELECT id FROM reviews WHERE card_id=ANY(%s))', (owned_cards,))
    database.execute('DELETE FROM review_attempt_receipts WHERE card_id=ANY(%s)', (owned_cards,))
    database.execute("DELETE FROM operation_receipts WHERE payload_json::jsonb->>'card_id'=ANY(%s)", (owned_cards,))
    database.execute('DELETE FROM reviews WHERE card_id=ANY(%s)', (owned_cards,))
    database.execute('DELETE FROM cards WHERE id=ANY(%s) AND repertoire_id=%s', (owned_cards, identity))
    # Queue DELETE triggers retain origins, so remove these after card cascades.
    database.execute('DELETE FROM queue_attempt_origins WHERE card_id=ANY(%s)', (owned_cards,))
    database.execute('DELETE FROM card_revisions WHERE card_id=ANY(%s)', (owned_cards,))
    database.execute('DELETE FROM repertoires WHERE id=%s', (identity,))


def cleanup_fixture(database, identity):
    card_ids(identity)
    with database.transaction():
        lock_fixture(database)
        owner = database.execute('SELECT source_name FROM repertoires WHERE id=%s FOR UPDATE', (identity,)).fetchone()
        if owner is None:
            return {'identity': identity, 'cleaned': False}
        if not owner[0].startswith(MARKER + ':'):
            raise RuntimeError('Redis fixture restoration conflict: ownership snapshot missing')
        saved = json.loads(owner[0][len(MARKER) + 1:])
        if (saved.get('marker'), saved.get('version'), saved.get('identity')) != (MARKER, VERSION, identity):
            raise RuntimeError('Redis fixture restoration conflict: invalid ownership snapshot')
        settings = snapshot_row(database, 'settings', 'id', 1)
        projection = snapshot_row(database, 'queue_projections', 'queue_date', saved['queue_date'])
        positions = dict(database.execute('SELECT id,position FROM daily_queue WHERE id=ANY(%s) ORDER BY id FOR UPDATE',
                                         ([entry[0] for entry in saved['displaced']],)).fetchall())
        validate_restoration(saved, settings, projection, positions)
        owned_cards = card_ids(identity)
        current_owners = dict(database.execute(
            'SELECT id,repertoire_id FROM cards WHERE id=ANY(%s) OR repertoire_id=%s ORDER BY id FOR UPDATE',
            (owned_cards, identity)).fetchall())
        if current_owners != dict.fromkeys(owned_cards, identity):
            raise RuntimeError('Redis fixture restoration conflict: synthetic card ownership')
        unrelated_link = database.execute(
            'SELECT repertoire_id FROM repertoire_cards WHERE card_id=ANY(%s) AND repertoire_id!=%s LIMIT 1 FOR UPDATE',
            (owned_cards, identity)).fetchone()
        if unrelated_link:
            raise RuntimeError('Redis fixture restoration conflict: synthetic card linked to unrelated repertoire')
        # Existing FOR UPDATE parent locks block FK-backed inserts until commit;
        # this SELECT is an ownership check, not a predicate lock for arbitrary SQL.
        queue_rows = database.execute(
            'SELECT id,card_id,queue_date,cycle,admission_repertoire_id FROM daily_queue '
            'WHERE id=ANY(%s) OR card_id=ANY(%s) OR admission_repertoire_id=%s ORDER BY id FOR UPDATE',
            ([entry['id'] for entry in saved['owned_queue_entries']], owned_cards, identity)).fetchall()
        validate_synthetic_queue_ownership(saved, queue_rows)
        delete_owned_rows(database, identity)
        if saved['settings_before'] is None:
            database.execute('DELETE FROM settings WHERE id=1')
        else:
            database.execute('UPDATE settings SET study_only_mode=%s WHERE id=1', (saved['settings_before']['study_only_mode'],))
        for entry_id, original_position, _ in saved['displaced']:
            database.execute('UPDATE daily_queue SET position=%s WHERE id=%s', (original_position, entry_id))
        if saved['projection_before'] is None:
            database.execute('DELETE FROM queue_projections WHERE queue_date=%s', (saved['queue_date'],))
        else:
            restore_row(database, 'queue_projections', 'queue_date', saved['projection_before'])
    return {'identity': identity, 'cleaned': True}


def inspect_fixture(database, identity):
    owned_cards = card_ids(identity)
    return {
        'reviews': database.execute('SELECT card_id,rating FROM reviews WHERE card_id=ANY(%s) ORDER BY card_id', (owned_cards,)).fetchall(),
        'receipts': database.execute('SELECT attempt_id,queue_entry_id,review_id,result_json FROM review_attempt_receipts WHERE card_id=ANY(%s) ORDER BY attempt_id', (owned_cards,)).fetchall(),
        'operations': database.execute("SELECT operation_id,state,response_json FROM operation_receipts WHERE command_name='cards.review' AND payload_json::jsonb->>'card_id'=ANY(%s) ORDER BY operation_id", (owned_cards,)).fetchall(),
        'queue': database.execute('SELECT id,status,card_id FROM daily_queue WHERE card_id=ANY(%s) ORDER BY position', (owned_cards,)).fetchall(),
    }


def audit_fixture(database, identity):
    """Raw receipt inventory, separate from the fault-state comparison contract."""
    card_ids(identity)
    owner = database.execute('SELECT source_name FROM repertoires WHERE id=%s', (identity,)).fetchone()
    queue_ids = []
    if owner and owner[0].startswith(MARKER + ':'):
        saved = json.loads(owner[0][len(MARKER) + 1:])
        queue_ids = [entry['id'] for entry in saved.get('owned_queue_entries', [])]
    columns = ('operation_id', 'command_name', 'payload_json', 'state')
    operations = [dict(zip(columns, row)) for row in database.execute(
        'SELECT operation_id,command_name,payload_json,state FROM operation_receipts ORDER BY operation_id').fetchall()]
    return {'queue_entry_ids': queue_ids, 'operations': operations}


def main():
    configure()
    action, identity = sys.argv[1:3]
    actions = {'seed': seed_fixture, 'inspect': inspect_fixture, 'cleanup': cleanup_fixture, 'audit': audit_fixture}
    if action not in actions:
        raise ValueError('Unknown fixture action')
    with psycopg.connect(DSN, connect_timeout=5) as database:
        result = actions[action](database, identity)
    # Connection context commits before reporting success, including cleanup.
    print(json.dumps(result, default=str))


if __name__ == '__main__':
    main()
