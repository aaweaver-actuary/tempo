"""Regression: retained introduction history must not re-admit old puzzles."""
import pytest
from native_study_fixture import owned_postgres
from app.services import postgres_queue_refresh as refresh, tactical_catalog
from tempo_storage.queue_refresh import reads

DAY = '2026-11-12'

def candidate(puzzle):
    return dict(card_id='card-'+puzzle, puzzle_id=puzzle, training_fen='fen',
                solution_json='[]', source_fen='source', pack_id='pack', rotation_cursor='')

def setup(db):
    db.execute("UPDATE settings SET tactics_new_per_day=2 WHERE id=1")
    db.execute("INSERT INTO tactic_rotation(id,last_pack_id) VALUES(1,'') ON CONFLICT(id) DO UPDATE SET last_pack_id='' ")
    db.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('pack',1) ON CONFLICT(pack_id) DO UPDATE SET active=1")
    db.execute("INSERT INTO tactic_introductions VALUES('old-missing','old-pack','2026-11-09'),('old-null','old-pack','2026-11-09')")
    db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,admitted_at) VALUES('old-null','old-pack',NULL)")

def test_retained_history_excluded_and_stale_publication_preserves_it(owned_postgres, monkeypatch):
    with owned_postgres() as db:
        setup(db)
        monkeypatch.setattr(reads, '_bounded_read', lambda sql, parameters=(): db.execute_native(sql, parameters).fetchall())
        monkeypatch.setattr(refresh, 'pack_records', lambda pack: [dict(PuzzleId=p, FEN='source') for p in ('old-missing','old-null','new')])
        monkeypatch.setattr(refresh, 'validate_puzzle_record', lambda record: ('fen', []))
        monkeypatch.setattr(refresh, 'card_id', lambda fen, solution: 'card-new')
        assert refresh._prepare_tactical_introduction(DAY)['puzzle_id'] == 'new'
        for puzzle in ('old-missing','old-null'):
            assert refresh._publish_tactical_introduction(db, DAY, candidate(puzzle)) is True
        assert db.execute("SELECT COUNT(*) FROM cards WHERE content_type='tactic'").fetchone()[0] == 0
        assert db.execute("SELECT admitted_at FROM tactic_progress WHERE puzzle_id='old-null'").fetchone()[0] is None
        assert db.execute("SELECT introduction_date FROM tactic_introductions WHERE puzzle_id='old-missing'").fetchone()[0] == '2026-11-09'
        assert refresh._publish_tactical_introduction(db, DAY, candidate('new')) is True
        assert db.execute("SELECT COUNT(*) FROM cards WHERE content_type='tactic'").fetchone()[0] == 1
        assert refresh._publish_tactical_introduction(db, DAY, candidate('new')) is True
        db.execute("UPDATE settings SET tactics_new_per_day=0 WHERE id=1")
        assert refresh._prepare_tactical_introduction(DAY) is None

def test_legacy_writer_skips_retained_history_and_continues(owned_postgres, monkeypatch):
    with owned_postgres() as db:
        # Each case owns a transaction, but prior cases committed; use distinct identifiers.
        db.execute("DELETE FROM daily_queue WHERE card_id='card-new'")
        db.execute("DELETE FROM cards WHERE id='card-new'")
        db.execute("DELETE FROM tactic_progress WHERE puzzle_id IN ('old-missing','old-null','new')")
        db.execute("DELETE FROM tactic_introductions WHERE puzzle_id IN ('old-missing','old-null','new')")
        setup(db)
        monkeypatch.setattr(tactical_catalog, 'pack_records', lambda pack: [dict(PuzzleId=p, FEN='source') for p in ('old-missing','old-null','new')])
        monkeypatch.setattr(tactical_catalog, 'validate_puzzle_record', lambda record: ('fen', []))
        monkeypatch.setattr(tactical_catalog, 'card_id', lambda fen, solution: 'card-new')
        tactical_catalog.seed_tactical_introductions(db, DAY)
        assert db.execute("SELECT puzzle_id FROM tactic_introductions WHERE introduction_date=%s", (DAY,)).fetchall()[0][0] == 'new'
        assert db.execute("SELECT admitted_at FROM tactic_progress WHERE puzzle_id='old-null'").fetchone()[0] is None
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card-new'").fetchone()[0] == 1
        tactical_catalog.seed_tactical_introductions(db, DAY)
        assert db.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='card-new'").fetchone()[0] == 1

def test_introduction_claim_rolls_back_with_failed_admission(owned_postgres):
    from tempo_storage.queue_refresh import admissions
    with pytest.raises(RuntimeError):
        with owned_postgres() as db:
            assert admissions.insert_tactical_introduction(db, 'rolled-back', 'pack', DAY)
            raise RuntimeError('simulate later admission failure')
    with owned_postgres() as db:
        assert db.execute("SELECT 1 FROM tactic_introductions WHERE puzzle_id='rolled-back'").fetchone() is None
        assert admissions.insert_tactical_introduction(db, 'rolled-back', 'pack', DAY)
        assert not admissions.insert_tactical_introduction(db, 'rolled-back', 'other', DAY)

def test_concurrent_cross_day_claim_has_one_winner(owned_postgres):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tempo_storage.queue_refresh import admissions
    claimed, attempted, release = Event(), Event(), Event()
    def first():
        with owned_postgres() as db:
            won = admissions.insert_tactical_introduction(db, 'racing', 'pack', DAY)
            claimed.set()
            assert release.wait(3)
            return won
    def second():
        assert claimed.wait(3)
        with owned_postgres() as db:
            attempted.set()
            return admissions.insert_tactical_introduction(db, 'racing', 'other', '2026-11-13')
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(first)
        b = pool.submit(second)
        assert attempted.wait(3)
        release.set()
        assert a.result(timeout=5) is True
        assert b.result(timeout=5) is False
    with owned_postgres() as db:
        assert tuple(db.execute("SELECT pack_id,introduction_date FROM tactic_introductions WHERE puzzle_id='racing'").fetchone()) == ('pack', DAY)
