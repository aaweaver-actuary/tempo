"""Native tactical refresh regression with an invocation-owned PostgreSQL database."""
import json

from test_product_game_lapse_postgres import owned_postgres
from app.services import postgres_queue_refresh as refresh, study_mode
from app.services.tactical_catalog import pack_records

DAY = '2026-11-04'


def test_stale_tactical_activation_allows_due_work_and_valid_pack(owned_postgres, monkeypatch):
    # The only connection seam points to this fixture's dedicated disposable DB.
    monkeypatch.setattr(refresh.read_storage, 'background_read_connection', owned_postgres)
    monkeypatch.setattr(refresh, 'background_read_connection', owned_postgres)
    monkeypatch.setattr(refresh, 'connection', lambda **_: owned_postgres())
    monkeypatch.setattr(refresh.read_storage, 'native_configured', lambda: True)
    monkeypatch.setattr(refresh.activity_gate, 'wait_for_foreground', lambda: None)
    monkeypatch.setattr(study_mode, 'read_connection', owned_postgres)
    monkeypatch.setattr(study_mode, 'background_read_connection', owned_postgres)
    with owned_postgres() as db:
        db.execute_native("UPDATE settings SET tactics_new_per_day=1,study_only_mode=1 WHERE id=1")
        db.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__tactics__','Tactics','fixture',%s) ON CONFLICT DO NOTHING", (DAY,))
        for cid, repertoire, content in [('fixture-opening','white','opening'), ('fixture-captured','__tactics__','tactic')]:
            db.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                content_type,introduced_at,source_ref) VALUES(%s,%s,'checkpoint',%s,'[\"e2e4\"]','learning',%s,%s,%s,%s)""",
                (cid,repertoire,'synthetic-fen',DAY,content,'2026-11-01',cid))
            db.execute_native("""INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,
                internal_rating,guided,source_kind,source_ref) VALUES(%s,'correct',%s,1,2,'good',0,'study',%s)""", (cid,DAY,cid))
        db.execute_native("INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,admitted_at) VALUES('fixture-captured','retired-history','fixture-captured','2026-11-01')")
        db.execute_native("INSERT INTO tactic_introductions(puzzle_id,pack_id,introduction_date) VALUES('fixture-captured','retired-history','2026-11-01')")
        db.execute_native("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('000-retired-fixture',1)")
        db.execute_native("INSERT INTO tactic_rotation(id,last_pack_id) VALUES(1,'') ON CONFLICT DO NOTHING")
        frozen = list(refresh.frozen_opening_order.activation(db))
        payload = {'queue_date': DAY, '_queue_phase': 'tactical_introductions', '_refresh_cycle_epoch': 1, 'frozen_token': frozen}
        db.execute_native("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,phase,payload_json,
            next_attempt_at,lease_token,created_at,updated_at) VALUES('fixture-refresh','daily_queue','fixture',1,'leased',
            'tactical_introductions',%s,%s,'fixture-lease',%s,%s)""", (json.dumps(payload),DAY,DAY,DAY))
        before = {table: [tuple(row) for row in db.execute_native(f'SELECT * FROM {table} ORDER BY 1').fetchall()]
                  for table in ('tactic_pack_activation','tactic_progress','tactic_introductions','tactic_rotation','reviews')}

    def slice_once():
        with owned_postgres() as db:
            row = db.execute_native("SELECT payload_json FROM background_tasks WHERE id='fixture-refresh'").fetchone()
            payload = json.loads(row[0])
            db.execute_native("UPDATE background_tasks SET state='leased',lease_token='fixture-lease' WHERE id='fixture-refresh'")
        task = {'id':'fixture-refresh','kind':'daily_queue','generation':1,'lease_token':'fixture-lease','payload':payload}
        assert refresh.execute_postgres_queue_refresh_slice(task)
        with owned_postgres() as db:
            row = db.execute_native("SELECT phase,payload_json FROM background_tasks WHERE id='fixture-refresh'").fetchone()
            return row[0], json.loads(row[1])

    phase, _ = slice_once()
    assert phase == 'reset_opening_new'
    with owned_postgres() as db:
        after = {table: [tuple(row) for row in db.execute_native(f'SELECT * FROM {table} ORDER BY 1').fetchall()]
                 for table in before}
        assert after == before
    for expected in ('reset_opening_stale','reconcile_unseen','admit_due'):
        phase, _ = slice_once()
        assert phase == expected
    for _ in range(2):
        assert slice_once()[0] == 'admit_due'
    with owned_postgres() as db:
        queued = {row[0] for row in db.execute_native('SELECT card_id FROM daily_queue WHERE queue_date=%s', (DAY,))}
        assert queued == {'fixture-opening','fixture-captured'}
        assert db.execute_native('SELECT COUNT(*) FROM tactic_introductions WHERE introduction_date=%s', (DAY,)).fetchone()[0] == 0
        # Fresh tactical phase and one free slot; retain the same stale activation.
        db.execute_native("INSERT INTO tactic_pack_activation(pack_id,active) VALUES('fork-easy-01',1)")
        payload = {'queue_date': DAY, '_queue_phase': 'tactical_introductions', '_refresh_cycle_epoch': 1, 'frozen_token': frozen}
        db.execute_native("UPDATE background_tasks SET payload_json=%s,phase='tactical_introductions' WHERE id='fixture-refresh'", (json.dumps(payload),))
    assert slice_once()[0] == 'tactical_introductions'
    assert slice_once()[0] == 'reset_opening_new'
    with owned_postgres() as db:
        rows = db.execute_native('SELECT puzzle_id,pack_id FROM tactic_introductions WHERE introduction_date=%s', (DAY,)).fetchall()
        assert [tuple(row) for row in rows] == [(pack_records('fork-easy-01')[0]['PuzzleId'], 'fork-easy-01')]
        assert db.execute_native('SELECT last_pack_id FROM tactic_rotation WHERE id=1').fetchone()[0] == 'fork-easy-01'
