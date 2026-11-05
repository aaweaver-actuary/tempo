import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / 'public/data'


def test_existing_tactical_puzzles_survive_splitting_into_25_card_packs():
    manifest = json.loads((DATA / 'tactics-catalog.json').read_text())
    records = [record for pack in manifest['packs'] for record in json.loads((DATA.parent / pack['asset']).read_text())]
    by_identity = {record['PuzzleId']: record for record in records}
    for original in json.loads((DATA / 'tactics-decks.json').read_text()):
        migrated = by_identity[original['PuzzleId']]
        for field in ('FEN', 'Moves', 'Rating', 'Themes', 'GameUrl'):
            assert migrated[field] == original[field]
        assert migrated['LegacyDeckId'] == original['DeckId']
        assert migrated['LegacyDeckPosition'] == original['DeckPosition']


def test_expanded_tactical_catalog_contains_every_requested_theme_and_complete_pack():
    from app.services.puzzles import validate_puzzle_record
    manifest = json.loads((DATA / 'tactics-catalog.json').read_text())
    assert len(manifest['themes']) == 47
    assert len(manifest['packs']) == 692
    identities = set()
    for pack in manifest['packs']:
        records = json.loads((DATA.parent / pack['asset']).read_text())
        assert len(records) == pack['count'] == 25
        assert [record['DeckPosition'] for record in records] == list(range(1, 26))
        for record in records:
            assert record['DeckId'] == pack['id']
            assert record['PuzzleId'] not in identities
            identities.add(record['PuzzleId'])
            validate_puzzle_record(record)
    assert len(identities) == 17300
    assert manifest['source']['sha256']


def test_active_tactical_packs_share_one_daily_introduction_quota(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from helpers import wait_for_daily_queue
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    with TestClient(app) as client:
        packs = ['hangingPiece-easy-01', 'fork-hard-01']
        assert client.put('/api/tactics/activation', json={'pack_ids': packs, 'active': True}).status_code == 200
        wait_for_daily_queue(client, 5)
        with database.connection() as db:
            rows = db.execute('SELECT pack_id FROM tactic_introductions').fetchall()
            assert len(rows) == 5
            assert sorted([sum(row[0] == pack for row in rows) for pack in packs]) == [2, 3]
            assert db.execute("SELECT COUNT(*) FROM tactic_progress WHERE clean_pass_at IS NOT NULL").fetchone()[0] == 0


def test_deactivating_a_tactical_pack_preserves_scheduled_reviews(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from helpers import wait_for_daily_queue
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    with TestClient(app) as client:
        pack = 'hangingPiece-easy-01'
        client.put('/api/tactics/activation', json={'pack_ids':[pack], 'active':True})
        before = wait_for_daily_queue(client, 5)['cards']
        client.put('/api/tactics/activation', json={'pack_ids':[pack], 'active':False})
        after = wait_for_daily_queue(client, 5)['cards']
        assert len(before) == len(after) == 5
        assert {card['id'] for card in before} == {card['id'] for card in after}


def test_tactical_pack_migration_preserves_reviews_scheduling_and_completion(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    with TestClient(app):
        with database.connection() as db:
            db.execute("INSERT OR IGNORE INTO repertoires(id,name,source_name,created_at,is_main) VALUES('__tactics__','Tactics','Lichess','2026-09-01',0)")
            db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,clean_pass_at) VALUES('00sHx','hangingPiece-easy','2026-09-02')")
        review_count = 0
        with database.connection() as db:
            db.execute('DROP TABLE tactic_pack_activation')
            db.execute('DROP TABLE tactic_introductions')
            db.execute('DROP TABLE tactic_rotation')
    # Reopening a legacy schema applies the additive migration after an online backup.
    database.initialize()
    backup = tmp_path / 'tempo.db.before-tactics-v1.bak'
    assert backup.exists()
    with database.connection() as db:
        row = db.execute("SELECT deck_id,clean_pass_at FROM tactic_progress WHERE puzzle_id='00sHx'").fetchone()
        assert tuple(row) == ('hangingPiece-easy','2026-09-02')
        assert db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == review_count


def test_practice_in_an_inactive_pack_still_admits_the_puzzle_to_reviews(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from app.services.tactical_catalog import pack_records
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    record = pack_records('fork-easy-01')[0]
    with TestClient(app) as client:
        response = client.post('/api/tactics/attempt', json={'attempt_id':'inactive-practice','puzzle_id':record['PuzzleId'],'deck_id':'fork-easy-01','correct':True,'clean':True,'source_fen':record['FEN'],'moves':record['Moves'].split(),'rating':record['Rating']})
        assert response.status_code == 200
        with database.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM cards WHERE source_ref=? AND content_type='tactic'",(record['PuzzleId'],)).fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM tactic_pack_activation WHERE pack_id='fork-easy-01' AND active=1").fetchone()[0] == 0


def test_tactical_completion_counts_distinct_clean_solves_across_practice_and_training(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from app.services.tactical_catalog import pack_records
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    record = pack_records('pin-easy-01')[0]
    payload = {'puzzle_id':record['PuzzleId'],'deck_id':'pin-easy-01','correct':True,'clean':True,'source_fen':record['FEN'],'moves':record['Moves'].split(),'rating':record['Rating']}
    with TestClient(app) as client:
        for attempt in ('practice-once','practice-retry'):
            assert client.post('/api/tactics/attempt',json=payload|{'attempt_id':attempt}).status_code == 200
        pack = next(pack for pack in client.get('/api/tactics/catalog').json()['packs'] if pack['id']=='pin-easy-01')
        assert pack['clean'] == 1
        assert pack['introduced'] == 1


def test_stale_active_pack_does_not_block_valid_tactic_introduction(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from app.services.tactical_catalog import pack_records, seed_tactical_introductions

    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    pack = 'fork-easy-01'
    first = pack_records(pack)[0]['PuzzleId']
    day = '2026-11-04'
    with TestClient(app):
        with database.connection() as db:
            db.execute("UPDATE settings SET tactics_new_per_day=1 WHERE id=1")
            db.executemany('INSERT INTO tactic_pack_activation(pack_id,active) VALUES(?,1)', [('000-retired-fixture',), (pack,)])
            seed_tactical_introductions(db, day)
            seed_tactical_introductions(db, day)
            assert [tuple(row) for row in db.execute('SELECT puzzle_id,pack_id FROM tactic_introductions')] == [(first, pack)]
            assert tuple(db.execute("SELECT pack_id,active FROM tactic_pack_activation WHERE pack_id='000-retired-fixture'").fetchone()) == ('000-retired-fixture', 1)


def test_introduction_pack_lookup_only_suppresses_absent_catalog_entries(tmp_path, monkeypatch):
    import pytest
    from app.services import tactical_catalog as tactics

    with pytest.raises(tactics.UnknownTacticalPack, match='^Unknown tactical pack$'):
        tactics.pack_records('000-retired-fixture')
    assert tactics.introduction_pack_records('000-retired-fixture') == ()

    monkeypatch.setattr(tactics, 'DATA_ROOT', tmp_path)
    tactics.catalog.cache_clear()
    tactics.pack_records.cache_clear()
    try:
        data = tmp_path / 'data'
        data.mkdir()
        manifest = data / 'tactics-catalog.json'
        with pytest.raises(FileNotFoundError):
            tactics.introduction_pack_records('known')
        manifest.write_text(json.dumps({'version': 2, 'packs': []}))
        with pytest.raises(ValueError, match='Unsupported tactics catalog version'):
            tactics.introduction_pack_records('known')
        manifest.write_text(json.dumps({'version': 1, 'packs': [{'id': 'known', 'asset': 'missing.json'}]}))
        with pytest.raises(FileNotFoundError):
            tactics.introduction_pack_records('known')
        (tmp_path / 'missing.json').write_text('{')
        with pytest.raises(json.JSONDecodeError):
            tactics.introduction_pack_records('known')
    finally:
        tactics.catalog.cache_clear()
        tactics.pack_records.cache_clear()


def test_stale_only_pack_preserves_due_tactic_and_history_during_queue_seed(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from tempo_legacy_database import connections as database
    from app.main import app
    from app.services.queue_application import seed_queue
    from app.services.tactical_catalog import seed_tactical_introductions

    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    with TestClient(app):
        with database.connection() as db:
            db.execute('UPDATE settings SET tactics_new_per_day=1 WHERE id=1')
            db.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('fork-easy-01',1)")
            seed_tactical_introductions(db, '2026-11-03')
            card_id = db.execute('SELECT card_id FROM tactic_progress').fetchone()[0]
            db.execute("UPDATE tactic_pack_activation SET active=0 WHERE pack_id='fork-easy-01'")
            db.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('000-retired-fixture',1)")
            tables = ('tactic_pack_activation', 'tactic_progress', 'tactic_introductions', 'tactic_rotation', 'reviews')
            before = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')] for table in tables}
            seed_queue(db, '2026-11-04')
            after = {table: [tuple(row) for row in db.execute(f'SELECT * FROM {table}')] for table in tables}
            assert after == before
            assert db.execute("SELECT COUNT(*) FROM tactic_introductions WHERE introduction_date='2026-11-04'").fetchone()[0] == 0
            assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE queue_date='2026-11-04' AND card_id=?", (card_id,)).fetchone()[0] == 1
