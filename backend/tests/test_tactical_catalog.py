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
    from app import database
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
    from app import database
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
    from app import database
    from app.main import app
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'tempo.db')
    with TestClient(app):
        with database.connection() as db:
            db.execute("INSERT OR IGNORE INTO repertoires VALUES('__tactics__','Tactics','Lichess','2026-09-01',0)")
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
    from app import database
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
    from app import database
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
